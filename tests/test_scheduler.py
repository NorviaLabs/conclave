"""C5 — the scheduler: each due time starts one run, or says why it did not.

Time is a Clock the test moves; nothing here waits on the wall clock.
"""

import asyncio

import pytest

from actions_support import Recorder, note, registry
from schedule_support import Clock, definition, hourly, schedule, utc
from conclave import ToolRegistry
from conclave.application.principal import Principal
from conclave.application.profile import HOSTED
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider

DONE = {"type": "final", "content": "Done", "text": "Digest"}


def provider(*script):
    return ScriptedProvider({"writer": list(script) or [DONE]})


class World:
    """One tenant's store, workspace, host and scheduler, on a hand-moved clock."""

    def __init__(self, tmp_path, loop, *, block=None, start=utc(2026, 11, 16, 9, 30), tools=None, script=(), profile=None,
                 provider_=None, autonomous=False, tool_names=()):
        from conclave.application.scheduler import ScheduleService

        self.tmp_path, self.clock, self.loop = tmp_path, Clock(start), loop
        self.workspace = Workspace(tmp_path / "applications.sqlite")
        self.saved = self.workspace.save(definition(block or hourly(), tools=tool_names, autonomous=autonomous))
        options = {"profile": profile} if profile else {}
        self.host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"),
                                 provider_ if provider_ is not None else provider(*script),
                                 tools or ToolRegistry(20000), reviewer="Ana Ruiz", **options)
        self.service = ScheduleService(self.host, self.workspace, clock=self.clock)

    @property
    def repository(self):
        return self.host.repository

    @property
    def app(self):
        return self.saved["id"]

    def tick(self):
        return self.loop.run_until_complete(self.service.tick())

    def status(self):
        return self.repository.schedules.status(self.app)

    def runs(self):
        return self.repository.list(self.app)

    def settle(self):
        async def wait():
            async with asyncio.timeout(5):
                while any(run["status"] in {"pending", "running"} for run in self.runs()):
                    await asyncio.sleep(0.01)
        self.loop.run_until_complete(wait())

    def close(self):
        self.loop.run_until_complete(self.host.close())


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def world(tmp_path, loop):
    made = []

    def build(**options):
        made.append(World(tmp_path, loop, **options))
        return made[-1]

    yield build
    for item in made:
        item.close()


def test_a_due_time_starts_one_run_with_the_saved_request(world):
    w = world()
    w.tick()  # the schedule is reconciled: hourly from 09:30
    assert w.status()["next_at"] == "2026-11-16T10:30:00+00:00" and w.runs() == []
    w.clock.now = utc(2026, 11, 16, 10, 30, 10)
    outcomes = w.tick()
    assert [item["outcome"] for item in outcomes] == ["started"]
    w.settle()
    runs = w.runs()
    assert len(runs) == 1 and runs[0]["status"] == "completed"
    record = w.repository.get(runs[0]["id"])
    assert record["request"]["task"] == "Summarise yesterday's support tickets"
    assert record["request"]["inputs"] == {"team": "billing"}
    assert record["request"]["revision"] == w.saved["revision"]
    assert record["request"]["trigger"] == {"kind": "schedule", "scheduled_for": "2026-11-16T10:30:00+00:00"}
    assert w.status()["next_at"] == "2026-11-16T11:30:00+00:00"
    assert w.tick() == [] and len(w.runs()) == 1, "ticking again in the same minute starts nothing"


def test_the_latest_saved_revision_is_what_runs(world):
    w = world()
    w.tick()
    changed = definition(hourly(request={"task": "Summarise this week's tickets", "inputs": {"team": "ops"}}))
    w.saved = w.workspace.save(changed, identifier=w.app, revision=w.saved["revision"])
    w.clock.now = utc(2026, 11, 16, 10, 30, 5)
    w.tick()
    w.settle()
    request = w.repository.get(w.runs()[0]["id"])["request"]
    assert request["revision"] == 2 and request["task"] == "Summarise this week's tickets" and request["inputs"] == {"team": "ops"}


def test_two_schedulers_on_one_store_start_one_run(world, tmp_path):
    from conclave.application.scheduler import ScheduleService

    w = world()
    w.tick()
    twin = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer="Ana Ruiz")
    other = ScheduleService(twin, w.workspace, clock=w.clock)
    w.clock.now = utc(2026, 11, 16, 10, 30, 1)

    async def both():
        return await asyncio.gather(w.service.tick(), other.tick())

    results = w.loop.run_until_complete(both())
    assert sum(item["outcome"] == "started" for outcome in results for item in outcome) == 1
    w.settle()
    assert len(w.runs()) == 1
    w.loop.run_until_complete(twin.close())


