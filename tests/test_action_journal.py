"""T3 — the action journal: what was being sent, written down before it is sent.

The journal lives in the run database. A write is recorded as `sending` together
with the agent's progress in one transaction before the tool runs, and its
outcome replaces both atomically. Only the transitions in the design are
possible; the arguments and a person's note are sealed and purged when a run
can no longer continue.
"""

import json
import os
import sqlite3
import stat
from contextlib import closing

import pytest

from actions_support import write_request
from conclave import RunBudget
from conclave.application.runs import RunRepository

FRAMES = [{"version": 1, "agent": "writer", "messages": [{"role": "user", "content": "frame-marker-7f3"}],
           "reply": "{}", "action": {"type": "tool", "name": "send_note", "arguments": {"text": "hello"}},
           "action_id": "a1", "observation": None, "notes": [], "checked": [], "step": 1, "reviewed": False,
           "nested": [], "delegation": None}]
BUDGET = {"used": 1, "per_agent": {"writer": 1}, "spent": {"tool_calls": 1.0}}


def start(action_id="a1", *, scope="announce", checkable=True, text="hello"):
    from conclave.actions import ActionStart

    return ActionStart(action_id=action_id, scope=scope, agent="writer", tool="send_note",
                       arguments={"text": text}, approval="approved", frames=FRAMES, checkable=checkable)


@pytest.fixture
def repository(tmp_path):
    return RunRepository(tmp_path / "runs.sqlite")


@pytest.fixture
def run_id(repository):
    return repository.create(write_request())


def raw(path, query, *parameters):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(query, parameters).fetchall()


def test_a_write_is_on_disk_with_its_progress_before_it_is_sent(repository, run_id):
    repository.journal.begin(run_id, start(), budget=BUDGET)
    rows = raw(repository.path, "SELECT id, run_id, stage, agent, tool, state, attempts, approval FROM studio_run_actions")
    assert rows == [("a1", run_id, "announce", "writer", "send_note", "sending", 1, "approved")]
    progress = RunRepository(repository.path).journal.progress(run_id)
    assert progress["announce"]["frames"] == FRAMES and progress["announce"]["budget"] == BUDGET
    assert progress["announce"]["output"] is None


def test_an_outcome_and_its_progress_change_together(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start(), budget=BUDGET)
    with pytest.raises(TypeError):
        journal.finish("a1", "completed", frames=[{"unserialisable": object()}], budget=BUDGET)
    assert journal.get("a1")["state"] == "sending", "a failed write of the outcome changes nothing"
    assert journal.progress(run_id)["announce"]["frames"] == FRAMES
    after = [{**FRAMES[0], "observation": "sent"}]
    journal.finish("a1", "completed", frames=after, budget={**BUDGET, "spent": {"tool_calls": 2.0}})
    assert journal.get("a1")["state"] == "completed" and journal.get("a1")["finished_at"]
    assert journal.progress(run_id)["announce"]["frames"] == after
    assert journal.progress(run_id)["announce"]["budget"]["spent"] == {"tool_calls": 2.0}


@pytest.mark.parametrize("path", [
    ["completed"], ["failed"], ["uncertain"],
    ["uncertain", ("done", "check")], ["uncertain", ("done", "Ana")], ["uncertain", ("resend", "check")],
    ["uncertain", ("resend", "Ana"), "undo"], ["uncertain", ("done", "Ana"), "undo"],
    ["uncertain", ("resend", "Ana"), "resend-begins", "completed"],
])
def test_allowed_transitions(repository, run_id, path):
    journal = repository.journal
    journal.begin(run_id, start(), budget=BUDGET)
    for step in path:
        if step == "undo":
            journal.undo("a1", by="Ana")
            assert journal.get("a1")["state"] == "uncertain"
        elif step == "resend-begins":
            assert journal.begin_resend("a1") == 2
            assert journal.get("a1")["state"] == "sending"
        elif isinstance(step, tuple):
            journal.settle("a1", step[0], by=step[1], note="seen" if step[0] == "done" and step[1] != "check" else None,
                           kind="check" if step[1] == "check" else "person")
            assert journal.get("a1")["state"] == step[0] and journal.get("a1")["settled_by"] == step[1]
        else:
            journal.finish("a1", step, frames=FRAMES, budget=BUDGET)
            assert journal.get("a1")["state"] == step


