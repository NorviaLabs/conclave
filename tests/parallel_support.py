"""Helpers for the parallel-steps tests: plans, a gated provider, drivers.

Concurrency here is arranged, not hoped for. Each member's model call waits on
its own gate until the test releases it, so "a is still running when b
finishes" is a fact of the test, whatever the machine's speed.
"""

import asyncio
import copy
import json
import random
import re

from conclave import Completion, ToolRegistry, Usage
from conclave.application.definition import run_application
from conclave.application.runs import RunRepository
from conclave.protocol import WRITE_UP_RULE
from conftest import CONFIG

UNLIMITED = {"max_delegations": None, "max_per_agent": None, "max_depth": None, "tool_calls": None,
             "wall_clock_seconds": None, "tokens": None, "cost_usd": None}


def plan(steps, edges=(), *, limit=3, budget=None, human=(), outputs=None, records=None):
    """A valid application: one member per step.

    `outputs` maps a step to the collection it writes (default: its own), so
    several steps can write one collection. `records` maps a collection to its
    merge policy; every other collection is text.
    """
    outputs = outputs or {}
    records = records or {}
    keys = list(dict.fromkeys(outputs.get(step, step) for step in steps))
    return {
        "version": 1, "name": "Parallel",
        "agents": [{"id": step, "name": step, "kind": "human" if step in human else "ai",
                    "instructions": "Work", "tools": []} for step in steps],
        "state": [({"key": key, "type": "records", "merge": records[key], "statuses": ["draft", "approved"]}
                   if key in records else {"key": key, "type": "text", "merge": "replace", "statuses": []}) for key in keys],
        "workflow": [{"id": step, "agent": step, "task": f"Do {step}", "output": outputs.get(step, step)} for step in steps],
        "connections": [{"from": before, "to": after} for before, after in edges],
        "views": [{"id": "view", "collection": keys[0], "layout": "cards" if keys[0] in records else "prose"}],
        "run_config": {**dict(vars(CONFIG)), "fan_out_limit": limit},
        "run_budget": {**UNLIMITED, **(budget or {})},
    }


def answer_for(definition, step):
    """The fixed answer a step gives: its own text, or records that collide.

    Records use a shared id ("shared") as well as their own, so an upsert or
    append gives a different state in a different merge order.
    """
    stage = next(item for item in definition["workflow"] if item["id"] == step)
    collection = next(item for item in definition["state"] if item["key"] == stage["output"])
    if collection["type"] == "text":
        return {"type": "final", "content": "Done", "text": f"{step} says hi"}
    return {"type": "final", "content": "Done", "records": [
        {"id": "shared", "title": f"From {step}", "body": f"{step} wrote this", "status": "draft", "source_refs": []},
        {"id": f"own-{step}", "title": step, "body": f"Only {step}", "status": "draft", "source_refs": []}]}


class Gated:
    """A provider whose calls wait on per-member gates.

    - `gated`: members whose calls wait until `release(name)`.
    - `delays`: members whose calls sleep that long instead.
    - `scripts`: per member, a list of actions, one per call; after the list
      runs out, or without one, the member gives its fixed answer. An
      exception in the list is raised by that call.
    - `tokens`: usage reported per call: a number, or a number per member.

    A write-up (the one call a step makes after a limit stops it) always gets
    the member's fixed answer, and is recorded in `write_ups`.
    """

    def __init__(self, definition, *, gated=(), delays=None, scripts=None, tokens=None):
        self.definition = definition
        self.gates = {name: asyncio.Event() for name in gated}
        self.delays = delays or {}
        self.scripts = {name: list(actions) for name, actions in (scripts or {}).items()}
        self.tokens = tokens
        self.started, self.finished, self.prompts, self.calls, self.write_ups = [], [], {}, [], []
        self.running = self.peak = 0

    @staticmethod
    def member(messages):
        match = re.search(r"You are (\S+) inside", messages[0]["content"])
        return match.group(1) if match else "?"

    def release(self, *names):
        for name in names:
            self.gates.setdefault(name, asyncio.Event()).set()

    async def complete(self, messages, **_):
        name = self.member(messages)
        self.calls.append(name)
        self.started.append(name)
        self.prompts.setdefault(name, []).append(messages[-1]["content"])
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if name in self.gates:
                await self.gates[name].wait()
            else:
                await asyncio.sleep(self.delays.get(name, 0))
        finally:
            self.running -= 1
        self.finished.append(name)
        queue = self.scripts.get(name)
        if messages[-1]["content"] == WRITE_UP_RULE:
            self.write_ups.append(name)
            action = answer_for(self.definition, name)
        else:
            action = queue.pop(0) if queue else answer_for(self.definition, name)
        if isinstance(action, BaseException):
            raise action
        tokens = self.tokens.get(name) if isinstance(self.tokens, dict) else self.tokens
        return Completion(text=json.dumps(action), usage=Usage(tokens, 0) if tokens else None)


async def until(predicate, *, timeout=10.0, what="condition"):
    """Wait for something the run will do, without betting on how long it takes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"Timed out waiting for {what}")
        await asyncio.sleep(0.005)


class Driven:
    """A run consumed in the background, so a test can act while it works."""

    def __init__(self, stream):
        self.events = []
        self.stream = stream
        self.task = asyncio.create_task(self._consume())

    async def _consume(self):
        async for event in self.stream:
            self.events.append(event)
        return self.events

    def types(self):
        return [event["type"] for event in self.events]

    def of(self, kind, stage=None):
        return [event for event in self.events if event["type"] == kind and (stage is None or event.get("stage") == stage)]

    async def done(self, timeout=20.0):
        return await asyncio.wait_for(self.task, timeout)


def start(definition, provider, task="Do the work", **kwargs):
    return Driven(run_application(definition, provider, ToolRegistry(20000), task, **kwargs))


def drive(definition, provider, task="Do the work", **kwargs):
    """Run to the end and return every event."""
    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), task, **kwargs)]
    return asyncio.run(go())


def request_for(definition):
    return {"version": 1, "application_id": "parallel", "revision": 1, "task": "Do the work", "inputs": {},
            "definition": copy.deepcopy(definition)}


def store_run(tmp_path, definition):
    repository = RunRepository(tmp_path / "runs.sqlite")
    return repository, repository.create(request_for(definition))


def collect(stream):
    async def go():
        return [event async for event in stream]
    return asyncio.run(go())


def without_run_ids(value):
    """Contributions carry a run id per agent run; equivalence ignores it."""
    if isinstance(value, dict):
        return {key: without_run_ids(item) for key, item in value.items() if key != "run_id"}
    if isinstance(value, (list, tuple)):
        return [without_run_ids(item) for item in value]
    return value


def commits(events):
    """What the shared results went through: the equivalence key."""
    return [without_run_ids((event["stage"], event["patch"], event["version"], sorted(event["keys"]), event["state"]))
            for event in events if event["type"] == "ApplicationStateChanged"]


def shuffled_delays(definition, seed):
    """Small random delays per member, so steps finish in a seeded random order."""
    chooser = random.Random(seed)
    return {step["agent"]: chooser.choice([0, 0.005, 0.01, 0.02, 0.04]) for step in definition["workflow"]}
