"""Local run records for a trusted Python host, separate from the Site database.

This is local persistence and review resume, not a production server.
Providers, tools, credentials and approval callbacks remain host-owned.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .definition import (LIMIT_NAMES, MAX_PARALLEL_STEPS, run_application, validate_definition, validate_request_inputs, validate_human_output,
                         action_label, _request_fingerprint)
from .journal import ActionJournal
from . import output_files
from .errors import Conflict

LOG = logging.getLogger(__name__)


# Statuses a run cannot come back from: what was uncertain in them stays on
# the record as unknown, and the details behind it are purged.
TERMINAL = {"completed", "cancelled", "failed"}

# How soon the same action may be checked with its service again.
RECHECK_SECONDS = 5

# Why an action left in flight is uncertain, by how its run ended.
IN_FLIGHT_REASONS = {
    "interrupted": "The host stopped while this was being sent",
    "cancelled": "The run was stopped while this was being sent",
}


def _who(entries: list[str]) -> str:
    """"ana@acme.com or anyone at @ops.acme.com"."""
    words = [f"anyone at {entry}" if entry.startswith("@") else entry for entry in entries]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


def answers_required(definition: dict, stage_id: str) -> int:
    step = next((item for item in definition["workflow"] if item["id"] == stage_id), {})
    return step.get("answers_required", 1)


def review_refusal(definition: dict, stage_id: str, *, starter: str | None, answered: list[str], reviewer: str) -> str | None:
    """Why `reviewer` may not answer this review, or None when they may.

    The application's `review_policy` names who may answer (people, or whole
    `@domain`s) and whether the person who started the run may; nobody answers
    one review twice. Names compare without case.
    """
    policy = definition.get("review_policy")
    key = reviewer.strip().casefold()
    if key in {item.casefold() for item in answered}:
        return "You already answered this review; it needs a different person"
    if policy is None:
        return None
    if not policy.get("allow_starter", False) and starter is not None and key == starter.strip().casefold():
        return "You started this run, so someone else must answer its review"
    reviewers = policy.get("reviewers")
    if reviewers is not None and not any(key == entry.casefold() if not entry.startswith("@") else key.endswith(entry.casefold())
                                         for entry in reviewers):
        return f"Only {_who(reviewers)} may answer this review"
    return None


def _combined(outputs: list[dict]) -> dict:
    """Every answer's records together, each keeping an id of its own."""
    if len(outputs) == 1:
        return outputs[0]
    records, seen = [], set()
    for output in outputs:
        for record in output["records"]:
            record, identifier, suffix = dict(record), record["id"], 2
            while record["id"] in seen:
                record["id"], suffix = f"{identifier}-{suffix}", suffix + 1
            seen.add(record["id"])
            records.append(record)
    return {"records": records}


class ActionsUnsettled(Conflict):
    """A run cannot continue while an action's outcome is still uncertain."""

    def __init__(self, actions: list[str]):
        count = len(actions)
        super().__init__(f"Settle {count} action{'s' if count != 1 else ''} before continuing")
        self.actions = list(actions)
        # How a surface names them, when it has looked them up.
        self.details: list[dict] = []


def validate_run_request(request: dict) -> dict:
    """Version 1 pins a saved application revision and its complete definition."""
    required = {"version", "application_id", "revision", "definition", "task", "inputs"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"resources", "trigger"}:
        raise ValueError("Use the version 1 run request fields")
    if "trigger" in request:
        # A schedule started this run, for this time, or an event did, when it
        # was received. Manual runs carry none.
        trigger = request["trigger"]
        kind = trigger.get("kind") if isinstance(trigger, dict) else None
        when_key, fields = {"schedule": ("scheduled_for", {"kind", "scheduled_for"}),
                            "event": ("received_at", {"kind", "event_id", "received_at"})}.get(kind, (None, None))
        if fields is None or set(trigger) != fields:
            raise ValueError("A run trigger names the schedule and the time it was for, or the event and when it was received")
        if kind == "event" and (not isinstance(trigger["event_id"], str) or not 1 <= len(trigger["event_id"]) <= 200):
            raise ValueError("An event trigger names its event in at most 200 characters")
        try:
            moment = datetime.fromisoformat(trigger[when_key]) if isinstance(trigger[when_key], str) else None
        except ValueError:
            moment = None
        if moment is None or moment.tzinfo is None:
            raise ValueError("A run trigger names the schedule and the time it was for, or the event and when it was received")
    resources = request.get("resources", {})
    if not isinstance(resources, dict) or set(resources) - {"source", "source_revision"} or "source_revision" in resources and "source" not in resources or any(not isinstance(value, str) or not value or len(value) > 64 for value in resources.values()):
        raise ValueError("Run resources must contain a named source resource, not a path")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ValueError("Unsupported run request version")
    if not isinstance(request["application_id"], str) or not request["application_id"].strip() or len(request["application_id"]) > 100:
        raise ValueError("A saved application ID is required")
    if type(request["revision"]) is not int or request["revision"] < 1:
        raise ValueError("A positive application revision is required")
    if not isinstance(request["task"], str) or not request["task"].strip() or len(request["task"]) > 20000:
        raise ValueError("A bounded task is required")
    if not isinstance(request["definition"], dict):
        raise ValueError("An application definition is required")
    validate_definition(request["definition"])
    normalized = copy.deepcopy(request)
    normalized["inputs"] = validate_request_inputs(request["definition"], request["inputs"])
    # Reject non-JSON data and nonfinite numbers before opening a run record.
    json.dumps(normalized, allow_nan=False)
    return normalized


