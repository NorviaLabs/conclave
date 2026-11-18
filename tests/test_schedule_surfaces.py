"""C6 and C7 — serving runs the scheduler, and REST, the preview and the CLI
show and pause it.
"""

import asyncio
import json

import pytest

from schedule_support import definition, hourly, schedule, utc
from conclave import ToolRegistry
from conclave.api import ApplicationAPI, TenantAPIs
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider, share


def api_for(tmp_path, **host_options):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), ToolRegistry(20000),
                        reviewer="Ana Ruiz", **host_options)
    return ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))


async def saved_schedule(api, block=None, principal=None):
    status, saved = await api.dispatch("POST", "/api/applications", {"definition": definition(block or hourly())},
                                       principal=principal or Principal("Ana Ruiz"))
    assert status == 201
    return saved["id"]


# --- C6: serving ---------------------------------------------------------------


def test_the_runtime_reports_schedules_only_while_a_scheduler_runs(tmp_path):
    async def exercise():
        api = api_for(tmp_path)
        assert (await api.dispatch("GET", "/api/runtime"))[1]["features"]["schedules"] is False
        await api.start_schedules()
        assert (await api.dispatch("GET", "/api/runtime"))[1]["features"]["schedules"] is True
        await api.stop_schedules()
        assert (await api.dispatch("GET", "/api/runtime"))[1]["features"]["schedules"] is False
        await api.host.close()

    asyncio.run(exercise())


def test_a_running_scheduler_is_woken_by_a_save(tmp_path):
    async def exercise():
        api = api_for(tmp_path)
        await api.start_schedules()
        application = await saved_schedule(api)
        async with asyncio.timeout(3):
            while api.host.repository.schedules.status(application)["next_at"] is None:
                await asyncio.sleep(0.01)
        await api.stop_schedules()
        await api.host.close()

    asyncio.run(exercise())


def tenant_store(root, tenant, *, scheduled):
    from conclave.application.storage import tenant_paths

    paths = tenant_paths(root, tenant)
    paths.root.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(paths.applications)
    saved = workspace.save(definition(hourly()) if scheduled else definition(None, start_mode="manual"))
    repository = RunRepository(paths.runs)
    if scheduled:
        repository.schedules.reconcile(saved["id"], hourly(), owner="Ana Ruiz", now=utc(2026, 11, 16, 9, 30))
    return saved["id"]


def test_startup_runs_every_tenants_schedules_without_waiting_for_a_visit(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    tenant_store(tmp_path, "acme", scheduled=True)
    tenant_store(tmp_path, "quiet", scheduled=False)

    async def exercise():
        tenants = TenantAPIs(tmp_path)
        await tenants.start_schedules()
        assert set(tenants.apis) == {"acme"}, "only the tenant with a schedule is started"
        assert tenants.apis["acme"].host.schedules is not None
        await tenants.close()
        assert tenants.apis == {}

    asyncio.run(exercise())


def test_a_tenant_first_seen_after_startup_still_runs_its_schedules(tmp_path, monkeypatch):
    """A tenant with no schedule at startup can make one later, and it runs.

    Studio offers "On a schedule" only when the runtime reports schedules, so a
    tenant whose service was built without a scheduler could never make one.
    """
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))

    def factory(*, workspace=None, principal=None):
        return api_for(tmp_path / principal.tenant)

    async def exercise():
        tenants = TenantAPIs(tmp_path, factory=factory)
        await tenants.start_schedules()
        assert tenants.apis == {}
        service = tenants.for_principal(Principal("Ana Ruiz", "newco"))
        assert service.host.schedules is not None
        assert (await service.dispatch("GET", "/api/runtime"))[1]["features"]["schedules"] is True
        await tenants.close()
        assert service.host.schedules is None

    for tenant in ("newco",):
        (tmp_path / tenant).mkdir()
    asyncio.run(exercise())


def test_without_lifespan_a_tenants_schedules_start_with_its_first_request(tmp_path):
    from conclave.asgi import application
    from test_asgi import call

    def factory(*, workspace=None, principal=None):
        return api_for(tmp_path)

    async def exercise():
        tenants = TenantAPIs(tmp_path, factory=factory)
        app = application(apis=tenants, token="t")
        status, _, body = await call(app, path="/api/runtime", headers=[("authorization", "Bearer t")])
        assert status == 200 and json.loads(body)["features"]["schedules"] is True
        await tenants.close()

    asyncio.run(exercise())


def test_the_asgi_app_starts_and_stops_schedules_on_lifespan(tmp_path):
    from conclave.asgi import application

    calls = []

    class Tenants(TenantAPIs):
        async def start_schedules(self):
            calls.append("start")

        async def close(self):
            calls.append("stop")

    app = application(apis=Tenants(tmp_path), token="t")
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent = []

    async def receive():
        # A real server waits between start-up and shutdown; the tenants are
        # started in the background meanwhile (performance review P3).
        await asyncio.sleep(0)
        return next(messages)

    async def send(message):
        sent.append(message["type"])

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert calls == ["start", "stop"]
    with pytest.raises(RuntimeError):
        asyncio.run(app({"type": "websocket"}, receive, send))


