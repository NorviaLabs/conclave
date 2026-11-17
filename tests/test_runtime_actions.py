"""T4 and T5 — the agent loop records each write before sending it, and resumes
from what it recorded.

These drive `ToolAgentRuntime` directly with an in-memory action log, so they
pin the core protocol without the run database: what is recorded when, what the
agent is told, and that nothing already sent is sent again.
"""

import asyncio
import json

import pytest

from actions_support import LOOK, Recorder, checker, note, registry
from conclave import AgentSpec, Completion, RunConfig, ToolAgentRuntime, ToolCalled, RunFinished
from conftest import ScriptedProvider, make_budget

CONFIG = RunConfig(max_tool_steps=6, max_notes=12, max_note_chars=300, max_tool_output_chars=20_000, fan_out_limit=4)
FINAL = {"type": "final", "content": "done"}
WRITER = AgentSpec(name="writer", instructions="Tell the team.", tools={"send_note", "look"})
HELPER = AgentSpec(name="helper", instructions="Send what you are asked to.", tools={"send_note"})


class FakeLog:
    """The action log a run database provides, kept in memory."""

    def __init__(self):
        self.order, self.rows, self.frames, self.checks, self.resolutions = [], {}, {}, {}, {}

    def begin(self, start):
        self.order.append(("begin", start.action_id))
        self.rows[start.action_id] = {"state": "sending", "start": start, "attempts": 1, "settled_by": None}
        self.frames[start.action_id] = [start.frames]

    def finish(self, action_id, state, *, frames, reason=""):
        self.order.append(("finish", action_id, state))
        self.rows[action_id]["state"] = state
        self.rows[action_id]["reason"] = reason
        self.frames[action_id].append(frames)

    def checked(self, action_id, result):
        self.order.append(("checked", action_id, result.result))
        self.checks[action_id] = result

    def settle(self, action_id, decision, *, by, note=None, kind="person"):
        self.order.append(("settle", action_id, decision, by))
        self.rows[action_id].update(state=decision, settled_by=by, settled_kind=kind)

    def resend(self, action_id):
        self.order.append(("resend", action_id))
        self.rows[action_id]["state"] = "sending"
        self.rows[action_id]["attempts"] += 1
        return self.rows[action_id]["attempts"]

    def resolution(self, action_id):
        if action_id in self.resolutions:
            return self.resolutions[action_id]
        row = self.rows[action_id]
        check = self.checks.get(action_id)
        return {"state": row["state"], "settled_by": row["settled_by"], "settled_kind": row.get("settled_kind"), "note": None,
                "tool": row["start"].tool, "arguments": row["start"].arguments,
                "check_summary": check.summary if check else None, "check_detail": check.detail if check else None}

    def states(self):
        return {action_id: row["state"] for action_id, row in self.rows.items()}


def build(provider, tools, log=None, *, approver=lambda request: True, agents=None, budget=None, config=CONFIG):
    kwargs = {"log": log} if log is not None else {}
    return ToolAgentRuntime(provider, tools, budget or make_budget(), agents or {"writer": WRITER, "helper": HELPER},
                            config=config, approver=approver, **kwargs)


async def collect(stream):
    return [event async for event in stream]


def run(runtime, spec=WRITER, **options):
    return asyncio.run(collect(runtime.stream(spec, "Tell the team", **options)))


def calls(events):
    return [event for event in events if isinstance(event, ToolCalled)]


# --- T4: the live protocol -------------------------------------------------


def test_without_a_log_a_write_behaves_exactly_as_before():
    recorder = Recorder()
    events = run(build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder)))
    called = calls(events)
    assert len(called) == 1 and called[0].ok and called[0].observation == "sent"
    assert called[0].action_id == "" and called[0].resent is False
    assert recorder.actions == [None], "no action is current without a log"


