"""Local run records for a trusted Python host, separate from the Site database.

This is local persistence and review resume, not a production server.
Providers, tools, credentials and approval callbacks remain host-owned.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .definition import run_application, validate_definition, validate_request_inputs, validate_human_output


def validate_run_request(request: dict) -> dict:
    """Version 1 pins a saved application revision and its complete definition."""
    required = {"version", "application_id", "revision", "definition", "task", "inputs"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"resources"}:
        raise ValueError("Use the version 1 run request fields")
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

    def __init__(self, path: str | Path, *, history_limit: int = 30):
        if type(history_limit) is not int or history_limit < 1:
            raise ValueError("history_limit must be a positive integer")
        self.history_limit = history_limit
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Runs this process is watching, for a caller that wants to be told
        # rather than ask. In-process only: replicas would need a shared signal,
        # which is the same work as moving the run claim into the database.
        self.watchers: dict[str, set[asyncio.Queue]] = {}
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS studio_runs (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_run_events (
                    run_id TEXT NOT NULL REFERENCES studio_runs(id),
                    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
            """)
            connection.executescript((Path(__file__).parent / "migrations" / "0001_reviews.sql").read_text())

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, request: dict) -> str:
        request = validate_run_request(request)
        run_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO studio_runs VALUES (?, ?, ?, ?)",
                (run_id, json.dumps(request, allow_nan=False), "pending", datetime.now(timezone.utc).isoformat()),
            )
        return run_id

    def get(self, run_id: str) -> dict:
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT request, status, created_at FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            events = connection.execute("SELECT sequence, payload FROM studio_run_events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
            reviews = connection.execute("SELECT id, stage, request, status, reviewer, output, submitted_at FROM studio_run_reviews WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        return {"id": run_id, "request": json.loads(row[0]), "status": row[1], "created_at": row[2],
                "events": [{"sequence": sequence, "event": json.loads(payload)} for sequence, payload in events],
                "reviews": [{"id": review[0], "stage": review[1], "request": json.loads(review[2]), "status": review[3],
                             "reviewer": review[4], "output": json.loads(review[5]) if review[5] else None,
                             "submitted_at": review[6]} for review in reviews]}

    def list(self, application_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, request, status, created_at FROM studio_runs ORDER BY created_at DESC, id DESC").fetchall()
        # ponytail: scan local run records; index application_id if history grows.
        return [{"id": row[0], "revision": request["revision"], "status": row[2], "created_at": row[3]}
                for row in rows if (request := json.loads(row[1]))["application_id"] == application_id][:self.history_limit]

    def cancel_waiting(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute("UPDATE studio_runs SET status = 'cancelled' WHERE id = ? AND status IN ('pending', 'waiting_for_human')", (run_id,)).rowcount
            if changed != 1:
                raise ValueError("This run cannot be cancelled while inactive")
            self._insert_event(connection, run_id, {"type": "ApplicationFinished", "status": "cancelled", "reason": "Stopped by the local operator"})
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
            payload = validate_human_output(output, review["output"], {"agent": review["agent"], "stage": review["stage"], "run_id": review_id})
            normalized = {"text" if review["output"]["type"] == "text" else "records": payload}
            timestamp = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE studio_run_reviews SET status = 'submitted', reviewer = ?, output = ?, submitted_at = ? WHERE id = ?",
                (reviewer, json.dumps(normalized, allow_nan=False), timestamp, review_id),
            )
            self._insert_event(connection, run_id, {"type": "HumanReviewSubmitted", "review_id": review_id,
                                                  "stage": review["stage"], "reviewer": reviewer, "submitted_at": timestamp})
        self._notify(run_id)

    def resume(self, run_id: str, provider, tools, *, approver=None, loop=None):
        """Continue a waiting run using its persisted, explicitly submitted review."""
        return self.execute(run_id, provider, tools, approver=approver, resume=True, loop=loop)

    def _claim_resume(self, run_id: str):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT request FROM studio_runs WHERE id = ? AND status = 'waiting_for_human'", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Only a waiting run can resume")
            # Submission events follow the waiting event; select that checkpoint
            # by type rather than assuming the latest event is ApplicationFinished.
            events = connection.execute("SELECT payload FROM studio_run_events WHERE run_id = ? ORDER BY sequence DESC", (run_id,)).fetchall()
            waiting = next((event for (payload,) in events if (event := json.loads(payload)).get("status") == "waiting_for_human" and event.get("type") == "ApplicationFinished"), None)
            if waiting is None or "checkpoint" not in waiting:
                raise ValueError("This run has no resumable review checkpoint")
            review = connection.execute(
                "SELECT stage, output FROM studio_run_reviews WHERE run_id = ? AND stage = ? AND status = 'submitted'",
                (run_id, waiting["stage"]),
            ).fetchone()
            if review is None:
                raise ValueError("Submit the pending review before resuming")
            connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
            return json.loads(row[0]), waiting["checkpoint"], {review[0]: json.loads(review[1])}

    def _claim(self, run_id: str) -> dict:
        with self._connect() as connection:
            changed = connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ? AND status = 'pending'", (run_id,)).rowcount
            if changed != 1:
                raise ValueError("Only a pending run can start; interrupted runs require explicit recovery")
        return self.get(run_id)["request"], None, {}

    def _insert_event(self, connection, run_id, event):
        sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM studio_run_events WHERE run_id = ?", (run_id,)).fetchone()[0]
        connection.execute("INSERT INTO studio_run_events VALUES (?, ?, ?)", (run_id, sequence, json.dumps(event, allow_nan=False)))

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

    def _append(self, run_id: str, event: dict) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if event["type"] == "HumanInputRequested":
                event["review_id"] = uuid.uuid4().hex
                connection.execute("INSERT INTO studio_run_reviews (id, run_id, stage, request, status) VALUES (?, ?, ?, ?, 'pending')",
                                   (event["review_id"], run_id, event["stage"], json.dumps(event, allow_nan=False)))
            if event["type"] == "ApplicationStateChanged":
                connection.execute("UPDATE studio_run_reviews SET status = 'consumed' WHERE run_id = ? AND stage = ? AND status IN ('pending', 'submitted')", (run_id, event["stage"]))
            if event["type"] == "HumanInputRecorded":
                review = connection.execute("SELECT id, reviewer FROM studio_run_reviews WHERE run_id = ? AND stage = ?", (run_id, event["stage"])).fetchone()
                if review:
                    event["review_id"], event["reviewer"] = review
            self._insert_event(connection, run_id, event)
            if event["type"] == "ApplicationFinished":
                connection.execute("UPDATE studio_runs SET status = ? WHERE id = ?", (event["status"], run_id))
        # After the write, so a watcher that re-reads sees this event and, for a
        # finished run, the status it set.
        self._notify(run_id)

    async def execute(self, run_id: str, provider, tools, *, approver=None, human_handler=None, resume=False, loop=None):
        """Start once, recording safe adapter events and a durable waiting review.

        A process crash leaves status 'running'; execution refuses to replay it.
        Closing or cancelling this iterator records 'interrupted'. Neither case
        implies that an external action is safe to repeat.
        """
        if resume and human_handler is not None:
            raise ValueError("Review resume uses persisted contributions")
        request, checkpoint, human_inputs = self._claim_resume(run_id) if resume else self._claim(run_id)
        finished = False
        try:
            async for event in run_application(
                request["definition"], provider, tools, request["task"],
                inputs=request["inputs"], approver=approver, human_handler=human_handler,
                checkpoint=checkpoint, human_inputs=human_inputs, loop=loop,
            ):
                self._append(run_id, event)
                finished = event["type"] == "ApplicationFinished"
                yield event
        except Exception:
            event = {"type": "ApplicationFinished", "status": "failed", "reason": "Run could not execute on the runtime host"}
            self._append(run_id, event)
            finished = True
            yield event
        finally:
            if not finished:
                self._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "Runtime execution was interrupted"})
