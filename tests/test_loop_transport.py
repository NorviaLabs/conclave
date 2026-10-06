"""The event transport: a loop's events, reported from where the loop runs.

A sandbox that runs an agent has to send back what happened, faithfully enough
that the control plane records the same run it would have recorded locally. The
one thing that does not cross is an agent's private scratchpad.
"""

import asyncio
import dataclasses

import pytest

from conclave import (
    AgentFinished,
    AgentStarted,
    AgentSpec,
    AgentStep,
    Approval,
    BudgetExceeded,
    Delegation,
    NoteRecorded,
    RunConfig,
    RunFinished,
    RunStarted,
    StoreChanged,
    ToolCalled,
    ToolDenied,
    Usage,
)
from conclave.application.definition import public_runtime_event, run_application
from conclave.application.runs import RunRepository
from conclave.application.sandbox import (
    ControlPlaneAuthority,
    InProcessChannel,
    SandboxedLoop,
    sandboxed_agent,
)
from conclave.application.service import LocalRunHost
from conclave.events import EVENT_TYPES, event_payload, rebuild_event
from conclave.protocol import AgentResult, PrivateState
from conclave.spec import Capability, Tool
from conftest import CONFIG, FINAL, ScriptedProvider, make_budget
from test_stream import with_tool
from test_studio_runs import request


CONFIGURED = RunConfig(max_tool_steps=4, max_notes=4, max_note_chars=200, max_tool_output_chars=500, fan_out_limit=1)
SPEC = AgentSpec(name="writer", instructions="Draft", tools={"list_files"})
AGENTS = {"writer": SPEC}


def result(content="Done", *, data=None, nested=None, notes=()):
    private = PrivateState(config=CONFIGURED)
    private.notes = list(notes)
    return AgentResult(content=content, private=private, data=data or {"text": content}, nested=nested or [])


def one_of_each():
    """One of every event the vocabulary declares."""
    return [
        RunStarted(run_id="r1", task="task", config=CONFIGURED),
        RunFinished(run_id="r1", agent="writer", status="completed", result=result()),
        AgentStarted(run_id="r1", parent_run_id=None, agent="writer", depth=1, task="task"),
        AgentFinished(run_id="r1", parent_run_id=None, agent="writer", status="completed", result=result()),
        AgentStep(run_id="r1", agent="writer", step=0, reply="{}", action="note"),
        ToolCalled(run_id="r1", agent="writer", tool="list_files", arguments={"path": "."},
                   capabilities=frozenset({Capability.READ}), approval=Approval.NOT_REQUIRED,
                   observation="one file", output_chars=8, truncated=False, ok=True),
        ToolDenied(run_id="r1", agent="writer", tool="git_push", reason="not-permitted",
                   observation="Permission denied", capabilities=frozenset({Capability.WRITE})),
        NoteRecorded(run_id="r1", agent="writer", note="a note", total=1, result="ok"),
        Delegation(run_id="r1", agent="writer", delegated_by="writer", depth=2, task="look", status="refused", reason="depth"),
        StoreChanged(keys=("brief",), writer="writer", version=2),
        BudgetExceeded(run_id="r1", dimension="tokens", limit=10.0, used=12.0),
    ]


def test_every_event_survives_the_wire():
    for event in one_of_each():
        rebuilt = rebuild_event(event_payload(event), CONFIGURED)

        assert type(rebuilt) is type(event)

        if isinstance(event, (RunFinished, AgentFinished)):
            # The handoff crosses; the scratchpad does not.
            assert rebuilt.result.content == event.result.content
            assert rebuilt.result.data == event.result.data
            assert rebuilt.result.nested == event.result.nested
            assert event.result.private.notes == []
        else:
            assert rebuilt == event


def test_the_vocabulary_is_covered_by_that_test():
    """A new event type has to be added to the wire deliberately."""
    assert set(EVENT_TYPES) == {type(event).__name__ for event in one_of_each()}