def test_a_write_is_recorded_before_the_tool_runs_and_completed_after():
    from conclave.actions import current_action

    log = FakeLog()
    seen = []

    class Watching(Recorder):
        async def __call__(self, **arguments):
            seen.append(log.rows[current_action.get()]["state"])
            return await super().__call__(**arguments)

    recorder = Watching()
    events = run(build(ScriptedProvider({"writer": [note("ship it"), FINAL]}), registry(recorder), log), scope="announce")
    action_id = recorder.actions[0]
    assert seen == ["sending"], "the tool ran only after the record existed"
    assert log.order == [("begin", action_id), ("finish", action_id, "completed")]
    start = log.rows[action_id]["start"]
    assert (start.scope, start.agent, start.tool, start.arguments, start.approval) == (
        "announce", "writer", "send_note", {"text": "ship it"}, "approved")
    before, after = log.frames[action_id]
    assert before[-1]["action_id"] == action_id and before[-1]["observation"] is None
    assert before[-1]["action"] == note("ship it") and json.loads(before[-1]["reply"]) == note("ship it")
    assert after[-1]["observation"] == "sent"
    assert calls(events)[0].action_id == action_id and calls(events)[0].ok


def test_a_clear_failure_is_reported_to_the_agent_and_never_checked():
    from conclave.actions import ActionNotPerformed

    log = FakeLog()
    recorder = Recorder(check=checker("found"))
    recorder.fail = [ActionNotPerformed("channel is archived")]
    events = run(build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder), log))
    called = calls(events)[0]
    assert not called.ok and called.observation == "Tool error: channel is archived"
    assert list(log.states().values()) == ["failed"]
    assert not log.checks


def test_an_unclear_failure_found_by_its_check_is_not_sent_again():
    log = FakeLog()
    recorder = Recorder(check=checker("found", summary="Found the note", detail="Message 1716 in #eng"))
    recorder.fail = [TimeoutError()]
    provider = ScriptedProvider({"writer": [note(), FINAL]})
    events = run(build(provider, registry(recorder), log))
    assert len(recorder.calls) == 1
    called = calls(events)[0]
    assert called.ok and "Message 1716 in #eng" in called.observation and "Do not repeat" in called.observation
    action_id = called.action_id
    assert log.order == [("begin", action_id), ("finish", action_id, "uncertain"),
                         ("checked", action_id, "found"), ("settle", action_id, "done", "check")]
    assert "Message 1716 in #eng" in provider.prompts[-1][1], "the agent is told what the check found"


def test_an_exact_absence_is_sent_once_more_under_the_same_action():
    log = FakeLog()
    recorder = Recorder(check=checker("absent", exact=True))
    recorder.fail = [TimeoutError()]
    events = run(build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder), log))
    assert len(recorder.calls) == 2 and recorder.actions[0] == recorder.actions[1]
    called = calls(events)
    assert len(called) == 1 and called[0].resent is True and called[0].ok
    action_id = called[0].action_id
    assert log.order[-3:] == [("settle", action_id, "resend", "check"), ("resend", action_id),
                              ("finish", action_id, "completed")]


def test_a_resend_that_is_also_unclear_stops_for_a_person():
    from conclave.actions import ActionStopped

    log = FakeLog()
    recorder = Recorder(check=checker("absent", exact=True))
    recorder.fail = [TimeoutError(), TimeoutError()]
    with pytest.raises(ActionStopped) as stopped:
        run(build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder), log))
    assert len(recorder.calls) == 2
    assert log.states() == {stopped.value.action_id: "uncertain"}
    assert log.order.count(("checked", stopped.value.action_id, "absent")) == 1, "one check, one resend, then a person"


@pytest.mark.parametrize("check", [None, "inconclusive", "unavailable", "absent-not-exact"])
def test_anything_a_check_cannot_settle_stops_the_run(check):
    from conclave.actions import ActionStopped

    log = FakeLog()
    recorder = Recorder(check=None if check is None else checker("absent" if check == "absent-not-exact" else check))
    recorder.fail = [OSError("connection reset")]
    with pytest.raises(ActionStopped) as stopped:
        run(build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder), log))
    assert len(recorder.calls) == 1
    assert log.states() == {stopped.value.action_id: "uncertain"}
    assert "send_note" in stopped.value.reason and "uncertain" in stopped.value.reason


