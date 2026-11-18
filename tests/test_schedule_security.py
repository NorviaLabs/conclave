"""Step 8 — the security fixes for scheduled applications (S1–S4).

S1: whoever last saves a change to what runs becomes the schedule's owner.
S2: a run records whom it acts as; a tenant started at boot acts for nobody.
S3: a schedule stops acting as an owner the deployment no longer admits.
S4: a scheduled time passes the same checks as a manual start.
"""

import asyncio
import json

import pytest

from actions_support import Recorder, note, registry
from schedule_support import Clock, definition, hourly, utc
from conclave import Capability, Tool, ToolRegistry
from conclave.api import ApplicationAPI, TenantAPIs
from conclave.application.principal import Principal
from conclave.application.profile import HOSTED
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider, share

DONE = {"type": "final", "content": "Done", "text": "Digest"}
ANA, MALLORY = Principal("ana@acme.test", "acme"), Principal("mallory@acme.test", "acme")


def hosted(tmp_path, *, tools=None, script=(), principal=Principal("host", "acme"), connections=False):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider({"writer": list(script) or [DONE]}),
                        tools or ToolRegistry(20000), reviewer=principal.name, profile=HOSTED, principal=principal)
    if connections:
        from cryptography.fernet import Fernet
        from conclave.integrations.connections import ConnectionStore
        host.connections = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key())
    return ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))


async def save(api, value, principal, saved=None):
    if saved is None:
        status, body = await api.dispatch("POST", "/api/applications", {"definition": value}, principal=principal)
        assert status == 201, body
        # Ana and Mallory work on the same applications.
        share(api, body["id"], *({ANA.name, MALLORY.name} - {principal.name}))
    else:
        status, body = await api.dispatch("PUT", f"/api/applications/{saved['id']}",
                                          {"definition": value, "revision": saved["revision"]}, principal=principal)
        assert status == 200, body
    return body


async def fire(api, clock):
    from conclave.application.scheduler import ScheduleService
    service = ScheduleService(api.host, api.workspace, clock=clock)
    outcomes = await service.tick()
    return service, outcomes


async def finished(api):
    async with asyncio.timeout(5):
        while any(not task.done() for task, _ in list(api.host.tasks.values())):
            await asyncio.sleep(0.01)


def later(hours=1, minutes=0, seconds=5):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return Clock(now + timedelta(hours=hours, minutes=minutes, seconds=seconds))


# --- S1: whoever last saves it owns it ----------------------------------------------


def test_a_save_by_someone_else_makes_the_schedule_theirs(tmp_path):
    async def exercise():
        api = hosted(tmp_path, connections=True)
        saved = await save(api, definition(hourly()), ANA)
        changed = definition(hourly(request={"task": "Mallory's instructions", "inputs": {"team": "x"}}), autonomous=True)
        saved = await save(api, changed, MALLORY, saved)
        status = api.host.repository.schedules.status(saved["id"])
        assert status["owner"] == MALLORY.name
        assert status["owner_since"], "when the schedule changed hands is recorded"
        _, outcomes = await fire(api, later(hours=1, minutes=1))
        run_id = next(item["run_id"] for item in outcomes if item["outcome"] == "started")
        await finished(api)
        assert api.host.connections.run_owner(run_id, "acme") == MALLORY.name
        events = [row["event"] for row in api.host.repository.get(run_id)["events"]]
        grant = next(event for event in events if event["type"] == "ToolAuthorizationGranted")
        assert grant["authorizer"] == MALLORY.name
        await api.host.close()

    asyncio.run(exercise())


def test_moving_cards_does_not_change_the_owner(tmp_path):
    async def exercise():
        api = hosted(tmp_path)
        saved = await save(api, definition(hourly()), ANA)
        moved = definition(hourly())
        moved["canvas"] = {"positions": {"write": {"x": 40, "y": 80}}}
        saved = await save(api, moved, MALLORY, saved)
        assert api.host.repository.schedules.status(saved["id"])["owner"] == ANA.name
        await api.host.close()

    asyncio.run(exercise())


def test_a_paused_schedule_changes_hands_and_stays_paused(tmp_path):
    async def exercise():
        api = hosted(tmp_path)
        saved = await save(api, definition(hourly()), ANA)
        status, _ = await api.dispatch("POST", f"/api/applications/{saved['id']}/schedule", {"action": "pause"}, principal=ANA)
        assert status == 200
        saved = await save(api, definition(hourly(request={"task": "Another task", "inputs": {"team": "x"}})), MALLORY, saved)
        status = api.host.repository.schedules.status(saved["id"])
        assert status["owner"] == MALLORY.name and status["paused"] is True and status["next_at"] is None
        code, _ = await api.dispatch("POST", f"/api/applications/{saved['id']}/schedule", {"action": "resume"}, principal=MALLORY)
        assert code == 200
        await api.host.close()

    asyncio.run(exercise())


