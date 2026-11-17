"""C3 — the host's record of each schedule, and the claim that makes a
scheduled time start exactly one run, even with two processes on one store.
"""

import sqlite3
from contextlib import closing

import pytest

from schedule_support import definition, hourly, schedule, utc
from conclave.application.runs import RunRepository

NOW = utc(2026, 11, 16, 9, 30)


def request(scheduled_for="2026-11-16T10:00:00+00:00"):
    return {"version": 1, "application_id": "digest", "revision": 1, "definition": definition(hourly()),
            "task": "Summarise", "inputs": {"team": "billing"},
            "trigger": {"kind": "schedule", "scheduled_for": scheduled_for}}


@pytest.fixture
def repository(tmp_path):
    return RunRepository(tmp_path / "runs.sqlite")


def test_a_new_schedule_gets_its_next_time_and_owner(repository):
    status = repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    assert status["active"] and not status["paused"] and status["owner"] == "Ana Ruiz"
    assert status["next_at"] == "2026-11-16T10:30:00+00:00", "an anchor of now: the first time is one interval on"
    again = repository.schedules.reconcile("digest", hourly(), owner="Someone Else", now=utc(2026, 11, 16, 9, 45))
    assert again["owner"] == "Ana Ruiz" and again["next_at"] == status["next_at"], "nothing changed, nothing moves"


def test_a_timing_change_reschedules_and_a_request_change_does_not(repository):
    first = repository.schedules.reconcile("digest", schedule(), owner="Ana Ruiz", now=NOW)
    assert first["next_at"] == "2026-11-16T17:30:00+00:00"
    same = repository.schedules.reconcile("digest", schedule(request={"task": "Other", "inputs": {"team": "ops"}}),
                                          owner=None, now=utc(2026, 11, 16, 10, 0))
    assert same["next_at"] == first["next_at"]
    moved = repository.schedules.reconcile("digest", schedule(times=["12:00"]), owner=None, now=utc(2026, 11, 16, 10, 0))
    assert moved["next_at"] == "2026-11-16T12:00:00+00:00"


def test_a_schedule_turned_off_or_deleted_stops(repository):
    repository.schedules.reconcile("digest", schedule(), owner="Ana Ruiz", now=NOW)
    off = repository.schedules.reconcile("digest", None, owner=None, now=NOW)
    assert off["active"] is False and off["next_at"] is None
    repository.schedules.forget("digest")
    assert repository.schedules.status("digest")["active"] is False
    assert repository.schedules.due(utc(2030, 1, 1, 0, 0)) == []


def test_due_lists_what_has_come_round(repository):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    assert repository.schedules.due(utc(2026, 11, 16, 10, 29)) == []
    due = repository.schedules.due(utc(2026, 11, 16, 10, 30))
    assert [(item["application_id"], item["next_at"]) for item in due] == [("digest", "2026-11-16T10:30:00+00:00")]


def test_a_time_is_claimed_and_its_run_created_exactly_once(repository, tmp_path):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    other = RunRepository(tmp_path / "runs.sqlite")  # a second process on the same store
    arguments = ("digest", "2026-11-16T10:30:00+00:00", "2026-11-16T11:30:00+00:00", request("2026-11-16T10:30:00+00:00"))
    first = repository.schedules.claim_and_create(*arguments)
    second = other.schedules.claim_and_create(*arguments)
    assert first and second is None
    assert repository.get(first)["request"]["trigger"]["scheduled_for"] == "2026-11-16T10:30:00+00:00"
    with closing(sqlite3.connect(repository.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM studio_runs").fetchone() == (1,)
    assert repository.schedules.status("digest")["next_at"] == "2026-11-16T11:30:00+00:00"
    assert repository.schedules.last_started_run("digest") == first


def test_a_claim_after_the_time_moved_starts_nothing(repository):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    assert repository.schedules.claim_and_create("digest", "2026-11-16T09:00:00+00:00", "2026-11-16T10:00:00+00:00",
                                                 request("2026-11-16T09:00:00+00:00")) is None
    with closing(sqlite3.connect(repository.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM studio_runs").fetchone() == (0,)


def test_a_claim_that_cannot_create_its_run_leaves_nothing_behind(repository):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    broken = request("2026-11-16T10:30:00+00:00")
    broken["task"] = ""
    with pytest.raises(ValueError):
        repository.schedules.claim_and_create("digest", "2026-11-16T10:30:00+00:00", "2026-11-16T11:30:00+00:00", broken)
    assert repository.schedules.status("digest")["recent"] == []
    assert repository.schedules.status("digest")["next_at"] == "2026-11-16T10:30:00+00:00"


def test_skipped_missed_and_failed_times_are_recorded(repository):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    repository.schedules.record("digest", "2026-11-16T10:30:00+00:00", "skipped", {"reason": "previous_run_active", "run_id": "r1"})
    repository.schedules.record("digest", "2026-11-16T11:30:00+00:00", "missed",
                                {"count": 3, "first": "2026-11-16T11:30:00+00:00", "last": "2026-11-16T13:30:00+00:00"})
    status = repository.schedules.status("digest")
    assert status["last"]["outcome"] == "missed" and status["last"]["detail"]["count"] == 3
    assert [slot["outcome"] for slot in status["recent"]] == ["missed", "skipped"], "newest first"
    with pytest.raises(ValueError):
        repository.schedules.record("digest", "2026-11-16T12:30:00+00:00", "exploded", {})


def test_pause_resume_and_turn_on_are_the_owners(repository):
    schedules = repository.schedules
    schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    with pytest.raises(ValueError, match="Only the schedule's owner"):
        schedules.pause("digest", by="Mallory")
    paused = schedules.pause("digest", by="Ana Ruiz")
    assert paused["paused"] and paused["next_at"] is None
    assert schedules.due(utc(2030, 1, 1, 0, 0)) == []
    resumed = schedules.resume("digest", hourly(), by="Ana Ruiz", now=utc(2026, 11, 17, 8, 10))
    assert not resumed["paused"] and resumed["next_at"] == "2026-11-17T08:30:00+00:00", "the next time from now, on the anchor"
    assert resumed["recent"] == [], "times passed while paused are not reported as missed"
    with pytest.raises(ValueError):
        schedules.turn_on("digest", hourly(), by="Mallory", now=NOW)


def test_a_schedule_with_no_owner_waits_until_someone_turns_it_on(repository):
    schedules = repository.schedules
    waiting = schedules.reconcile("digest", hourly(), owner=None, now=NOW)
    assert waiting["owner"] is None and waiting["waiting"] is True and waiting["next_at"] is None
    assert schedules.due(utc(2030, 1, 1, 0, 0)) == []
    on = schedules.turn_on("digest", hourly(), by="Ana Ruiz", now=NOW)
    assert on["owner"] == "Ana Ruiz" and on["waiting"] is False and on["next_at"]


def test_the_recent_list_is_capped(repository):
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    for hour in range(30):
        repository.schedules.record("digest", utc(2026, 11, 17, 0, 0).replace(hour=hour % 24, day=17 + hour // 24).isoformat(),
                                    "skipped", {"reason": "previous_run_active"})
    assert len(repository.schedules.status("digest")["recent"]) == 20


def test_the_migration_is_repeatable(tmp_path):
    RunRepository(tmp_path / "runs.sqlite")
    repository = RunRepository(tmp_path / "runs.sqlite")
    repository.schedules.reconcile("digest", hourly(), owner="Ana Ruiz", now=NOW)
    assert RunRepository(tmp_path / "runs.sqlite").schedules.status("digest")["owner"] == "Ana Ruiz"
