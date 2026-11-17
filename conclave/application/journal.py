"""The action journal: every external write, recorded before it is sent.

A run records an action as `sending`, with the agent's progress, in one
transaction before the tool runs; the outcome replaces both in another. When a
run stops, whatever is still `sending` becomes `uncertain`, and stays that way
until a check or a person settles it. Nothing uncertain is sent again without
that.

States and the only ways between them:

    sending   -> completed | failed | uncertain
    uncertain -> done | resend                 (a check, or a person)
    done | resend (a person's) -> uncertain    (undo, until the run continues)
    resend    -> sending                       (when the run continues)

The arguments, a person's note, what a check saw and the agent's progress are
sealed with the host key when there is one, and purged once the run can no
longer continue. What remains is the history: which action, when, what
happened to it and who decided.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


SENDING, COMPLETED, FAILED, UNCERTAIN, DONE, RESEND = "sending", "completed", "failed", "uncertain", "done", "resend"
FINISHED = {COMPLETED, FAILED, UNCERTAIN}
NOTE_LIMIT = 500

PUBLIC = ("id", "run_id", "stage", "agent", "tool", "approval", "state", "attempts", "checkable", "started_at",
          "finished_at", "reason", "check_result", "check_summary", "checked_at", "settled_by", "settled_kind",
          "settled_at", "final")


class Unreadable(ValueError):
    """Sealed details this host's key cannot open."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Sealer:
    """Seals private details with the host key, or keeps them as plain JSON.

    Without a key the run database itself is the boundary, and the repository
    makes it readable by this user only.
    """

    def __init__(self, key: bytes | str | None):
        self.cipher = Fernet(key) if key else None

    def seal(self, value: Any) -> bytes | str:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return self.cipher.encrypt(text.encode()) if self.cipher else text

    def open(self, blob: bytes | str | None) -> Any:
        if blob is None:
            return None
        if isinstance(blob, str):
            return json.loads(blob)
        if self.cipher is None:
            raise Unreadable("Saved details are sealed with a key this host does not have")
        try:
            return json.loads(self.cipher.decrypt(blob))
        except InvalidToken:
            raise Unreadable("Saved details cannot be read with this host's key") from None


def refused(current: str, wanted: str) -> ValueError:
    return ValueError(f"This action cannot change from {current} to {wanted}")


