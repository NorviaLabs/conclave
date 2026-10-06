"""Run tool calls behind a boundary.

The control plane decides everything that needs authority: whether the call is
permitted, whether a person approved it, what it costs. Only then does the call
cross to an executor. A sandbox is handed a grant — the tools it may run, and
the sources they may read — and nothing it could use to authorize itself: no
approver, no budget, no store, and no other tenant's data.

Both sides enforce the grant. The control plane refuses to send a call outside
it, and the sandbox refuses to run one that arrives anyway, so neither a bug nor
a forged request turns into a tool call nobody granted. Truncation stays in the
control plane as well: a sandbox does not decide how much text reaches a prompt.

An agent loop can cross the same boundary. `ControlPlaneAuthority` holds the
provider, the budget and the approver and answers a loop's questions;
`SandboxAuthority` asks them over a transport. The loop itself is unchanged, so
a sandbox runs exactly the loop this repository tests, and the counters and the
approval never leave the control plane.

The transport is the deployment's — a microkernel API, a queue, or the
in-process call used here. `serve_call` is what runs on the sandbox side, and
the source containment and extension filters inside the tools apply there too,
because the sandbox builds its own tools from the source it was given.
"""

import asyncio

from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Mapping,
    Protocol,
)

from ..approval import (
    Approval,
    Approver,
)
from ..authority import (
    InProcessAuthority,
    ModelCall,
    RunAuthority,
)
from ..budget import RunBudget
from ..events import (
    AgentFinished,
    Event,
    RunFinished,
    rebuild_event,
    event_payload,
)
from ..protocol import AgentResult
from ..providers import (
    Completion,
    ModelProvider,
    Usage,
)
from ..registry import ToolExecutor, ToolInvocation
from ..runtime import ToolAgentRuntime
from ..spec import (
    AgentSpec,
    Tool,
)
from ..config import RunConfig
from .resources import request_resources


@dataclass(frozen=True)
class Grant:
    """One run's scope inside a sandbox."""

    run_id: str
    tools: tuple[str, ...]
    sources: tuple[str, ...] = ()

    def allows(self, name: str) -> bool:
        return name in self.tools

    def payload(self) -> dict[str, Any]:
        """What crosses the boundary: a scope, never a credential."""

        return {"run_id": self.run_id, "tools": list(self.tools), "sources": list(self.sources)}


def grant_for(request: dict, tools, *, run_id: str = "") -> Grant:
    """The scope one run's sandbox gets.

    The tools are the ones this run's agents may use and the host registered,
    and the source is the one the run was bound to — the same binding the
    control plane resolved, so the sandbox reads what the request pinned.
    """

    declared = {name for agent in request["definition"]["agents"] for name in agent["tools"]}
    source = request_resources(request).get("source")

    return Grant(run_id=run_id, tools=tuple(sorted(declared & set(tools.tools))),
                 sources=(source,) if isinstance(source, str) else ())


class SandboxTransport(Protocol):
    """How a call reaches the sandbox. A deployment supplies a real one."""

    async def call(self, request: dict[str, Any]) -> dict[str, Any]: ...


class SandboxExecutor(ToolExecutor):
    """The control plane's side of the boundary."""

    def __init__(self, transport: SandboxTransport, grant: Grant):
        self.transport, self.grant = transport, grant

    async def execute(self, invocation: ToolInvocation) -> str:
        if not self.grant.allows(invocation.name):
            raise RuntimeError(f"{invocation.name} is not granted to this run's sandbox")

        response = await self.transport.call({"grant": self.grant.payload(), "tool": invocation.name,
                                              "arguments": invocation.arguments, "run_id": invocation.run_id,
                                              "agent": invocation.agent})

        if not isinstance(response, dict) or ("text" in response) == ("error" in response):
            raise RuntimeError("The tool sandbox returned an unusable response")

        if "error" in response:
            raise RuntimeError(str(response["error"]))

        return response["text"]


async def serve_call(tools: Mapping[str, Tool], request: dict[str, Any]) -> dict[str, Any]:
    """The sandbox's side: run one call if the grant allows it.

    `tools` holds only what this sandbox was built with, so a call for anything
    else cannot run even if it arrives. A deployment must still authenticate the
    control plane: this enforces the grant it was given, and cannot tell a
    legitimate one from a forged one.
    """

    grant = request.get("grant") if isinstance(request.get("grant"), dict) else {}
    name = request.get("tool")
    allowed = grant.get("tools") if isinstance(grant.get("tools"), list) else []
    tool = tools.get(name) if isinstance(name, str) and name in allowed else None

    if tool is None:
        return {"error": f"{name} is not granted to this sandbox"}

    arguments = request.get("arguments")

    if not isinstance(arguments, dict):
        return {"error": "Provide the tool arguments"}

    try:
        return {"text": await tool.func(**arguments)}

    except Exception as error:
        return {"error": f"Tool error: {error}"}