def test_a_check_is_asked_about_this_action_and_these_arguments():
    calls_seen = []
    log = FakeLog()
    recorder = Recorder(check=checker("found", calls=calls_seen))
    recorder.fail = [TimeoutError()]
    run(build(ScriptedProvider({"writer": [note("exact words"), FINAL]}), registry(recorder), log))
    arguments, context = calls_seen[0]
    assert arguments == {"text": "exact words"}
    assert context.action_id == recorder.actions[0] and context.arguments == arguments and context.started_at


def test_reads_and_refused_writes_are_never_recorded():
    log = FakeLog()
    recorder = Recorder()
    events = run(build(ScriptedProvider({"writer": [LOOK, note(), FINAL]}), registry(recorder), log,
                       approver=lambda request: False))
    assert not log.order and not recorder.calls
    assert [event.tool for event in calls(events)] == ["look"]


def test_a_delegated_write_carries_the_frames_of_the_agents_waiting_on_it():
    log = FakeLog()
    recorder = Recorder()
    provider = ScriptedProvider({
        "writer": [{"type": "delegate", "agent": "helper", "task": "Send the note", "purpose": "announce"}, FINAL],
        "helper": [note("from the helper"), FINAL],
    })
    run(build(provider, registry(recorder), log), scope="announce")
    action_id = recorder.actions[0]
    frames = log.frames[action_id][0]
    assert [frame["agent"] for frame in frames] == ["writer", "helper"]
    assert frames[0]["action"]["type"] == "delegate" and frames[0]["observation"] is None
    assert frames[0]["delegation"]["agent"] == "helper"
    assert log.rows[action_id]["start"].scope == "announce", "a child writes on behalf of the same step"


def test_a_run_cancelled_mid_write_leaves_the_write_in_flight():
    log = FakeLog()
    recorder = Recorder()

    async def exercise():
        recorder.gate = asyncio.Event()
        stream = build(ScriptedProvider({"writer": [note(), FINAL]}), registry(recorder), log).stream(WRITER, "task")
        task = asyncio.create_task(collect(stream))
        await recorder.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert list(log.states().values()) == ["sending"], "only the run's end may decide it is uncertain"


# --- T5: resuming from saved progress -------------------------------------


def frames_of_a_pending_write(recorder, *, delegated=False):
    """Run until the write is recorded, and keep what was saved before it ran."""

    log = FakeLog()
    recorder.gate = asyncio.Event()
    script = {"writer": [note(), FINAL]}
    if delegated:
        script = {"writer": [{"type": "delegate", "agent": "helper", "task": "Send it", "purpose": "p"}, FINAL],
                  "helper": [note(), FINAL]}

    async def exercise():
        stream = build(ScriptedProvider(script), registry(recorder), log).stream(WRITER, "task", scope="announce")
        task = asyncio.create_task(collect(stream))
        await recorder.started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    action_id = recorder.actions[0]
    return action_id, log.frames[action_id][0]


def resumed(frames, resolution, *, script=None, tools=None, approver=lambda request: True, budget=None):
    log = FakeLog()
    action_id = frames[-1]["action_id"]
    log.resolutions[action_id] = resolution
    log.rows[action_id] = {"state": resolution["state"], "start": None, "attempts": 1, "settled_by": resolution.get("settled_by")}
    log.frames[action_id] = [frames]
    provider = ScriptedProvider(script or {"writer": [FINAL], "helper": [FINAL]})
    recorder = Recorder()
    runtime = build(provider, tools or registry(recorder), log, approver=approver, budget=budget)
    events = asyncio.run(collect(runtime.stream(WRITER, "task", scope="announce", resume=frames)))
    return events, provider, recorder, log


DONE_BY_ANA = {"state": "done", "settled_by": "Ana Ruiz", "settled_kind": "person", "note": "Posted at 10:41", "check_summary": None,
               "check_detail": None, "tool": "send_note"}


