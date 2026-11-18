"""A step with its own time limit is stopped while it works when it runs over.

`workflow[].time_limit_seconds` bounds one step's real working time — model
calls, tools, members it asks, output repair — but not time spent waiting for a
person to approve an action. A step over its limit is cancelled where it is,
recorded as `timed_out`, and the run stops once the steps beside it finish,
with a checkpoint: Continue runs the step again with its full limit.

Limits are whole seconds from 10 up; the tests run the clock 100 times faster.
"""

import asyncio
import copy
import time

import pytest

from conclave import Capability, Tool, ToolRegistry, Usage
from conclave.application import definition as definition_module
from conclave.application.definition import run_application, validate_definition
from conclave.application.presentation import render_run
from conclave.application.run_compare import compare_runs
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, public_record
from conclave.application.versions import compare_definitions
from conftest import CONFIG, ScriptedProvider
from test_studio_host import wait_for_status
from test_studio_runs import collect

FINAL = {"type": "final", "content": "Done", "text": "Notes"}


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """10 seconds of a step's limit pass in 0.1 s here."""
    monkeypatch.setattr(definition_module, "STEP_TIME_SCALE", 0.01)


class Slow(ScriptedProvider):
    """Answers after `delays[agent]` real seconds; says when it was stopped."""

    def __init__(self, scripts=None, delays=None, **options):
        super().__init__(scripts, **options)
        self.delays, self.cancelled, self.calls = delays or {}, [], []

    async def complete(self, messages):
        name = self.agent_name(messages)
        self.calls.append(name)
        delay = self.delays.get(name, 0)
        if isinstance(delay, list):
            delay = delay.pop(0) if delay else 0
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        return await super().complete(messages)


def plan(limit=10, parallel=False, **extra):
    """draft (AI, limited) -> finish (AI); or side by side with `parallel`."""
    definition = {
        "version": 1, "name": "Release",
        "agents": [{"id": "writer", "instructions": "Write", "tools": ["publish"]}, {"id": "editor", "instructions": "Edit", "tools": []}],
        "state": [{"key": "notes", "type": "text", "merge": "replace", "statuses": []},
                  {"key": "final", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": "draft", "name": "Draft the notes", "agent": "writer", "task": "Draft", "output": "notes", **extra},
                     {"id": "finish", "name": "Finish", "agent": "editor", "task": "Finish", "output": "final"}],
        "connections": [] if parallel else [{"from": "draft", "to": "finish"}],
        "views": [{"id": "notes_view", "collection": "notes", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    if limit is not None:
        definition["workflow"][0]["time_limit_seconds"] = limit
    return definition


def publish_tool():
    async def publish(**arguments):
        return "published"
    return Tool(name="publish", description="Publish", parameters={}, func=publish, capabilities=frozenset({Capability.WRITE}))


def tools():
    registry = ToolRegistry(20000)
    registry.register(publish_tool())
    return registry


def run(definition, provider, **options):
    async def go():
        return [event async for event in run_application(definition, provider, tools(), "Release", **options)]
    start = time.perf_counter()
    events = asyncio.run(go())
    return events, time.perf_counter() - start


def finished(events, stage):
    return [event for event in events if event["type"] == "StageFinished" and event["stage"] == stage][-1]


# --- Setting a limit -----------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [10, 300, 86400])
def test_step_time_limit_accepted(limit):
    validate_definition(plan(limit))


@pytest.mark.parametrize("limit", [9, 0, -5, 86401, 12.5, True, "60", None])
def test_step_time_limit_refused_when_out_of_range(limit):
    definition = plan(None)
    definition["workflow"][0]["time_limit_seconds"] = limit
    with pytest.raises(ValueError, match="time limit"):
        validate_definition(definition)


def test_step_time_limit_refused_on_a_persons_step():
    definition = plan(None)
    definition["agents"][1]["kind"] = "human"
    definition["agents"][1]["tools"] = []
    definition["workflow"][1]["time_limit_seconds"] = 60
    with pytest.raises(ValueError, match="person"):
        validate_definition(definition)


def test_step_time_limit_named_in_a_version_comparison():
    changes = compare_definitions(plan(300), plan(600))
    texts = [change["text"] for area in changes["areas"] for change in area["changes"]]
    assert "Step “Draft the notes”: time limit was 5 m, now 10 m" in texts
    removed = compare_definitions(plan(300), plan(None))
    assert "Step “Draft the notes”: time limit was 5 m, now none" in [change["text"] for area in removed["areas"] for change in area["changes"]]


# --- Running over --------------------------------------------------------------------------------


def test_step_timeout_stops_the_step_while_it_works():
    provider = Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 5})
    events, seconds = run(plan(10), provider)
    assert seconds < 2, "the step was stopped, not waited out"
    assert provider.cancelled == ["writer"], "the model call under way was stopped"
    draft = finished(events, "draft")
    assert draft["status"] == "timed_out" and draft["time_limit_seconds"] == 10
    assert 10 <= draft["elapsed_seconds"] < 30
    assert draft["reason"] == "“Draft the notes” ran past its time limit (10 s)"
    assert not any(event["type"] == "ApplicationStateChanged" and event["stage"] == "draft" for event in events), "no half-done output"
    assert not any(event["type"] == "StageStarted" and event["stage"] == "finish" for event in events)
    end = events[-1]
    assert end["type"] == "ApplicationFinished" and end["status"] == "stopped" and end["timed_out"] is True
    assert end["stage"] == "draft" and end["time_limit_seconds"] == 10 and "checkpoint" in end


def test_step_timeout_keeps_what_the_step_used():
    provider = Slow({"writer": [{"type": "note", "content": "One"}, FINAL]}, delays={"writer": [0, 5]},
                    usage=Usage(input_tokens=30, output_tokens=5))
    events, _ = run(plan(10), provider)
    usage = finished(events, "draft")["usage"]
    assert usage["model_calls"] == 1 and usage["input_tokens"] == 30, "the call that finished is counted"


def test_step_timeout_lets_the_steps_beside_it_finish():
    provider = Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 5, "editor": 0.02})
    events, _ = run(plan(10, parallel=True), provider)
    assert finished(events, "finish")["status"] == "completed"
    # Its result waits for the earlier step, as any step's does; the run keeps it.
    assert events[-1]["checkpoint"]["patches"]["finish"] == "Notes"
    assert finished(events, "draft")["status"] == "timed_out" and events[-1]["status"] == "stopped"