class InProcessTransport:
    """A transport that is just a call.

    The same request and response shape a remote sandbox uses, without a second
    process. Useful to exercise a deployment before it has a kernel; it is not
    isolation.
    """

    def __init__(self, tools: Mapping[str, Tool]):
        self.tools = tools

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return await serve_call(self.tools, request)


def in_process_sandbox():
    """A factory for `LocalRunHost(sandbox=...)`: each run gets its own grant.

    A deployment with a real kernel writes its own factory instead, because the
    transport must be built per run: one transport holds one run's tools.
    """

    def build(tools, request):
        return SandboxExecutor(InProcessTransport(tools.tools), grant_for(request, tools))

    return build


class ControlPlaneAuthority:
    """The control plane's side of an agent loop: it answers, and it decides.

    It holds the provider, the budget and the approver, and it decides a
    destructive call from *its own* tool declaration — a sandbox that reports a
    write tool as harmless changes nothing, because the name is looked up here.
    """

    def __init__(self, provider: ModelProvider, budget: RunBudget, tools: Mapping[str, Tool], approver: Approver | None = None):
        self.inner = InProcessAuthority(provider, budget, approver)
        self.tools = tools

    async def answer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Answer one question from a loop that runs elsewhere."""

        kind = request.get("kind")

        if kind == "complete":
            completion = await self.inner.complete(ModelCall(run_id=str(request.get("run_id", "")), agent=str(request.get("agent", "")),
                                                             messages=request.get("messages") or []))
            usage = completion.usage
            return {"text": completion.text,
                    "usage": None if usage is None else {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}}

        if kind == "approve":
            tool = self.tools.get(request.get("tool"))
            decision = (await self.inner.approve(tool, str(request.get("agent", "")), request.get("arguments") or {}, str(request.get("run_id", "")))
                        if tool is not None else Approval.REFUSED)
            return {"decision": str(decision)}

        if kind == "reserve":
            return {"refusal": await self.inner.reserve(str(request.get("dimension")), float(request.get("amount", 1)))}

        if kind == "settle":
            amount = request.get("amount")
            return {"refusal": await self.inner.settle(str(request.get("dimension")), None if amount is None else float(amount))}

        if kind == "reserve_delegation":
            return {"refusal": await self.inner.reserve_delegation(str(request.get("agent")), int(request.get("depth", 1)),
                                                                   tuple(request.get("ancestry") or ()))}

        if kind == "may_delegate":
            return {"allowed": self.inner.may_delegate(int(request.get("depth", 1)))}

        if kind == "exceeded":
            return {"exceeded": await self.inner.exceeded(str(request.get("dimension")))}

        if kind == "numbers":
            limit, used = await self.inner.numbers(str(request.get("dimension")))
            return {"limit": limit, "used": used}

        raise ValueError(f"Unknown authority request: {kind}")


class SandboxAuthority(RunAuthority):
    """The sandbox's side: ask the control plane, hold nothing.

    The declared depth limit travels with the run, because a prompt needs it;
    the counters never do, so `numbers` always asks. A deployment that needs
    depth to be checked remotely gets that for free: `reserve_delegation` is
    what enforces it.
    """

    def __init__(self, transport: SandboxTransport, *, max_depth: int | None = None):
        self.transport, self.declared_depth = transport, max_depth

    async def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        response = await self.transport.call(request)

        if not isinstance(response, dict):
            raise RuntimeError("The control plane returned an unusable response")

        return response

    async def complete(self, call: ModelCall) -> Completion:
        response = await self.ask({"kind": "complete", "run_id": call.run_id, "agent": call.agent, "messages": call.messages})
        usage = response.get("usage")

        return Completion(text=str(response.get("text", "")),
                          usage=None if usage is None else Usage(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens")))

    async def approve(self, tool: Tool, agent: str, arguments: dict[str, Any], run_id: str) -> Approval:
        response = await self.ask({"kind": "approve", "run_id": run_id, "agent": agent, "tool": tool.name, "arguments": arguments})
        decision = response.get("decision")

        if decision not in {str(item) for item in Approval}:
            raise RuntimeError("The control plane returned an unusable decision")

        return Approval(decision)

    async def reserve(self, dimension: str, amount: float = 1) -> str | None:
        response = await self.ask({"kind": "reserve", "dimension": dimension, "amount": amount})

        return response.get("refusal")

    async def settle(self, dimension: str, amount: float | None) -> str | None:
        response = await self.ask({"kind": "settle", "dimension": dimension, "amount": amount})

        return response.get("refusal")

    async def reserve_delegation(self, agent: str, depth: int, ancestry: tuple[str, ...]) -> str | None:
        response = await self.ask({"kind": "reserve_delegation", "agent": agent, "depth": depth, "ancestry": list(ancestry)})

        return response.get("refusal")

    async def exceeded(self, dimension: str) -> bool:
        return bool((await self.ask({"kind": "exceeded", "dimension": dimension})).get("exceeded"))

    async def numbers(self, dimension: str) -> tuple[float | None, float]:
        response = await self.ask({"kind": "numbers", "dimension": dimension})

        return (response.get("limit"), float(response.get("used", 0.0)))

    def may_delegate(self, depth: int) -> bool:
        """Declared policy, so it needs no round trip.

        `reserve_delegation` is what actually enforces depth, on the side that
        holds the counters.
        """

        return self.declared_depth is None or depth < self.declared_depth

    @property
    def max_depth(self) -> int | None:
        return self.declared_depth


class InProcessControlPlane:
    """A transport that answers authority requests in this process.

    The same request and response shape a remote control plane uses, so a
    deployment can run its loop through this boundary before it has a kernel —
    and a test can drive both sides without a network.
    """

    def __init__(self, authority: ControlPlaneAuthority):
        self.authority = authority

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.authority.answer(request)


def sandboxed_loop(transport: SandboxTransport, *, tools: Any, agents: Any, config: RunConfig, max_depth: int | None = None) -> ToolAgentRuntime:
    """The loop as a sandbox runs it: granted tools, and an authority that asks.

    There is deliberately no provider, no budget and no approver here. The loop
    asks for the model call, the money and the permission over `transport`, and
    holds nothing it could answer with.
    """

    return ToolAgentRuntime(None, tools, None, agents, config=config,
                            authority=SandboxAuthority(transport, max_depth=max_depth))


class LoopChannel(SandboxTransport, Protocol):
    """One sandboxed loop, both directions.

    The sandbox asks over `call` and reports over `emit`; the control plane
    answers and reads. A deployment implements this over its kernel API; the
    in-process channel below is the same shape without a second process.
    """

    async def emit(self, payload: dict[str, Any]) -> None: ...


async def run_agent(channel: LoopChannel, spec: AgentSpec, task: str, *, tools: Any, agents: Any, config: RunConfig,
                    max_depth: int | None = None, depth: int = 1, ancestry: tuple[str, ...] = (),
                    parent_run_id: str | None = None) -> None:
    """The sandbox's side: run one agent and report what happens.

    This is what a kernel calls. The loop is the loop this repository tests;
    every event goes out over the channel as it happens, so a control plane can
    record the run while it is still going.
    """

    loop = sandboxed_loop(channel, tools=tools, agents=agents, config=config, max_depth=max_depth)

    async for event in loop.stream(spec, task, depth=depth, ancestry=ancestry, parent_run_id=parent_run_id):
        await channel.emit(event_payload(event))


class SandboxedLoop:
    """The control plane's side: an agent run that happens somewhere else.

    It offers the same `stream` a local runtime does, so whatever drives the
    loop does not care where it runs, and it stops the sandbox when the caller
    stops reading.
    """

    def __init__(self, channel: LoopChannel, start: Any, *, config: RunConfig):
        self.channel, self.start, self.config = channel, start, config

    async def stream(self, spec: AgentSpec, task: str, *, depth: int = 1, ancestry: tuple[str, ...] = (),
                     parent_run_id: str | None = None) -> AsyncIterator[Event]:
        running = asyncio.ensure_future(self.start(spec, task, depth, ancestry, parent_run_id))
        stop = {RunFinished} if depth == 1 else {AgentFinished}
        finished = False

        try:
            async for payload in self.channel.events():
                event = rebuild_event(payload, self.config)
                yield event

                if type(event) in stop:
                    finished = True
                    return

        finally:
            # A caller that stops reading stops the loop, rather than leaving a
            # sandbox working on something nobody is watching.
            if not running.done():
                running.cancel()

            outcome = await asyncio.gather(running, return_exceptions=True)

        if finished:
            return

        # The events stopped without the loop finishing: the sandbox broke, and
        # saying so is better than reporting a run that quietly did nothing.
        failure = next(
            (item for item in outcome if isinstance(item, BaseException) and not isinstance(item, asyncio.CancelledError)),
            None,
        )

        if failure is not None:
            raise RuntimeError(f"The sandbox failed: {failure}") from failure

        raise RuntimeError("The sandbox stopped without finishing the run")


class InProcessChannel:
    """A channel where both sides are in this process.

    The same two directions a kernel provides — questions one way, events the
    other — without a second process. It is not isolation; it is what lets a
    deployment exercise the boundary, and what the tests drive.
    """

    def __init__(self, authority: ControlPlaneAuthority):
        self.authority = authority
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.authority.answer(request)

    async def emit(self, payload: dict[str, Any]) -> None:
        await self.queue.put(payload)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            payload = await self.queue.get()

            if payload is None:
                return

            yield payload

    def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(None)


def sandboxed_agent(channel: InProcessChannel, *, tools: Any, agents: Any, config: RunConfig, max_depth: int | None = None) -> SandboxedLoop:
    """Run one agent through this channel, as the control plane's side sees it."""

    async def start(spec, task, depth, ancestry, parent_run_id):
        try:
            await run_agent(channel, spec, task, tools=tools, agents=agents, config=config, max_depth=max_depth,
                            depth=depth, ancestry=ancestry, parent_run_id=parent_run_id)
        finally:
            channel.close()

    return SandboxedLoop(channel, start, config=config)
