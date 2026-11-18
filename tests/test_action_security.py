"""Step 8 — the security fixes for resumable external writes (S1–S9).

Each test reproduces one finding from
docs/architecture/resumable-writes-security-review.md and pins its fix.
"""

import asyncio
import json
import sqlite3
import subprocess
import time
from contextlib import closing

import pytest

from conclave.tools import processes

from actions_support import Recorder, checker, registry, write_request
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from test_action_host import by_tool, crashed_mid_write, host_for
from test_runtime_actions import FakeLog, frames_of_a_pending_write, resumed


def start(run_id, action_id="a1", text="hello", tool="send_note"):
    from conclave.actions import ActionStart
    return ActionStart(action_id, "announce", "writer", tool, {"text": text}, "approved", [{"agent": "writer"}], False)


def uncertain(repository, run_id, action_id="a1", **options):
    repository.journal.begin(run_id, start(run_id, action_id, **options), budget={})
    repository.journal.finish(action_id, "uncertain", frames=[{"agent": "writer"}], budget={})


# --- S1: only the run's approver decides ----------------------------------------


@pytest.mark.parametrize("command, extra", [("actions", {}), ("check", {"action_id": None}),
                                            ("settle", {"action_id": None, "decision": "resend"})])
def test_another_person_in_the_tenant_cannot_see_or_decide_a_run(tmp_path, command, extra):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        value = {"run_id": run_id, **{key: action_id if item is None else item for key, item in extra.items()}}
        with pytest.raises(ValueError, match="approves this run"):
            await host.handle(command, value, principal=Principal("Mallory"))
        assert by_tool((await host.handle("actions", {"run_id": run_id}, principal=Principal("Ana Ruiz")))[1]["actions"])["state"] == "uncertain"
        await host.close()

    asyncio.run(exercise())


# --- S2: a run's log reaches only that run ----------------------------------------


