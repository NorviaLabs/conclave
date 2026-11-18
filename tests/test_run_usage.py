"""What a run used, step by step, and warnings before a limit is reached.

Each model call is reported as it happens (`ModelCalled`: who, on which
backend, the tokens the backend measured and how long it took, never the
prompt or the reply). Every finished step carries its own totals, the run
record adds them up per step, and a run with a limit warns once, at 80% of it
unless the application chose its own threshold.
"""

import asyncio
import copy
import json
import math
from dataclasses import replace

import pytest

from conclave import Capability, ModelCalled, RunBudget, SharedStore, StoreSchema, Tool, ToolRegistry, Usage
from conclave.application.definition import (_request_fingerprint, _restore_checkpoint, public_runtime_event,
                                             run_application, validate_definition)
from conclave.application.presentation import render_run
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, public_record
from conclave.events import event_payload, rebuild_event
from conftest import AGENTS, CONFIG, ScriptedProvider, make_budget
from test_studio_host import wait_for_status
from test_studio_runs import collect, request, with_final_stage

MEASURED = Usage(input_tokens=30, output_tokens=12)


class Scripted(ScriptedProvider):
    """A scripted backend that also serves the backends a step names."""

    def for_call(self, provider, model):
        return self


def call(name, **arguments):
    return {"type": "tool", "name": name, "arguments": arguments}


def text(value):
    return {"type": "final", "content": "Done", "text": value}


def tool(name, *, fails=False):
    async def run(**arguments):
        if fails:
            raise RuntimeError("cannot look")
        return "looked"
    return Tool(name=name, description=name, parameters={}, func=run, capabilities=frozenset({Capability.READ}))


def registry():
    tools = ToolRegistry(20000)
    tools.register(tool("look"))
    tools.register(tool("broken", fails=True))
    tools.register(tool("hidden"))
    return tools


