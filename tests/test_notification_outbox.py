"""N3 — the outbox: one notice per waiting point, written in the same
transaction as the event that makes the run wait."""

import asyncio
import sqlite3

import pytest

from conclave import ToolRegistry
from notification_support import provider, run_request, slack


def collect(stream):
    async def drain():
        return [event async for event in stream]
    return asyncio.run(drain())


def repository(tmp_path):
    from conclave.application.runs import RunRepository
    return RunRepository(tmp_path / "runs.sqlite")


def waiting(stage="review"):
    return {"type": "ApplicationFinished", "status": "waiting_for_human", "reason": "Host human input handler is required",
            "stage": stage, "state": {}, "views": [], "checkpoint": {"version": 1, "patches": {}}}


def started(store, block=slack()):
    run_id = store.create(run_request(block))
    store._claim(run_id)
    return run_id


def last_sequence(store, run_id):
    return store.get(run_id)["events"][-1]["sequence"]


def test_a_review_stop_leaves_one_notice_due(tmp_path):
    store = repository(tmp_path)
    run_id = store.create(run_request(slack()))
    collect(store.execute(run_id, provider(), ToolRegistry(20000)))
    record = store.get(run_id)
    assert record["status"] == "waiting_for_human"
    stop = next(row for row in record["events"] if row["event"]["type"] == "ApplicationFinished")
    notices = store.notices.due(run_id)
    assert [(item["run_id"], item["sequence"], item["state"]) for item in notices] == [(run_id, stop["sequence"], "due")]


def test_a_pause_leaves_one_notice_due_and_names_the_next_step(tmp_path):
    store = repository(tmp_path)
    run_id = store.create(run_request(slack()))
    collect(store.execute(run_id, provider(), ToolRegistry(20000), pause_requested=lambda: True))
    stop = store.get(run_id)["events"][-1]
    assert stop["event"]["status"] == "paused" and stop["event"]["next_stage"] == "write"
    assert [item["sequence"] for item in store.notices.due(run_id)] == [stop["sequence"]]


def test_a_run_without_the_setting_records_exactly_what_it_did_before(tmp_path):
    store = repository(tmp_path)
    plain, noticed = store.create(run_request(None)), store.create(run_request(slack()))
    collect(store.execute(plain, provider(), ToolRegistry(20000)))
    collect(store.execute(noticed, provider(), ToolRegistry(20000)))
    assert store.notices.due(plain) == [] and len(store.notices.due(noticed)) == 1
    shape = lambda run_id: [(row["event"]["type"], row["event"].get("status")) for row in store.get(run_id)["events"]]  # noqa: E731
    assert shape(plain) == shape(noticed), "the setting adds a notice, not an event, when the run stops"


def test_the_notice_and_its_waiting_point_are_written_together(tmp_path):
    store = repository(tmp_path)
    run_id = started(store)
    with sqlite3.connect(tmp_path / "runs.sqlite") as connection:
        connection.execute("CREATE TRIGGER refuse BEFORE INSERT ON studio_run_notices BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.Error):
        store._append(run_id, waiting())
    record = store.get(run_id)
    assert record["status"] == "running", "the waiting point was not recorded without its notice"
    assert not any(row["event"]["type"] == "ApplicationFinished" for row in record["events"])
    assert store.notices.due(run_id) == []


def test_waiting_again_after_resuming_is_a_new_notice(tmp_path):
    store = repository(tmp_path)
    run_id = started(store)
    store._append(run_id, waiting())
    first = last_sequence(store, run_id)
    store._append(run_id, {"type": "ApplicationResumed", "name": "Support digest", "state": {}, "views": []})
    store._append(run_id, waiting("send"))
    second = last_sequence(store, run_id)
    assert [item["sequence"] for item in store.notices.due(run_id)] == [first, second]


def test_a_notice_is_claimed_once_even_by_two_processes(tmp_path):
    from conclave.application.runs import RunRepository

    store = repository(tmp_path)
    run_id = started(store)
    store._append(run_id, waiting())
    sequence = last_sequence(store, run_id)
    other = RunRepository(tmp_path / "runs.sqlite")
    assert store.notices.claim(run_id, sequence) is True
    assert other.notices.claim(run_id, sequence) is False
    assert store.notices.claim(run_id, sequence) is False
    assert store.notices.due(run_id) == []


def test_recording_the_outcome_adds_an_event_and_never_changes_the_status(tmp_path):
    store = repository(tmp_path)
    run_id = started(store)
    store._append(run_id, waiting())
    sequence = last_sequence(store, run_id)
    store.notices.claim(run_id, sequence)
    detail = {"via": "slack", "target": "#reviews", "step": {"id": "review", "name": "Review the digest"}}
    store.notices.finish(run_id, sequence, "sent", detail)
    record = store.get(run_id)
    assert record["status"] == "waiting_for_human"
    assert record["events"][-1]["event"] | {"timestamp": None} == {
        "type": "NotificationSent", "waiting_sequence": sequence, **detail, "timestamp": None}
    assert store.notices.state(run_id, sequence) == "sent"
    store.notices.finish(run_id, sequence, "failed", {**detail, "reason": "late"})
    assert store.notices.state(run_id, sequence) == "sent", "an outcome is recorded once"
    assert sum(row["event"]["type"].startswith("Notification") for row in store.get(run_id)["events"]) == 1


def test_still_waiting_means_that_waiting_point_is_the_run_s_latest_stop(tmp_path):
    store = repository(tmp_path)
    run_id = started(store)
    store._append(run_id, waiting())
    sequence = last_sequence(store, run_id)
    assert store.notices.still_waiting(run_id, sequence) is True
    store._append(run_id, {"type": "ApplicationFinished", "status": "cancelled", "reason": "Stopped"})
    assert store.notices.still_waiting(run_id, sequence) is False
