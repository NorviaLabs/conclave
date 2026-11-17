"""P5: token and time limits are real stops, with one step or several spending.

Once a run-wide limit is reached, no new step starts; a step already working
makes no further model call except one write-up of what it has. The run then
stops with its finished work saved, and says which limit stopped it. The limit
is part of the saved run, so such a run is not offered Continue.
"""

import asyncio

import pytest

from conclave import ToolRegistry
from conclave.application.definition import run_application
from conclave.application.sandbox import ControlPlaneAuthority, InProcessChannel, sandboxed_agent
from conclave.application.service import public_record
from parallel_support import Gated, collect, drive, plan, start, store_run, until

NOTE = {"type": "note", "content": "Still looking"}


def test_several_steps_spending_at_once_stop_at_the_token_limit():
    # a, b and c work side by side; d needs a. Each call reports 80 tokens.
    definition = plan(["a", "b", "c", "d"], [("a", "d")], limit=3, budget={"tokens": 200})
    provider = Gated(definition, tokens=80, scripts={step: [NOTE] * 6 for step in "abc"})
    events = drive(definition, provider)
    finished = events[-1]
    assert finished["status"] == "stopped" and finished["budget"] == "tokens"
    assert finished["reason"] == "Stopped: the run reached its token limit (200)"
    assert "d" not in provider.calls, "no step starts once the limit is reached"
    # Each of the three made its first call before any was charged, then one
    # write-up each: within the bound of calls in flight + 2 x steps at once.
    assert len(provider.calls) <= 3 + 2 * 3
    assert sorted(provider.write_ups) == ["a", "b", "c"]
    assert set(finished["checkpoint"]["patches"]) == {"a", "b", "c"}, "what they wrote up is saved"
    cut = {event["stage"]: event.get("cut_short") for event in events if event["type"] == "StageFinished"}
    assert cut == {"a": "tokens", "b": "tokens", "c": "tokens"}


def test_one_at_a_time_the_step_after_the_limit_does_not_start():
    definition = plan(["a", "b", "c"], [("a", "b"), ("b", "c")], limit=1, budget={"tokens": 100})
    provider = Gated(definition, tokens=80)
    events = drive(definition, provider)
    assert events[-1]["status"] == "stopped" and events[-1]["budget"] == "tokens"
    assert provider.calls == ["a", "b"], "b crossed the limit with its answer; c never started"
    assert set(events[-1]["checkpoint"]["patches"]) == {"a", "b"}


def test_a_running_step_stops_asking_when_another_step_uses_up_the_tokens():
    definition = plan(["a", "b"], limit=2, budget={"tokens": 100})
    provider = Gated(definition, gated=["a"], tokens={"a": 10, "b": 500}, scripts={"a": [NOTE] * 6})

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "b"), what="b to use up the tokens")
        provider.release("a")
        return await run.done()

    events = asyncio.run(go())
    assert provider.calls.count("a") == 2, "its call in flight, then its write-up"
    assert provider.write_ups == ["a"]
    a_finished = next(event for event in events if event["type"] == "StageFinished" and event["stage"] == "a")
    assert a_finished["status"] == "completed" and a_finished["cut_short"] == "tokens"
    # Every step finished, one of them cut short: the run is complete, and the
    # step says what stopped it.
    assert events[-1]["status"] == "completed"


def test_the_time_limit_stops_the_run_before_the_next_step():
    definition = plan(["a", "b", "c"], [("a", "b"), ("b", "c")], limit=1, budget={"wall_clock_seconds": 0.1})
    provider = Gated(definition, delays={"a": 0.2})
    events = drive(definition, provider)
    assert events[-1]["status"] == "stopped" and events[-1]["budget"] == "wall_clock_seconds"
    assert provider.calls == ["a"]