def plan(**budget):
    """research -> write, both AI; the researcher may look (and may ask a helper)."""
    return {
        "version": 1, "name": "Usage",
        "agents": [{"id": "researcher", "instructions": "Research", "tools": ["look", "broken"], "provider": "opencode", "model": "m1"},
                   {"id": "helper", "instructions": "Help", "tools": ["look"]},
                   {"id": "writer", "instructions": "Write", "tools": []}],
        "state": [{"key": "notes", "type": "text", "merge": "replace", "statuses": []},
                  {"key": "brief", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": "research", "name": "Research the issue", "agent": "researcher", "task": "Research", "output": "notes"},
                     {"id": "write", "name": "Write it up", "agent": "writer", "task": "Write", "output": "brief"}],
        "connections": [{"from": "research", "to": "write"}],
        "views": [{"id": "brief_view", "collection": "brief", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None, **budget},
    }


def run(definition, provider, tools=None, **options):
    async def go():
        return [event async for event in run_application(definition, provider, tools or registry(), "Find out", **options)]
    return asyncio.run(go())


def of(events, kind):
    return [event for event in events if event["type"] == kind]


def finished(events, stage):
    return next(event for event in events if event["type"] == "StageFinished" and event["stage"] == stage)


# --- Each model call, as it happens ----------------------------------------------------


def test_usage_model_called_is_reported_after_every_model_call(runtime):
    spec = replace(AGENTS["repo-researcher"], provider="opencode", model="m1")
    provider = Scripted({"repo-researcher": [{"type": "note", "content": "One"}, {"type": "final", "content": "Done"}]},
                                usage=MEASURED)

    async def go():
        return [event async for event in runtime(provider).stream(spec, "task")]
    calls = [event for event in asyncio.run(go()) if isinstance(event, ModelCalled)]
    assert len(calls) == len(provider.prompts) == 2
    assert {(item.agent, item.provider, item.model, item.input_tokens, item.output_tokens) for item in calls} == {
        ("repo-researcher", "opencode", "m1", 30, 12)}
    assert all(isinstance(item.seconds, float) and item.seconds >= 0 for item in calls)


def test_usage_model_called_without_measured_tokens_says_none(runtime):
    provider = Scripted({"repo-researcher": [{"type": "final", "content": "Done"}]})

    async def go():
        return [event async for event in runtime(provider).stream(AGENTS["repo-researcher"], "task")]
    (called,) = [event for event in asyncio.run(go()) if isinstance(event, ModelCalled)]
    assert called.input_tokens is None and called.output_tokens is None


def test_usage_write_up_call_is_reported_too(runtime):
    from conclave.protocol import WRITE_UP_RULE
    budget = make_budget(wall_clock_seconds=1)
    budget.spent["wall_clock_seconds"] = 2
    provider = Scripted({"repo-researcher": [{"type": "note", "content": "One"}, {"type": "final", "content": "Done"}]},
                                usage=MEASURED)

    async def go():
        return [event async for event in runtime(provider, run_budget=budget).stream(AGENTS["repo-researcher"], "task")]
    calls = [event for event in asyncio.run(go()) if isinstance(event, ModelCalled)]
    assert WRITE_UP_RULE in provider.prompts[-1][1]
    assert len(calls) == len(provider.prompts) == 2


def test_usage_model_called_accepts_values_at_their_bounds():
    ModelCalled(run_id="r", agent="a" * 200, provider="p" * 200, model="m" * 200, input_tokens=10**12, output_tokens=0, seconds=86_400)


def test_usage_model_called_carries_no_prompt_or_reply():
    event = ModelCalled(run_id="r", agent="a", provider="p", model="m", input_tokens=1, output_tokens=2, seconds=0.5)
    assert public_runtime_event(event) == {"type": "ModelCalled", "run_id": "r", "agent": "a", "provider": "p", "model": "m",
                                           "input_tokens": 1, "output_tokens": 2, "seconds": 0.5}


@pytest.mark.parametrize("field, value", [("input_tokens", -1), ("output_tokens", 1.5), ("input_tokens", True),
                                          ("seconds", -0.1), ("seconds", math.inf), ("seconds", "1"),
                                          # Bounded, so a loop elsewhere cannot fill the run record.
                                          ("agent", "a" * 201), ("provider", "p" * 201), ("model", "m" * 201),
                                          ("input_tokens", 10**12 + 1), ("output_tokens", 10**15), ("seconds", 86_400.5)])
def test_usage_model_called_from_a_sandbox_is_checked(field, value):
    payload = event_payload(ModelCalled(run_id="r", agent="a", provider="p", model="m", input_tokens=1, output_tokens=2, seconds=0.5))
    assert rebuild_event(payload, CONFIG) == ModelCalled(run_id="r", agent="a", provider="p", model="m",
                                                         input_tokens=1, output_tokens=2, seconds=0.5)
    with pytest.raises(ValueError):
        rebuild_event({**payload, field: value}, CONFIG)


# --- Each step's totals ----------------------------------------------------------------


def test_usage_every_finished_step_carries_its_totals():
    provider = Scripted({"researcher": [call("look"), call("broken"), call("hidden"), text("Notes")],
                                 "writer": [text("Brief")]}, usage=MEASURED)
    events = run(plan(), provider)
    assert events[-1]["status"] == "completed"
    research = finished(events, "research")["usage"]
    assert research == {"model_calls": 4, "measured_calls": 4, "member_calls": 0, "input_tokens": 120, "output_tokens": 48,
                        "tool_calls": 3, "tool_failures": 1, "tool_denied": 1, "seconds": research["seconds"]}
    assert isinstance(research["seconds"], float) and research["seconds"] >= 0
    assert finished(events, "write")["usage"]["model_calls"] == 1
    calls = of(events, "ModelCalled")
    assert [item["stage"] for item in calls] == ["research"] * 4 + ["write"]
    assert calls[0]["provider"] == "opencode" and calls[0]["model"] == "m1"


def test_usage_calls_by_a_member_the_step_asked_count_to_the_step():
    provider = Scripted({"researcher": [{"type": "delegate", "agent": "helper", "task": "Look", "purpose": "Look"}, text("Notes")],
                                 "helper": [call("look"), {"type": "final", "content": "Looked"}],
                                 "writer": [text("Brief")]}, usage=MEASURED)
    events = run(plan(), provider)
    research = finished(events, "research")["usage"]
    assert research["model_calls"] == 4 and research["member_calls"] == 2 and research["tool_calls"] == 1


def test_usage_unmeasured_calls_are_counted_but_not_measured():
    provider = Scripted({"researcher": [text("Notes")], "writer": [text("Brief")]})
    events = run(plan(), provider)
    usage = finished(events, "research")["usage"]
    assert usage["model_calls"] == 1 and usage["measured_calls"] == 0 and usage["input_tokens"] == 0
    assert of(events, "ModelCalled")[0]["input_tokens"] is None


def test_usage_output_repair_call_is_counted_and_marked():
    provider = Scripted({"researcher": [{"type": "final", "content": "Done", "text": ["not", "text"]}, text("Notes")], "writer": [text("Brief")]},
                                usage=MEASURED)
    events = run(plan(), provider)
    assert of(events, "OutputRepairCompleted")
    repair = [item for item in of(events, "ModelCalled") if item.get("repair")]
    assert len(repair) == 1 and repair[0]["stage"] == "research" and repair[0]["input_tokens"] == 30
    assert finished(events, "research")["usage"]["model_calls"] == 2


def test_usage_is_on_a_step_that_stopped():
    provider = Scripted({"researcher": ["not json"] * 3, "writer": [text("Brief")]}, usage=MEASURED)
    definition = plan()
    definition["run_config"]["max_tool_steps"] = 1
    events = run(definition, provider)
    stopped = finished(events, "research")
    assert stopped["status"] != "completed" and stopped["usage"]["model_calls"] >= 1


# --- Warnings before a limit -----------------------------------------------------------


def test_budget_warning_is_automatic_at_80_percent_of_a_limit():
    # Each call measures 42 tokens: the second passes 80 of 100, the third would pass the limit.
    provider = Scripted({"researcher": [call("look"), text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    events = run(plan(tokens=100), provider)
    (warning,) = of(events, "BudgetWarning")
    assert {key: warning[key] for key in ("dimension", "threshold", "limit", "automatic", "used", "stage")} == {
        "dimension": "tokens", "threshold": 80, "limit": 100, "automatic": True, "used": 84, "stage": "research"}


def test_budget_warning_for_tool_calls_is_automatic_too():
    provider = Scripted({"researcher": [call("look"), call("look"), call("look"), call("look"), text("Notes")],
                                 "writer": [text("Brief")]})
    events = run(plan(tool_calls=5), provider)
    (warning,) = of(events, "BudgetWarning")
    assert warning["dimension"] == "tool_calls" and warning["threshold"] == 4 and warning["limit"] == 5 and warning["used"] == 4


def test_budget_warning_explicit_threshold_is_used_instead():
    definition = plan(tokens=1000)
    definition["budget_warnings"] = {"tokens": 40}
    validate_definition(definition)
    provider = Scripted({"researcher": [text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    (warning,) = of(run(definition, provider), "BudgetWarning")
    assert warning["threshold"] == 40 and warning["limit"] == 1000 and warning["automatic"] is False and warning["used"] == 42


def test_budget_warning_null_threshold_turns_the_automatic_warning_off():
    definition = plan(tokens=100)
    definition["budget_warnings"] = {"tokens": None}
    validate_definition(definition)
    provider = Scripted({"researcher": [call("look"), text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    assert of(run(definition, provider), "BudgetWarning") == []


def test_budget_warning_is_not_given_without_a_limit():
    provider = Scripted({"researcher": [call("look"), text("Notes")], "writer": [text("Brief")]},
                                usage=Usage(input_tokens=100000, output_tokens=1))
    assert of(run(plan(), provider), "BudgetWarning") == []


def reviewed_plan(**budget):
    definition = plan(**budget)
    definition["agents"].append({"id": "reviewer", "kind": "human", "instructions": "Review", "tools": []})
    definition["workflow"].insert(1, {"id": "review", "agent": "reviewer", "task": "Review", "output": "notes"})
    definition["connections"] = [{"from": "research", "to": "review"}, {"from": "review", "to": "write"}]
    return definition


def test_budget_warning_is_kept_in_the_checkpoint_and_not_given_again():
    # Four calls measure 168 tokens: past 160 of 200, short of the limit.
    definition = reviewed_plan(tokens=200)
    provider = Scripted({"researcher": [call("look"), call("look"), call("look"), text("Notes")],
                                 "writer": [text("Brief")]}, usage=MEASURED)
    events = run(definition, provider)
    assert events[-1]["status"] == "waiting_for_human"
    assert len(of(events, "BudgetWarning")) == 1 and events[-1]["checkpoint"]["warned"] == ["tokens"]
    resumed = run(definition, provider, checkpoint=events[-1]["checkpoint"], human_inputs={"review": {"text": "Looks right"}})
    assert of(resumed, "BudgetWarning") == []


@pytest.mark.parametrize("warned", ["tokens", ["bogus"], ["tokens", "tokens"], ["cost_usd"], [1]])
def test_budget_warning_a_tampered_checkpoint_is_refused(warned):
    definition = reviewed_plan(tokens=200)
    provider = Scripted({"researcher": [call("look"), call("look"), call("look"), text("Notes")]}, usage=MEASURED)
    checkpoint = run(definition, provider)[-1]["checkpoint"]
    fingerprint = _request_fingerprint(definition, "Find out", {})

    def restore(changed):
        async def go():
            budget = RunBudget(**definition["run_budget"])
            store = SharedStore(StoreSchema(keys=frozenset(item["key"] for item in definition["state"])),
                                {item["key"]: "" for item in definition["state"]})
            return await _restore_checkpoint(changed, fingerprint, definition, store, budget)
        return asyncio.run(go())

    restore(checkpoint)
    with pytest.raises(ValueError, match="warning"):
        restore({**copy.deepcopy(checkpoint), "warned": warned})


# --- The run record --------------------------------------------------------------------


def executed(tmp_path, value, provider, tools=None):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value)
    collect(repository.execute(run_id, provider, tools or registry()))
    return repository.get(run_id)


def test_usage_run_record_adds_up_each_step_and_the_run(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan(tokens=1000, tool_calls=10)}
    provider = Scripted({"researcher": [call("look"), call("broken"), text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    usage = public_record(executed(tmp_path, value, provider))["usage"]
    assert usage["recorded"] is True
    assert set(usage["steps"]) == {"research", "write"}
    research = usage["steps"]["research"]
    assert (research["model_calls"], research["input_tokens"], research["output_tokens"], research["tool_calls"],
            research["tool_failures"], research["runs"], research["unmeasured"]) == (3, 90, 36, 2, 1, 1, [])
    total = usage["total"]
    assert (total["model_calls"], total["measured_calls"], total["tokens"], total["tool_calls"]) == (4, 4, 168, 2)
    assert total["cost_usd"] is None
    assert usage["unmeasured_backends"] == []
    assert usage["limits"]["tokens"] == {"limit": 1000, "used": 168, "warn_at": 800}
    assert usage["limits"]["tool_calls"] == {"limit": 10, "used": 2, "warn_at": 8}
    assert "wall_clock_seconds" not in usage["limits"] and usage["warnings"] == []


def test_usage_run_record_names_backends_that_did_not_measure(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan()}
    usage = public_record(executed(tmp_path, value, Scripted({"researcher": [text("Notes")], "writer": [text("Brief")]})))["usage"]
    assert usage["steps"]["research"]["unmeasured"] == ["opencode"]
    assert usage["steps"]["write"]["unmeasured"] == ["default"]
    assert usage["unmeasured_backends"] == ["default", "opencode"]
    assert usage["total"]["measured_calls"] == 0 and usage["total"]["tokens"] == 0


def test_usage_run_record_adds_up_a_step_that_ran_again(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan()}
    record = executed(tmp_path, value, Scripted({"researcher": [text("Notes")], "writer": [text("Brief")]}, usage=MEASURED))
    again = copy.deepcopy(next(item for item in record["events"] if item["event"]["type"] == "StageFinished"
                               and item["event"]["stage"] == "research"))
    again["sequence"] = 10_000
    record["events"].append(again)
    research = public_record(record)["usage"]["steps"]["research"]
    assert research["runs"] == 2 and research["model_calls"] == 2 and research["input_tokens"] == 60


def test_usage_run_record_lists_its_warnings(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan(tokens=100)}
    provider = Scripted({"researcher": [call("look"), text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    usage = public_record(executed(tmp_path, value, provider))["usage"]
    assert usage["warnings"] == [{"dimension": "tokens", "used": 84, "threshold": 80, "limit": 100, "automatic": True,
                                  "stage": "research"}]


def test_usage_old_run_record_says_per_step_usage_was_not_recorded():
    record = {"id": "old", "status": "completed", "request": request(), "reviews": [],
              "events": [{"sequence": 1, "event": {"type": "StageFinished", "stage": "draft", "agent": "writer", "status": "completed"}},
                         {"sequence": 2, "event": {"type": "ApplicationFinished", "status": "completed",
                                                   "metrics": {"tool_calls": 9, "wall_clock_seconds": 182.0}}}]}
    usage = public_record(record)["usage"]
    assert usage["recorded"] is False and usage["steps"] == {}
    assert usage["total"]["tool_calls"] == 9 and usage["total"]["seconds"] == 182.0
    assert usage["total"]["tokens"] is None and usage["total"]["model_calls"] is None


def test_usage_is_in_the_record_the_host_returns(tmp_path):
    async def exercise():
        provider = Scripted(default=text("Brief"), usage=MEASURED)
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="operator")
        _, started = await host.handle("start", with_final_stage())
        await wait_for_status(host, started["id"], "waiting_for_human")
        _, record = await host.handle("get", {"run_id": started["id"]})
        assert record["usage"]["steps"]["draft"]["input_tokens"] == 30
        _, status = await host.handle("runtime", {})
        assert status["features"]["run_usage"] is True
        await host.close()
    asyncio.run(exercise())


def test_usage_record_never_holds_prompts_or_replies(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan()}
    provider = Scripted({"researcher": [text("SECRET-REPLY-TEXT")], "writer": [text("Brief")]}, usage=MEASURED)
    usage = public_record(executed(tmp_path, value, provider))["usage"]
    assert "SECRET" not in json.dumps(usage) and "Research" not in json.dumps(usage)


# --- The terminal -----------------------------------------------------------------------


def test_usage_render_run_prints_each_step_and_the_warnings(tmp_path):
    value = {**request(), "inputs": {}, "definition": plan(tokens=100)}
    provider = Scripted({"researcher": [call("look"), text("Notes")], "writer": [text("Brief")]}, usage=MEASURED)
    text_block = render_run(public_record(executed(tmp_path, value, provider)))
    assert "Usage" in text_block
    assert "  research  2 model calls · 84 tokens · 1 tool call" in text_block
    assert "Warning    Close to the token limit: 84 of 100 (84%)" in text_block


def test_usage_render_run_of_an_old_run_says_it_was_not_recorded():
    record = {"id": "old", "status": "completed", "request": request(), "reviews": [],
              "events": [{"sequence": 1, "event": {"type": "ApplicationFinished", "status": "completed", "metrics": {"tool_calls": 9}}}]}
    assert "Per-step usage was not recorded for this run" in render_run(record)


def test_usage_a_local_call_past_the_bounds_is_clipped_not_failed():
    from types import SimpleNamespace
    from conclave.runtime import model_called
    spec = replace(AGENTS["repo-researcher"], name="n" * 300, provider="p" * 300)
    called = model_called("r", spec, SimpleNamespace(usage=Usage(input_tokens=10**13, output_tokens=5)), 10**6)
    assert len(called.agent) == len(called.provider) == 200
    assert called.input_tokens is None and called.output_tokens == 5 and called.seconds == 86_400
