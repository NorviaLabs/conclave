"""An agent loop that runs where the provider, the budget and the approver are not.

The loop is the same loop the rest of the suite tests. What changes is where it
gets the model call, the money and the permission: over a transport, from a
control plane that holds all three. These tests run the real loop on both sides
of that boundary and compare what comes out.
"""

import asyncio

import pytest

from conclave import (
    Approval,
    AgentSpec,
    RunBudget,
    ToolAgentRuntime,
    ToolCalled,
    ToolDenied,
)
from conclave.application.sandbox import (
    ControlPlaneAuthority,
    InProcessControlPlane,
    SandboxAuthority,
    sandboxed_loop,
)
from conclave.spec import Capability, Tool
from conftest import CONFIG, FINAL, ScriptedProvider, make_budget
from test_stream import with_tool


SPEC = AgentSpec(name="repo-researcher", instructions="Investigate", tools={"list_files"})
AGENTS = {"repo-researcher": SPEC}


def tool_call(name="list_files", arguments=None):
    return {"type": "tool", "name": name, "arguments": arguments or {"path": "."}}


def drive(runtime, spec=SPEC):
    async def main():
        return [event async for event in runtime.stream(spec, "task")]

    return asyncio.run(main())


def script(*actions):
    return ScriptedProvider({"repo-researcher": list(actions)})


def small():
    async def func(path):
        return "ok"

    return func


def declarations(name="list_files", *capabilities):
    """What the control plane says a tool does, whatever a sandbox claims."""
    return {name: Tool(name, "d", {}, small(), frozenset(capabilities))}


def control_plane(*, provider, budget, declares=None, approver=None):
    """The control plane's side, and the transport a sandbox would reach it by."""
    authority = ControlPlaneAuthority(provider, budget, declares if declares is not None else declarations("list_files", Capability.READ), approver)
    return authority, InProcessControlPlane(authority)


def shape(events):
    return [type(event).__name__ for event in events]


def test_the_same_loop_runs_through_the_boundary(tmp_path):
    """Equivalence: a sandboxed run produces the run this repository tests."""
    provider = script(tool_call(), FINAL)

    here = drive(ToolAgentRuntime(provider, with_tool("list_files", small(), Capability.READ), make_budget(), AGENTS, config=CONFIG))

    _, transport = control_plane(provider=script(tool_call(), FINAL), budget=make_budget())
    there = drive(sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG))

    assert shape(there) == shape(here)
    assert there[-1].result.content == here[-1].result.content
    assert [event.tool for event in there if isinstance(event, ToolCalled)] == ["list_files"]


def test_the_loop_holds_nothing_it_could_answer_with():
    """The absence is the point: no provider, no budget, no approver."""
    _, transport = control_plane(provider=script(FINAL), budget=make_budget())
    runtime = sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG)

    assert runtime.provider is None
    assert runtime.budget is None
    assert runtime.approver is None
    assert isinstance(runtime.authority, SandboxAuthority)
    assert not hasattr(runtime.authority, "budget")


def test_the_control_plane_meters_what_the_sandbox_spent():
    budget = make_budget()
    authority, transport = control_plane(provider=script(FINAL), budget=budget)
    events = drive(sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG))

    assert budget.spent_on("tool_calls") == 0.0
    assert [event.dimension for event in events if type(event).__name__ == "BudgetExceeded"] == []


def test_a_control_plane_budget_stops_the_call_in_the_sandbox():
    budget = RunBudget(max_delegations=1, max_per_agent=1, max_depth=1, tool_calls=0)
    _, transport = control_plane(provider=script(tool_call(), FINAL), budget=budget)
    events = drive(sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG))
    denied = [event for event in events if isinstance(event, ToolDenied)]

    assert [event.reason for event in denied] == ["budget"]
    assert "tool-call budget is spent (0/0)" in denied[0].observation


def test_a_control_plane_refusal_stops_the_call_in_the_sandbox():
    asked = []

    def approver(request):
        asked.append(request)
        return False

    _, transport = control_plane(provider=script(tool_call(), FINAL), budget=make_budget(),
                                 declares=declarations("list_files", Capability.WRITE), approver=approver)
    tools = with_tool("list_files", small(), Capability.WRITE)
    events = drive(sandboxed_loop(transport, tools=tools, agents=AGENTS, config=CONFIG))
    denied = [event for event in events if isinstance(event, ToolDenied)]

    assert [event.reason for event in denied] == ["approval-refused"]
    assert [request.tool for request in asked] == ["list_files"]
    assert asked[0].capabilities == frozenset({Capability.WRITE})


