"""Where the host keeps each schedule's next time, and what became of each time.

One row per scheduled application: its next time, its owner (whom its runs act
as), whether it is paused. One row per scheduled time that came round: started
(with its run), skipped, missed or failed. That row's primary key is the
exactly-once guarantee: a time is claimed by inserting it in the same
transaction that creates the run, so two processes on one store, or a host
that restarts, can never start the same time twice.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .errors import Conflict
from .schedules import format_local, next_time, timing_fingerprint


OUTCOMES = ("skipped", "missed", "failed")
RECENT = 20


def instant(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def stamp(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


class ScheduleStore:
    """Schedules and scheduled times for the runs in one run database."""

    def __init__(self, repository):
        self.repository = repository

    def connect(self):
        return self.repository._connect()

    # -- Keeping the state in step with the saved application -------------------

    def reconcile(self, application_id: str, schedule: dict | None, *, owner: str | None, now: datetime,
                  take_over: bool = False) -> dict:
        """Bring the stored state in line with the saved schedule.

        A new schedule is anchored now. A change to *when* it runs reschedules
        from now; a change to only what it runs with does not. No schedule
        (turned off) leaves nothing due. The owner is kept once recorded, unless
        `take_over`: someone saved a change to what runs, and it now runs as
        them, never as whoever saved it before.
        """

        with self.connect() as connection:
            row = self._row(connection, application_id)
            if schedule is None:
                if row is not None:
                    connection.execute("UPDATE studio_schedules SET active = 0, next_at = NULL, updated_at = ? "
                                       "WHERE application_id = ?", (stamp(now), application_id))
            else:
                fingerprint = timing_fingerprint(schedule)
                if row is None:
                    connection.execute(
                        "INSERT INTO studio_schedules (application_id, fingerprint, time_zone, anchor, next_at, owner, "
                        "owner_since, active, paused, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?)",
                        (application_id, fingerprint, schedule["time_zone"], stamp(now),
                         stamp(next_time(schedule, now, now)) if owner else None, owner,
                         stamp(now) if owner else None, stamp(now)))
                else:
                    keeper = owner if take_over and owner else row["owner"] or owner
                    since = stamp(now) if keeper != row["owner"] else row["owner_since"]
                    changed = fingerprint != row["fingerprint"] or not row["active"]
                    anchor = now if changed else instant(row["anchor"])
                    runnable = keeper and not row["paused"]
                    if changed or (keeper and not row["owner"]):
                        next_at = stamp(next_time(schedule, now, anchor)) if runnable else None
                    else:
                        next_at = row["next_at"]
                    connection.execute(
                        "UPDATE studio_schedules SET fingerprint = ?, time_zone = ?, anchor = ?, next_at = ?, owner = ?, "
                        "owner_since = ?, active = 1, updated_at = ? WHERE application_id = ?",
                        (fingerprint, schedule["time_zone"], stamp(anchor), next_at, keeper, since, stamp(now),
                         application_id))
        return self.status(application_id)

    def forget(self, application_id: str) -> None:
        """The application is gone: no more times. What happened stays as history."""
        with self.connect() as connection:
            connection.execute("DELETE FROM studio_schedules WHERE application_id = ?", (application_id,))

    def rows(self) -> dict[str, dict]:
        """Every stored schedule, by application, in one query."""
        with self.connect() as connection:
            return {row[0]: self._shape(row) for row in connection.execute(f"SELECT {COLUMNS} FROM studio_schedules")}

    def applications(self) -> list[str]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute("SELECT application_id FROM studio_schedules")]

    # -- What is due, and what became of each time ------------------------------

    def due(self, now: datetime) -> list[dict]:
        """Schedules whose time has come: active, not paused, with an owner."""

        with self.connect() as connection:
            rows = [self._shape(row) for row in connection.execute(
                f"SELECT {COLUMNS} FROM studio_schedules WHERE active = 1 AND paused = 0 AND owner IS NOT NULL "
                "AND next_at IS NOT NULL")]
        return sorted((row for row in rows if instant(row["next_at"]) <= now), key=lambda row: row["next_at"])

    def earliest(self) -> datetime | None:
        with self.connect() as connection:
            times = [instant(row[0]) for row in connection.execute(
                "SELECT next_at FROM studio_schedules WHERE active = 1 AND paused = 0 AND owner IS NOT NULL "
                "AND next_at IS NOT NULL")]
        return min(times) if times else None

    def record(self, application_id: str, scheduled_for: str, outcome: str, detail: dict | None = None,
               *, next_at: str | None = None, expected: str | None = None) -> bool:
        """Record a time that did not start a run, and optionally move on.

        With `next_at`, the schedule advances in the same transaction, only if
        its next time is still `expected` (another process may have moved it).
        """

        if outcome not in OUTCOMES:
            raise ValueError("A scheduled time is skipped, missed or failed")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if next_at is not None:
                moved = connection.execute(
                    "UPDATE studio_schedules SET next_at = ?, updated_at = ? WHERE application_id = ? AND next_at = ?",
                    (next_at, stamp(datetime.now(timezone.utc)), application_id, expected or scheduled_for)).rowcount
                if not moved:
                    return False
            connection.execute(
                "INSERT OR IGNORE INTO studio_schedule_slots (application_id, scheduled_for, outcome, detail, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)", (application_id, scheduled_for, outcome, json.dumps(detail or {}),
                                           stamp(datetime.now(timezone.utc))))
        return True

    def claim_and_create(self, application_id: str, scheduled_for: str, next_at: str, request: dict, *,
                         expected: str | None = None, actor: str | None = None) -> str | None:
        """Take one scheduled time and create its run, in one transaction.

        Returns the run id, or None when this time is no longer this process's
        to take: another process took it, or the schedule moved on.
        """

        from .runs import validate_run_request

        request = validate_run_request(request)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            moved = connection.execute(
                "UPDATE studio_schedules SET next_at = ?, updated_at = ? WHERE application_id = ? AND next_at = ? "
                "AND active = 1 AND paused = 0", (next_at, stamp(datetime.now(timezone.utc)), application_id,
                                                  expected or scheduled_for)).rowcount
            if not moved:
                return None
            if connection.execute("SELECT 1 FROM studio_schedule_slots WHERE application_id = ? AND scheduled_for = ?",
                                  (application_id, scheduled_for)).fetchone():
                connection.rollback()
                return None
            run_id = self.repository._insert_run(connection, request, actor=actor)
            connection.execute(
                "INSERT INTO studio_schedule_slots (application_id, scheduled_for, outcome, run_id, detail, recorded_at) "
                "VALUES (?, ?, 'started', ?, ?, ?)", (application_id, scheduled_for, run_id,
                                                       json.dumps({"revision": request["revision"]}),
                                                       stamp(datetime.now(timezone.utc))))
        return run_id

    def last_started_run(self, application_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT run_id FROM studio_schedule_slots WHERE application_id = ? AND outcome = 'started' "
                                     "ORDER BY scheduled_for DESC LIMIT 1", (application_id,)).fetchone()
        return row[0] if row else None

    # -- The owner's controls ---------------------------------------------------

    def pause(self, application_id: str, *, by: str) -> dict:
        with self.connect() as connection:
            row = self._owned(connection, application_id, by)
            if row["paused"]:
                raise Conflict("This schedule is already paused")
            connection.execute("UPDATE studio_schedules SET paused = 1, next_at = NULL, paused_by = ?, paused_at = ?, "
                               "updated_at = ? WHERE application_id = ?",
                               (by, stamp(datetime.now(timezone.utc)), stamp(datetime.now(timezone.utc)), application_id))
        return self.status(application_id)

    def resume(self, application_id: str, schedule: dict, *, by: str, now: datetime) -> dict:
        """Carry on from now: times passed while paused are not reported as missed."""
        with self.connect() as connection:
            row = self._owned(connection, application_id, by)
            if not row["paused"]:
                raise Conflict("This schedule is not paused")
            connection.execute("UPDATE studio_schedules SET paused = 0, paused_by = NULL, paused_at = NULL, next_at = ?, "
                               "updated_at = ? WHERE application_id = ?",
                               (stamp(next_time(schedule, now, instant(row["anchor"]))), stamp(now), application_id))
        return self.status(application_id)

    def turn_on(self, application_id: str, schedule: dict, *, by: str, now: datetime) -> dict:
        """Give a schedule with no owner one: its runs will act as them."""
        with self.connect() as connection:
            row = self._row(connection, application_id)
            if row is None or not row["active"]:
                raise Conflict("This application has no schedule to turn on")
            if row["owner"] and row["owner"] != by:
                raise ValueError("Only the schedule's owner can pause or resume it")
            if row["owner"]:
                raise Conflict("This schedule is already on")
            connection.execute("UPDATE studio_schedules SET owner = ?, owner_since = ?, next_at = ?, updated_at = ? "
                               "WHERE application_id = ?",
                               (by, stamp(now), None if row["paused"] else stamp(next_time(schedule, now, instant(row["anchor"]))),
                                stamp(now), application_id))
        return self.status(application_id)

    def disown(self, application_id: str) -> None:
        """The owner can no longer act here: nothing runs until someone turns it on."""
        with self.connect() as connection:
            connection.execute("UPDATE studio_schedules SET owner = NULL, owner_since = NULL, next_at = NULL, "
                               "updated_at = ? WHERE application_id = ?",
                               (stamp(datetime.now(timezone.utc)), application_id))

    # -- Reading ------------------------------------------------------------------

    def status(self, application_id: str) -> dict:
        """What Studio, REST and the CLI show: next time, who, and what became of recent times."""

        with self.connect() as connection:
            row = self._row(connection, application_id)
            slots = connection.execute(
                "SELECT scheduled_for, outcome, run_id, detail, recorded_at FROM studio_schedule_slots "
                "WHERE application_id = ? ORDER BY scheduled_for DESC LIMIT ?", (application_id, RECENT)).fetchall()
        zone = row["time_zone"] if row else None
        recent = [self._slot(slot, zone) for slot in slots]
        if row is None:
            return {"application_id": application_id, "active": False, "paused": False, "waiting": False, "owner": None,
                    "owner_since": None,
                    "time_zone": None, "anchor": None, "next_at": None, "next_local": None, "paused_by": None,
                    "paused_at": None, "last": recent[0] if recent else None, "recent": recent}
        return {"application_id": application_id, "active": bool(row["active"]), "paused": bool(row["paused"]),
                "waiting": bool(row["active"]) and row["owner"] is None, "owner": row["owner"],
                "owner_since": row["owner_since"], "time_zone": zone,
                "anchor": row["anchor"], "next_at": row["next_at"],
                "next_local": format_local(instant(row["next_at"]), zone) if row["next_at"] else None,
                "paused_by": row["paused_by"], "paused_at": row["paused_at"],
                "last": recent[0] if recent else None, "recent": recent}

    # -- Internals ----------------------------------------------------------------

    def _row(self, connection, application_id: str) -> dict | None:
        row = connection.execute(f"SELECT {COLUMNS} FROM studio_schedules WHERE application_id = ?", (application_id,)).fetchone()
        return self._shape(row) if row else None

    def _owned(self, connection, application_id: str, by: str) -> dict:
        row = self._row(connection, application_id)
        if row is None or not row["active"]:
            raise Conflict("This application has no schedule")
        if row["owner"] != by:
            raise ValueError("Only the schedule's owner can pause or resume it")
        return row

    @staticmethod
    def _shape(row) -> dict:
        return dict(zip(FIELDS, row))

    @staticmethod
    def _slot(slot, zone: str | None) -> dict:
        scheduled_for, outcome, run_id, detail, recorded_at = slot
        detail = json.loads(detail) if detail else {}
        if zone:
            for key in ("first", "last"):
                if detail.get(key):
                    detail[key + "_local"] = format_local(instant(detail[key]), zone)
        return {"scheduled_for": scheduled_for, "outcome": outcome, "run_id": run_id, "detail": detail,
                "recorded_at": recorded_at,
                "scheduled_local": format_local(instant(scheduled_for), zone) if zone else None}


FIELDS = ("application_id", "fingerprint", "time_zone", "anchor", "next_at", "owner", "owner_since", "active", "paused",
          "paused_by", "paused_at", "updated_at")
COLUMNS = ", ".join(FIELDS)