class ActionJournal:
    """Actions and progress for the runs in one run database."""

    def __init__(self, connect, *, key: bytes | str | None = None):
        # `connect` is the repository's own: one SQLite connection per
        # operation, foreign keys on, committed when the block ends.
        self.connect = connect
        self.sealer = Sealer(key)

    # -- Recording ------------------------------------------------------------

    def begin(self, run_id: str, start, *, budget: dict | None) -> None:
        """Record an action as being sent, with the progress to resume it from."""

        call = self.sealer.seal(start.arguments)
        budget_text = json.dumps(budget or {}, allow_nan=False)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO studio_run_actions (id, run_id, stage, agent, tool, approval, state, attempts, checkable, call, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'sending', 1, ?, ?, ?)",
                (start.action_id, run_id, start.scope, start.agent, start.tool, start.approval, int(bool(start.checkable)),
                 call, now()))
            frames = self._store_frames(connection, run_id, start.scope, start.frames)
            self._save_progress(connection, run_id, start.scope, frames, budget_text)

    def finish(self, action_id: str, state: str, *, frames=None, budget: dict | None = None, reason: str = "",
               run_id: str | None = None) -> None:
        """Record how an action ended, together with the progress after it.

        A write that completed or clearly failed will never be sent again, so
        its arguments are dropped here rather than kept until the run ends.
        """

        if state not in FINISHED:
            raise refused(SENDING, state)
        budget_text = json.dumps(budget or {}, allow_nan=False) if budget is not None else None
        with self.connect() as connection:
            row = self._row(connection, action_id, run_id)
            if row["state"] != SENDING:
                raise refused(row["state"], state)
            connection.execute("UPDATE studio_run_actions SET state = ?, finished_at = ?, reason = ?, "
                               "call = CASE WHEN ? THEN NULL ELSE call END WHERE id = ?",
                               (state, now(), reason or None, state in (COMPLETED, FAILED), action_id))
            if frames is not None:
                sealed = self._store_frames(connection, row["run_id"], row["stage"], frames)
                self._save_progress(connection, row["run_id"], row["stage"], sealed, budget_text)

    def mark_in_flight_uncertain(self, run_id: str, reason: str, connection=None) -> list[dict]:
        """When a run ends, whatever was still being sent may or may not have happened.

        Given the repository's connection, this happens in the same transaction
        that records the run's end.
        """

        if connection is None:
            with self.connect() as own:
                return self.mark_in_flight_uncertain(run_id, reason, own)
        ids = [row[0] for row in connection.execute(
            "SELECT id FROM studio_run_actions WHERE run_id = ? AND state = 'sending' ORDER BY started_at, rowid", (run_id,))]
        connection.executemany("UPDATE studio_run_actions SET state = 'uncertain', finished_at = ?, reason = ? WHERE id = ?",
                               [(now(), reason, action_id) for action_id in ids])
        return [self._public(self._row(connection, action_id)) for action_id in ids]

    def record_check(self, action_id: str, result, *, run_id: str | None = None) -> None:
        with self.connect() as connection:
            self._row(connection, action_id, run_id)
            connection.execute(
                "UPDATE studio_run_actions SET check_result = ?, check_summary = ?, check_detail = ?, checked_at = ? WHERE id = ?",
                (result.result, result.summary, self.sealer.seal(result.detail) if result.detail else None, now(), action_id))

    # -- Deciding -------------------------------------------------------------

    def settle(self, action_id: str, decision: str, *, by: str, note: str | None = None, kind: str = "person",
               run_id: str | None = None) -> None:
        """Settle an uncertain action: it happened (`done`), or send it again (`resend`).

        `kind` says what settled it — a `check`, or a `person` — and is set by
        the code path, never read from a name: a person called "check" is
        still a person.
        """

        if decision not in (DONE, RESEND):
            raise ValueError("Choose done or resend")
        if kind not in ("check", "person"):
            raise ValueError("An action is settled by a check or a person")
        if note is not None and (not isinstance(note, str) or len(note) > NOTE_LIMIT):
            raise ValueError(f"A note is text of at most {NOTE_LIMIT} characters")
        with self.connect() as connection:
            row = self._row(connection, action_id, run_id)
            if row["state"] != UNCERTAIN:
                raise refused(row["state"], decision)
            connection.execute(
                "UPDATE studio_run_actions SET state = ?, settled_by = ?, settled_kind = ?, settled_note = ?, settled_at = ?, "
                "final = 0 WHERE id = ?", (decision, by, kind, self.sealer.seal(note) if note else None, now(), action_id))

    def undo(self, action_id: str, *, by: str) -> None:
        """Withdraw a person's decision, while the run has not continued with it."""

        with self.connect() as connection:
            row = self._row(connection, action_id)
            if row["state"] not in (DONE, RESEND) or row["settled_kind"] != "person":
                raise refused(row["state"], UNCERTAIN)
            if row["final"]:
                raise ValueError("This choice was already used when the run continued")
            connection.execute(
                "UPDATE studio_run_actions SET state = 'uncertain', settled_by = NULL, settled_kind = NULL, settled_note = NULL, "
                "settled_at = NULL WHERE id = ?", (action_id,))

    def finalize(self, run_id: str, connection=None) -> None:
        """Make every decision on a run final: the run is continuing with them.

        Called inside the claim that resumes the run, so no undo can land
        between a decision being read and it being acted on.
        """

        statement = "UPDATE studio_run_actions SET final = 1 WHERE run_id = ? AND state IN ('done', 'resend')"
        if connection is not None:
            connection.execute(statement, (run_id,))
            return
        with self.connect() as own:
            own.execute(statement, (run_id,))

    def begin_resend(self, action_id: str, *, run_id: str | None = None) -> int:
        """Send a settled `resend` again: it is in flight once more."""

        with self.connect() as connection:
            row = self._row(connection, action_id, run_id)
            if row["state"] != RESEND:
                raise refused(row["state"], SENDING)
            connection.execute("UPDATE studio_run_actions SET state = 'sending', attempts = attempts + 1, finished_at = NULL "
                               "WHERE id = ?", (action_id,))
            return row["attempts"] + 1

    # -- Reading --------------------------------------------------------------

    def get(self, action_id: str) -> dict:
        with self.connect() as connection:
            return self._public(self._row(connection, action_id))

    def list(self, run_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(f"SELECT {', '.join(PUBLIC)}, call FROM studio_run_actions WHERE run_id = ? "
                                      "ORDER BY started_at, rowid", (run_id,)).fetchall()
        return [self._public(dict(zip((*PUBLIC, "call"), row))) for row in rows]

    def arguments(self, action_id: str) -> dict | None:
        """What would be sent again: offered only while it could be."""

        with self.connect() as connection:
            row = self._row(connection, action_id)
        if row["state"] not in (UNCERTAIN, RESEND):
            return None
        try:
            return self.sealer.open(row["call"])
        except Unreadable:
            return None

    def call(self, action_id: str) -> dict | None:
        """The arguments, for the host that sends or checks them. Not for display."""

        with self.connect() as connection:
            return self.sealer.open(self._row(connection, action_id)["call"])

    def note(self, action_id: str) -> str | None:
        with self.connect() as connection:
            row = self._row(connection, action_id)
        try:
            return self.sealer.open(row["settled_note"])
        except Unreadable:
            return None

    def resolution(self, action_id: str, *, run_id: str | None = None) -> dict | None:
        """What is known about an action, for an agent continuing after it.

        A resend carries the recorded call — exactly what the person saw and
        approved — which is what is sent, whatever saved progress says.
        """

        with self.connect() as connection:
            try:
                row = self._row(connection, action_id, run_id)
            except KeyError:
                return None
            detail = connection.execute("SELECT check_detail FROM studio_run_actions WHERE id = ?", (action_id,)).fetchone()[0]

        def opened(blob):
            try:
                return self.sealer.open(blob)
            except Unreadable:
                return None

        resolution = {"state": row["state"], "settled_by": row["settled_by"], "settled_kind": row["settled_kind"],
                      "note": opened(row["settled_note"]), "check_summary": row["check_summary"],
                      "check_detail": opened(detail), "tool": row["tool"]}
        if row["state"] == RESEND:
            resolution["arguments"] = opened(row["call"])
        return resolution

    def counts(self, run_id: str, connection=None) -> dict[str, int]:
        """How many uncertain actions a check may settle, and how many need a person."""

        if connection is None:
            with self.connect() as own:
                return self.counts(run_id, own)
        to_check, needs = connection.execute(
            "SELECT COALESCE(SUM(checkable = 1 AND check_result IS NULL), 0), "
            "COALESCE(SUM(NOT (checkable = 1 AND check_result IS NULL)), 0) "
            "FROM studio_run_actions WHERE run_id = ? AND state = 'uncertain'", (run_id,)).fetchone()
        return {"to_check": int(to_check), "needs_decision": int(needs)}

    def counts_for(self, run_ids: list[str], connection) -> dict[str, dict[str, int]]:
        """`counts` for many runs in one query, on the caller's connection."""

        counts = {run_id: {"to_check": 0, "needs_decision": 0} for run_id in run_ids}
        if not run_ids:
            return counts
        marks = ", ".join("?" for _ in run_ids)
        for run_id, to_check, needs in connection.execute(
                "SELECT run_id, COALESCE(SUM(checkable = 1 AND check_result IS NULL), 0), "
                "COALESCE(SUM(NOT (checkable = 1 AND check_result IS NULL)), 0) FROM studio_run_actions "
                f"WHERE state = 'uncertain' AND run_id IN ({marks}) GROUP BY run_id", list(run_ids)):
            counts[run_id] = {"to_check": int(to_check), "needs_decision": int(needs)}
        return counts

    def details(self, run_id: str) -> dict[str, dict]:
        """Every action's call and note in one read, for the person deciding about them.

        The call is given only while it could still be sent (uncertain, or
        chosen to send again); a note only for a person's decision.
        """

        with self.connect() as connection:
            rows = connection.execute("SELECT id, state, call, settled_note, settled_kind FROM studio_run_actions WHERE run_id = ?",
                                      (run_id,)).fetchall()

        def opened(blob):
            try:
                return self.sealer.open(blob)
            except Unreadable:
                return None

        return {action_id: {"call": opened(call), "arguments": opened(call) if state in (UNCERTAIN, RESEND) else None,
                            "note": opened(note) if kind == "person" else None}
                for action_id, state, call, note, kind in rows}

    def uncertain(self, run_id: str) -> list[str]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT id FROM studio_run_actions WHERE run_id = ? AND state = 'uncertain' ORDER BY started_at, rowid",
                (run_id,))]

    # -- Progress -------------------------------------------------------------

    def progress(self, run_id: str, connection=None) -> dict[str, dict]:
        """Each step's saved progress: the agents' frames, the budget then, any output."""

        if connection is None:
            with self.connect() as own:
                return self.progress(run_id, own)
        rows = connection.execute("SELECT stage, frames, budget, output, updated_at FROM studio_run_progress "
                                  "WHERE run_id = ? ORDER BY updated_at", (run_id,)).fetchall()
        return {stage: {"frames": self._load_frames(connection, run_id, stage, self.sealer.open(frames)),
                        "budget": json.loads(budget), "output": self.sealer.open(output), "updated_at": updated}
                for stage, frames, budget, output, updated in rows}

    def progress_budgets(self, run_id: str, connection) -> list[tuple[str, dict]]:
        """When each step's progress was saved and the budget then, without opening anything sealed."""

        return [(updated, json.loads(budget)) for updated, budget in connection.execute(
            "SELECT updated_at, budget FROM studio_run_progress WHERE run_id = ?", (run_id,))]

    def progress_stages(self, run_id: str) -> list[str]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute("SELECT stage FROM studio_run_progress WHERE run_id = ?", (run_id,))]

    def save_output(self, run_id: str, stage: str, output: Any) -> None:
        """Keep a step's result once its agent finished, so it is not asked again."""

        with self.connect() as connection:
            connection.execute("UPDATE studio_run_progress SET output = ?, updated_at = ? WHERE run_id = ? AND stage = ?",
                               (self.sealer.seal(output), now(), run_id, stage))

    def forget_progress(self, run_id: str, stages) -> None:
        with self.connect() as connection:
            connection.executemany("DELETE FROM studio_run_progress WHERE run_id = ? AND stage = ?",
                                   [(run_id, stage) for stage in stages])
            connection.executemany("DELETE FROM studio_run_transcript WHERE run_id = ? AND stage = ?",
                                   [(run_id, stage) for stage in stages])

    def purge(self, run_id: str, connection=None) -> None:
        """The run can no longer continue: keep the history, not the details."""

        if connection is None:
            with self.connect() as own:
                return self.purge(run_id, own)
        connection.execute("UPDATE studio_run_actions SET call = NULL, settled_note = NULL, check_detail = NULL "
                           "WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM studio_run_progress WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM studio_run_transcript WHERE run_id = ?", (run_id,))

    def for_run(self, run_id: str, *, budget=None) -> "RunActionLog":
        return RunActionLog(self, run_id, budget)

    # -- Internals ------------------------------------------------------------

    def _row(self, connection, action_id: str, run_id: str | None = None) -> dict:
        """One action. Given a run, only that run's: anything else is not found."""

        scope, parameters = ("AND run_id = ?", (action_id, run_id)) if run_id is not None else ("", (action_id,))
        cursor = connection.execute(f"SELECT {', '.join(PUBLIC)}, call, settled_note FROM studio_run_actions WHERE id = ? {scope}",
                                    parameters)
        row = cursor.fetchone()
        if row is None:
            raise KeyError(action_id)
        return dict(zip((*PUBLIC, "call", "settled_note"), row))

    def _public(self, row: dict) -> dict:
        public = {key: row[key] for key in PUBLIC}
        public["checkable"], public["final"] = bool(public["checkable"]), bool(public["final"])
        try:
            self.sealer.open(row.get("call"))
            public["unreadable"] = False
        except Unreadable:
            public["unreadable"] = True
        return public

    def _store_frames(self, connection, run_id, stage, frames) -> bytes | str:
        """Seal a frame stack, appending each agent's new messages to its transcript.

        A frame that names its `log` keeps its conversation as rows, one per
        message, and only the messages added since the last save are written:
        recording a write costs what the conversation grew by, not its length.
        The system prompt (position 0) is rebuilt every step and never stored.
        """

        stored = []
        for frame in frames:
            messages, log = frame.get("messages"), frame.get("log")
            if not log or not isinstance(messages, list):
                stored.append(frame)
                continue
            saved = connection.execute("SELECT COALESCE(MAX(position), 0) FROM studio_run_transcript "
                                       "WHERE run_id = ? AND stage = ? AND log = ?", (run_id, stage, log)).fetchone()[0]
            connection.executemany(
                "INSERT INTO studio_run_transcript (run_id, stage, log, position, message) VALUES (?, ?, ?, ?, ?)",
                [(run_id, stage, log, position, self.sealer.seal(messages[position]))
                 for position in range(max(1, saved + 1), len(messages))])
            stored.append({**frame, "messages": {"log": log, "count": len(messages)}})
        return self.sealer.seal(stored)

    def _load_frames(self, connection, run_id, stage, frames):
        """A saved frame stack with each agent's conversation read back from its transcript."""

        loaded = []
        for frame in frames or []:
            reference = frame.get("messages")
            if isinstance(reference, dict) and "log" in reference:
                rows = connection.execute(
                    "SELECT message FROM studio_run_transcript WHERE run_id = ? AND stage = ? AND log = ? "
                    "AND position < ? ORDER BY position", (run_id, stage, reference["log"], reference["count"])).fetchall()
                frame = {**frame, "messages": [{"role": "system", "content": ""}] + [self.sealer.open(row[0]) for row in rows]}
            loaded.append(frame)
        return loaded

    @staticmethod
    def _save_progress(connection, run_id, stage, frames, budget_text) -> None:
        """Replace a step's progress; a record without a budget keeps the last one."""

        updated = connection.execute(
            "UPDATE studio_run_progress SET frames = ?, budget = COALESCE(?, budget), updated_at = ? "
            "WHERE run_id = ? AND stage = ?", (frames, budget_text, now(), run_id, stage)).rowcount
        if not updated:
            connection.execute("INSERT INTO studio_run_progress (run_id, stage, frames, budget, updated_at) VALUES (?, ?, ?, ?, ?)",
                               (run_id, stage, frames, budget_text or "{}", now()))