def test_a_scratchpad_does_not_cross():
    private = PrivateState(config=CONFIGURED)
    private.notes = ["half-formed thought", "another"]
    private.checked = ["claim:1"]
    event = AgentFinished(run_id="r1", parent_run_id=None, agent="writer", status="completed",
                          result=AgentResult(content="Done", private=private, data={"text": "Done"}))

    payload = event_payload(event)

    assert "half-formed thought" not in repr(payload)
    assert "private" not in payload["result"] and set(payload["result"]) == {"content", "data", "nested"}

    rebuilt = rebuild_event(payload, CONFIGURED)

    assert rebuilt.result.private.notes == [] and rebuilt.result.private.checked == []
    assert rebuilt.result.content == "Done" and rebuilt.result.data == {"text": "Done"}


def test_a_payload_that_is_not_an_event_is_refused():
    for payload in ("not a payload", {}, {"type": "AgentTeleported"}, {"type": "ToolCalled", "run_id": "r1"}):
        with pytest.raises(ValueError):
            rebuild_event(payload, CONFIGURED)


def control_plane(*, provider, budget, approver=None, declares=None):
    """The control plane's side, declaring what it says the tool does."""
    declared = declares or frozenset({Capability.READ})

    return ControlPlaneAuthority(provider, budget, {"list_files": Tool("list_files", "d", {}, list_files, declared)}, approver)


async def list_files(path):
    return "one file"


def test_the_same_run_arrives_through_the_transport():
    """Equivalence: what the durable record would see is the same either way."""
    script = [{"type": "tool", "name": "list_files", "arguments": {"path": "."}}, FINAL]
    budget = make_budget()
    channel = InProcessChannel(control_plane(provider=ScriptedProvider({"writer": list(script)}), budget=budget))

    async def exercise():
        loop = sandboxed_agent(channel, tools=with_tool("list_files", list_files, Capability.READ), agents=AGENTS, config=CONFIGURED)
        return [event async for event in loop.stream(SPEC, "task")]

    through = asyncio.run(exercise())

    local = asyncio.run(_collect(_local_loop(script)))

    # The run ids differ (they name two different runs); everything the durable
    # record keeps is identical.
    assert [without_ids(event) for event in through] == [without_ids(event) for event in local]
    assert through[-1].result.content == local[-1].result.content
    assert budget.spent_on("tool_calls") == 1.0


def without_ids(event):
    projected = public_runtime_event(event) or {}

    return {key: value for key, value in projected.items() if key not in {"run_id", "parent_run_id"}}


def _local_loop(script):
    from conclave import ToolAgentRuntime

    return ToolAgentRuntime(ScriptedProvider({"writer": list(script)}), with_tool("list_files", list_files, Capability.READ),
                            make_budget(), AGENTS, config=CONFIGURED)


async def _collect(loop):
    return [event async for event in loop.stream(SPEC, "task")]


class Recording(InProcessChannel):
    """A channel that keeps what the sandbox sent, in order."""

    def __init__(self, authority):
        super().__init__(authority)
        self.sent = []

    async def emit(self, payload):
        self.sent.append(payload["type"])
        await super().emit(payload)


def test_events_leave_the_sandbox_as_they_happen():
    """Reported in the loop's own order, not gathered up at the end."""
    channel = Recording(control_plane(provider=ScriptedProvider({"writer": [{"type": "tool", "name": "list_files", "arguments": {"path": "."}}, FINAL]}),
                                     budget=make_budget()))

    async def exercise():
        loop = sandboxed_agent(channel, tools=with_tool("list_files", list_files, Capability.READ), agents=AGENTS, config=CONFIGURED)
        return [event async for event in loop.stream(SPEC, "task")]

    received = asyncio.run(exercise())

    assert channel.sent == ["RunStarted", "AgentStarted", "AgentStep", "ToolCalled", "AgentStep", "AgentFinished", "RunFinished"]
    assert [type(event).__name__ for event in received] == channel.sent