def test_the_control_plane_decides_from_its_own_declaration():
    """A sandbox that understates a tool changes nothing."""
    _, transport = control_plane(provider=script(tool_call(), FINAL), budget=make_budget(),
                                 declares=declarations("list_files", Capability.WRITE), approver=lambda request: False)

    # The sandbox claims a harmless tool; the control plane's registry says WRITE.
    claiming = with_tool("list_files", small(), Capability.READ)
    events = drive(sandboxed_loop(transport, tools=claiming, agents=AGENTS, config=CONFIG))

    assert [event.reason for event in events if isinstance(event, ToolDenied)] == ["approval-refused"]


def test_an_unknown_tool_is_refused_rather_than_approved():
    """A tool the control plane has never heard of is refused, not approved."""
    _, transport = control_plane(provider=script(tool_call("git_push", {}), FINAL), budget=make_budget(), approver=lambda request: True)
    spec = AgentSpec(name="repo-researcher", instructions="Investigate", tools={"git_push"})
    events = drive(sandboxed_loop(transport, tools=with_tool("git_push", small(), Capability.WRITE), agents={"repo-researcher": spec}, config=CONFIG), spec)

    # Not in the control plane's registry, so the model is told it was refused.
    assert [event.reason for event in events if isinstance(event, ToolDenied)] == ["approval-refused"]


def test_depth_is_declared_policy_enforced_where_the_counters_are():
    """The prompt may know the depth limit; the counter is still remote."""
    budget = make_budget(max_depth=1)
    _, transport = control_plane(provider=script({"type": "delegate", "agent": "repo-researcher", "task": "look"}, FINAL), budget=budget)
    runtime = sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG, max_depth=1)

    assert runtime.authority.max_depth == 1 and runtime.authority.may_delegate(0)
    assert not runtime.authority.may_delegate(1), "declared policy answers without asking"
    assert budget.used == 0, "no counter moved for a question"

    events = drive(runtime)
    delegation = [event for event in events if type(event).__name__ == "Delegation"]

    assert [event.status for event in delegation] == ["refused"]
    assert "maximum depth is 1" in delegation[0].reason
    assert budget.used == 0, "the refused delegation was never charged"


def test_the_control_plane_answers_only_what_it_knows():
    authority, transport = control_plane(provider=script(FINAL), budget=make_budget())

    async def exercise():
        with pytest.raises(ValueError, match="Unknown authority request"):
            await authority.answer({"kind": "grant-myself-more-budget"})

        assert await authority.answer({"kind": "numbers", "dimension": "tokens"}) == {"limit": None, "used": 0.0}

    asyncio.run(exercise())


def test_the_sandbox_reports_an_unusable_answer():
    class Confused:
        async def call(self, request):
            return "not a response"

    runtime = sandboxed_loop(Confused(), tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG)

    with pytest.raises(RuntimeError, match="unusable response"):
        drive(runtime)


def test_a_bad_decision_is_not_taken_as_approval():
    _, transport = control_plane(provider=script(tool_call(), FINAL), budget=make_budget())

    class Sloppy:
        async def call(self, request):
            return {"decision": "probably-fine"} if request["kind"] == "approve" else await transport.call(request)

    runtime = sandboxed_loop(Sloppy(), tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG)

    with pytest.raises(RuntimeError, match="unusable decision"):
        drive(runtime)


def test_the_sandbox_sees_the_model_reply_and_its_own_usage():
    """Tokens are measured by the control plane and reported to the loop."""
    from conclave import Usage

    provider = ScriptedProvider({"repo-researcher": [FINAL]}, usage=Usage(input_tokens=30, output_tokens=12))
    budget = make_budget()
    _, transport = control_plane(provider=provider, budget=budget)
    events = drive(sandboxed_loop(transport, tools=with_tool("list_files", small(), Capability.READ), agents=AGENTS, config=CONFIG))

    assert events[-1].result.content == FINAL["content"]
    assert budget.spent_on("tokens") == 42