def test_a_restart_does_not_start_a_claimed_time_again(world, tmp_path):
    from conclave.application.scheduler import ScheduleService

    w = world()
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 30, 2)
    w.tick()
    w.settle()
    restarted = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer="Ana Ruiz")
    again = ScheduleService(restarted, w.workspace, clock=w.clock)
    w.clock.now = utc(2026, 11, 16, 10, 31, 0)
    assert w.loop.run_until_complete(again.tick()) == []
    assert len(w.runs()) == 1
    w.loop.run_until_complete(restarted.close())


def test_times_missed_while_the_host_was_down_are_reported_not_run(world):
    w = world()
    w.tick()
    w.clock.now = utc(2026, 11, 16, 13, 0)  # 10:30, 11:30 and 12:30 passed while nothing ran
    outcomes = w.tick()
    assert [item["outcome"] for item in outcomes] == ["missed"]
    assert w.runs() == []
    last = w.status()["last"]
    assert last["outcome"] == "missed" and last["detail"]["count"] == 3
    assert (last["detail"]["first"], last["detail"]["last"]) == ("2026-11-16T10:30:00+00:00", "2026-11-16T12:30:00+00:00")
    assert w.status()["next_at"] == "2026-11-16T13:30:00+00:00"


def test_catching_up_runs_only_the_latest_missed_time(world):
    w = world(block=hourly(catch_up="latest"))
    w.tick()
    w.clock.now = utc(2026, 11, 16, 13, 0)
    outcomes = sorted(item["outcome"] for item in w.tick())
    assert outcomes == ["missed", "started"]
    w.settle()
    runs = w.runs()
    assert len(runs) == 1
    assert w.repository.get(runs[0]["id"])["request"]["trigger"]["scheduled_for"] == "2026-11-16T12:30:00+00:00"
    missed = next(slot for slot in w.status()["recent"] if slot["outcome"] == "missed")
    assert missed["detail"]["count"] == 2 and missed["detail"]["last"] == "2026-11-16T11:30:00+00:00"


def test_a_little_lateness_is_not_a_miss(world):
    w = world()
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 31, 55)  # under two minutes late
    assert [item["outcome"] for item in w.tick()] == ["started"]
    w.settle()


@pytest.mark.parametrize("state", ["running", "waiting_for_human", "paused", "unsettled"])
def test_a_time_is_skipped_while_the_previous_scheduled_run_is_still_going(world, state):
    w = world()
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    w.tick()
    w.settle()
    previous = w.runs()[0]["id"]
    with w.repository._connect() as connection:
        connection.execute("UPDATE studio_runs SET status = ? WHERE id = ?",
                           ("interrupted" if state == "unsettled" else state, previous))
    if state == "unsettled":
        from conclave.actions import ActionStart
        w.repository.journal.begin(previous, ActionStart("a1", "write", "writer", "send_note", {}, "approved", [], False), budget={})
        w.repository.journal.finish("a1", "uncertain", frames=[], budget={})
    w.clock.now = utc(2026, 11, 16, 11, 30, 3)
    outcomes = w.tick()
    assert [item["outcome"] for item in outcomes] == ["skipped"]
    last = w.status()["last"]
    assert last["detail"] == {"reason": "previous_run_active", "run_id": previous}
    assert len(w.runs()) == 1
    assert w.status()["next_at"] == "2026-11-16T12:30:00+00:00"


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled", "stopped"])
def test_a_finished_previous_run_does_not_block(world, state):
    w = world()
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    w.tick()
    w.settle()
    with w.repository._connect() as connection:
        connection.execute("UPDATE studio_runs SET status = ? WHERE id = ?", (state, w.runs()[0]["id"]))
    w.clock.now = utc(2026, 11, 16, 11, 30, 3)
    assert [item["outcome"] for item in w.tick()] == ["started"]
    w.settle()


def test_a_manual_run_does_not_block_a_scheduled_one(world):
    w = world()
    w.tick()
    manual = w.repository.create({"version": 1, "application_id": w.app, "revision": 1, "definition": w.saved["definition"],
                                  "task": "By hand", "inputs": {"team": "billing"}})
    with w.repository._connect() as connection:
        connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (manual,))
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    assert [item["outcome"] for item in w.tick()] == ["started"]


def test_inputs_that_no_longer_match_fail_that_time_and_the_schedule_carries_on(world):
    w = world()
    w.tick()
    changed = definition(hourly())
    changed["inputs"].append({"id": "region", "label": "Region", "type": "text", "required": True})
    # Saved the way an older host (or a direct edit) could: the schedule's inputs lack Region.
    with w.workspace.connect() as connection:
        connection.execute("UPDATE applications SET definition = ? WHERE id = ?", (__import__("json").dumps(changed), w.app))
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    assert [item["outcome"] for item in w.tick()] == ["failed"]
    last = w.status()["last"]
    assert "Region" in last["detail"]["reason"] and w.runs() == []
    assert w.status()["next_at"] == "2026-11-16T11:30:00+00:00"


