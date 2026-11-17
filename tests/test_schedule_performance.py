"""Step 9 — performance fixes for scheduled applications (P1–P4; P5 is in
studio/scripts/check-schedules.mjs).

P1: a tick with nothing to do costs the same however many applications there are.
P2: "is the previous run still going" does not load that run's events.
P3: the server reports start-up at once; tenants are started behind it.
P4: a long outage is counted, not listed, and catch-up runs the latest time.
"""

import asyncio
from contextlib import contextmanager
from datetime import timedelta

import pytest

from schedule_support import Clock, definition, hourly, utc
from conclave import ToolRegistry
from conclave.api import TenantAPIs
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider

START = utc(2026, 11, 16, 9, 30)


@contextmanager
def counted(repository):
    count = {"connections": 0, "writes": 0}
    original = repository._connect

    @contextmanager
    def counting():
        count["connections"] += 1
        with original() as connection:
            before = connection.total_changes
            yield connection
            if connection.total_changes != before:
                count["writes"] += 1

    repository._connect = counting
    try:
        yield count
    finally:
        repository._connect = original


def scheduler(tmp_path, *, scheduled=1, manual=0, block=None, clock=None):
    from conclave.application.scheduler import ScheduleService

    workspace = Workspace(tmp_path / "applications.sqlite")
    for _ in range(scheduled):
        workspace.save(definition(block or hourly()))
    for _ in range(manual):
        workspace.save(definition(None, start_mode="manual"))
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), ToolRegistry(20000), reviewer="Ana Ruiz")
    return ScheduleService(host, workspace, clock=clock or Clock(START))


# --- P1 ----------------------------------------------------------------------------


@pytest.mark.parametrize("scheduled, manual", [(0, 0), (50, 0), (0, 200), (50, 150)])
def test_a_tick_with_nothing_to_do_costs_the_same_for_any_number_of_applications(tmp_path, scheduled, manual):
    service = scheduler(tmp_path, scheduled=scheduled, manual=manual)

    async def exercise():
        await service.tick()  # the first pass records the schedules
        service.clock.advance(seconds=30)
        with counted(service.host.repository) as count:
            assert await service.tick() == []
        await service.host.close()
        return count

    count = asyncio.run(exercise())
    assert count["writes"] == 0, count
    assert count["connections"] <= 3, count


def test_a_tick_still_notices_a_schedule_changed_outside_the_api(tmp_path):
    service = scheduler(tmp_path)

    async def exercise():
        await service.tick()
        saved = service.workspace.applications()[0]
        service.workspace.save(definition(hourly(every_minutes=15)), identifier=saved["id"], revision=saved["revision"])
        service.clock.advance(seconds=30)
        await service.tick()
        assert service.store.status(saved["id"])["next_at"] == "2026-11-16T09:45:30+00:00"
        await service.host.close()

    asyncio.run(exercise())


# --- P2 ----------------------------------------------------------------------------


@pytest.mark.parametrize("status, unsettled, going", [
    ("running", 0, True), ("waiting_for_human", 0, True), ("completed", 0, False),
    ("interrupted", 1, True), ("interrupted", 0, False)])
def test_still_going_does_not_load_the_previous_run(tmp_path, monkeypatch, status, unsettled, going):
    service = scheduler(tmp_path)
    repository = service.host.repository
    from test_studio_runs import request
    run_id = repository.create(request())
    state = {"status": status, "actions": {"to_check": 0, "needs_decision": unsettled, "unknown": 0}}
    monkeypatch.setattr(repository, "run_state", lambda identifier: state if identifier == run_id else None, raising=False)

    def whole_run(*args, **kwargs):
        raise AssertionError("the whole run was loaded")

    monkeypatch.setattr(repository, "get", whole_run)
    assert service.still_going(run_id) is going
    asyncio.run(service.host.close())