class RunRepository:
    """One SQLite connection per operation; events commit before they are yielded."""

    # How many steps of one run may work at once here; the host sets it from
    # CONCLAVE_MAX_PARALLEL_STEPS.
    max_parallel_steps = MAX_PARALLEL_STEPS

    def __init__(self, path: str | Path, *, history_limit: int = 30, key: bytes | str | None = None):
        if type(history_limit) is not int or history_limit < 1:
            raise ValueError("history_limit must be a positive integer")
        self.history_limit = history_limit
        # The sequence of each run's newest checkpointed event that this process
        # wrote, so the one it replaces is found without a search.
        self._checkpoints: dict[str, int] = {}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Runs this process is watching, for a caller that wants to be told
        # rather than ask. In-process only: replicas would need a shared signal,
        # which is the same work as moving the run claim into the database.
        self.watchers: dict[str, set[asyncio.Queue]] = {}
        # Runs a person is stopping right now: closing one records that it was
        # cancelled, not that the host lost it.
        self.stopping: set[str] = set()
        # Whom each run acts as, once read (see `actor`).
        self._actors: dict[str, str | None] = {}
        with self._connect() as connection:
            # One transaction for the whole schema: each statement on its own
            # would commit (and sync to disk) separately, which made opening a
            # new store several times slower.
            migrations = Path(__file__).parent / "migrations"
            connection.executescript("BEGIN;\n" + """
                CREATE TABLE IF NOT EXISTS studio_runs (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_run_events (
                    run_id TEXT NOT NULL REFERENCES studio_runs(id),
                    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
            """ + "".join((migrations / name).read_text() + "\n" for name in
                          ("0001_reviews.sql", "0002_actions.sql", "0003_transcript.sql", "0004_schedules.sql",
                           "0005_run_actors.sql", "0006_notices.sql", "0008_review_answers.sql", "0009_events.sql", "0010_run_status.sql", "0011_run_files.sql")) + "COMMIT;")
            # Added after the journal first shipped: who settled an action, as a
            # kind rather than a name (a person named "check" is a person).
            if "settled_kind" not in {row[1] for row in connection.execute("PRAGMA table_info(studio_run_actions)")}:
                connection.execute("ALTER TABLE studio_run_actions ADD COLUMN settled_kind TEXT")
                connection.execute("UPDATE studio_run_actions SET settled_kind = CASE settled_by WHEN 'check' THEN 'check' "
                                   "ELSE 'person' END WHERE settled_by IS NOT NULL")
            # Added while schedules were reviewed: when a schedule last changed
            # hands. A store made before that has the table without it.
            if "owner_since" not in {row[1] for row in connection.execute("PRAGMA table_info(studio_schedules)")}:
                connection.execute("ALTER TABLE studio_schedules ADD COLUMN owner_since TEXT")
            # Added with revision rounds: a review asked again is a new row for
            # the same step. A store that still allows one review per step is
            # rebuilt once, keeping its reviews.
            if "round" not in {row[1] for row in connection.execute("PRAGMA table_info(studio_run_reviews)")}:
                connection.executescript("BEGIN;\n" + (migrations / "0007_review_rounds.sql").read_text() + "\nCOMMIT;")
        # What a run was sending, and where it was, can name people and hold
        # message text: the database is this user's alone, and the details in
        # it are sealed with the host key when there is one.
        self.path.chmod(0o600)
        self.journal = ActionJournal(self._connect, key=key)
        from .event_store import EventStore
        self.events = EventStore(self, key=key)
        from .schedule_store import ScheduleStore
        self.schedules = ScheduleStore(self)
        from .notices import NoticeStore, NotificationOwners
        self.notices = NoticeStore(self)
        self.notification_owners = NotificationOwners(self)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, request: dict, *, actor: str | None = None) -> str:
        """A pending run; `actor` is whom it acts as, recorded with it."""
        request = validate_run_request(request)
        with self._connect() as connection:
            return self._insert_run(connection, request, actor=actor)

    def _insert_run(self, connection, request: dict, *, actor: str | None = None) -> str:
        """Insert a validated request as a pending run, inside the caller's transaction."""
        run_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO studio_runs VALUES (?, ?, ?, ?)",
            (run_id, json.dumps(request, allow_nan=False), "pending", datetime.now(timezone.utc).isoformat()),
        )
        if actor is not None:
            connection.execute("INSERT INTO studio_run_actors (run_id, actor) VALUES (?, ?)", (run_id, actor))
        self._remember_actor(run_id, actor)
        if (request["definition"].get("notifications") or {}).get("waiting"):
            # Its "waiting for you" messages are sent as whoever owns the
            # notifications now — who last changed the words this run will
            # quote — and by nobody else later.
            owner = connection.execute("SELECT owner FROM studio_notification_owners WHERE application_id = ?",
                                       (request["application_id"],)).fetchone()
            sender = owner[0] if owner else actor
            if sender is not None:
                connection.execute("INSERT INTO studio_run_senders (run_id, sender) VALUES (?, ?)", (run_id, sender))
        return run_id

    def actor(self, run_id: str) -> str | None:
        """Whom a run acts as, when that was recorded (runs from before it was are not).

        It never changes once the run exists, so it is read once per process:
        every approval and decision asks, and none should cost a query.
        """
        if run_id in self._actors:
            return self._actors[run_id]
        with self._connect() as connection:
            row = connection.execute("SELECT actor FROM studio_run_actors WHERE run_id = ?", (run_id,)).fetchone()
            exists = row is not None or connection.execute("SELECT 1 FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
        if exists:
            self._remember_actor(run_id, row[0] if row else None)
        return row[0] if row else None

    def _remember_actor(self, run_id: str, actor: str | None) -> None:
        if len(self._actors) >= 10000:
            self._actors.clear()
        self._actors[run_id] = actor

    def get(self, run_id: str) -> dict:
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT request, status, created_at FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            events = connection.execute("SELECT sequence, payload FROM studio_run_events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
            reviews = connection.execute("SELECT id, stage, request, status, reviewer, output, submitted_at, round FROM studio_run_reviews WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
            answers: dict[str, list] = {}
            for review_id, reviewer, output, submitted in connection.execute(
                    "SELECT review_id, reviewer, output, submitted_at FROM studio_run_review_answers WHERE run_id = ? ORDER BY rowid", (run_id,)):
                answers.setdefault(review_id, []).append({"reviewer": reviewer, "output": json.loads(output), "submitted_at": submitted})
            starter = connection.execute("SELECT actor FROM studio_run_actors WHERE run_id = ?", (run_id,)).fetchone()
            counts = self.journal.counts_for([run_id], connection)[run_id]
            files = self._files(connection, run_id)
        parsed = [{"sequence": sequence, "event": json.loads(payload)} for sequence, payload in events]
        request = json.loads(row[0])
        return {"id": run_id, "request": request, "status": row[1], "created_at": row[2], "started_by": starter[0] if starter else None,
                "journal": self._journaled(parsed), "actions": self._action_counts(row[1], counts),
                "events": parsed, "files": files,
                "reviews": [{"id": review[0], "stage": review[1], "request": json.loads(review[2]), "status": review[3],
                             "reviewer": review[4], "output": json.loads(review[5]) if review[5] else None,
                             "submitted_at": review[6], "round": review[7], "answers": answers.get(review[0], []),
                             "required": answers_required(request["definition"], review[1])} for review in reviews]}

    # Runs an event started that are still going, for one application: through
    # the status index, so only runs still going are read.
    ACTIVE_EVENT_RUNS = ("SELECT COUNT(*) FROM studio_runs WHERE status IN ('pending', 'running', 'paused', 'waiting_for_human') "
                         "AND json_extract(request, '$.application_id') = ? AND json_extract(request, '$.trigger.kind') = 'event'")

    def active_event_runs(self, connection, application_id: str) -> int:
        return connection.execute(self.ACTIVE_EVENT_RUNS, (application_id,)).fetchone()[0]

    def application_of(self, run_id: str) -> str:
        """The application a run belongs to, without reading its events."""
        with self._connect() as connection:
            row = connection.execute("SELECT json_extract(request, '$.application_id') FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0]

    def review_access(self, run_id: str) -> dict:
        """What deciding who a waiting review is for needs, without the run's events.

        Status, definition, who started it, and the review it waits for (its
        step, how many answers it needs, and who has answered).
        """
        with self._connect() as connection:
            row = connection.execute("SELECT status, request FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            starter = connection.execute("SELECT actor FROM studio_run_actors WHERE run_id = ?", (run_id,)).fetchone()
            review = connection.execute("SELECT id, stage FROM studio_run_reviews WHERE run_id = ? AND status = 'pending' "
                                        "ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
            answered = [item[0] for item in connection.execute(
                "SELECT reviewer FROM studio_run_review_answers WHERE review_id = ? ORDER BY rowid", (review[0],))] if review else []
        request = json.loads(row[1])
        return {"status": row[0], "request": request, "started_by": starter[0] if starter else None,
                "review": {"id": review[0], "stage": review[1], "answered": answered,
                           "required": answers_required(request["definition"], review[1])} if review else None}

    def run_state(self, run_id: str) -> dict | None:
        """A run's status and its action counts, without reading its events."""
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            counts = self.journal.counts_for([run_id], connection)[run_id]
        return {"status": row[0], "actions": self._action_counts(row[0], counts)}

    def list(self, application_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, request, status, created_at FROM studio_runs ORDER BY created_at DESC, id DESC").fetchall()
            # ponytail: scan local run records; index application_id if history grows.
            runs = [{"id": row[0], "revision": request["revision"], "status": row[2], "created_at": row[3],
                     "trigger": request.get("trigger", {"kind": "manual"})}
                    for row in rows if (request := json.loads(row[1]))["application_id"] == application_id][:self.history_limit]
            # One query for every listed run's actions, not one per run.
            counts = self.journal.counts_for([run["id"] for run in runs], connection)
        for run in runs:
            run["actions"] = self._action_counts(run["status"], counts[run["id"]])
        return runs

    @staticmethod
    def _journaled(events) -> bool:
        """Whether this run recorded its actions, so an interruption can be settled and continued."""

        return any(item["event"].get("type") in {"ApplicationStarted", "ApplicationResumed"} and item["event"].get("journal") == 1
                   for item in events)

    @staticmethod
    def _action_counts(status: str, counts: dict) -> dict:
        if status in TERMINAL:
            # Nothing can be decided on a run that cannot continue: what was
            # uncertain is recorded as an unknown outcome.
            return {"to_check": 0, "needs_decision": 0, "unknown": counts["to_check"] + counts["needs_decision"]}
        return {**counts, "unknown": 0}

    # -- Actions ---------------------------------------------------------------

    def actions(self, run_id: str, *, record: dict | None = None) -> list[dict]:
        if record is None:
            self.get(run_id)
        return self.journal.list(run_id)

    def _decidable(self, run_id: str, action_id: str) -> dict:
        record = self.get(run_id)
        action = self.journal.get(action_id)
        if action["run_id"] != run_id:
            raise KeyError(action_id)
        if action["final"]:
            raise ValueError("This choice was already used when the run continued")
        if record["status"] in TERMINAL or record["status"] in {"running", "pending"}:
            raise ValueError("This run is not waiting for a decision about its actions")
        return action

    def settle(self, run_id: str, action_id: str, decision: str, *, by: str, note: str | None = None) -> None:
        """Record a person's decision about an uncertain action, or undo it.

        `done`: it happened, and is not sent again; `resend`: send it once more
        when the run continues. Both can be undone until the run continues.
        """

        if decision not in {"done", "resend", "undo"}:
            raise ValueError("Choose done, resend or undo")
        if note is not None and decision != "done":
            raise ValueError("Only a decision that the action happened takes a note")
        action = self._decidable(run_id, action_id)
        if decision == "undo":
            self.journal.undo(action_id, by=by)
            self._append(run_id, {"type": "ActionDecisionUndone", "action_id": action_id, "stage": action["stage"],
                                  "tool": action["tool"], "undone_by": by})
            return
        self.journal.settle(action_id, decision, by=by, note=note)
        self._append(run_id, {"type": "ActionSettled", "action_id": action_id, "stage": action["stage"], "tool": action["tool"],
                              "decision": decision, "settled_by": by, "settled_kind": "person"})

    async def check_actions(self, run_id: str, tools, *, only: str | None = None) -> list[dict]:
        """Ask each uncertain action's service whether it happened, and settle what a check can.

        Found: done. Absent, by a read that can be trusted, of an approved
        call: sent once more when the run continues. Anything else waits for a
        person. A check only reads, through what the run may already read.
        """

        from conclave.authority import decide, run_check
        from conclave.actions import Resolution

        candidates = [only] if only is not None else self.journal.uncertain(run_id)
        for action_id in candidates:
            action = self._decidable(run_id, action_id)
            if action["state"] != "uncertain":
                raise ValueError("Only an uncertain action is checked")
            if action["checked_at"] and action["check_result"] and (
                    datetime.now(timezone.utc) - datetime.fromisoformat(action["checked_at"])).total_seconds() < RECHECK_SECONDS:
                # Asked again at once: the last answer stands, rather than
                # sending the service another request with the person's token.
                continue
            try:
                arguments = self.journal.call(action_id) or {}
                result = await run_check(tools.tool(action["tool"]), action_id, arguments, action["started_at"])
            except ValueError:
                from conclave.actions import CheckResult
                result = CheckResult("unavailable", "Saved details cannot be read with this host's key")
            self.journal.record_check(action_id, result)
            self._append(run_id, {"type": "ActionChecked", "action_id": action_id, "stage": action["stage"],
                                  "tool": action["tool"], "result": result.result, "summary": result.summary})
            settled = decide(self.journal.for_run(run_id), action_id, result, action["approval"], Resolution("stop"))
            if settled.kind != "stop":
                self._append(run_id, {"type": "ActionSettled", "action_id": action_id, "stage": action["stage"],
                                      "tool": action["tool"], "decision": settled.kind, "settled_by": "check",
                                      "settled_kind": "check"})
        return self.journal.list(run_id)

    def cancel_waiting(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A run that stopped or was interrupted can be abandoned rather
            # than continued; whatever it left uncertain stays on the record.
            changed = connection.execute("UPDATE studio_runs SET status = 'cancelled' WHERE id = ? AND status IN "
                                         "('pending', 'waiting_for_human', 'paused', 'stopped', 'interrupted')", (run_id,)).rowcount
            if changed != 1:
                raise ValueError("This run cannot be cancelled while inactive")
            self._insert_event(connection, run_id, {"type": "ApplicationFinished", "status": "cancelled", "reason": "Stopped by the local operator"})
            self.journal.purge(run_id, connection)
        self._notify(run_id)

    def submit_review(self, run_id: str, review_id: str, *, reviewer: str, output: dict) -> None:
        """Record one explicit contribution. The host authenticates the reviewer.

        Submission does not run agents or authorize external tools. Invalid,
        stale and duplicate contributions leave the waiting review untouched.
        """
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValueError("A bounded reviewer identity is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT r.request FROM studio_run_reviews r JOIN studio_runs a ON a.id = r.run_id "
                "WHERE r.id = ? AND r.run_id = ? AND r.status = 'pending' AND a.status = 'waiting_for_human'",
                (review_id, run_id),
            ).fetchone()
            if row is None:
                raise ValueError("This review is not waiting for input")
            review = json.loads(row[0])
            definition = json.loads(connection.execute("SELECT request FROM studio_runs WHERE id = ?", (run_id,)).fetchone()[0])["definition"]
            starter = connection.execute("SELECT actor FROM studio_run_actors WHERE run_id = ?", (run_id,)).fetchone()
            answered = [item[0] for item in connection.execute(
                "SELECT reviewer FROM studio_run_review_answers WHERE review_id = ? ORDER BY rowid", (review_id,))]
            refusal = review_refusal(definition, review["stage"], starter=starter[0] if starter else None, answered=answered, reviewer=reviewer)
            if refusal:
                raise ValueError(refusal)
            payload = validate_human_output(output, review["output"], {"agent": review["agent"], "stage": review["stage"], "run_id": review_id})
            normalized = {"text" if review["output"]["type"] == "text" else "records": payload}
            timestamp = datetime.now(timezone.utc).isoformat()
            outputs = [json.loads(item[0]) for item in connection.execute(
                "SELECT output FROM studio_run_review_answers WHERE review_id = ? ORDER BY rowid", (review_id,))] + [normalized]
            if sum(len(item.get("records", [])) for item in outputs) > 200:
                raise ValueError("This review's answers cannot hold more than 200 records together")
            connection.execute("INSERT INTO studio_run_review_answers VALUES (?, ?, ?, ?, ?, ?)",
                               (review_id, run_id, reviewer, reviewer.strip().casefold(), json.dumps(normalized, allow_nan=False), timestamp))
            required = answers_required(definition, review["stage"])
            # Complete once it has the answers it needs, or at the first
            # rejection: more answers could not change a rejection.
            rejected = any(record.get("status") == "rejected" for record in normalized.get("records", []))
            complete = len(outputs) >= required or rejected
            if complete:
                connection.execute(
                    "UPDATE studio_run_reviews SET status = 'submitted', reviewer = ?, output = ?, submitted_at = ? WHERE id = ?",
                    (reviewer, json.dumps(_combined(outputs), allow_nan=False), timestamp, review_id),
                )
            self._insert_event(connection, run_id, {"type": "HumanReviewSubmitted", "review_id": review_id,
                                                  "stage": review["stage"], "reviewer": reviewer, "submitted_at": timestamp,
                                                  "answers": len(outputs), "required": required, "complete": complete})
        self._notify(run_id)

    def resume(self, run_id: str, provider, tools, *, approver=None, loop=None, pause_requested=None):
        """Continue a waiting, paused or budget-stopped run from its checkpoint.

        A run that stopped because a budget ran out finished its steps, so it can
        be continued from the last one that completed: the work already done is
        not repeated, and the spend already recorded is not forgotten.
        """
        return self.execute(run_id, provider, tools, approver=approver, resume=True, loop=loop,
                            pause_requested=pause_requested)

    def _claim_resume(self, run_id: str):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT request, status FROM studio_runs WHERE id = ? AND status IN ('waiting_for_human', 'paused', 'stopped', 'interrupted')", (run_id,)).fetchone()
            events = [{"sequence": sequence, "event": json.loads(payload)} for sequence, payload in connection.execute(
                "SELECT sequence, payload FROM studio_run_events WHERE run_id = ? ORDER BY sequence", (run_id,))] if row else []
            if row is None or row[1] == "interrupted" and not self._journaled(events):
                # A run recorded before the journal cannot say what it was sending.
                raise ValueError("Only a waiting, paused or stopped run can resume")
            # Nothing uncertain is sent again, or carried past, without a check
            # or a person: the run waits until every action is settled.
            unsettled = [item[0] for item in connection.execute(
                "SELECT id FROM studio_run_actions WHERE run_id = ? AND state = 'uncertain' ORDER BY started_at, rowid", (run_id,))]
            if unsettled:
                raise ActionsUnsettled(unsettled)
            # The decisions the run continues with are final from here: no
            # undo can land between reading them and acting on them.
            self.journal.finalize(run_id, connection)
            if row[1] == "interrupted":
                request = json.loads(row[0])
                checkpoint = next((item["event"]["checkpoint"] for item in reversed(events) if "checkpoint" in item["event"]), None)
                if checkpoint is None:
                    # Stopped before any step finished: start from the beginning,
                    # with the saved progress of the steps that had begun.
                    checkpoint = {"version": 1, "patches": {},
                                  "request_fingerprint": _request_fingerprint(request["definition"], request["task"], request["inputs"]),
                                  "budget": {"used": 0, "per_agent": {}, "spent": {}}}
                submitted = {stage: json.loads(output) for stage, output in connection.execute(
                    "SELECT stage, output FROM studio_run_reviews WHERE run_id = ? AND status = 'submitted'", (run_id,))}
                connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
                return request, self._with_latest_budget(run_id, checkpoint, events, connection), submitted
            # A pause needs no contribution; a review does. Both resume from the
            # checkpoint their own stop recorded, so completed steps are not redone.
            paused, stopped = row[1] == "paused", row[1] == "stopped"
            # Submission events follow the waiting event; select that checkpoint
            # by type rather than assuming the latest event is ApplicationFinished.
            stop_status = "paused" if paused else "stopped" if stopped else "waiting_for_human"
            waiting = next((event for item in reversed(events) if (event := item["event"]).get("status") == stop_status and event.get("type") == "ApplicationFinished"), None)
            if waiting is None or "checkpoint" not in waiting:
                raise ValueError("This run stopped without a checkpoint to resume from")
            if waiting.get("budget"):
                raise ValueError(f"This run reached its {LIMIT_NAMES.get(waiting['budget'], 'limit')}, which is fixed for this run. "
                                 "Start a new run with a higher limit.")
            if stopped:
                # Nothing to contribute: the stop was a budget, not a review, and
                # the checkpoint holds every stage that finished.
                connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
                return json.loads(row[0]), self._with_latest_budget(run_id, waiting["checkpoint"], events, connection), {}
            if paused:
                connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
                return json.loads(row[0]), waiting["checkpoint"], {}
            review = connection.execute(
                "SELECT stage, output FROM studio_run_reviews WHERE run_id = ? AND stage = ? AND status = 'submitted'",
                (run_id, waiting["stage"]),
            ).fetchone()
            if review is None:
                raise ValueError("Submit the pending review before resuming")
            connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
            return json.loads(row[0]), waiting["checkpoint"], {review[0]: json.loads(review[1])}

    def _with_latest_budget(self, run_id: str, checkpoint: dict, events: list[dict], connection) -> dict:
        """The checkpoint, with the most recent spend the run recorded.

        A step that was part-way through when the run stopped spent budget
        after the last checkpoint; its saved progress recorded that, and a run
        that continues must not forget it.
        """

        taken = next((item["event"].get("timestamp", "") for item in reversed(events) if "checkpoint" in item["event"]), "")
        # Budgets and times only: nothing sealed is opened while the claim holds the lock.
        latest = max(((updated, budget) for updated, budget in self.journal.progress_budgets(run_id, connection)
                      if budget and updated > taken), default=None)
        if latest is None:
            return checkpoint
        return {**checkpoint, "budget": latest[1]}

    def _claim(self, run_id: str) -> dict:
        with self._connect() as connection:
            changed = connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ? AND status = 'pending'", (run_id,)).rowcount
            if changed != 1:
                raise ValueError("Only a pending run can start; interrupted runs require explicit recovery")
        return self.get(run_id)["request"], None, {}

    def retry_facts(self, run_id: str) -> dict:
        """What deciding an automatic retry needs, read from the end of the record.

        The run's status, its last stop, and the retries since a step last
        completed: only those fields, event by event backwards, stopping at
        that step, so a long run costs what its last step recorded.
        """
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            stop, tries = None, 0
            for kind, status, transient, budget in connection.execute(
                    "SELECT json_extract(payload, '$.type'), json_extract(payload, '$.status'), json_extract(payload, '$.transient'), "
                    "json_extract(payload, '$.budget') FROM studio_run_events WHERE run_id = ? ORDER BY sequence DESC", (run_id,)):
                if kind == "StageFinished" and status == "completed":
                    break
                if stop is None and kind == "ApplicationFinished":
                    stop = {"transient": transient, "budget": budget}
                tries += kind == "RetryScheduled"
        return {"status": row[0], "stop": stop or {}, "tries": tries}

    def latest(self, run_id: str) -> dict:
        """A run's status, how it was started, and its last event, without the rest."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT r.status, json_extract(r.request, '$.trigger.kind'), "
                "(SELECT payload FROM studio_run_events WHERE run_id = r.id ORDER BY sequence DESC LIMIT 1) "
                "FROM studio_runs r WHERE r.id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return {"status": row[0], "trigger": row[1], "last": json.loads(row[2]) if row[2] else None}

    def versions_used(self, application_id: str) -> dict[int, dict]:
        """For each saved version of an application its runs used: how many, and
        one run's stored definition (the earliest), to recover it from."""
        used: dict[int, dict] = {}
        with self._connect() as connection:
            for run_id, revision, definition in connection.execute(
                    "SELECT id, json_extract(request, '$.revision'), json_extract(request, '$.definition') FROM studio_runs "
                    "WHERE json_extract(request, '$.application_id') = ? ORDER BY created_at, id", (application_id,)):
                entry = used.setdefault(revision, {"runs": 0, "run_id": run_id, "definition": definition})
                entry["runs"] += 1
        return {revision: {**entry, "definition": json.loads(entry["definition"])} for revision, entry in used.items()}

    def pending_retries(self) -> list[tuple[str, dict]]:
        """Stopped runs whose last event is an automatic retry, with that event."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT r.id, e.payload FROM studio_runs r JOIN studio_run_events e ON e.run_id = r.id "
                "WHERE r.status = 'stopped' AND e.sequence = (SELECT MAX(sequence) FROM studio_run_events WHERE run_id = r.id) "
                "AND json_extract(e.payload, '$.type') = 'RetryScheduled'").fetchall()
        return [(run_id, json.loads(payload)) for run_id, payload in rows]

    def sweep_abandoned(self) -> list[str]:
        """Mark runs nothing is executing as interrupted, and say so.

        Called by a host as it takes over its store. One process owns a run store
        (the tenant paths say so), and a process that has just started is not
        running anything: a run left 'running' by a process that is gone would
        otherwise hide its work behind a status that can never change.

        The stop is recorded without a checkpoint on purpose. A run that was cut
        off mid-flight may have had an external action in it, and this records
        that it stopped rather than offering to replay it.
        """

        with self._connect() as connection:
            abandoned = [row[0] for row in connection.execute("SELECT id FROM studio_runs WHERE status = 'running'")]

        for run_id in abandoned:
            self._append(run_id, {"type": "ApplicationFinished", "status": "interrupted",
                                  "reason": "The host stopped while this run was working"})
        # A message some process was sending when it stopped may or may not
        # have gone out: said so once, and never sent again.
        self.notices.recover()
        return abandoned

    @staticmethod
    def _connection_fields(tool: str) -> dict:
        parts = tool.split(".")
        return {"connection_id": parts[1], "operation": parts[2]} if len(parts) == 3 and parts[0] == "connection" else {}

    def _insert_event(self, connection, run_id, event):
        sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM studio_run_events WHERE run_id = ?", (run_id,)).fetchone()[0]
        if "checkpoint" in event:
            self._supersede_checkpoint(connection, run_id)
        # When this happened, on the host's clock. A step that is working is
        # reported by how long it has been working, which only the record can say:
        # a page that measured it would restart the clock on every reload, and
        # would be describing itself rather than the run.
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        connection.execute("INSERT INTO studio_run_events VALUES (?, ?, ?)", (run_id, sequence, json.dumps(event, allow_nan=False)))
        if "checkpoint" in event:
            self._checkpoints[run_id] = sequence
        return sequence

    def _supersede_checkpoint(self, connection, run_id):
        """Take the checkpoint off the run's previous checkpointed event.

        A run saves a checkpoint after every step it finishes, and each holds
        every output so far; only the newest is ever read (to resume, and for
        the run's spend). Keeping one makes the record grow with the steps
        rather than their square. The event itself stays, with its own output.
        """
        previous = self._checkpoints.get(run_id)
        if previous is None:
            # Not written by this process: found once, by looking.
            found = connection.execute("SELECT sequence FROM studio_run_events WHERE run_id = ? AND json_type(payload, '$.checkpoint') IS NOT NULL "
                                       "ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
            previous = found[0] if found else None
        if previous is not None:
            # In SQLite itself, so the previous event is not read back into Python.
            connection.execute("UPDATE studio_run_events SET payload = json_remove(payload, '$.checkpoint') "
                               "WHERE run_id = ? AND sequence = ?", (run_id, previous))

    def _notify(self, run_id: str) -> None:
        """Tell anything watching this run to read it again.

        Each watcher holds one slot, so a slow reader coalesces rather than
        queueing up: every notification means "the record changed", and the
        latest record is the one that matters.
        """

        for queue in list(self.watchers.get(run_id, ())):
            try:
                queue.put_nowait(run_id)
            except asyncio.QueueFull:
                pass

    async def watch(self, run_id: str):
        """The run's record now, and again whenever it changes.

        A caller that wants to be told uses this instead of asking on a timer.
        The generator ends when it is closed, which is what stops the watching.
        """

        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.watchers.setdefault(run_id, set()).add(queue)
        try:
            yield self.get(run_id)
            while True:
                await queue.get()
                yield self.get(run_id)
        finally:
            watching = self.watchers.get(run_id)
            if watching is not None:
                watching.discard(queue)
                if not watching:
                    self.watchers.pop(run_id, None)

    def _append(self, run_id: str, event: dict, *, prepared=None) -> None:
        # A step's output that is stored as a file is written first, outside
        # the transaction, and recorded with the event in it; if that fails,
        # the file is removed, so neither exists without the other. A run
        # writes it beforehand, beside its loop (`prepared`).
        if prepared is not None:
            stored = prepared["stored"]
        else:
            stored = self._write_output_file(run_id, event) if event["type"] == "ApplicationStateChanged" else None
        try:
            self._append_recorded(run_id, event, stored)
        except BaseException:
            if stored is not None and stored.get("path") is not None:
                stored["path"].unlink(missing_ok=True)
            raise

    def _append_recorded(self, run_id: str, event: dict, stored: dict | None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if event["type"] == "ApplicationFinished":
                # Whatever the run was still sending when it ended may or may
                # not have happened: it is uncertain, and said so on the record
                # before the end itself.
                reason = IN_FLIGHT_REASONS.get(event.get("status"), "The run stopped while this was being sent")
                for action in self.journal.mark_in_flight_uncertain(run_id, reason, connection):
                    self._insert_event(connection, run_id, {"type": "ActionUncertain", "action_id": action["id"],
                                                            "stage": action["stage"], "agent": action["agent"],
                                                            "tool": action["tool"], **self._connection_fields(action["tool"]),
                                                            "reason": reason})
                if event.get("status") in TERMINAL:
                    self.journal.purge(run_id, connection)
            if event["type"] == "HumanInputRequested":
                event["review_id"] = uuid.uuid4().hex
                connection.execute("INSERT INTO studio_run_reviews (id, run_id, stage, request, status, round) VALUES (?, ?, ?, ?, 'pending', ?)",
                                   (event["review_id"], run_id, event["stage"], json.dumps(event, allow_nan=False),
                                    (event.get("revision") or {}).get("round", 1)))
            if event["type"] == "ApplicationStateChanged":
                connection.execute("UPDATE studio_run_reviews SET status = 'consumed' WHERE run_id = ? AND stage = ? AND status IN ('pending', 'submitted')", (run_id, event["stage"]))
            if event["type"] == "RevisionRequested":
                # Answered, and sent the work back: the next round is a new review.
                connection.execute("UPDATE studio_run_reviews SET status = 'revised' WHERE run_id = ? AND stage = ? AND status IN ('pending', 'submitted')", (run_id, event["stage"]))
            if event["type"] == "HumanInputRecorded":
                # The latest round of this step's review is the one just answered.
                review = connection.execute("SELECT id, reviewer FROM studio_run_reviews WHERE run_id = ? AND stage = ? ORDER BY rowid DESC LIMIT 1", (run_id, event["stage"])).fetchone()
                if review:
                    event["review_id"], event["reviewer"] = review
                    # Everyone whose answer it is, in the order they gave it.
                    answered = [item[0] for item in connection.execute(
                        "SELECT reviewer FROM studio_run_review_answers WHERE review_id = ? ORDER BY rowid", (review[0],))]
                    if answered:
                        event["reviewers"] = answered
            sequence = self._insert_event(connection, run_id, event)
            if stored is not None:
                self._record_output_file(connection, run_id, stored)
            if event["type"] == "ApplicationFinished":
                connection.execute("UPDATE studio_runs SET status = ? WHERE id = ?", (event["status"], run_id))
                if event.get("status") in {"waiting_for_human", "paused"}:
                    # Someone is waited on: when the application says where to
                    # tell them, the one notice for this waiting point is due,
                    # written with the event so neither exists without the other.
                    request = json.loads(connection.execute("SELECT request FROM studio_runs WHERE id = ?", (run_id,)).fetchone()[0])
                    waiting = (request["definition"].get("notifications") or {}).get("waiting")
                    if waiting:
                        self.notices.insert_due(connection, run_id, sequence, {"via": waiting["via"], "connection": waiting["connection"]})
        # After the write, so a watcher that re-reads sees this event and, for a
        # finished run, the status it set.
        self._notify(run_id)

    # --- Stored output files ---------------------------------------------------------------------

    @property
    def files_root(self) -> Path:
        return self.path.parent / "run-files"

    def _file_path(self, run_id: str, file_id: str) -> Path:
        # Only ids this store made name a file; anything else names nothing.
        if not re.fullmatch(r"[0-9a-f]{32}", file_id or "") or not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
            raise KeyError(file_id)
        return self.files_root / run_id / file_id

    def _write_output_file(self, run_id: str, event: dict) -> dict | None:
        """Write the file a step's output becomes, if it becomes one; say why not if it cannot."""
        stage, patch = event.get("stage"), event.get("patch")
        if not stage or patch is None:
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT request FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
        definition = json.loads(row[0])["definition"] if row else {}
        step = next((item for item in definition.get("workflow") or [] if item["id"] == stage), None)
        collection = next((item for item in definition.get("state") or [] if step and item["key"] == step["output"]), None)
        if collection is None or output_files.file_format(collection) is None:
            return None
        try:
            name, media_type, body = output_files.render_file(collection, patch)
        except ValueError as error:
            return {"skipped": str(error), "stage": stage, "output": collection["key"], "path": None}
        file_id = uuid.uuid4().hex
        folder = self.files_root / run_id
        path, temporary = folder / file_id, folder / (file_id + ".partial")
        try:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.files_root.chmod(0o700)
            folder.chmod(0o700)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(body)
                os.replace(temporary, path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        except OSError as error:
            # A full disk or a folder it may not write costs the file, not the
            # step's result: the run goes on and says so. What went wrong, and
            # where, is for whoever runs this host.
            LOG.warning("Run %s: the file for step %s could not be written: %s", run_id, stage, error)
            return {"skipped": "The file could not be written", "stage": stage, "output": collection["key"], "path": None}
        shown, lines = output_files.preview(body)
        return {"id": file_id, "path": path, "stage": stage, "output": collection["key"], "name": name,
                "format": output_files.file_format(collection), "media_type": media_type, "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(), "preview": shown, "lines": lines}

    def _record_output_file(self, connection, run_id: str, stored: dict) -> None:
        if "skipped" in stored:
            self._insert_event(connection, run_id, {"type": "OutputFileSkipped", "stage": stored["stage"], "output": stored["output"],
                                                    "reason": stored["skipped"]})
            return
        round_ = connection.execute("SELECT COUNT(*) FROM studio_run_files WHERE run_id = ? AND stage = ?",
                                    (run_id, stored["stage"])).fetchone()[0] + 1
        connection.execute("INSERT INTO studio_run_files (id, run_id, stage, output, round, name, format, media_type, size, sha256, preview, lines, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (stored["id"], run_id, stored["stage"], stored["output"], round_, stored["name"], stored["format"],
                            stored["media_type"], stored["size"], stored["sha256"], stored["preview"], stored["lines"],
                            datetime.now(timezone.utc).isoformat()))
        self._insert_event(connection, run_id, {"type": "OutputFileStored", "stage": stored["stage"], "output": stored["output"],
                                                "file_id": stored["id"], "name": stored["name"], "format": stored["format"],
                                                "size": stored["size"], "round": round_})

    FILE_COLUMNS = ("id", "stage", "output", "round", "name", "format", "media_type", "size", "sha256", "preview", "lines", "created_at")

    def _files(self, connection, run_id: str) -> list[dict]:
        rows = connection.execute(f"SELECT {', '.join(self.FILE_COLUMNS)} FROM studio_run_files WHERE run_id = ? ORDER BY rowid",
                                  (run_id,)).fetchall()
        files = []
        for row in rows:
            item = dict(zip(self.FILE_COLUMNS, row))
            # Cheap: there, and the size it was. The bytes are checked on download.
            try:
                item["available"] = self._file_path(run_id, item["id"]).stat().st_size == item["size"]
            except (OSError, KeyError):
                item["available"] = False
            files.append(item)
        return files

    def files(self, run_id: str) -> list[dict]:
        """The files a run stored, oldest first."""
        with self._connect() as connection:
            return self._files(connection, run_id)

    def read_file(self, run_id: str, file_id: str) -> "output_files.FileDownload":
        """One stored file, only from its own run, and only if it is still exactly what was stored."""
        path = self._file_path(run_id, file_id)
        with self._connect() as connection:
            row = connection.execute("SELECT name, media_type, size, sha256 FROM studio_run_files WHERE id = ? AND run_id = ?",
                                     (file_id, run_id)).fetchone()
        if row is None:
            raise KeyError(file_id)
        name, media_type, size, digest = row
        try:
            body = path.read_bytes()
        except OSError:
            raise output_files.FileChanged("This file is missing or was changed since the run stored it") from None
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise output_files.FileChanged("This file is missing or was changed since the run stored it")
        return output_files.FileDownload(body=body, media_type=media_type, name=name)

    async def execute(self, run_id: str, provider, tools, *, approver=None, human_handler=None, resume=False, loop=None, pause_requested=None):
        """Start once, recording safe adapter events and a durable waiting review.

        A process crash leaves status 'running'; execution refuses to replay it.
        Closing or cancelling this iterator records 'interrupted'. Neither case
        implies that an external action is safe to repeat.
        """
        if resume and human_handler is not None:
            raise ValueError("Review resume uses persisted contributions")
        request, checkpoint, human_inputs = self._claim_resume(run_id) if resume else self._claim(run_id)
        finished = started = False
        progress = self.journal.progress(run_id) if resume else {}
        try:
            async for event in run_application(
                request["definition"], provider, tools, request["task"],
                inputs=request["inputs"], approver=approver, human_handler=human_handler,
                checkpoint=checkpoint, human_inputs=human_inputs, loop=loop,
                pause_requested=pause_requested, log=self.journal.for_run(run_id), progress=progress,
                max_parallel_steps=self.max_parallel_steps,
            ):
                started = True
                if event["type"] == "ApplicationStateChanged":
                    # Making and writing the file can take a while for a large
                    # output: done beside the loop, so the host keeps answering.
                    stored = await asyncio.to_thread(self._write_output_file, run_id, event)
                    self._append(run_id, event, prepared={"stored": stored})
                else:
                    self._append(run_id, event)
                finished = event["type"] == "ApplicationFinished"
                yield event
        except Exception as error:
            if resume and not started and isinstance(error, ValueError):
                # Refused before anything ran: the run is left as it was, and
                # a person decides, rather than a saved run being failed for good.
                event = {"type": "ApplicationFinished", "status": "stopped",
                         "reason": "This run's saved checkpoint could not be restored, so nothing was run. Cancel it, or start a new run."}
            else:
                event = {"type": "ApplicationFinished", "status": "failed", "reason": f"Run could not execute on the runtime host ({type(error).__name__})"}
            self._append(run_id, event)
            finished = True
            yield event
        finally:
            if not finished:
                self._append(run_id, {"type": "ApplicationFinished", "status": "cancelled", "reason": "Stopped by the local operator"}
                             if run_id in self.stopping else
                             {"type": "ApplicationFinished", "status": "interrupted", "reason": "Runtime execution was interrupted"})