def test_a_write_a_person_marked_done_is_not_sent_and_the_agent_is_told():
    action_id, frames = frames_of_a_pending_write(Recorder())
    events, provider, recorder, _ = resumed(frames, DONE_BY_ANA)
    assert not recorder.calls
    assert not calls(events), "nothing was called, so no call is reported"
    told = provider.prompts[0][1]
    assert "TOOL RESULT for send_note" in told
    assert "Ana Ruiz confirmed this action already happened" in told and "Posted at 10:41" in told
    assert isinstance(events[-1], RunFinished) and events[-1].status == "completed"


def test_a_write_chosen_for_resend_is_sent_once_without_asking_again():
    asked = []
    budget = make_budget(tool_calls=5)
    action_id, frames = frames_of_a_pending_write(Recorder())
    events, provider, recorder, log = resumed(
        frames, {"state": "resend", "settled_by": "Ana Ruiz", "settled_kind": "person", "note": None, "check_summary": None,
                 "check_detail": None, "tool": "send_note", "arguments": {"text": "hello"}},
        approver=lambda request: asked.append(request) or True, budget=budget)
    assert len(recorder.calls) == 1 and recorder.actions == [action_id]
    assert not asked, "choosing Send again was the approval"
    assert budget.spent_on("tool_calls") == 0, "the original call was already counted"
    called = calls(events)
    assert len(called) == 1 and called[0].resent and called[0].action_id == action_id
    assert log.order == [("resend", action_id), ("finish", action_id, "completed")]
    assert "sent" in provider.prompts[0][1]


def test_a_write_still_unsettled_cannot_be_resumed():
    from conclave.actions import ActionStopped

    _, frames = frames_of_a_pending_write(Recorder())
    with pytest.raises(ActionStopped):
        resumed(frames, {"state": "uncertain", "settled_by": None, "note": None, "check_summary": None,
                         "check_detail": None, "tool": "send_note"})


def test_a_write_inside_a_delegation_resumes_inside_the_delegated_agent():
    budget = make_budget()
    action_id, frames = frames_of_a_pending_write(Recorder(), delegated=True)
    used = budget.used
    events, provider, recorder, _ = resumed(
        frames, DONE_BY_ANA, budget=budget,
        script={"helper": [{"type": "final", "content": "helper confirmed the note"}],
                "writer": [FINAL]})
    assert [name for name, _ in provider.prompts] == ["helper", "writer"]
    assert "Ana Ruiz confirmed" in provider.prompts[0][1]
    assert "helper confirmed the note" in provider.prompts[1][1], "the parent hears the child's answer"
    assert budget.used == used, "the delegation was already counted when it started"
    assert not recorder.calls


def test_a_resumed_agent_keeps_its_notes_and_gets_a_fresh_allowance():
    seen = []

    class Reading(ScriptedProvider):
        async def complete(self, messages):
            seen.append(messages[0]["content"])
            return await super().complete(messages)

    _, frames = frames_of_a_pending_write(Recorder())
    frames = [{**frames[-1], "notes": ["remember the release channel"], "step": CONFIG.max_tool_steps}]
    log = FakeLog()
    log.resolutions[frames[-1]["action_id"]] = DONE_BY_ANA
    provider = Reading({"writer": [LOOK, FINAL]})
    runtime = build(provider, registry(Recorder()), log)
    events = asyncio.run(collect(runtime.stream(WRITER, "task", scope="announce", resume=frames)))
    assert "remember the release channel" in seen[0]
    assert events[-1].status == "completed" and not events[-1].result.exhausted_by


def test_progress_saved_after_a_write_continues_from_its_result():
    log = FakeLog()
    recorder = Recorder()
    run(build(ScriptedProvider({"writer": [note(), LOOK, FINAL]}), registry(recorder), log))
    after = log.frames[recorder.actions[0]][-1]
    assert after[-1]["observation"] == "sent"
    fresh = FakeLog()
    provider = ScriptedProvider({"writer": [FINAL]})
    again = Recorder()
    asyncio.run(collect(build(provider, registry(again), fresh).stream(WRITER, "task", resume=after)))
    assert not again.calls and not fresh.order, "a completed write is history, not a question"
    assert "TOOL RESULT for send_note" in provider.prompts[0][1] and "sent" in provider.prompts[0][1]