# --- C7: REST and preview ----------------------------------------------------------


def test_the_schedule_status_says_when_it_runs_next(tmp_path):
    async def exercise():
        api = api_for(tmp_path)
        application = await saved_schedule(api, schedule())
        # Saving used the real clock; start the record again from a fixed "now".
        api.host.repository.schedules.forget(application)
        api.host.repository.schedules.reconcile(application, schedule(), owner="Ana Ruiz", now=utc(2026, 11, 16, 12, 0))
        status, body = await api.dispatch("GET", f"/api/applications/{application}/schedule")
        assert status == 200
        assert body["active"] is True and body["paused"] is False and body["owner"] == "Ana Ruiz"
        assert body["time_zone"] == "Europe/London"
        assert body["next_at"] == "2026-11-16T17:30:00+00:00" and body["next_local"] == "Mon 16 Nov, 17:30"
        assert body["last"] is None and body["recent"] == []
        assert (await api.dispatch("GET", "/api/applications/missing/schedule"))[0] == 404
        await api.host.close()

    asyncio.run(exercise())


def test_only_the_owner_pauses_resumes_or_turns_on(tmp_path):
    async def exercise():
        api = api_for(tmp_path)
        application = await saved_schedule(api)
        share(api, application, "Mallory")  # she may edit it, but the schedule is Ana's
        route = f"/api/applications/{application}/schedule"
        assert (await api.dispatch("POST", route, {"action": "pause"}, principal=Principal("Mallory")))[0] == 400
        status, body = await api.dispatch("POST", route, {"action": "pause"}, principal=Principal("Ana Ruiz"))
        assert status == 200 and body["paused"] is True and body["next_at"] is None
        assert (await api.dispatch("POST", route, {"action": "pause"}, principal=Principal("Ana Ruiz")))[0] == 409
        status, body = await api.dispatch("POST", route, {"action": "resume"}, principal=Principal("Ana Ruiz"))
        assert status == 200 and body["paused"] is False and body["next_at"]
        assert (await api.dispatch("POST", route, {"action": "turn_on"}, principal=Principal("Ana Ruiz")))[0] == 409
        for bad in ({"action": "delete"}, {"action": "pause", "by": "Mallory"}, {}):
            assert (await api.dispatch("POST", route, bad, principal=Principal("Ana Ruiz")))[0] == 400
        await api.host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("block, expected", [
    (schedule(), ["Mon 16 Nov, 17:30", "Wed 18 Nov, 09:00", "Wed 18 Nov, 17:30"]),
    (schedule(kind="daily", days=None, times=["09:00"]), ["Tue 17 Nov, 09:00", "Wed 18 Nov, 09:00", "Thu 19 Nov, 09:00"]),
    (schedule(kind="interval", days=None, times=None, every_minutes=120, starts_at="2026-11-17T09:00"),
     ["Tue 17 Nov, 09:00", "Tue 17 Nov, 11:00", "Tue 17 Nov, 13:00"]),
])
def test_the_preview_shows_the_next_three_times(tmp_path, block, expected):
    async def exercise():
        api = api_for(tmp_path)
        status, body = await api.dispatch("POST", "/api/schedules/preview",
                                          {"schedule": block, "definition": definition(None), "after": "2026-11-16T12:00:00+00:00"})
        assert status == 200
        assert [item["local"] for item in body["times"]] == expected
        assert all(item["time_zone"] == "Europe/London" and item["at"].endswith("+00:00") for item in body["times"])
        await api.host.close()

    asyncio.run(exercise())


def test_the_preview_says_what_is_wrong(tmp_path):
    async def exercise():
        api = api_for(tmp_path)
        status, body = await api.dispatch("POST", "/api/schedules/preview",
                                          {"schedule": hourly(every_minutes=2), "definition": definition(None)})
        assert status == 400 and "every 5 minutes" in body["error"]
        await api.host.close()

    asyncio.run(exercise())


# --- C7: the terminal -------------------------------------------------------------


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    from applications import cli

    api = api_for(tmp_path)
    application = asyncio.run(saved_schedule(api))
    monkeypatch.setattr(cli, "configured_api", lambda **kwargs: api_for(tmp_path))
    return cli, application


def test_the_terminal_shows_and_pauses_a_schedule(terminal, capsys):
    cli, application = terminal
    cli.run(["schedule", "status", application])
    status = json.loads(capsys.readouterr().out)
    assert status["active"] is True and status["next_at"]
    cli.run(["schedule", "pause", application])
    assert json.loads(capsys.readouterr().out)["paused"] is True
    cli.run(["schedule", "resume", application])
    assert json.loads(capsys.readouterr().out)["paused"] is False


def test_the_terminal_refuses_an_unknown_application(terminal):
    cli, _ = terminal
    with pytest.raises(SystemExit) as refused:
        cli.run(["schedule", "status", "missing"])
    assert refused.value.code == 2