# --- S2: a run remembers whom it acts as ----------------------------------------------


def test_a_run_records_whom_it_acts_as(tmp_path):
    from test_studio_runs import request

    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(request(), actor="ana@acme.test")
    assert repository.actor(run_id) == "ana@acme.test"
    assert repository.actor(repository.create(request())) is None


def test_approvals_on_a_boot_started_tenant_go_to_whoever_started_the_run(tmp_path, monkeypatch):
    from test_schedule_surfaces import tenant_store

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    tenant_store(tmp_path, "acme", scheduled=True)

    async def exercise():
        tenants = TenantAPIs(tmp_path)
        await tenants.start_schedules()
        api = tenants.apis["acme"]
        assert api.host.principal.source == "system", "a tenant started at boot acts for nobody"
        recorder = Recorder()
        api.host.tools.register(recorder.tool())
        api.host.provider = ScriptedProvider({"writer": [note(), DONE]})
        saved = await save(api, definition(None, start_mode="manual", tools=("send_note",)), ANA)
        # Even people who may run it are not asked to approve a run someone else started.
        share(api, saved["id"], api.host.principal.name, "scheduler", role="runner")
        status, run = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                               "task": "x", "inputs": {"team": "x"}}, principal=ANA)
        assert status == 201
        async with asyncio.timeout(5):
            while not api.host.tool_approvals.pending:
                await asyncio.sleep(0.01)
        assert [item["owner"] for item in api.host.tool_approvals.pending.values()] == [ANA.name]
        for name, expected in ((ANA.name, 1), (api.host.principal.name, 0), ("scheduler", 0)):
            status, body = await api.dispatch("GET", f"/api/runs/{run['id']}/tool-approvals", {}, principal=Principal(name, "acme"))
            assert len(body["approvals"]) == expected, name
        await tenants.close()

    asyncio.run(exercise())


def test_a_scheduled_run_acts_as_its_owner_without_a_connection_store(tmp_path):
    async def exercise():
        api = hosted(tmp_path)
        saved = await save(api, definition(hourly(), autonomous=True), ANA)
        _, outcomes = await fire(api, later(hours=1, minutes=1))
        run_id = next(item["run_id"] for item in outcomes if item["outcome"] == "started")
        await finished(api)
        assert api.host.repository.actor(run_id) == ANA.name
        events = [row["event"] for row in api.host.repository.get(run_id)["events"]]
        assert next(event for event in events if event["type"] == "ToolAuthorizationGranted")["authorizer"] == ANA.name
        await api.host.close()

    asyncio.run(exercise())


def test_uncertain_actions_are_decided_by_the_person_the_run_acts_as(tmp_path):
    async def exercise():
        api = hosted(tmp_path, principal=Principal("host", "acme"))
        saved = await save(api, definition(None, start_mode="manual"), ANA)
        share(api, saved["id"], "host", role="runner")
        status, run = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                               "task": "x", "inputs": {"team": "x"}}, principal=ANA)
        await finished(api)
        status, _ = await api.dispatch("GET", f"/api/runs/{run['id']}/actions", {}, principal=ANA)
        assert status == 200
        status, body = await api.dispatch("GET", f"/api/runs/{run['id']}/actions", {}, principal=Principal("host", "acme"))
        assert status == 400 and "approves this run" in body["error"]
        await api.host.close()

    asyncio.run(exercise())


def test_a_system_host_approves_nothing_for_runs_that_name_nobody(tmp_path):
    """Runs created before runs recorded their actor fall back to the host;
    a host started for schedules is not a person, so nobody is asked."""
    from test_studio_runs import request

    async def exercise():
        api = hosted(tmp_path, principal=Principal("scheduler", "acme", source="system"))
        run_id = api.host.repository.create(request())
        assert api.host._run_approver(run_id) is None
        status, body = await api.dispatch("GET", f"/api/runs/{run_id}/actions", {}, principal=Principal("scheduler", "acme"))
        assert status == 400
        await api.host.close()

    asyncio.run(exercise())


# --- S3: owners the deployment no longer admits --------------------------------------