def test_a_runs_log_cannot_read_or_change_another_runs_actions(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    mine, theirs = repository.create(write_request()), repository.create(write_request())
    uncertain(repository, theirs, "b1")
    repository.journal.settle("b1", "done", by="Ana", note="their private note")
    log = repository.journal.for_run(mine)
    assert log.resolution("b1") is None
    for attempt in (lambda: log.resend("b1"), lambda: log.finish("b1", "completed", frames=[{"forged": True}]),
                    lambda: log.checked("b1", __import__("conclave.actions", fromlist=["CheckResult"]).CheckResult("found", "x")),
                    lambda: log.settle("b1", "done", by="check", kind="check")):
        with pytest.raises(KeyError):
            attempt()
    assert repository.journal.get("b1")["state"] == "done"
    assert repository.journal.progress(theirs)["announce"]["frames"] == [{"agent": "writer"}]


def test_a_sandbox_control_plane_cannot_reach_another_runs_actions(tmp_path):
    from conclave.application.sandbox import ControlPlaneAuthority
    from conftest import ScriptedProvider, make_budget

    repository = RunRepository(tmp_path / "runs.sqlite")
    mine, theirs = repository.create(write_request()), repository.create(write_request())
    uncertain(repository, theirs, "b1")
    repository.journal.settle("b1", "resend", by="Ana")
    plane = ControlPlaneAuthority(ScriptedProvider(), make_budget(), {}, log=repository.journal.for_run(mine))

    async def exercise():
        for request in ({"kind": "begin_resend", "action_id": "b1"},
                        {"kind": "finish_action", "action_id": "b1", "state": "completed", "frames": [{"forged": True}]},
                        {"kind": "resolve_action", "action_id": "b1"}):
            answer = None
            try:
                answer = await plane.answer(request)
            except (KeyError, ValueError):
                continue
            assert answer.get("kind") == "stop", request

    asyncio.run(exercise())
    assert repository.journal.get("b1")["state"] == "resend"


# --- S3: a configured key seals the journal ----------------------------------------


@pytest.mark.parametrize("variable", ["CONCLAVE_INTEGRATION_KEY", "CONCLAVE_RUN_KEY"])
def test_a_configured_host_seals_what_it_records(tmp_path, monkeypatch, variable):
    from cryptography.fernet import Fernet, InvalidToken
    from conclave.application.service import configured_host

    key = Fernet.generate_key()
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(variable, key.decode())
    host = configured_host(workspace=tmp_path)
    run_id = host.repository.create(write_request())
    uncertain(host.repository, run_id, text="sealed at rest")
    with closing(sqlite3.connect(host.repository.path)) as connection:
        dump = "\n".join(connection.iterdump())
        blob = connection.execute("SELECT call FROM studio_run_actions").fetchone()[0]
    assert "sealed at rest" not in dump
    assert host.repository.journal.arguments("a1") == {"text": "sealed at rest"}
    if variable == "CONCLAVE_INTEGRATION_KEY":
        with pytest.raises(InvalidToken):
            Fernet(key).decrypt(blob)  # one key, one purpose: the journal's key is derived from it


# --- S4: a resend sends exactly the recorded call -----------------------------------


def test_a_resend_sends_the_recorded_call_not_the_saved_progress():
    _, frames = frames_of_a_pending_write(Recorder())
    tampered = [{**frames[-1], "action": {**frames[-1]["action"], "arguments": {"text": "not what was approved"}}}]
    events, provider, recorder, _ = resumed(tampered, {"state": "resend", "settled_by": "Ana", "settled_kind": "person",
                                                       "note": None, "check_summary": None, "check_detail": None,
                                                       "tool": "send_note", "arguments": {"text": "what was approved"}})
    assert recorder.calls == [{"text": "what was approved"}]


def test_a_resend_whose_progress_names_another_tool_stops():
    from conclave.actions import ActionStopped

    _, frames = frames_of_a_pending_write(Recorder())
    with pytest.raises(ActionStopped):
        resumed(frames, {"state": "resend", "settled_by": "Ana", "settled_kind": "person", "note": None,
                         "check_summary": None, "check_detail": None, "tool": "delete_everything",
                         "arguments": {"text": "hello"}})


def test_the_journal_hands_a_resend_its_recorded_call(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    uncertain(repository, run_id, text="recorded words")
    repository.journal.settle("a1", "resend", by="Ana")
    assert repository.journal.for_run(run_id).resolution("a1")["arguments"] == {"text": "recorded words"}


# --- S5: a check is a kind of settlement, not a name --------------------------------


def test_a_person_named_check_is_still_a_person(tmp_path):
    from conclave.actions import settled_observation

    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    uncertain(repository, run_id)
    repository._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "stopped"})
    repository.settle(run_id, "a1", "done", by="check", note="trust me")
    resolution = repository.journal.resolution("a1")
    assert resolution["settled_kind"] == "person"
    told = settled_observation(resolution)
    assert "Conclave checked" not in told and "check confirmed this action already happened" in told
    repository.settle(run_id, "a1", "undo", by="check")
    assert repository.journal.get("a1")["state"] == "uncertain"


def test_only_a_check_settles_as_a_check(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    uncertain(repository, run_id)
    repository._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "stopped"})
    tools = registry(Recorder(check=checker("found")))
    asyncio.run(repository.check_actions(run_id, tools))
    assert repository.journal.get("a1")["settled_kind"] == "check"


# --- S6: checks are paced like the connection's calls -------------------------------


def test_bound_checks_share_the_connections_lock_and_pacing(tmp_path):
    from cryptography.fernet import Fernet
    from conclave import ToolRegistry
    from conclave.actions import ActionContext
    from conclave.integrations.connections import ConnectionStore

    starts = []

    class Slow:
        def __init__(self, hosts):
            pass

        async def request(self, method, url, **kwargs):
            starts.append(time.monotonic())
            await asyncio.sleep(0.05)
            return []

    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), transport_factory=Slow)
    store.create({"id": "forge", "integration": "github", "name": "Forge", "applications": ["app"],
                  "operations": ["create_pull_request", "read_pull_request"], "credentials": {"token": "t"}}, "local", "owner")
    tools = store.bind(ToolRegistry(1000), {"agents": [{"id": "a", "tools": ["connection.forge.create_pull_request"]}]},
                       "local", "owner", "app")
    check = tools.tool("connection.forge.create_pull_request").check
    arguments = {"owner": "o", "repo": "r", "title": "t", "head": "h", "base": "main"}
    context = ActionContext("a1", "2026-11-16T10:40:00+00:00", arguments)

    async def exercise():
        await asyncio.gather(*(check.run(arguments, context) for _ in range(3)))

    asyncio.run(exercise())
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    assert len(starts) == 3 and all(gap >= 0.19 for gap in gaps), gaps