@pytest.mark.parametrize("path, refused", [
    (["completed"], ("settle", "done")),
    (["failed"], ("settle", "resend")),
    (["completed"], ("finish", "uncertain")),
    ([], ("settle", "done")),
    (["uncertain", ("done", "check")], ("undo", None)),
    (["uncertain"], ("undo", None)),
    (["uncertain", ("done", "Ana")], ("settle", "resend")),
    (["uncertain"], ("resend-begins", None)),
])
def test_forbidden_transitions(repository, run_id, path, refused):
    journal = repository.journal
    journal.begin(run_id, start(), budget=BUDGET)
    for step in path:
        if isinstance(step, tuple):
            journal.settle("a1", step[0], by=step[1], kind="check" if step[1] == "check" else "person")
        else:
            journal.finish("a1", step, frames=FRAMES, budget=BUDGET)
    before = journal.get("a1")["state"]
    kind, value = refused
    with pytest.raises(ValueError, match="This action cannot change from"):
        if kind == "settle":
            journal.settle("a1", value, by="Ana")
        elif kind == "finish":
            journal.finish("a1", value, frames=FRAMES, budget=BUDGET)
        elif kind == "undo":
            journal.undo("a1", by="Ana")
        else:
            journal.begin_resend("a1")
    assert journal.get("a1")["state"] == before


def test_a_decision_is_provisional_until_the_run_continues(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start(), budget=BUDGET)
    journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET, reason="The host stopped")
    journal.settle("a1", "done", by="Ana", note="Posted at 10:41")
    assert journal.get("a1")["final"] is False
    journal.undo("a1", by="Ana")
    assert journal.note("a1") is None, "an undone note is forgotten"
    journal.settle("a1", "resend", by="Ana")
    journal.finalize(run_id)
    assert journal.get("a1")["final"] is True
    with pytest.raises(ValueError, match="already used when the run continued"):
        journal.undo("a1", by="Ana")


def test_only_in_flight_writes_become_uncertain_when_a_run_ends(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start("a1"), budget=BUDGET)
    journal.begin(run_id, start("a2"), budget=BUDGET)
    journal.finish("a2", "completed", frames=FRAMES, budget=BUDGET)
    marked = journal.mark_in_flight_uncertain(run_id, "The host stopped while this was being sent")
    assert [row["id"] for row in marked] == ["a1"]
    assert journal.get("a1")["state"] == "uncertain"
    assert journal.get("a1")["reason"] == "The host stopped while this was being sent"
    assert journal.get("a2")["state"] == "completed"
    assert journal.mark_in_flight_uncertain(run_id, "again") == []


def test_checks_are_recorded_and_counted(repository, run_id):
    from conclave.actions import CheckResult

    journal = repository.journal
    for action_id, checkable in (("a1", True), ("a2", False), ("a3", True)):
        journal.begin(run_id, start(action_id, checkable=checkable), budget=BUDGET)
        journal.finish(action_id, "uncertain", frames=FRAMES, budget=BUDGET)
    assert journal.counts(run_id) == {"to_check": 2, "needs_decision": 1}
    journal.record_check("a3", CheckResult("inconclusive", "GitHub did not respond"))
    row = journal.get("a3")
    assert (row["check_result"], row["check_summary"]) == ("inconclusive", "GitHub did not respond") and row["checked_at"]
    assert journal.counts(run_id) == {"to_check": 1, "needs_decision": 2}
    journal.record_check("a1", CheckResult("found", "Found pull request #866", detail="#866 open"))
    journal.settle("a1", "done", by="check", kind="check")
    assert journal.counts(run_id) == {"to_check": 0, "needs_decision": 2}
    assert journal.resolution("a1") == {"state": "done", "settled_by": "check", "settled_kind": "check", "note": None,
                                        "check_summary": "Found pull request #866", "check_detail": "#866 open",
                                        "tool": "send_note"}


def test_the_public_list_carries_no_arguments_or_notes(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start(text="private words"), budget=BUDGET)
    journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET)
    journal.settle("a1", "done", by="Ana", note="private note")
    listed = journal.list(run_id)
    assert len(listed) == 1 and listed[0]["id"] == "a1"
    assert "private" not in json.dumps(listed)
    assert {"call", "arguments", "note", "settled_note", "frames"}.isdisjoint(listed[0])


def test_arguments_are_offered_only_while_they_could_be_sent_again(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start(text="resend me"), budget=BUDGET)
    assert journal.arguments("a1") is None, "nothing to decide while it is still being sent"
    journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET)
    assert journal.arguments("a1") == {"text": "resend me"}
    journal.settle("a1", "resend", by="Ana")
    assert journal.arguments("a1") == {"text": "resend me"}, "a provisional resend shows what will be sent"
    journal.undo("a1", by="Ana")
    journal.settle("a1", "done", by="Ana")
    assert journal.arguments("a1") is None
    journal.begin(run_id, start("a2"), budget=BUDGET)
    journal.finish("a2", "completed", frames=FRAMES, budget=BUDGET)
    assert journal.arguments("a2") is None