def test_a_host_without_a_model_backend_fails_the_time_rather_than_the_run(world):
    w = world(provider_=False)
    w.host.provider = None
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    assert [item["outcome"] for item in w.tick()] == ["failed"]
    assert "model backend" in w.status()["last"]["detail"]["reason"] and w.runs() == []


@pytest.mark.parametrize("autonomous, sent", [(False, False), (True, True)])
def test_nobody_approves_writes_on_a_schedule(world, autonomous, sent):
    recorder = Recorder()
    w = world(tools=registry(recorder), script=(note(), DONE), autonomous=autonomous, tool_names=("send_note",))
    w.tick()
    w.clock.now = utc(2026, 11, 16, 10, 30, 3)
    w.tick()
    w.settle()
    assert bool(recorder.calls) is sent
    assert w.runs()[0]["status"] == "completed", "a refused write is reported to the agent, not left waiting"


def test_a_paused_schedule_starts_nothing_and_resumes_from_now(world):
    w = world()
    w.tick()
    w.repository.schedules.pause(w.app, by="Ana Ruiz")
    w.clock.now = utc(2026, 11, 16, 14, 0)
    assert w.tick() == [] and w.runs() == []
    w.repository.schedules.resume(w.app, hourly(), by="Ana Ruiz", now=w.clock.now)
    assert w.status()["next_at"] == "2026-11-16T14:30:00+00:00"
    assert w.status()["recent"] == []


def test_a_hosted_schedule_with_no_owner_waits(world):
    w = world(profile=HOSTED)
    w.tick()
    assert w.status()["waiting"] is True
    w.clock.now = utc(2026, 11, 16, 12, 0)
    assert w.tick() == [] and w.runs() == []
    w.repository.schedules.turn_on(w.app, hourly(), by="Ana Ruiz", now=w.clock.now)
    w.clock.now = utc(2026, 11, 16, 12, 30, 2)
    assert [item["outcome"] for item in w.tick()] == ["started"]
    w.settle()


def test_a_run_acts_as_the_schedules_owner(world):
    from cryptography.fernet import Fernet
    from conclave.integrations.connections import ConnectionStore

    w = world(profile=HOSTED)
    w.host.connections = ConnectionStore(w.tmp_path / "connections.sqlite", Fernet.generate_key())
    w.tick()
    w.repository.schedules.turn_on(w.app, hourly(), by="Bea Owner", now=w.clock.now)
    w.clock.now = utc(2026, 11, 16, 10, 30, 2)
    w.tick()
    w.settle()
    assert w.host.connections.run_owner(w.runs()[0]["id"], w.host.principal.tenant) == "Bea Owner"


def test_saving_through_the_api_records_who_scheduled_it(tmp_path):
    from conclave.api import ApplicationAPI

    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer="host", profile=HOSTED)
        api = ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": definition(hourly())},
                                           principal=Principal("Ana Ruiz"))
        assert status == 201
        assert host.repository.schedules.status(saved["id"])["owner"] == "Ana Ruiz"
        await host.close()

    asyncio.run(exercise())


def test_an_edit_to_the_timing_reschedules_and_to_the_request_does_not(world):
    w = world()
    w.tick()
    before = w.status()["next_at"]
    w.saved = w.workspace.save(definition(hourly(request={"task": "Other", "inputs": {"team": "ops"}})),
                               identifier=w.app, revision=w.saved["revision"])
    w.clock.now = utc(2026, 11, 16, 9, 40)
    w.tick()
    assert w.status()["next_at"] == before
    w.saved = w.workspace.save(definition(hourly(every_minutes=15)), identifier=w.app, revision=w.saved["revision"])
    w.tick()
    assert w.status()["next_at"] == "2026-11-16T09:55:00+00:00"


def test_a_deleted_or_manual_application_stops_its_schedule(world):
    w = world()
    w.tick()
    w.saved = w.workspace.save(definition(None, start_mode="manual"), identifier=w.app, revision=w.saved["revision"])
    w.tick()
    assert w.status()["active"] is False and w.status()["next_at"] is None
    w.workspace.delete(w.app, w.saved["revision"])
    w.clock.now = utc(2026, 11, 17, 0, 0)
    assert w.tick() == [] and w.runs() == []


def test_the_service_sleeps_until_the_next_time_but_never_long(world):
    w = world()
    w.tick()
    from conclave.application.scheduler import ScheduleService

    assert w.service.seconds_until_next() == pytest.approx(30)  # 10:30 is an hour away: look again in 30s
    w.clock.now = utc(2026, 11, 16, 10, 29, 50)
    assert w.service.seconds_until_next() == pytest.approx(10)
    assert ScheduleService.MISSED_AFTER.total_seconds() == 120