def test_the_run_state_is_read_without_its_events(tmp_path):
    from test_studio_runs import request

    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(request())
    repository._claim(run_id)
    for _ in range(50):
        repository._append(run_id, {"type": "AgentMessage", "agent": "writer", "content": "x"})
    assert repository.run_state(run_id) == {"status": "running", "actions": {"to_check": 0, "needs_decision": 0, "unknown": 0}}
    assert repository.run_state("missing") is None


# --- P3 ----------------------------------------------------------------------------


def test_start_up_is_reported_before_the_tenants_are_started(tmp_path):
    from conclave.asgi import application

    release, calls = asyncio.Event(), []

    class Tenants(TenantAPIs):
        async def start_schedules(self):
            calls.append("scan")
            await release.wait()
            calls.append("scanned")

        async def close(self):
            calls.append("close")

    async def exercise():
        app = application(apis=Tenants(tmp_path), token="t")
        inbox, sent = asyncio.Queue(), []

        async def send(message):
            sent.append(message["type"])

        serving = asyncio.create_task(app({"type": "lifespan"}, inbox.get, send))
        await inbox.put({"type": "lifespan.startup"})
        async with asyncio.timeout(2):
            while "lifespan.startup.complete" not in sent:
                await asyncio.sleep(0.01)
        assert calls == ["scan"], "start-up completes while the tenants are still being started"
        await inbox.put({"type": "lifespan.shutdown"})
        async with asyncio.timeout(2):
            await serving
        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert calls == ["scan", "close"], "shutdown stops an unfinished scan, then closes"

    asyncio.run(exercise())


# --- P4 ----------------------------------------------------------------------------


def test_the_latest_time_and_the_count_are_worked_out_not_listed():
    from conclave.application.schedules import count_between, previous_time

    every_five = hourly(every_minutes=5)
    end = START + timedelta(days=365)
    assert previous_time(every_five, end + timedelta(minutes=3), START) == end
    assert count_between(every_five, START, end, START) == 365 * 24 * 12
    from schedule_support import schedule
    block = schedule()  # weekly: Mon, Wed, Fri at 09:00 and 17:30, Europe/London
    assert count_between(block, utc(2026, 11, 16, 0, 0), utc(2026, 11, 23, 0, 0), None) == 6
    assert previous_time(block, utc(2026, 11, 19, 12, 0), None) == utc(2026, 11, 18, 17, 30)


def test_a_long_outage_reports_the_true_count(tmp_path):
    clock = Clock(START)
    service = scheduler(tmp_path, block=hourly(every_minutes=5), clock=clock)

    async def exercise():
        await service.tick()
        clock.now = START + timedelta(days=30, minutes=1)
        outcomes = await service.tick()
        assert [item["outcome"] for item in outcomes] == ["missed", "started"]
        app = service.workspace.applications()[0]["id"]
        missed = next(slot for slot in service.store.status(app)["recent"] if slot["outcome"] == "missed")
        assert missed["detail"]["count"] == 30 * 24 * 12 - 1
        await asyncio.sleep(0.05)
        await service.host.close()

    asyncio.run(exercise())


def test_catching_up_after_a_long_outage_runs_the_latest_time(tmp_path):
    clock = Clock(START)
    service = scheduler(tmp_path, block=hourly(every_minutes=5, catch_up="latest"), clock=clock)

    async def exercise():
        await service.tick()
        clock.now = START + timedelta(days=30, minutes=4)
        outcomes = await service.tick()
        started = next(item for item in outcomes if item["outcome"] == "started")
        assert started["scheduled_for"] == (START + timedelta(days=30)).isoformat()
        app = service.workspace.applications()[0]["id"]
        missed = next(slot for slot in service.store.status(app)["recent"] if slot["outcome"] == "missed")
        assert missed["detail"]["count"] == 30 * 24 * 12 - 1
        await asyncio.sleep(0.05)
        await service.host.close()

    asyncio.run(exercise())