def test_the_loop_waits_for_the_control_plane_before_acting():
    """The decision gates the call; the sandbox does not act first and ask later."""
    order = []

    async def watched(path):
        order.append("ran")
        return "one file"

    def approver(request):
        order.append("asked")
        return True

    channel = InProcessChannel(control_plane(provider=ScriptedProvider({"writer": [{"type": "tool", "name": "list_files", "arguments": {"path": "."}}, FINAL]}),
                                            budget=make_budget(), approver=approver, declares=frozenset({Capability.WRITE})))

    async def exercise():
        loop = sandboxed_agent(channel, tools=with_tool("list_files", watched, Capability.WRITE), agents=AGENTS, config=CONFIGURED)
        return [event async for event in loop.stream(SPEC, "task")]

    asyncio.run(exercise())

    assert order == ["asked", "ran"]


def test_stopping_the_reader_stops_the_loop():
    """Nothing keeps working on something nobody is watching."""
    gate = asyncio.Event()

    async def blocked(path):
        # Still working when the caller walks away.
        await gate.wait()
        return "late"

    budget = make_budget()
    channel = InProcessChannel(control_plane(provider=ScriptedProvider({"writer": [{"type": "tool", "name": "list_files", "arguments": {"path": "."}}, FINAL]}),
                                            budget=budget))
    stopped = []

    async def exercise():
        loop = sandboxed_agent(channel, tools=with_tool("list_files", blocked, Capability.READ), agents=AGENTS, config=CONFIGURED)
        stream = loop.stream(SPEC, "task")
        await anext(stream)
        await stream.aclose()

        # The sandbox's task was cancelled: it closed its side of the channel
        # and never reported the run as finished.
        drained = []
        while not channel.queue.empty():
            item = channel.queue.get_nowait()
            if item is not None:
                drained.append(item["type"])
        stopped.append((channel.closed, "RunFinished" in drained))

    asyncio.run(exercise())

    assert stopped == [(True, False)]


def test_a_whole_application_runs_with_its_loop_in_a_sandbox(tmp_path):
    """The application layer does not care where the loop runs."""
    channel = InProcessChannel(control_plane(provider=ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": "Draft"}]}),
                                            budget=make_budget()))

    def factory(provider, tools, budget, agents, *, config, approver=None):
        # The sandbox gets the run's granted tools and asks for everything else.
        return sandboxed_agent(channel, tools=tools, agents=agents, config=config, max_depth=budget.max_depth)

    value = request()
    value["definition"]["agents"] = [{"id": "writer", "instructions": "Write a brief", "tools": ["list_files"]}]
    value["definition"]["workflow"] = [{"id": "draft", "agent": "writer", "task": "Draft the brief", "output": "brief"}]
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), with_tool("list_files", list_files, Capability.READ),
                        reviewer="ada", loop=factory)
    run_id = host.repository.create(value)

    async def exercise():
        return [event async for event in host.repository.execute(run_id, host.provider, host.tools, loop=factory)]

    events = asyncio.run(exercise())

    # The durable record drops the agent's steps and replies, wherever the loop
    # ran; the rest of the run is the same.
    assert [event["type"] for event in events] == ["ApplicationStarted", "StageStarted", "RunStarted", "AgentStarted",
                                                  "AgentFinished", "RunFinished", "ApplicationStateChanged",
                                                  "ApplicationFinished"]
    assert events[-1]["status"] == "completed" and events[-1]["state"]["brief"] == "Draft"


def test_the_application_reports_a_failing_sandbox_rather_than_hanging(tmp_path):
    """A sandbox that cannot answer fails the run; it does not stall it."""
    class Silent(InProcessChannel):
        async def call(self, request):
            raise RuntimeError("the kernel is gone")

    channel = Silent(control_plane(provider=ScriptedProvider(), budget=make_budget()))
    value = request()
    value["definition"]["agents"] = [{"id": "writer", "instructions": "Write a brief", "tools": []}]
    value["definition"]["workflow"] = [{"id": "draft", "agent": "writer", "task": "Draft", "output": "brief"}]
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value)

    async def exercise():
        def factory(provider, tools, budget, agents, *, config, approver=None):
            return sandboxed_agent(channel, tools=tools, agents=agents, config=config)

        return [event async for event in repository.execute(run_id, ScriptedProvider(), with_tool("x", list_files, Capability.READ), loop=factory)]

    events = asyncio.run(exercise())

    assert events[-1]["status"] == "failed"