def test_step_timeout_applies_to_an_output_repair():
    provider = Slow({"writer": [{"type": "final", "content": "Done", "text": ["not", "text"]}, FINAL]}, delays={"writer": [0, 5]})
    events, seconds = run(plan(10), provider)
    assert any(event["type"] == "OutputRepairStarted" for event in events)
    assert finished(events, "draft")["status"] == "timed_out" and seconds < 2


def test_step_timeout_not_without_a_limit():
    provider = Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 0.3})
    events, _ = run(plan(None), provider)
    assert events[-1]["status"] == "completed"


def test_step_timeout_not_when_it_finishes_in_time():
    provider = Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 0.02})
    events, _ = run(plan(10), provider)
    assert events[-1]["status"] == "completed" and finished(events, "draft")["status"] == "completed"


def test_step_time_limit_pauses_while_a_person_approves():
    asked = []

    async def slow_person(request):
        asked.append(request.tool)
        await asyncio.sleep(0.4)  # four times the step's whole limit here
        return True
    provider = Slow({"writer": [{"type": "tool", "name": "publish", "arguments": {}}, FINAL], "editor": [FINAL]})
    events, _ = run(plan(10), provider, approver=slow_person)
    assert asked == ["publish"]
    assert finished(events, "draft")["status"] == "completed" and events[-1]["status"] == "completed"


def test_step_time_limit_still_counts_work_after_an_approval():
    async def quick_person(request):
        return True
    provider = Slow({"writer": [{"type": "tool", "name": "publish", "arguments": {}}, FINAL]}, delays={"writer": [0, 5]})
    events, seconds = run(plan(10), provider, approver=quick_person)
    assert finished(events, "draft")["status"] == "timed_out" and seconds < 2


# --- After a timeout -----------------------------------------------------------------------------


def wrapped(definition):
    return {"version": 1, "application_id": "release", "revision": 2, "task": "Release", "inputs": {}, "definition": definition}


def test_step_timeout_continue_runs_it_again_with_its_full_limit(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(wrapped(plan(10)))
    collect(repository.execute(run_id, Slow({"writer": [FINAL]}, delays={"writer": 5}), tools()))
    record = repository.get(run_id)
    assert record["status"] == "stopped" and public_record(record)["resumable"] is True
    # Continued: the step starts again with all 10 s, and here it needs 0.05 s of 0.1 s.
    provider = Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 0.05})
    events = collect(repository.resume(run_id, provider, tools()))
    assert events[-1]["status"] == "completed" and finished(events, "draft")["status"] == "completed"


