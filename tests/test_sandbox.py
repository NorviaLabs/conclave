"""Tool calls behind a boundary: what crosses it, and what never does.

The point of the seam is the *absence*: an executor receives the call and the
run it belongs to, and nothing that could authorize it. These tests drive a real
run through a recording executor, and drive the sandbox side directly.
"""

import asyncio
import dataclasses

import pytest

from conclave import (
    TRUNCATION_MARKER,
    AgentSpec,
    RunBudget,
    ToolAgentRuntime,
    ToolCalled,
    ToolDenied,
    ToolRegistry,
)
from conclave.application.resources import register_source_tools
from conclave.application.runs import RunRepository
from conclave.application.sandbox import (
    Grant,
    InProcessTransport,
    SandboxExecutor,
    grant_for,
    in_process_sandbox,
    serve_call,
)
from conclave.application.service import LocalRunHost
from conclave.registry import ToolExecutor, ToolInvocation
from conclave.spec import Capability, Tool
from conftest import CONFIG, FINAL, ScriptedProvider, make_budget
from test_application_resources import setup_resources
from test_studio_runs import request


def tool(name, func, *capabilities):
    return Tool(name, "d", {}, func, frozenset(capabilities))


def answering(text):
    async def func(**arguments):
        return text

    return func


class Recorder(ToolExecutor):
    """An executor that answers instead of running anything here."""

    def __init__(self, text="recorded"):
        self.text, self.invocations, self.calls = text, [], 0

    async def execute(self, invocation):
        self.calls += 1
        self.invocations.append(invocation)
        return self.text


def run_tool_call(*, executor, func, capabilities=(Capability.READ,), budget=None, approver=None, name="list_files"):
    """One scripted run whose single action is a tool call."""
    provider = ScriptedProvider({"repo-researcher": [{"type": "tool", "name": name, "arguments": {"path": "."}}, FINAL]})
    registry = ToolRegistry(CONFIG.max_tool_output_chars, executor=executor)
    registry.register(tool(name, func, *capabilities))
    spec = AgentSpec(name="repo-researcher", instructions="i", tools={name})
    runtime = ToolAgentRuntime(provider, registry, budget or make_budget(), {"repo-researcher": spec}, config=CONFIG, approver=approver)

    async def main():
        return [event async for event in runtime.stream(spec, "task")]

    return asyncio.run(main())


def test_the_call_crosses_and_the_tool_does_not_run_here():
    ran = []

    async def func(path):
        ran.append(path)
        return "ran here"

    events = run_tool_call(executor=Recorder("from the sandbox"), func=func)
    called = [event for event in events if isinstance(event, ToolCalled)]

    assert ran == [], "the call ran on the control plane"
    assert called[0].observation == "from the sandbox" and called[0].ok


def test_an_invocation_carries_no_authority():
    """The seam's whole guarantee, checked against its own shape."""
    assert {field.name for field in dataclasses.fields(ToolInvocation)} == {"name", "arguments", "run_id", "agent"}

    recorder = Recorder()
    events = run_tool_call(executor=recorder, func=lambda path: "here")
    invocation = recorder.invocations[0]

    assert invocation.name == "list_files" and invocation.arguments == {"path": "."}
    assert invocation.agent == "repo-researcher"
    assert invocation.run_id == [event.run_id for event in events if isinstance(event, ToolCalled)][0]


def test_the_output_cap_stays_on_the_control_plane():
    """A sandbox does not decide how much text reaches a prompt."""
    recorder = Recorder("x" * 40_000)
    events = run_tool_call(executor=recorder, func=lambda path: "here")
    called = [event for event in events if isinstance(event, ToolCalled)][0]

    assert recorder.calls == 1
    assert called.truncated and called.output_chars == CONFIG.max_tool_output_chars + len(TRUNCATION_MARKER)
    assert len(called.observation) == called.output_chars


def test_a_refused_approval_never_reaches_the_sandbox():
    recorder = Recorder()
    events = run_tool_call(executor=recorder, func=lambda path: "here", capabilities=(Capability.WRITE,),
                           approver=lambda request: False)
    denied = [event for event in events if isinstance(event, ToolDenied)]

    assert [event.reason for event in denied] == ["approval-refused"]
    assert recorder.calls == 0, "a refused call was sent to the sandbox anyway"


def test_an_exhausted_budget_never_reaches_the_sandbox():
    recorder = Recorder()
    budget = RunBudget(max_delegations=1, max_per_agent=1, max_depth=1, tool_calls=0)
    events = run_tool_call(executor=recorder, func=lambda path: "here", budget=budget)
    denied = [event for event in events if isinstance(event, ToolDenied)]

    assert [event.reason for event in denied] == ["budget"]
    assert recorder.calls == 0


def test_a_failing_sandbox_is_a_tool_error_not_a_crash():
    class Broken(ToolExecutor):
        async def execute(self, invocation):
            raise RuntimeError("the sandbox is gone")

    events = run_tool_call(executor=Broken(), func=lambda path: "here")
    called = [event for event in events if isinstance(event, ToolCalled)][0]

    assert called.ok is False and called.observation == "Tool error: the sandbox is gone"
    assert events[-1].result.content, "the run still finished"


def test_the_grant_names_only_what_this_run_may_use():
    """Not the host's whole tool set: the tools this run's agents declared."""
    registry = ToolRegistry(None)
    register_source_tools(registry)
    registry.register(tool("git_status", lambda: "x", Capability.READ))
    value = request()
    value["definition"]["agents"][0]["tools"] = ["read_file", "git_status"]
    value["resources"] = {"source": "alpha"}

    grant = grant_for(value, registry, run_id="r1")

    assert grant.tools == ("git_status", "read_file")
    assert grant.sources == ("alpha",) and grant.run_id == "r1"
    assert "search_files" not in grant.tools
    assert grant.allows("read_file") and not grant.allows("search_files")