def test_an_action_is_not_checked_again_within_moments(tmp_path):
    calls = []

    async def exercise():
        recorder = Recorder(check=checker("inconclusive", calls=calls))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        for _ in range(3):
            _, listed = await host.handle("check", {"run_id": run_id, "action_id": action_id})
        assert by_tool(listed["actions"])["check"]["result"] == "inconclusive"
        await host.close()

    asyncio.run(exercise())
    assert len(calls) == 1, "repeated requests reuse the last answer"


# --- S7: labels never read another owner's connection --------------------------------


def test_a_label_is_read_as_the_runs_owner(tmp_path):
    from cryptography.fernet import Fernet
    from conclave.integrations.connections import ConnectionStore

    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        host.connections = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key())
        host.connections.create({"id": "forge", "integration": "github", "name": "Bob's GitHub", "applications": ["app"],
                                 "operations": ["create_pull_request"], "credentials": {"token": "bob"}}, "local", "Bob")
        host.connections.pin_run(run_id, "local", "Ana Ruiz")
        uncertain(host.repository, run_id, "x1", tool="connection.forge.create_pull_request")
        reads = []
        original = host.connections._read
        host.connections._read = lambda identifier, tenant, owner: reads.append(owner) or original(identifier, tenant, owner)
        listed = (await host.handle("actions", {"run_id": run_id}))[1]["actions"]
        assert next(item for item in listed if item["id"] == "x1")["label"] == "forge · Create pull request"
        assert "Bob" not in reads
        await host.close()

    asyncio.run(exercise())


# --- S8: a pull request number is a number ------------------------------------------


@pytest.mark.parametrize("number", ["--web", "--repo=attacker/repo", "12 13", "", "-1"])
def test_a_pull_request_number_cannot_become_an_option(tmp_path, monkeypatch, number):
    from conclave.actions import ActionContext
    from conclave.tools.changes import Changes, build_change_tools

    ran = []
    monkeypatch.setattr(processes, "run_command", lambda command, **kwargs: ran.append(command) or subprocess.CompletedProcess(command, 0, "{}", ""))
    with pytest.raises(ValueError):
        asyncio.run(Changes(tmp_path).merge_pull_request(number))
    tools = {tool.name: tool for tool in build_change_tools(Changes(tmp_path))}
    context = ActionContext("a1", "2026-11-16T10:40:00+00:00", {"number": number})
    assert asyncio.run(tools["merge_pull_request"].check.run({"number": number}, context)).result == "inconclusive"
    for field in ("branch", "base"):
        arguments = {"title": "t", "branch": "fix/x", "base": "main", field: "-x"}
        assert asyncio.run(tools["open_pull_request"].check.run(arguments, context)).result == "inconclusive"
    assert not [command for command in ran if command[0] == "gh"]


# --- S9: details are dropped once nothing can resend them ----------------------------


@pytest.mark.parametrize("state, kept", [("completed", False), ("failed", False), ("uncertain", True)])
def test_a_finished_write_forgets_its_arguments(tmp_path, state, kept):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    repository.journal.begin(run_id, start(run_id), budget={})
    repository.journal.finish("a1", state, frames=[{"agent": "writer"}], budget={})
    with closing(sqlite3.connect(repository.path)) as connection:
        call = connection.execute("SELECT call FROM studio_run_actions").fetchone()[0]
    assert (call is not None) is kept