def test_step_timeout_is_not_retried_automatically(tmp_path):
    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), Slow({"writer": [FINAL]}, delays={"writer": 5}), tools(),
                            reviewer="operator")
        _, started = await host.handle("start", wrapped(plan(10)))
        await wait_for_status(host, started["id"], "stopped")
        _, record = await host.handle("get", {"run_id": started["id"]})
        assert record["retry"] is None and record["resumable"] is True
        assert not any(item["event"]["type"] == "RetryScheduled" for item in record["events"])
        _, runtime = await host.handle("runtime", {})
        assert runtime["features"]["step_time_limits"] is True
        await host.close()
    asyncio.run(exercise())


def test_step_timeout_in_the_terminal_and_a_run_comparison(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    first = repository.create(wrapped(plan(10)))
    collect(repository.execute(first, Slow({"writer": [FINAL], "editor": [FINAL]}, delays={"writer": 0.01}), tools()))
    second = repository.create(wrapped(plan(10)))
    collect(repository.execute(second, Slow({"writer": [FINAL]}, delays={"writer": 5}), tools()))
    record = public_record(repository.get(second))
    assert "  1  draft   writer  timed out (10 s limit)" in render_run(record)
    comparison = compare_runs(public_record(repository.get(first)), record)
    draft = next(step for step in comparison["steps"] if step["id"] == "draft")
    assert draft["status"]["b"] == {"status": "timed_out", "reason": "“Draft the notes” ran past its time limit (10 s)"}
    assert draft["differs"] is True


# --- Security: what a stopped step was doing stops too -------------------------------------------


def alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A process that has exited but is not yet collected is not running.
    try:
        with open(f"/proc/{pid}/stat") as status:
            return status.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def test_step_timeout_kills_a_command_the_step_was_running(tmp_path, monkeypatch):
    import json as json_module
    import os
    import sys
    from conclave.tools.changes import Changes, build_change_tools

    monkeypatch.setattr(definition_module, "STEP_TIME_SCALE", 0.1)  # 10 s of limit in 1 s
    marker = tmp_path / "pid"
    script = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    registry = ToolRegistry(20000)
    for item in build_change_tools(Changes(tmp_path)):
        registry.register(item)
    definition = plan(10)
    definition["agents"][0]["tools"] = ["run_tests"]
    command = " ".join([sys.executable, "-c", json_module.dumps(script)])
    provider = Slow({"writer": [{"type": "tool", "name": "run_tests", "arguments": {"command": command, "timeout": 120}}, FINAL]})

    async def approve(request):
        return True

    async def go():
        return [event async for event in run_application(definition, provider, registry, "Release", approver=approve)]
    started = time.perf_counter()
    try:
        events = asyncio.run(go())
        assert time.perf_counter() - started < 5
        assert finished(events, "draft")["status"] == "timed_out"
        pid = int(marker.read_text())
        deadline = time.perf_counter() + 3
        while alive(pid) and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert not alive(pid), "the command the stopped step started is still running"
    finally:
        if marker.exists() and alive(int(marker.read_text())):
            os.kill(int(marker.read_text()), 9)


def test_step_timeout_during_a_write_leaves_it_to_be_checked(tmp_path):
    """A write cut off by the limit may or may not have happened: nothing sends it again unchecked."""
    started = []

    async def hanging_publish(**arguments):
        started.append(arguments)
        await asyncio.sleep(30)
        return "published"
    registry = ToolRegistry(20000)
    registry.register(Tool(name="publish", description="Publish", parameters={}, func=hanging_publish,
                           capabilities=frozenset({Capability.WRITE})))

    async def approve(request):
        return True
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(wrapped(plan(10)))
    provider = Slow({"writer": [{"type": "tool", "name": "publish", "arguments": {"text": "4.2"}}, FINAL]})
    events = collect(repository.execute(run_id, provider, registry, approver=approve))
    assert started and finished(events, "draft")["status"] == "timed_out"
    assert repository.journal.uncertain(run_id), "the cut-off write is uncertain"
    with pytest.raises(Exception, match="Settle 1 action"):
        collect(repository.resume(run_id, provider, registry, approver=approve))


def test_step_time_limit_command_runner_keeps_its_own_timeout_and_output(tmp_path):
    import subprocess
    import sys
    from conclave.tools.processes import run_command
    done = run_command([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], cwd=tmp_path, timeout=30)
    assert (done.returncode, done.stdout, done.stderr) == (3, "out\n", "err\n")
    marker = tmp_path / "pid"
    script = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_command([sys.executable, "-c", script], cwd=tmp_path, timeout=1)
    assert not alive(int(marker.read_text())), "a command past its own timeout is killed too"
