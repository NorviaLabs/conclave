"""Shared builders for the resumable external writes tests.

Nothing here imports the new interfaces at module level: each test imports what
it exercises, so before the feature exists every test fails on its own.
"""

import asyncio
import copy

from conclave import Capability, Tool, ToolRegistry
from conftest import CONFIG, ScriptedProvider


WRITE = frozenset({Capability.WRITE, Capability.NETWORK})
READ = frozenset({Capability.READ})


class Recorder:
    """A destructive tool that records each call and the action it ran under.

    `gate` holds a call open until the test releases it, which is how a test
    stops a run *while* a write is in flight. `fail` is a queue of exceptions to
    raise, one per call.
    """

    def __init__(self, name="send_note", *, result="sent", check=None, check_unavailable=""):
        self.name, self.result = name, result
        self.calls, self.actions, self.fail = [], [], []
        self.gate = None
        self.started = asyncio.Event()
        self.check, self.check_unavailable = check, check_unavailable

    async def __call__(self, **arguments):
        from conclave.actions import current_action

        self.calls.append(dict(arguments))
        self.actions.append(current_action.get())
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise self.fail.pop(0)
        return self.result

    def tool(self):
        return Tool(self.name, "Send a note to the team", {"text": "string"}, self, WRITE,
                    check=self.check, check_unavailable=self.check_unavailable)


async def look(**arguments):
    return "nothing new"


def registry(*recorders):
    tools = ToolRegistry(20000)
    for recorder in recorders:
        tools.register(recorder.tool())
    tools.register(Tool("look", "Look around", {}, look, READ))
    return tools


def checker(result, *, summary="", exact=False, detail="", calls=None):
    """A Check that always answers `result`, recording what it was asked."""
    from conclave.actions import Check, CheckResult

    async def run(arguments, context):
        if calls is not None:
            calls.append((dict(arguments), context))
        return CheckResult(result, summary or f"check said {result}", exact=exact, detail=detail)

    return Check(run=run, describe=lambda arguments: "Will look for the note")


def note(text="hello"):
    return {"type": "tool", "name": "send_note", "arguments": {"text": text}}


LOOK = {"type": "tool", "name": "look", "arguments": {}}


def final(text="Announced"):
    return {"type": "final", "content": "Done", "text": text}


def write_request(*, parallel=False, max_tool_steps=None, fan_out=4, autonomous=False):
    """One AI step that may send a note; optionally a second, parallel one."""

    config = dict(vars(CONFIG))
    config.update(max_tool_steps=max_tool_steps, fan_out_limit=fan_out)
    agents = [{"id": "writer", "instructions": "Tell the team", "tools": ["send_note", "look"]}]
    state = [{"key": "notice", "type": "text", "merge": "replace", "statuses": []}]
    workflow = [{"id": "announce", "agent": "writer", "task": "Announce", "output": "notice"}]
    if parallel:
        agents.append({"id": "builder", "instructions": "Open the change", "tools": ["open_change", "look"]})
        state.append({"key": "change", "type": "text", "merge": "replace", "statuses": []})
        workflow.append({"id": "build", "agent": "builder", "task": "Build", "output": "change"})
    definition = {
        "version": 1, "name": "Announce", "agents": agents, "state": state, "workflow": workflow,
        "connections": [] if parallel else None,
        "views": [{"id": "notice_view", "collection": "notice", "layout": "prose"}],
        "run_config": config,
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    if definition["connections"] is None:
        del definition["connections"]
    if autonomous:
        # A host approves declared tools up front, so nothing waits on a person.
        definition["autonomous_execution"] = True
    return copy.deepcopy({"version": 1, "application_id": "announce", "revision": 1,
                          "task": "Tell the team the fix is ready", "inputs": {}, "definition": definition})


def approve(request):
    return True


async def drain(stream):
    return [event async for event in stream]


async def run_until_blocked(stream, recorder):
    """Consume a run in the background until `recorder` is mid-call, then stop it.

    What a process that dies mid-write leaves behind, as far as this process can
    show it: the consumer is cancelled while the tool is still waiting.
    """

    recorder.gate = asyncio.Event()
    task = asyncio.create_task(drain(stream))
    async with asyncio.timeout(5):
        await recorder.started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await stream.aclose()


def scripted(**scripts):
    return ScriptedProvider({name: list(items) for name, items in scripts.items()})
