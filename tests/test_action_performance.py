"""Step 9 — the performance fixes for resumable external writes (P1–P5).

Each test pins one finding from
docs/architecture/resumable-writes-performance-review.md. They count work
(bytes sealed, snapshots built, connections opened) rather than time it, so
they stay stable on any machine.
"""

import asyncio
import json
import sqlite3
import sys
from contextlib import closing, contextmanager

import pytest

from actions_support import Recorder, note, registry, write_request
from conclave import AgentSpec, Capability, Completion, RunBudget, RunConfig, Tool, ToolAgentRuntime, ToolRegistry
from conclave.actions import ActionStart
from conclave.application.runs import RunRepository
from conftest import ScriptedProvider, make_budget
from test_runtime_actions import FINAL, HELPER, WRITER, FakeLog

CONFIG = RunConfig(max_tool_steps=None, max_notes=12, max_note_chars=300, max_tool_output_chars=None, fan_out_limit=1)


def frame(log, messages, **extra):
    return {"version": 1, "agent": "writer", "log": log, "messages": messages, "reply": "{}", "action": {"type": "tool"},
            "action_id": "a1", "observation": None, "notes": [], "checked": [], "step": 1, "reviewed": False,
            "nested": [], "delegation": None, **extra}


def conversation(length):
    return [{"role": "system", "content": "the system prompt, rebuilt every step"}] + [
        {"role": "user" if index % 2 else "assistant", "content": f"message {index} " + "x" * 50} for index in range(1, length)]


def rows(path, query, *parameters):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(query, parameters).fetchall()


# --- P1: the transcript is appended, not rewritten ---------------------------------