def test_a_time_whose_owner_is_no_longer_admitted_fails_and_waits_for_someone(tmp_path):
    from conclave.application.scheduler import ScheduleService

    async def exercise():
        api = hosted(tmp_path)
        saved = await save(api, definition(hourly()), ANA)
        service = ScheduleService(api.host, api.workspace, clock=later(hours=1, minutes=1), admits=lambda owner: owner != ANA.name)
        outcomes = await service.tick()
        assert [item["outcome"] for item in outcomes if item["outcome"] != "missed"] == ["failed"]
        status = api.host.repository.schedules.status(saved["id"])
        assert "can no longer use this workspace" in status["last"]["detail"]["reason"]
        assert status["owner"] is None and status["waiting"] is True and status["next_at"] is None
        assert api.host.repository.list(saved["id"]) == []
        code, _ = await api.dispatch("POST", f"/api/applications/{saved['id']}/schedule", {"action": "turn_on"}, principal=MALLORY)
        assert code == 200 and api.host.repository.schedules.status(saved["id"])["owner"] == MALLORY.name
        await api.host.close()

    asyncio.run(exercise())


def test_tenant_schedulers_use_the_deployments_admission_check(tmp_path):
    from test_schedule_surfaces import api_for

    def factory(*, workspace=None, principal=None):
        return api_for(tmp_path)

    async def exercise():
        admits = lambda owner: True  # noqa: E731
        tenants = TenantAPIs(tmp_path, factory=factory, admits=admits)
        assert tenants.for_principal(ANA).host.schedules.admits is admits
        await tenants.close()

    asyncio.run(exercise())


def test_google_sign_in_decides_who_may_still_own_a_schedule(tmp_path):
    from conclave.asgi import application
    from conclave.identity import LoginConfig

    config = LoginConfig.from_value({
        "client_id": "c", "client_secret": "s", "authorize_url": "https://accounts.example/auth",
        "token_url": "https://accounts.example/token", "jwks_url": "https://accounts.example/keys",
        "hosts": ["accounts.example"], "tenant": "acme", "domains": ["acme.test"], "emails": [],
        "redirect_uris": ["http://127.0.0.1:8123/auth/google/callback"]})

    class Login:
        def __init__(self):
            self.config = config

    tenants = TenantAPIs(tmp_path)
    application(apis=tenants, token="t", login=Login())
    assert tenants.admits("ana@acme.test") is True
    assert tenants.admits("ana@elsewhere.test") is False
    assert tenants.admits("build-bot") is True, "names that did not come from Google sign-in are the deployment's to judge"


# --- S4: the same checks as a manual start ---------------------------------------------


async def unmarked_push(**arguments):
    return "pushed"


def test_a_scheduled_time_is_refused_by_the_checks_a_manual_start_makes(tmp_path):
    async def exercise():
        tools = ToolRegistry(20000)
        tools.register(Tool("push_branch", "Push", {"branch": "string"}, unmarked_push, frozenset({Capability.READ})))
        api = hosted(tmp_path, tools=tools)
        saved = await save(api, definition(hourly(), tools=("push_branch",)), ANA)
        _, outcomes = await fire(api, later(hours=1, minutes=1))
        assert [item["outcome"] for item in outcomes if item["outcome"] != "missed"] == ["failed"]
        status = api.host.repository.schedules.status(saved["id"])
        assert "must be registered as destructive" in status["last"]["detail"]["reason"]
        assert api.host.repository.list(saved["id"]) == [], "no run is created for a refused time"
        await api.host.close()

    asyncio.run(exercise())


def test_a_scheduled_run_pins_the_version_of_its_source(tmp_path):
    from conclave.application.resources import load_resources, register_source_tools

    async def exercise():
        folder = tmp_path / "exports"
        folder.mkdir()
        (folder / "tickets.md").write_text("Ticket 1")
        resources = load_resources(None, configured={"exports": {"path": str(folder), "name": "Support exports"}})
        tools = ToolRegistry(20000)
        register_source_tools(tools)
        api = hosted(tmp_path, tools=tools)
        api.host.resources = resources
        block = hourly(request={"task": "Summarise", "inputs": {"team": "x"}, "resources": {"source": "exports"}})
        await save(api, definition(block, tools=("read_file",)), ANA)
        _, outcomes = await fire(api, later(hours=1, minutes=1))
        run_id = next(item["run_id"] for item in outcomes if item["outcome"] == "started")
        await finished(api)
        assert api.host.repository.get(run_id)["request"]["resources"] == {
            "source": "exports", "source_revision": resources["exports"]["revision"]}
        await api.host.close()

    asyncio.run(exercise())
