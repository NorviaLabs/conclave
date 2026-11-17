"""C4 — a run says whether a schedule started it, and for which time."""

import copy

import pytest

from schedule_support import definition, hourly
from conclave.application.runs import RunRepository, validate_run_request

BASE = {"version": 1, "application_id": "digest", "revision": 1, "definition": definition(hourly()),
        "task": "Summarise", "inputs": {"team": "billing"}}


def test_a_scheduled_request_keeps_its_trigger(tmp_path):
    value = {**copy.deepcopy(BASE), "trigger": {"kind": "schedule", "scheduled_for": "2026-11-16T10:00:00+00:00"}}
    assert validate_run_request(value)["trigger"] == value["trigger"]
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value)
    assert repository.get(run_id)["request"]["trigger"]["scheduled_for"] == "2026-11-16T10:00:00+00:00"


@pytest.mark.parametrize("trigger", [
    {"kind": "manual"}, {"kind": "schedule"}, {"kind": "schedule", "scheduled_for": "tomorrow"},
    {"kind": "schedule", "scheduled_for": "2026-11-16T10:00:00"}, {"kind": "schedule", "scheduled_for": "2026-11-16T10:00:00+00:00", "x": 1},
    "schedule", None,
])
def test_only_a_well_formed_schedule_trigger_is_accepted(trigger):
    with pytest.raises(ValueError, match="trigger"):
        validate_run_request({**copy.deepcopy(BASE), "trigger": trigger})


def test_history_says_how_each_run_started(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    manual = repository.create(copy.deepcopy(BASE))
    scheduled = repository.create({**copy.deepcopy(BASE), "trigger": {"kind": "schedule", "scheduled_for": "2026-11-16T10:00:00+00:00"}})
    rows = {row["id"]: row for row in repository.list("digest")}
    assert rows[manual]["trigger"] == {"kind": "manual"}
    assert rows[scheduled]["trigger"] == {"kind": "schedule", "scheduled_for": "2026-11-16T10:00:00+00:00"}