@pytest.fixture
def journaled(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    return repository, repository.create(write_request())


def begin(repository, run_id, frames, action_id="a1"):
    repository.journal.begin(run_id, ActionStart(action_id, "announce", "writer", "send_note", {"text": "t"}, "approved", frames, False),
                             budget={})


def test_saved_progress_gives_back_the_whole_conversation(journaled):
    repository, run_id = journaled
    parent = frame("parent-log", conversation(6), agent="writer", delegation={"agent": "helper", "prompt": "p"})
    child = frame("child-log", conversation(4), agent="helper")
    begin(repository, run_id, [parent, child])
    restored = repository.journal.progress(run_id)["announce"]["frames"]
    assert [item["messages"][1:] for item in restored] == [parent["messages"][1:], child["messages"][1:]]
    assert restored[0]["delegation"] == {"agent": "helper", "prompt": "p"}
    assert all(item["messages"][0]["role"] == "system" for item in restored), "the system prompt is rebuilt, not stored"


def test_each_save_appends_only_the_new_messages(journaled):
    repository, run_id = journaled
    begin(repository, run_id, [frame("log-1", conversation(5))])
    repository.journal.finish("a1", "completed", frames=[frame("log-1", conversation(7), observation="sent")], budget={})
    begin(repository, run_id, [frame("log-1", conversation(9))], action_id="a2")
    stored = rows(repository.path, "SELECT position FROM studio_run_transcript WHERE run_id = ? ORDER BY position", run_id)
    assert [position for (position,) in stored] == list(range(1, 9)), "each message once; the system prompt never"
    blob = rows(repository.path, "SELECT frames FROM studio_run_progress WHERE run_id = ?", run_id)[0][0]
    assert "message 3 " not in blob, "a saved frame refers to its messages instead of repeating them"
    assert repository.journal.progress(run_id)["announce"]["frames"][0]["messages"][1:] == conversation(9)[1:]


def test_sealed_messages_are_unreadable_at_rest(tmp_path):
    from cryptography.fernet import Fernet

    repository = RunRepository(tmp_path / "runs.sqlite", key=Fernet.generate_key())
    run_id = repository.create(write_request())
    begin(repository, run_id, [frame("log-1", conversation(4))])
    with closing(sqlite3.connect(repository.path)) as connection:
        assert "message 2 " not in "\n".join(connection.iterdump())
    assert repository.journal.progress(run_id)["announce"]["frames"][0]["messages"][1:] == conversation(4)[1:]


def test_forgotten_and_purged_progress_takes_its_transcript_with_it(journaled):
    repository, run_id = journaled
    begin(repository, run_id, [frame("log-1", conversation(4))])
    repository.journal.forget_progress(run_id, ["announce"])
    assert rows(repository.path, "SELECT COUNT(*) FROM studio_run_transcript") == [(0,)]
    begin(repository, run_id, [frame("log-2", conversation(4))], action_id="a2")
    repository.journal.purge(run_id)
    assert rows(repository.path, "SELECT COUNT(*) FROM studio_run_transcript") == [(0,)]


def test_an_agent_keeps_one_log_across_its_writes_and_a_resume():
    log = FakeLog()
    recorder = Recorder()
    runtime = ToolAgentRuntime(ScriptedProvider({"writer": [note("one"), note("two"), FINAL]}), registry(recorder), make_budget(),
                               {"writer": WRITER}, config=CONFIG, approver=lambda request: True, log=log)
    asyncio.run(_drain(runtime.stream(WRITER, "task", scope="announce")))
    logs = {frames[-1]["log"] for saved in log.frames.values() for frames in saved}
    assert len(logs) == 1 and next(iter(logs))
    after = log.frames[recorder.actions[0]][-1]
    again = FakeLog()
    resumed = ToolAgentRuntime(ScriptedProvider({"writer": [note("three"), FINAL]}), registry(Recorder()), make_budget(),
                               {"writer": WRITER}, config=CONFIG, approver=lambda request: True, log=again)
    asyncio.run(_drain(resumed.stream(WRITER, "task", scope="announce", resume=after)))
    assert {frames[-1]["log"] for saved in again.frames.values() for frames in saved} == logs, "a resumed agent continues its log"


def sealed_bytes_for(writes, monkeypatch, tmp_path):
    from conclave.application import journal as module

    total = [0]
    original = module.Sealer.seal

    def counting(self, value):
        sealed = original(self, value)
        total[0] += len(sealed)
        return sealed

    monkeypatch.setattr(module.Sealer, "seal", counting)
    repository = RunRepository(tmp_path / f"runs-{writes}.sqlite")
    run_id = repository.create(write_request())
    log = repository.journal.for_run(run_id)

    async def write(**arguments):
        return "y" * 2000

    tools = ToolRegistry(None)
    tools.register(Tool("write", "w", {"text": "string"}, write, frozenset({Capability.WRITE})))
    spec = AgentSpec(name="writer", instructions="w", tools={"write"})
    script = [{"type": "tool", "name": "write", "arguments": {"text": str(index)}} for index in range(writes)] + [FINAL]
    runtime = ToolAgentRuntime(ScriptedProvider({"writer": script}), tools, RunBudget(None, None, None), {"writer": spec},
                               config=CONFIG, approver=lambda request: True, log=log)
    asyncio.run(_drain(runtime.stream(spec, "task", scope="announce")))
    return total[0]


def test_recording_grows_with_the_step_not_with_its_square(tmp_path, monkeypatch):
    short, long = sealed_bytes_for(20, monkeypatch, tmp_path), sealed_bytes_for(40, monkeypatch, tmp_path)
    assert long / short < 2.6, f"doubling the writes multiplied the recorded bytes by {long / short:.1f}"


# --- P2: nothing is snapshotted when nothing records ------------------------------


def test_a_run_without_a_log_never_copies_its_conversation(monkeypatch):
    from conclave import runtime as module

    copies = []
    real = module.copy.deepcopy
    monkeypatch.setattr(module.copy, "deepcopy", lambda value, *args: copies.append(1) or real(value, *args))
    provider = ScriptedProvider({"writer": [note(), {"type": "delegate", "agent": "helper", "task": "Send it", "purpose": "p"}, FINAL],
                                 "helper": [note(), FINAL]})
    runtime = ToolAgentRuntime(provider, registry(Recorder()), make_budget(), {"writer": WRITER, "helper": HELPER},
                               config=CONFIG, approver=lambda request: True)
    asyncio.run(_drain(runtime.stream(WRITER, "task")))
    assert copies == []


def test_the_sandbox_asks_once_whether_anything_records():
    from conclave.application.sandbox import ControlPlaneAuthority, InProcessChannel, sandboxed_agent

    asked = []

    class Counting(InProcessChannel):
        async def call(self, request):
            if request.get("kind") == "records_actions":
                asked.append(1)
            return await super().call(request)

    plane = ControlPlaneAuthority(ScriptedProvider({"writer": [note("one"), note("two"), FINAL]}), make_budget(),
                                  {"send_note": Recorder().tool()}, lambda request: True)
    channel = Counting(plane)
    loop = sandboxed_agent(channel, tools=registry(Recorder()), agents={"writer": WRITER}, config=CONFIG)
    asyncio.run(_drain(loop.stream(WRITER, "task")))
    assert asked == [1]


# --- P3: a resume claim opens no sealed progress ------------------------------------


def test_claiming_a_resume_never_decrypts_saved_progress(tmp_path, monkeypatch):
    from conclave.application import journal as module

    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    repository._claim(run_id)
    repository._append(run_id, {"type": "ApplicationStarted", "name": "Announce", "state": {}, "views": [], "journal": 1})
    begin(repository, run_id, [frame("log-1", conversation(4))])
    repository.journal.finish("a1", "completed", frames=[frame("log-1", conversation(6))], budget={"used": 0, "per_agent": {}, "spent": {}})
    repository._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "stopped"})
    opened = []
    original = module.Sealer.open
    monkeypatch.setattr(module.Sealer, "open", lambda self, blob: opened.append(blob) or original(self, blob))
    repository._claim_resume(run_id)
    assert opened == []