def test_both_sides_refuse_a_call_outside_the_grant():
    """Neither a bug nor a forged request becomes an ungranted tool call."""
    tools = {"read_file": tool("read_file", answering("text"), Capability.READ),
             "delete": tool("delete", answering("gone"), Capability.WRITE)}
    grant = Grant(run_id="r1", tools=("read_file",))
    transport = InProcessTransport(tools)

    async def exercise():
        # The control plane will not send it.
        with pytest.raises(RuntimeError, match="not granted"):
            await SandboxExecutor(transport, grant).execute(ToolInvocation("delete", {}))

        # And the sandbox will not run it, even asked directly.
        assert await serve_call(tools, {"grant": grant.payload(), "tool": "delete", "arguments": {}}) == {"error": "delete is not granted to this sandbox"}
        assert await serve_call(tools, {"grant": {}, "tool": "read_file", "arguments": {"path": "a"}}) == {"error": "read_file is not granted to this sandbox"}
        assert await serve_call(tools, {"grant": grant.payload(), "tool": "read_file", "arguments": {"path": "a"}}) == {"text": "text"}

    asyncio.run(exercise())


def test_the_sandbox_reports_its_own_failures_as_errors():
    async def explode(path):
        raise ValueError("no such file")

    async def exercise():
        tools = {"read_file": tool("read_file", explode, Capability.READ)}
        grant = Grant(run_id="r1", tools=("read_file",))

        assert await serve_call(tools, {"grant": grant.payload(), "tool": "read_file", "arguments": {"path": "a"}}) == {"error": "Tool error: no such file"}
        assert await serve_call(tools, {"grant": grant.payload(), "tool": "read_file"}) == {"error": "Provide the tool arguments"}

        with pytest.raises(RuntimeError, match="no such file"):
            await SandboxExecutor(InProcessTransport(tools), grant).execute(ToolInvocation("read_file", {"path": "a"}))

    asyncio.run(exercise())


def one_stage_run(*tools, source="alpha"):
    """A request whose single agent may use these tools against one source."""
    value = request()
    value["definition"]["agents"] = [{"id": "writer", "instructions": "Write a brief", "tools": list(tools)}]
    value["definition"]["workflow"] = [{"id": "draft", "agent": "writer", "task": "Draft the brief", "output": "brief"}]
    value["resources"] = {"source": source}
    return value


def sandboxed_host(tmp_path, *, transport=None, provider=None):
    """A host whose runs cross the boundary, with the sources they read."""
    tools, resources, _, _ = setup_resources(tmp_path)
    seen = []

    def factory(bound, value):
        grant = grant_for(value, bound)
        seen.append(grant)
        return SandboxExecutor(transport(bound) if transport else InProcessTransport(bound.tools), grant)

    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider or ScriptedProvider(), tools, reviewer="ada",
                        resources=resources, source_extensions={".md"}, sandbox=factory)
    return host, seen


def test_a_host_gives_each_run_its_own_grant(tmp_path):
    """Runs do not share an executor, and the host's registry is untouched."""
    host, seen = sandboxed_host(tmp_path)

    alpha, beta = host.run_tools(one_stage_run("read_file")), host.run_tools(one_stage_run("read_file", source="beta"))

    assert alpha is not beta and alpha.executor is not beta.executor
    assert host.tools.executor is None, "the host's shared registry was given a run's executor"
    assert [grant.sources for grant in seen] == [("alpha",), ("beta",)]
    assert asyncio.run(alpha.execute("read_file", {"path": "brief.md"}, run_id="r1", agent="writer")) == "1: Alpha evidence"
    assert asyncio.run(beta.execute("read_file", {"path": "brief.md"}, run_id="r2", agent="writer")) == "1: Beta evidence"


def test_a_sandboxed_host_runs_a_whole_run_through_the_boundary(tmp_path):
    """The boundary is invisible to the application: same run, same events."""
    calls = []

    class Recording:
        def __init__(self, bound):
            self.inner = InProcessTransport(bound.tools)

        async def call(self, value):
            calls.append((value["tool"], value["grant"]["sources"]))
            return await self.inner.call(value)

    provider = ScriptedProvider({"writer": [{"type": "tool", "name": "read_file", "arguments": {"path": "brief.md"}},
                                           {"type": "final", "content": "Done", "text": "Draft"}]})
    host, _ = sandboxed_host(tmp_path, transport=Recording, provider=provider)
    value = one_stage_run("read_file")
    run_id = host.repository.create(value)

    async def exercise():
        tools = host.run_tools(value)
        return [event async for event in host.repository.execute(run_id, host.provider, tools)]

    events = asyncio.run(exercise())
    called = [event for event in events if event["type"] == "ToolCalled"]

    assert calls == [("read_file", ["alpha"])], "the call did not go through the boundary"
    # The public event carries the shape of the call, not the text it read.
    assert called[0]["tool"] == "read_file" and called[0]["ok"] and called[0]["output_chars"] > 0
    assert events[-1]["status"] == "completed"


def test_the_in_process_sandbox_is_a_factory(tmp_path):
    """`in_process_sandbox()` is what a deployment uses before it has a kernel."""
    tools, resources, _, _ = setup_resources(tmp_path)
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), tools, reviewer="ada",
                        resources=resources, source_extensions={".md"}, sandbox=in_process_sandbox())
    bound = host.run_tools(one_stage_run("read_file"))

    assert isinstance(bound.executor, SandboxExecutor)
    assert asyncio.run(bound.execute("read_file", {"path": "brief.md"})) == "1: Alpha evidence"