def budget_snapshot(budget) -> dict:
    if budget is None:
        return {"used": 0, "per_agent": {}, "spent": {}}
    return {"used": budget.used, "per_agent": dict(budget.per_agent), "spent": dict(budget.spent)}


class RunActionLog:
    """One run's view of the journal: the `ActionLog` its runtime writes to.

    It snapshots the run's budget with each record, so a run that continues
    from saved progress does not forget what it already spent.
    """

    def __init__(self, journal: ActionJournal, run_id: str, budget=None):
        self.journal, self.run_id, self.budget = journal, run_id, budget
        # The steps that have sent something, and so have progress to keep.
        self.progressed = set(journal.progress_stages(run_id))

    def attach_budget(self, budget) -> None:
        self.budget = budget

    def begin(self, start) -> None:
        self.journal.begin(self.run_id, start, budget=budget_snapshot(self.budget))
        self.progressed.add(start.scope)

    # Every call names this run: a log, or a sandbox speaking through one,
    # cannot read or change another run's actions even knowing their ids.

    def finish(self, action_id: str, state: str, *, frames, reason: str = "") -> None:
        self.journal.finish(action_id, state, frames=frames, budget=budget_snapshot(self.budget), reason=reason,
                            run_id=self.run_id)

    def checked(self, action_id: str, result) -> None:
        self.journal.record_check(action_id, result, run_id=self.run_id)

    def settle(self, action_id: str, decision: str, *, by: str, note: str | None = None, kind: str = "person") -> None:
        self.journal.settle(action_id, decision, by=by, note=note, kind=kind, run_id=self.run_id)

    def resend(self, action_id: str) -> int:
        return self.journal.begin_resend(action_id, run_id=self.run_id)

    def resolution(self, action_id: str) -> dict | None:
        return self.journal.resolution(action_id, run_id=self.run_id)

    def save_output(self, stage: str, output: Any) -> None:
        self.journal.save_output(self.run_id, stage, output)

    def forget_progress(self, stages) -> None:
        self.journal.forget_progress(self.run_id, stages)
        self.progressed.difference_update(stages)
