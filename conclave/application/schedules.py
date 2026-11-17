"""Schedules: when an application starts on its own, and with what.

A schedule lives in the definition (`start_mode: "schedule"` plus a `schedule`
block): a fixed interval, or daily or weekly local times, in a named time zone,
with the request each run starts with. This module validates a block and
answers, as pure functions of a schedule and an instant, when it runs next.
Nothing here reads the clock: the caller says what "now" is.

Local times are wall-clock times in the schedule's zone. A time the clocks
skip (spring forward) runs at the first valid minute after it; a time the
clocks repeat (fall back) runs once, at its first occurrence. An interval is
elapsed time, counted from its anchor, and does not move with the clocks.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


KINDS = ("interval", "daily", "weekly")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SETTINGS = {
    "interval": {"kind", "every_minutes", "time_zone", "starts_at", "catch_up", "request"},
    "daily": {"kind", "times", "time_zone", "starts_at", "catch_up", "request"},
    "weekly": {"kind", "times", "days", "time_zone", "starts_at", "catch_up", "request"},
}
TIMING = ("kind", "every_minutes", "times", "days", "time_zone", "starts_at")
MIN_EVERY, MAX_EVERY, MAX_TIMES = 5, 7 * 24 * 60, 24
TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
START = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
SEARCH_DAYS = 400


def validate_schedule(schedule: Any, definition: dict) -> dict:
    """Check a schedule block against its definition; return it normalized.

    The messages are the ones Studio shows for the same mistakes
    (`tests/data/schedule_cases.json` holds both sides to that).
    """

    from .definition import validate_request_inputs

    if not isinstance(schedule, dict) or schedule.get("kind") not in KINDS:
        raise ValueError("Choose interval, daily or weekly")
    kind = schedule["kind"]
    for key in sorted(set(schedule) - SETTINGS[kind]):
        raise ValueError(f"Unsupported schedule setting: {key}")
    normalized: dict[str, Any] = {"kind": kind}
    if kind == "interval":
        every = schedule.get("every_minutes")
        if type(every) is not int or every < MIN_EVERY:
            raise ValueError(f"Run at most every {MIN_EVERY} minutes")
        if every > MAX_EVERY:
            raise ValueError("Run at least once a week")
        normalized["every_minutes"] = every
    else:
        times = schedule.get("times")
        if not isinstance(times, list) or not 1 <= len(times) <= MAX_TIMES or any(
                not isinstance(item, str) or not TIME.fullmatch(item) for item in times):
            raise ValueError(f"Give 1 to {MAX_TIMES} times as HH:MM")
        if len(set(times)) != len(times):
            raise ValueError("Use each time once")
        normalized["times"] = sorted(times)
    if kind == "weekly":
        days = schedule.get("days")
        if not isinstance(days, list) or not 1 <= len(days) <= 7 or any(item not in DAYS for item in days):
            raise ValueError("Choose the days: " + ", ".join(DAYS))
        if len(set(days)) != len(days):
            raise ValueError("Choose each day once")
        normalized["days"] = sorted(days, key=DAYS.index)
    normalized["time_zone"] = zone_name(schedule.get("time_zone"))
    if "starts_at" in schedule:
        normalized["starts_at"] = local_start(schedule["starts_at"])
    catch_up = schedule.get("catch_up", "skip")
    if catch_up not in ("skip", "latest"):
        raise ValueError("Choose skip or latest for missed times")
    normalized["catch_up"] = catch_up
    request = schedule.get("request")
    if not isinstance(request, dict):
        raise ValueError("Say what each scheduled run should do")
    for key in sorted(set(request) - {"task", "inputs", "resources"}):
        raise ValueError(f"Unsupported scheduled request setting: {key}")
    task = request.get("task")
    if not isinstance(task, str) or not task.strip() or len(task) > 20000:
        raise ValueError("Say what each scheduled run should do")
    normalized_request = {"task": task, "inputs": validate_request_inputs(definition, request.get("inputs") or {})}
    if "resources" in request:
        resources = request["resources"]
        if not isinstance(resources, dict) or set(resources) != {"source"} or not isinstance(resources["source"], str) \
                or not 1 <= len(resources["source"]) <= 64:
            raise ValueError("Scheduled runs use a named source resource, not a path")
        normalized_request["resources"] = {"source": resources["source"]}
    normalized["request"] = normalized_request
    return normalized


def zone_name(value: Any) -> str:
    message = "Choose a time zone this host knows, such as Europe/London"
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError(message)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(message) from None
    return value


def local_start(value: Any) -> str:
    message = "Give the start as a local date and time, YYYY-MM-DDTHH:MM"
    if not isinstance(value, str) or not START.fullmatch(value):
        raise ValueError(message)
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(message) from None
    return value


def timing_fingerprint(schedule: dict) -> str:
    """What decides *when* it runs. Changing the request does not reschedule."""
    timing = {key: schedule.get(key) for key in TIMING}
    return hashlib.sha256(json.dumps(timing, sort_keys=True).encode()).hexdigest()


def wall_time(zone: ZoneInfo, day, hour: int, minute: int) -> datetime:
    """A local wall time on a day, as an instant in UTC.

    A time the clocks skip moves to the first minute that exists after it;
    a time the clocks repeat is its first occurrence (fold 0).
    """

    naive = datetime(day.year, day.month, day.day, hour, minute)
    for step in range(0, 24 * 60):
        candidate = naive + timedelta(minutes=step)
        local = candidate.replace(tzinfo=zone, fold=0)
        if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == candidate:
            return local.astimezone(timezone.utc)
    raise ValueError("No valid local time")  # pragma: no cover - zones skip at most hours, not a day


def start_instant(schedule: dict) -> datetime | None:
    if not schedule.get("starts_at"):
        return None
    start = datetime.fromisoformat(schedule["starts_at"])
    return wall_time(ZoneInfo(schedule["time_zone"]), start.date(), start.hour, start.minute)


def next_time(schedule: dict, after: datetime, anchor: datetime | None = None) -> datetime:
    """The first scheduled instant strictly after `after`, in UTC.

    An interval counts from `starts_at`, else from `anchor` (when the schedule
    was first turned on). Nothing runs before `starts_at`.
    """

    after = after.astimezone(timezone.utc)
    start = start_instant(schedule)
    if schedule["kind"] == "interval":
        origin = start or anchor
        if origin is None:
            raise ValueError("An interval needs an anchor")
        origin = origin.astimezone(timezone.utc)
        if after < origin:
            return origin
        every = timedelta(minutes=schedule["every_minutes"])
        return origin + ((after - origin) // every + 1) * every
    zone = ZoneInfo(schedule["time_zone"])
    floor = max(after, start - timedelta(microseconds=1)) if start else after
    day = floor.astimezone(zone).date() - timedelta(days=1)
    days = set(schedule.get("days") or DAYS)
    clock = [tuple(int(part) for part in value.split(":")) for value in schedule["times"]]
    for _ in range(SEARCH_DAYS):
        if DAYS[day.weekday()] in days:
            for hour, minute in clock:
                instant = wall_time(zone, day, hour, minute)
                if instant > floor:
                    return instant
        day += timedelta(days=1)
    raise ValueError("This schedule has no time within a year")  # pragma: no cover


def previous_time(schedule: dict, at: datetime, anchor: datetime | None = None) -> datetime | None:
    """The latest scheduled instant at or before `at`, in UTC; None before the first.

    The mirror of `next_time`, so the latest of a long run of missed times is
    found directly rather than by listing every one before it.
    """

    at = at.astimezone(timezone.utc)
    start = start_instant(schedule)
    if schedule["kind"] == "interval":
        origin = start or anchor
        if origin is None:
            raise ValueError("An interval needs an anchor")
        origin = origin.astimezone(timezone.utc)
        if at < origin:
            return None
        every = timedelta(minutes=schedule["every_minutes"])
        return origin + ((at - origin) // every) * every
    zone = ZoneInfo(schedule["time_zone"])
    day = at.astimezone(zone).date() + timedelta(days=1)
    days = set(schedule.get("days") or DAYS)
    clock = sorted((tuple(int(part) for part in value.split(":")) for value in schedule["times"]), reverse=True)
    for _ in range(SEARCH_DAYS):
        if DAYS[day.weekday()] in days:
            for hour, minute in clock:
                moment = wall_time(zone, day, hour, minute)
                if moment <= at:
                    return moment if start is None or moment >= start else None
        day -= timedelta(days=1)
    return None


def count_between(schedule: dict, after: datetime, until: datetime, anchor: datetime | None = None) -> int:
    """How many scheduled instants fall in (after, until], without listing them.

    Arithmetic for an interval; day by day for daily and weekly schedules, so
    the work grows with the days covered rather than being capped.
    """

    after, until = after.astimezone(timezone.utc), until.astimezone(timezone.utc)
    if until <= after:
        return 0
    start = start_instant(schedule)
    if schedule["kind"] == "interval":
        origin = (start or anchor).astimezone(timezone.utc)
        every = timedelta(minutes=schedule["every_minutes"])

        def upto(moment):
            return 0 if moment < origin else (moment - origin) // every + 1
        return upto(until) - upto(after)
    zone = ZoneInfo(schedule["time_zone"])
    days = set(schedule.get("days") or DAYS)
    clock = [tuple(int(part) for part in value.split(":")) for value in schedule["times"]]
    day, last, count = after.astimezone(zone).date() - timedelta(days=1), until.astimezone(zone).date() + timedelta(days=1), 0
    while day <= last:
        if DAYS[day.weekday()] in days:
            for hour, minute in clock:
                moment = wall_time(zone, day, hour, minute)
                if after < moment <= until and (start is None or moment >= start):
                    count += 1
        day += timedelta(days=1)
    return count


def due_between(schedule: dict, start: datetime, end: datetime, anchor: datetime | None = None, *,
                limit: int = 1000) -> list[datetime]:
    """The scheduled instants in (start, end], at most `limit` of them."""

    found, instant = [], start
    while len(found) < limit:
        instant = next_time(schedule, instant, anchor)
        if instant > end:
            break
        found.append(instant)
    return found


def format_local(instant: datetime, zone: str) -> str:
    """An instant as the schedule's zone reads it: "Mon 16 Nov, 17:30"."""
    local = instant.astimezone(ZoneInfo(zone))
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, {local.strftime('%H:%M')}"
