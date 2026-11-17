"""Security review of parallel steps (docs/architecture/parallel-steps-security-review.md).

PS1: a run-wide limit holds even for a loop that ignores it. The authority the
     control plane answers through refuses model calls once the limit is
     reached and a small write-up allowance is used.
PS2: stopping a run never hangs, even when its event queue is full.
"""

import asyncio
import time

import pytest

from conclave import Capability, Completion, RunFinished, RunStarted, Tool, ToolRegistry, Usage
from conclave.application.definition import run_application
from conclave.application.sandbox import ControlPlaneAuthority, InProcessChannel, SandboxAuthority
from conclave.authority import ModelCall
from conclave.budget import LimitReached
from conclave.protocol import AgentResult, PrivateState
from conftest import CONFIG, make_budget
from parallel_support import Gated, plan

NOTE = '{"type": "note", "content": "more"}'


class Spender:
    """A model that answers every call with 80 tokens."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **_):
        self.calls += 1
        return Completion(text=NOTE, usage=Usage(80, 0))


async def greedy(authority, count):
    """A loop that ignores every limit: it asks, settles and asks again."""
    answered = 0
    for _ in range(count):
        try:
            completion = await authority.complete(ModelCall(run_id="r", agent="a", messages=[{"role": "user", "content": "x"}]))
        except LimitReached:
            break
        await authority.settle("tokens", completion.usage.total_tokens)
        answered += 1
    return answered


def test_the_control_plane_stops_answering_once_the_limit_and_its_allowance_are_spent():
    budget = make_budget(tokens=100)
    budget.post_limit_calls = 2
    provider = Spender()
    plane = ControlPlaneAuthority(provider, budget, {})
    sandbox = SandboxAuthority(InProcessChannel(plane))
    answered = asyncio.run(greedy(sandbox, 10))
    # 80, then 160 reaches the limit; two more are allowed for write-ups.
    assert answered == 4 and provider.calls == 4
    assert budget.spent_on("tokens") == 320


def test_a_standalone_runtime_without_an_allowance_is_not_refused():
    """The allowance is set by the application runner; the core alone keeps its old behaviour."""
    budget = make_budget(tokens=100)
    plane = ControlPlaneAuthority(Spender(), budget, {})
    assert asyncio.run(greedy(SandboxAuthority(InProcessChannel(plane)), 5)) == 5


class GreedyLoop:
    """A sandboxed loop, modified to ignore the run's limits."""

    def __init__(self, provider, tools, budget, agents, *, config, approver=None):
        self.authority = SandboxAuthority(InProcessChannel(ControlPlaneAuthority(provider, budget, {}, approver)))

    async def stream(self, spec, prompt):
        yield RunStarted(run_id="greedy", task=prompt, config=CONFIG)
        for _ in range(50):
            completion = await self.authority.complete(ModelCall(run_id="greedy", agent=spec.name, messages=[{"role": "user", "content": "x"}]))
            await self.authority.settle("tokens", completion.usage.total_tokens)
        yield RunFinished(run_id="greedy", agent=spec.name, status="completed",
                          result=AgentResult(content="Done", private=PrivateState(CONFIG), data={"text": "spent it all"}))


def test_a_run_whose_loop_ignores_the_limit_is_stopped_at_the_limit_anyway():
    definition = plan(["a", "b"], [("a", "b")], limit=1, budget={"tokens": 100, "max_depth": 1})
    provider = Spender()

    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Work", loop=GreedyLoop)]

    events = asyncio.run(go())
    # One step at once, depth 1: two further calls after 160 reached the limit.
    assert provider.calls == 4, "not the 50 the loop asked for"
    assert events[-1]["status"] == "stopped" and events[-1]["budget"] == "tokens"
    stopped = next(event for event in events if event["type"] == "StageFinished" and event["stage"] == "a")
    assert stopped["status"] == "stopped" and "token limit" in stopped["reason"]


def test_a_cooperative_run_never_reaches_the_allowance():
    """Every write-up a well-behaved loop makes fits in the allowance."""
    definition = plan(["a", "b", "c", "d"], [("a", "d")], limit=3, budget={"tokens": 200})
    provider = Gated(definition, tokens=80, scripts={step: [{"type": "note", "content": "x"}] * 6 for step in "abc"})

    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Work")]

    events = asyncio.run(go())
    assert events[-1]["status"] == "stopped" and events[-1]["budget"] == "tokens"
    assert all(event["status"] == "completed" for event in events if event["type"] == "StageFinished"), "every write-up was answered"


def test_closing_a_run_whose_event_queue_is_full_does_not_hang():
    definition = plan(["a", "b", "c", "d"], limit=4)
    for agent in definition["agents"]:
        agent["tools"] = ["look"]
    tools = ToolRegistry(20000)

    async def look(**_):
        return "nothing"

    tools.register(Tool("look", "Look", {}, look, frozenset({Capability.READ})))
    provider = Gated(definition, scripts={step: [{"type": "tool", "name": "look", "arguments": {}}] * 7 for step in "abcd"})

    async def go():
        stream = run_application(definition, provider, tools, "Work")
        seen = []
        while sum(event["type"] == "StageStarted" for event in seen) < 4:
            seen.append(await anext(stream))
        # Nobody reads any more: the steps fill the queue and wait on it.
        await asyncio.sleep(0.3)
        started = time.monotonic()
        await asyncio.wait_for(stream.aclose(), 2)
        return time.monotonic() - started

    assert asyncio.run(go()) < 2