# --- P4 and P5: reads stay flat as a run grows --------------------------------------


@contextmanager
def counted_connections(repository):
    count = [0]
    original = repository._connect

    @contextmanager
    def counting():
        count[0] += 1
        with original() as connection:
            yield connection

    repository._connect, repository.journal.connect = counting, counting
    try:
        yield count
    finally:
        repository._connect, repository.journal.connect = original, original


def actions_connections(tmp_path, how_many):
    from conclave.application.service import LocalRunHost

    repository = RunRepository(tmp_path / f"runs-{how_many}.sqlite")
    run_id = repository.create(write_request())
    repository._claim(run_id)
    for index in range(how_many):
        repository.journal.begin(run_id, ActionStart(f"a{index}", "announce", "writer", "send_note", {"text": "t"}, "approved",
                                                     [frame(f"log-{index}", conversation(2))], False), budget={})
    repository._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "stopped"})
    host = LocalRunHost(repository, ScriptedProvider(), registry(Recorder()), reviewer="Ana Ruiz")
    with counted_connections(repository) as count:
        asyncio.run(host.handle("actions", {"run_id": run_id}))
    return count[0]


def test_listing_actions_costs_the_same_for_five_or_fifty(tmp_path):
    few, many = actions_connections(tmp_path, 5), actions_connections(tmp_path, 50)
    assert few == many and many <= 4, (few, many)


def test_history_counts_actions_in_one_go(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    ids = [repository.create(write_request()) for _ in range(12)]
    repository._claim(ids[0])
    begin(repository, ids[0], [frame("log-1", conversation(2))])
    repository._append(ids[0], {"type": "ApplicationFinished", "status": "interrupted", "reason": "stopped"})
    with counted_connections(repository) as count:
        listed = repository.list("announce")
    assert count[0] <= 2, count[0]
    assert next(run for run in listed if run["id"] == ids[0])["actions"] == {"to_check": 0, "needs_decision": 1, "unknown": 0}
    assert all(run["actions"] == {"to_check": 0, "needs_decision": 0, "unknown": 0} for run in listed if run["id"] != ids[0])


async def _drain(stream):
    return [event async for event in stream]
