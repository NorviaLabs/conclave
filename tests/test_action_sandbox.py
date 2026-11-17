"""T11 — a loop that runs in a sandbox records its writes on the control plane.

The journal, the checks and the settlement stay where the authority is; the
sandbox asks for them over the same transport it already uses, and may never
settle or finalise anything itself.
"""

import asyncio

import pytest

from actions_support import Recorder, checker, note, registry
from conclave import AgentSpec, Approval, Capability, RunConfig, ToolCalled
from conclave.application.sandbox import ControlPlaneAuthority, InProcessChannel, sandboxed_agent
from conclave.events import event_payload, rebuild_event
from conftest import ScriptedProvider, make_budget
from test_runtime_actions import DONE_BY_ANA, FINAL, FakeLog, frames_of_a_pending_write

CONFIG = RunConfig(max_tool_steps=4, max_notes=4, max_note_chars=200, max_tool_output_chars=500, fan_out_limit=1)
WRITER = AgentSpec(name="writer", instructions="Tell the team.", tools={"send_note", "look"})


def plane(provider, log, declared):
    """The control plane's side: its own declaration of the tool decides, and checks."""
    return ControlPlaneAuthority(provider, make_budget(), {tool.name: tool for tool in declared.tools.values()},
                                 lambda request: True, log=log)


async def collect(stream):
    return [event async for event in stream]


def through_sandbox(script, sandbox_tool, declared_tool=None, log=None, **options):
    log = log if log is not None else FakeLog()
    channel = InProcessChannel(plane(ScriptedProvider(script), log, registry(declared_tool or sandbox_tool)))
    loop = sandboxed_agent(channel, tools=registry(sandbox_tool), agents={"writer": WRITER}, config=CONFIG)
    return asyncio.run(collect(loop.stream(WRITER, "task", **options))), log


def test_a_sandboxed_write_is_recorded_on_the_control_plane_before_it_runs():
    recorder = Recorder()
    events, log = through_sandbox({"writer": [note(), FINAL]}, recorder, scope="announce")
    action_id = recorder.actions[0]
    assert action_id and log.order == [("begin", action_id), ("finish", action_id, "completed")]
    assert log.rows[action_id]["start"].scope == "announce"
    assert log.frames[action_id][0][-1]["action_id"] == action_id
    called = [event for event in events if isinstance(event, ToolCalled)]
    assert called[0].action_id == action_id


def test_the_control_planes_check_settles_an_unclear_sandboxed_write():
    in_sandbox = Recorder()
    in_sandbox.fail = [TimeoutError()]
    declared = Recorder(check=checker("found", detail="Found on the control plane"))
    events, log = through_sandbox({"writer": [note(), FINAL]}, in_sandbox, declared)
    assert len(in_sandbox.calls) == 1 and not declared.calls
    called = [event for event in events if isinstance(event, ToolCalled)][0]
    assert called.ok and "Found on the control plane" in called.observation


def test_a_stop_crosses_the_boundary_as_a_stop():
    from conclave.actions import ActionStopped

    recorder = Recorder()
    recorder.fail = [OSError("reset")]
    with pytest.raises(ActionStopped):
        through_sandbox({"writer": [note(), FINAL]}, recorder)


def test_a_sandboxed_loop_resumes_from_saved_progress():
    _, frames = frames_of_a_pending_write(Recorder())
    log = FakeLog()
    log.resolutions[frames[-1]["action_id"]] = DONE_BY_ANA
    recorder = Recorder()
    events, _ = through_sandbox({"writer": [FINAL]}, recorder, log=log, scope="announce", resume=frames)
    assert not recorder.calls
    assert events[-1].status == "completed"


@pytest.mark.parametrize("kind", ["settle_action", "finalize_actions", "undo_action"])
def test_a_sandbox_can_never_decide_for_a_person(kind):
    authority = plane(ScriptedProvider(), FakeLog(), registry(Recorder()))
    with pytest.raises(ValueError, match="Unknown authority request"):
        asyncio.run(authority.answer({"kind": kind, "action_id": "a1", "decision": "done"}))


def test_the_new_tool_call_fields_survive_the_wire():
    event = ToolCalled(run_id="r1", agent="writer", tool="send_note", arguments={"text": "x"},
                       capabilities=frozenset({Capability.WRITE}), approval=Approval.APPROVED, observation="sent",
                       output_chars=4, truncated=False, ok=True, action_id="a1", resent=True)
    assert rebuild_event(event_payload(event), CONFIG) == event