def test_a_run_stopped_by_a_run_wide_limit_is_not_offered_continue(tmp_path):
    definition = plan(["a", "b"], [("a", "b")], limit=1, budget={"tokens": 50})
    repository, run_id = store_run(tmp_path, definition)
    collect(repository.execute(run_id, Gated(definition, tokens=80), ToolRegistry(20000)))
    record = repository.get(run_id)
    assert record["status"] == "stopped"
    assert public_record(record)["resumable"] is False
    with pytest.raises(ValueError, match="token limit"):
        collect(repository.resume(run_id, Gated(definition), ToolRegistry(20000)))
    assert repository.get(run_id)["status"] == "stopped", "the refused resume changes nothing"


def test_the_token_limit_holds_for_a_loop_in_a_sandbox():
    definition = plan(["a"], budget={"tokens": 100})
    provider = Gated(definition, tokens=80, scripts={"a": [NOTE] * 6})

    def factory(provider, tools, budget, agents, *, config, approver=None):
        channel = InProcessChannel(ControlPlaneAuthority(provider, budget, {}, approver))
        return sandboxed_agent(channel, tools=tools, agents=agents, config=config, max_depth=budget.max_depth)

    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Work", loop=factory)]

    events = asyncio.run(go())
    # 80, then the write-up: a second call of about 80 would pass 100 (design §15).
    assert provider.calls == ["a", "a"], "80, then the write-up"
    assert provider.write_ups == ["a"]
    finished = next(event for event in events if event["type"] == "StageFinished")
    assert finished["cut_short"] == "tokens"


# --- Design §15 (found after step 6): a further call that would likely pass the
# limit, given the calls already in flight, is not made; the step writes up.

def spent_tokens(events):
    return max((event["used"] for event in events if event["type"] == "BudgetExceeded" and event["dimension"] == "tokens"),
               default=0)


def test_steps_side_by_side_do_not_start_calls_that_would_pass_the_limit_together():
    # What the browser run measured: 48,000 spent, three 12,000-token calls
    # about to start side by side under a 50,000 limit. It spent 120,000.
    definition = plan(["scope", "a", "b", "c", "d"], [("scope", step) for step in "abc"] + [("a", "d")], limit=3,
                      budget={"tokens": 50000})
    provider = Gated(definition, tokens=12000, scripts={step: [NOTE] * 6 for step in "abc"})
    events = drive(definition, provider)
    asked = [name for name in provider.calls if name in "abc"]
    assert sorted(asked) == ["a", "a", "b", "b", "c", "c"], "each step's first call, then only its write-up"
    assert sorted(provider.write_ups) == ["a", "b", "c"]
    assert events[-1]["status"] == "stopped" and events[-1]["budget"] == "tokens"
    assert events[-1]["checkpoint"]["budget"]["spent"]["tokens"] <= 84000


def test_one_step_alone_writes_up_instead_of_a_call_that_would_pass_the_limit():
    definition = plan(["a"], budget={"tokens": 100})
    provider = Gated(definition, tokens=30, scripts={"a": [NOTE] * 6})
    events = drive(definition, provider)
    assert provider.calls == ["a", "a", "a", "a"], "30, 60, 90, then the write-up instead of a call to 120 and beyond"
    assert provider.write_ups == ["a"]
    finished = next(event for event in events if event["type"] == "StageFinished")
    assert finished["cut_short"] == "tokens"


def test_the_projection_holds_for_a_loop_in_a_sandbox():
    definition = plan(["a"], budget={"tokens": 100})
    provider = Gated(definition, tokens=30, scripts={"a": [NOTE] * 6})

    def factory(provider, tools, budget, agents, *, config, approver=None):
        channel = InProcessChannel(ControlPlaneAuthority(provider, budget, {}, approver))
        return sandboxed_agent(channel, tools=tools, agents=agents, config=config, max_depth=budget.max_depth)

    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Work", loop=factory)]

    asyncio.run(go())
    assert provider.calls == ["a", "a", "a", "a"]
    assert provider.write_ups == ["a"]
