"""The scheduler: starts each scheduled application when its time comes.

One service per host. Each tick it brings the stored schedules in line with the
saved applications, then deals with every time that has come round:

- more than `MISSED_AFTER` late means the host was not running then: the
  missed times are recorded once, together, and not run in a burst (with
  `catch_up: "latest"`, only the latest is run);
- if the previous scheduled run is still going, the time is skipped;
- if the saved request no longer fits the application, or no model backend is
  available, the time fails, and the schedule carries on;
- otherwise the time is claimed and its run created in one transaction, and the
  run starts as the schedule's owner, with nobody to approve its writes.

The clock and the sleep are injected, so every decision here can be tested
without waiting for one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .schedule_store import instant, stamp
from .schedules import count_between, next_time, previous_time, timing_fingerprint, validate_schedule


LOG = logging.getLogger(__name__)

# The statuses in which a scheduled run is still going, so the next time waits.
ACTIVE = {"pending", "running", "paused", "waiting_for_human"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleService:
    MISSED_AFTER = timedelta(minutes=2)
    MAX_SLEEP = 30.0

    def __init__(self, host, workspace, *, clock: Callable[[], datetime] = utc_now, sleep=None,
                 admits: Callable[[str], bool] | None = None):
        self.host, self.workspace, self.clock = host, workspace, clock
        # Whether the deployment still admits a schedule's owner. A schedule
        # must not go on acting as someone who can no longer sign in.
        self.admits = admits
        self.sleep = sleep
        self.woken = asyncio.Event()
        self.task: asyncio.Task | None = None

    @property
    def store(self):
        return self.host.repository.schedules

    # -- One pass -------------------------------------------------------------

    async def tick(self) -> list[dict]:
        """Reconcile, then handle every time that has come round. Returns what happened."""

        now = self.clock()
        schedules = self.reconcile(now)
        outcomes = []
        for row in self.store.due(now):
            saved = schedules.get(row["application_id"])
            if saved is None:
                continue
            outcomes.extend(await self.handle(row, saved, now))
        return outcomes

    def reconcile(self, now: datetime) -> dict[str, dict]:
        """Each saved scheduled application, and the store brought in line with them.

        Saving, pausing and resuming reconcile at once; this pass is for what
        changed some other way. It reads the stored schedules in one query and
        writes only the ones that differ, so a pass with nothing to do costs the
        same however many applications there are.
        """

        local_owner = self.host.principal.name if self.host.profile.name == "local" else None
        rows = self.store.rows()
        saved = {}
        applications = self.workspace.applications()
        for application in applications:
            definition = application["definition"]
            block = definition.get("schedule") if definition.get("start_mode") == "schedule" else None
            row = rows.get(application["id"])
            if block is None:
                if row is not None and row["active"]:
                    self.store.reconcile(application["id"], None, owner=local_owner, now=now)
                continue
            saved[application["id"]] = application
            if (row is None or not row["active"] or row["fingerprint"] != timing_fingerprint(block)
                    or row["owner"] is None and local_owner is not None):
                self.store.reconcile(application["id"], block, owner=local_owner, now=now)
        for application_id in set(rows) - {item["id"] for item in applications}:
            self.store.forget(application_id)
        return saved

    async def handle(self, row: dict, application: dict, now: datetime) -> list[dict]:
        definition = application["definition"]
        schedule = definition["schedule"]
        anchor, due = instant(row["anchor"]), instant(row["next_at"])
        # Everything from the stored next time up to now came round: the
        # latest of them is found directly and the rest counted, so an outage
        # of a month costs the same as one of an hour.
        latest = max(due, previous_time(schedule, now, anchor) or due)
        total = 1 + count_between(schedule, due, latest, anchor)
        on_time = now - latest <= self.MISSED_AFTER
        fire = latest if on_time or schedule.get("catch_up") == "latest" else None
        count = total - (1 if fire is not None else 0)
        missed = None
        if count:
            last = latest if fire is None else previous_time(schedule, latest - timedelta(microseconds=1), anchor)
            missed = (count, due, last)
        following = stamp(next_time(schedule, now, anchor))
        outcomes = []
        if missed:
            count, first, last = missed
            detail = {"count": count, "first": stamp(first), "last": stamp(last)}
            if self.store.record(row["application_id"], stamp(first), "missed", detail,
                                 next_at=None if fire else following, expected=row["next_at"]):
                outcomes.append({"application_id": row["application_id"], "scheduled_for": stamp(first), "outcome": "missed"})
        if fire is not None:
            result = await self.fire(row, application, fire, following)
            if result:
                outcomes.append(result)
        return outcomes

    async def fire(self, row: dict, application: dict, scheduled_for: datetime, following: str) -> dict | None:
        """Start one scheduled time, or record why it did not start."""

        application_id, when = row["application_id"], stamp(scheduled_for)

        def recorded(outcome, detail):
            if self.store.record(application_id, when, outcome, detail, next_at=following, expected=row["next_at"]):
                return {"application_id": application_id, "scheduled_for": when, "outcome": outcome}
            return None

        previous = self.store.last_started_run(application_id)
        if previous and self.still_going(previous):
            return recorded("skipped", {"reason": "previous_run_active", "run_id": previous})
        if self.admits is not None and not self.admits(row["owner"]):
            outcome = recorded("failed", {"reason": "The schedule's owner can no longer use this workspace. "
                                                    "Someone must turn it on again"})
            # Nobody owns it now: it waits until a current member turns it on.
            self.store.disown(application_id)
            return outcome
        if self.host.provider is None and self.host.backends is None:
            return recorded("failed", {"reason": "No model backend is available on this host"})
        definition = application["definition"]
        from .principal import Principal
        try:
            block = validate_schedule(definition["schedule"], definition)
            request = {"version": 1, "application_id": application_id, "revision": application["revision"],
                       "definition": definition, "task": block["request"]["task"], "inputs": block["request"]["inputs"],
                       "trigger": {"kind": "schedule", "scheduled_for": when}}
            if "resources" in block["request"]:
                request["resources"] = block["request"]["resources"]
            # The same checks as a person's start, acting as the owner.
            request = await self.host.prepare_run(request, Principal(row["owner"], self.host.principal.tenant))
            run_id = self.store.claim_and_create(application_id, when, following, request, expected=row["next_at"],
                                                 actor=row["owner"])
        except ValueError as error:
            return recorded("failed", {"reason": str(error)})
        except Exception as error:  # never let a reason carry more than its kind
            LOG.warning("Scheduled time for %s could not be prepared: %s", application_id, type(error).__name__)
            return recorded("failed", {"reason": "This run could not be prepared on this host"})
        if run_id is None:
            return None  # another process took this time
        connections = getattr(self.host, "connections", None)
        if connections is not None:
            # The run acts as the schedule's owner, as a manual run acts as the
            # person who started it.
            connections.pin_run(run_id, self.host.principal.tenant, row["owner"])
        try:
            await self.host._schedule(run_id, approvals="refuse")
        except Exception as error:  # the run is recorded; it failed to start
            LOG.warning("Scheduled run %s could not start: %s", run_id, error)
            self.host.repository._append(run_id, {"type": "ApplicationFinished", "status": "failed",
                                                  "reason": "The scheduled run could not start on this host"})
        return {"application_id": application_id, "scheduled_for": when, "outcome": "started", "run_id": run_id}

    def still_going(self, run_id: str) -> bool:
        """A previous scheduled run that is still going, or waiting on a person."""
        record = self.host.repository.run_state(run_id)
        if record is None:
            return False
        if record["status"] in ACTIVE:
            return True
        unsettled = record["actions"]["to_check"] + record["actions"]["needs_decision"]
        return record["status"] in {"interrupted", "stopped"} and unsettled > 0

    # -- Running ----------------------------------------------------------------

    def seconds_until_next(self) -> float:
        earliest = self.store.earliest()
        if earliest is None:
            return self.MAX_SLEEP
        return max(0.0, min(self.MAX_SLEEP, (earliest - self.clock()).total_seconds()))

    def wake(self) -> None:
        """Something changed (a save, a pause): look again now."""
        self.woken.set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:  # one bad pass must not stop the schedules
                LOG.warning("Scheduler pass failed: %s", error)
            self.woken.clear()
            if self.sleep is not None:
                await self.sleep(self.seconds_until_next())
                continue
            try:
                await asyncio.wait_for(self.woken.wait(), self.seconds_until_next())
            except TimeoutError:
                pass

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