def test_with_a_key_nothing_private_is_readable_at_rest(tmp_path):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    repository = RunRepository(tmp_path / "runs.sqlite", key=key)
    run_id = repository.create(write_request())
    repository.journal.begin(run_id, start(text="sealed words"), budget=BUDGET)
    repository.journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET)
    repository.journal.settle("a1", "done", by="Ana", note="sealed note")
    with closing(sqlite3.connect(tmp_path / "runs.sqlite")) as connection:
        dump = "\n".join(connection.iterdump())
    assert "sealed words" not in dump and "sealed note" not in dump and "frame-marker-7f3" not in dump
    assert RunRepository(tmp_path / "runs.sqlite", key=key).journal.note("a1") == "sealed note"


def test_without_a_key_the_database_is_private_to_this_user(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(write_request())
    repository.journal.begin(run_id, start(), budget=BUDGET)
    assert stat.S_IMODE(os.stat(tmp_path / "runs.sqlite").st_mode) == 0o600


def test_details_sealed_with_another_key_are_unreadable_not_empty(tmp_path):
    from cryptography.fernet import Fernet

    repository = RunRepository(tmp_path / "runs.sqlite", key=Fernet.generate_key())
    run_id = repository.create(write_request())
    repository.journal.begin(run_id, start(), budget=BUDGET)
    repository.journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET)
    other = RunRepository(tmp_path / "runs.sqlite", key=Fernet.generate_key()).journal
    assert other.arguments("a1") is None
    assert other.get("a1")["unreadable"] is True
    assert repository.journal.get("a1")["unreadable"] is False


def test_a_finished_run_keeps_its_history_but_not_its_details(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start(), budget=BUDGET)
    journal.finish("a1", "uncertain", frames=FRAMES, budget=BUDGET)
    journal.settle("a1", "done", by="Ana", note="a note")
    journal.save_output(run_id, "announce", "Announced")
    journal.purge(run_id)
    assert journal.get("a1")["state"] == "done" and journal.get("a1")["settled_by"] == "Ana"
    assert raw(repository.path, "SELECT call, settled_note FROM studio_run_actions") == [(None, None)]
    assert journal.progress(run_id) == {}


def test_progress_is_kept_per_step_and_forgotten_once_a_checkpoint_covers_it(repository, run_id):
    journal = repository.journal
    journal.begin(run_id, start("a1", scope="announce"), budget=BUDGET)
    journal.begin(run_id, start("a2", scope="build"), budget=BUDGET)
    journal.save_output(run_id, "build", {"text": "Built"})
    assert set(journal.progress(run_id)) == {"announce", "build"}
    assert journal.progress(run_id)["build"]["output"] == {"text": "Built"}
    journal.forget_progress(run_id, ["announce"])
    assert set(journal.progress(run_id)) == {"build"}


def test_a_run_scoped_log_snapshots_the_budget_it_is_bound_to(repository, run_id):
    budget = RunBudget(max_delegations=None, max_per_agent=None, max_depth=None)
    budget.reserve("writer", 1, ())
    budget.reserve_dimension("tool_calls", 1)
    log = repository.journal.for_run(run_id, budget=budget)
    log.begin(start())
    assert repository.journal.progress(run_id)["announce"]["budget"] == {
        "used": 1, "per_agent": {"writer": 1}, "spent": {"tool_calls": 1}}
    log.finish("a1", "uncertain", frames=FRAMES, reason="No answer")
    log.settle("a1", "resend", by="check")
    assert log.resend("a1") == 2
    log.finish("a1", "completed", frames=FRAMES)
    assert log.resolution("a1")["state"] == "completed"
    log.save_output("announce", "Announced")
    assert repository.journal.progress(run_id)["announce"]["output"] == "Announced"
    log.forget_progress(["announce"])
    assert repository.journal.progress(run_id) == {}


def test_the_migration_adds_the_journal_to_an_existing_database(tmp_path):
    path = tmp_path / "runs.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE studio_runs (id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE studio_run_events (run_id TEXT NOT NULL REFERENCES studio_runs(id), sequence INTEGER NOT NULL,
                                            payload TEXT NOT NULL, PRIMARY KEY (run_id, sequence));
            INSERT INTO studio_runs VALUES ('old', '{}', 'completed', '2026-01-01T00:00:00+00:00');
        """)
    RunRepository(path)
    repository = RunRepository(path)
    run_id = repository.create(write_request())
    repository.journal.begin(run_id, start(), budget=BUDGET)
    assert raw(path, "SELECT status FROM studio_runs WHERE id = 'old'") == [("completed",)]
    assert repository.journal.list("old") == []
