"""People and roles on an application, and the routes that check them.

Each application has people with a role — owner, editor, runner or viewer —
and every application and run route checks it: someone with no role on the
application is told it does not exist (404), someone whose role is too low is
told why (403). An application saved before roles existed, with nobody named,
stays open to everyone in the tenant until someone limits access.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from conclave import ToolRegistry
from conclave.application.access import PERMISSIONS, ROLES, refusal, role_allows
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conclave.api import ApplicationAPI
from conftest import ScriptedProvider
from test_studio_runs import request, with_final_stage

ANA, RAJ, PRIYA, LI, EVE = (Principal(name) for name in ("ana@acme.com", "raj@acme.com", "priya@acme.com", "li@acme.com", "eve@acme.com"))
OWNER, EDITOR, RUNNER, VIEWER, OUTSIDER = ANA, RAJ, PRIYA, LI, EVE
BY_ROLE = {"owner": OWNER, "editor": EDITOR, "runner": RUNNER, "viewer": VIEWER}


def make(tmp_path):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(default={"type": "final", "content": "Done", "text": "Brief"}),
                        ToolRegistry(20000), reviewer="operator")
    return ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))


async def created(api, principal=OWNER, definition=None):
    status, saved = await api.dispatch("POST", "/api/applications", {"definition": definition or request()["definition"]}, principal=principal)
    assert status == 201, saved
    return saved


async def grant(api, application, person, role, by=OWNER):
    status, answer = await api.dispatch("POST", f"/api/applications/{application}/access", {"person": person, "role": role}, principal=by)
    assert status == 200, answer
    return answer


async def team(api):
    """An application Ana owns, with an editor, a runner and a viewer, and one of its runs."""
    saved = await created(api)
    for person, role in ((EDITOR.name, "editor"), (RUNNER.name, "runner"), (VIEWER.name, "viewer")):
        await grant(api, saved["id"], person, role)
    value = {**with_final_stage(), "application_id": saved["id"], "revision": saved["revision"], "definition": saved["definition"]}
    run_id = api.host.repository.create(value, actor=OWNER.name)
    return saved, run_id


# --- The roles ---------------------------------------------------------------------------------


@pytest.mark.parametrize("role, permission, allowed", [
    ("viewer", "view", True), ("viewer", "run", False), ("runner", "run", True), ("runner", "edit", False),
    ("editor", "edit", True), ("editor", "own", False), ("owner", "own", True), ("owner", "view", True), (None, "view", False)])
def test_role_allows_what_it_includes(role, permission, allowed):
    assert role_allows(role, permission) is allowed


def test_role_ranking_and_refusal_in_words():
    assert ROLES == ("viewer", "runner", "editor", "owner")
    assert PERMISSIONS == {"view": "viewer", "run": "runner", "edit": "editor", "own": "owner"}
    assert refusal("viewer", "run") == "Your role on this application is viewer; running it needs runner"
    assert refusal("runner", "edit") == "Your role on this application is runner; changing it needs editor"
    assert refusal("editor", "own") == "Your role on this application is editor; that needs owner"


# --- People on an application --------------------------------------------------------------------


def test_member_who_creates_an_application_owns_it(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        status, access = await api.dispatch("GET", f"/api/applications/{saved['id']}/access", principal=OWNER)
        assert status == 200 and access["open"] is False and access["you"] == "owner"
        assert [(item["person"], item["role"], item["added_by"]) for item in access["people"]] == [(OWNER.name, "owner", OWNER.name)]
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=OUTSIDER))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_member_open_application_is_everyones_until_limited(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved = api.workspace.save(request()["definition"])  # saved before roles existed: nobody named
        status, access = await api.dispatch("GET", f"/api/applications/{saved['id']}/access", principal=EVE)
        assert status == 200 and access["open"] is True and access["you"] == "owner" and access["people"] == []
        assert (await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": saved["definition"], "revision": 1}, principal=EVE))[0] == 200
        # Limiting access starts with whoever limits it, as owner.
        status, answer = await api.dispatch("POST", f"/api/applications/{saved['id']}/access", {"person": RAJ.name, "role": "owner"}, principal=EVE)
        assert status == 400 and "yourself" in answer["error"]
        await grant(api, saved["id"], EVE.name, "owner", by=EVE)
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=RAJ))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_member_last_owner_stays_an_owner(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        for change in (("POST", {"person": OWNER.name, "role": "editor"}), ("DELETE", None)):
            path = f"/api/applications/{saved['id']}/access" + ("" if change[0] == "POST" else f"/{OWNER.name}")
            status, answer = await api.dispatch(change[0], path, change[1] or {}, principal=OWNER)
            assert status == 400 and "only owner" in answer["error"]
        await grant(api, saved["id"], RAJ.name, "owner")
        assert (await api.dispatch("DELETE", f"/api/applications/{saved['id']}/access/{OWNER.name}", {}, principal=OWNER))[0] == 200
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=OWNER))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_member_may_remove_themselves_but_not_others(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved, _ = await team(api)
        status, answer = await api.dispatch("DELETE", f"/api/applications/{saved['id']}/access/{RUNNER.name}", {}, principal=EDITOR)
        assert status == 403 and "needs owner" in answer["error"]
        assert (await api.dispatch("DELETE", f"/api/applications/{saved['id']}/access/{VIEWER.name}", {}, principal=VIEWER))[0] == 200
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=VIEWER))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("body", [{"person": "", "role": "viewer"}, {"person": "x" * 101, "role": "viewer"}, {"person": "a@b.c", "role": "admin"},
                                  {"person": "a@b.c"}, {"person": "a@b.c", "role": "viewer", "extra": 1}, {"person": 5, "role": "viewer"}])
def test_member_changes_are_checked(tmp_path, body):
    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        assert (await api.dispatch("POST", f"/api/applications/{saved['id']}/access", body, principal=OWNER))[0] == 400
        await api.host.close()
    asyncio.run(exercise())


def test_member_changes_are_logged_and_shown(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved, _ = await team(api)
        await grant(api, saved["id"], RUNNER.name, "editor")
        await api.dispatch("DELETE", f"/api/applications/{saved['id']}/access/{VIEWER.name}", {}, principal=OWNER)
        _, access = await api.dispatch("GET", f"/api/applications/{saved['id']}/access", principal=RUNNER)
        assert access["you"] == "editor"
        assert [(item["by"], item["person"], item["change"], item["role"]) for item in access["log"]][:3] == [
            (OWNER.name, VIEWER.name, "removed", None), (OWNER.name, RUNNER.name, "changed", "editor"), (OWNER.name, VIEWER.name, "added", "viewer")]
        assert all(item["at"] for item in access["log"])
        await api.host.close()
    asyncio.run(exercise())


def test_member_list_of_applications_shows_what_you_may_open(tmp_path):
    async def exercise():
        api = make(tmp_path)
        mine = await created(api, OWNER)
        theirs = await created(api, EVE)
        open_one = api.workspace.save(request()["definition"])
        await grant(api, theirs["id"], OWNER.name, "viewer", by=EVE)
        _, listed = await api.dispatch("GET", "/api/applications", principal=OWNER)
        seen = {item["id"]: (item["role"], item["open"]) for item in listed["applications"]}
        assert seen == {mine["id"]: ("owner", False), theirs["id"]: ("viewer", False), open_one["id"]: ("owner", True)}
        _, listed = await api.dispatch("GET", "/api/applications", principal=RAJ)
        assert [item["id"] for item in listed["applications"]] == [open_one["id"]]
        await api.host.close()
    asyncio.run(exercise())


def test_member_access_goes_with_a_deleted_application(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved, _ = await team(api)
        assert (await api.dispatch("DELETE", f"/api/applications/{saved['id']}", {"revision": saved["revision"]}, principal=OWNER))[0] == 204
        assert api.workspace.access(saved["id"]) == []
        await api.host.close()
    asyncio.run(exercise())


# --- Every route checks the caller's role -------------------------------------------------------------


MISSING = {"version": "1", "person": "nobody@acme.com", "fixture": "missing", "test": "missing", "file": "0" * 32, "approval": "missing"}


def routes(app, run, revision, names=MISSING):
    """(method, path, body, permission, resource exists for an allowed caller)."""
    brief, n = request()["definition"], names
    return [
        ("GET", f"/api/applications/{app}", None, "view", True),
        ("PUT", f"/api/applications/{app}", {"definition": brief, "revision": revision}, "edit", True),
        ("DELETE", f"/api/applications/{app}", {"revision": 99}, "own", True),
        ("GET", f"/api/applications/{app}/versions", None, "view", True),
        ("GET", f"/api/applications/{app}/versions/{n['version']}", None, "view", True),
        ("GET", f"/api/applications/{app}/versions/compare?from=1&to=1", None, "view", True),
        ("POST", f"/api/applications/{app}/versions/{n['version']}/restore", {"revision": 99}, "edit", True),
        ("GET", f"/api/applications/{app}/schedule", None, "view", True),
        ("POST", f"/api/applications/{app}/schedule", {"action": "pause"}, "edit", True),
        ("GET", f"/api/applications/{app}/notifications", None, "view", True),
        ("POST", "/api/notifications/preview", {"definition": brief, "application_id": app}, "edit", True),
        ("GET", f"/api/applications/{app}/event", None, "edit", True),
        ("POST", f"/api/applications/{app}/event", {"action": "create"}, "own", True),
        ("GET", f"/api/applications/{app}/access", None, "view", True),
        ("POST", f"/api/applications/{app}/access", {"person": "new@acme.com", "role": "viewer"}, "own", True),
        ("DELETE", f"/api/applications/{app}/access/{n['person']}", {}, "own", False),
        ("POST", f"/api/applications/{app}/fixtures/preview", {"run_id": run, "step_id": "draft"}, "edit", False),
        ("POST", f"/api/applications/{app}/fixtures", {"run_id": run, "step_id": "draft", "name": "Example"}, "edit", False),
        ("POST", f"/api/applications/{app}/fixtures/author", {"step_id": "draft", "name": "Example"}, "edit", True),
        ("GET", f"/api/applications/{app}/fixtures", None, "edit", True),
        ("GET", f"/api/applications/{app}/fixtures/{n['fixture']}", None, "edit", False),
        ("DELETE", f"/api/applications/{app}/fixtures/{n['fixture']}", {}, "edit", False),
        ("POST", f"/api/applications/{app}/tests", {"fixture_id": "missing", "scope": "step"}, "edit", False),
        ("GET", f"/api/applications/{app}/tests", None, "edit", True),
        ("GET", f"/api/applications/{app}/tests/{n['test']}", None, "edit", False),
        ("GET", f"/api/applications/{app}/tests/{n['test']}/compare?other=x", None, "edit", False),
        ("GET", f"/api/runs?application_id={app}", None, "view", True),
        ("POST", "/api/runs", {"version": 1, "application_id": app, "revision": revision, "task": "Brief", "inputs": {"sources": "x"}}, "run", True),
        ("GET", f"/api/runs/{run}", None, "view", True),
        ("GET", f"/api/runs/{run}/events?after=0", None, "view", True),
        ("GET", f"/api/runs/{run}/reviews", None, "view", True),
        ("POST", f"/api/runs/{run}/reviews", {"review_id": "missing", "output": {"text": "Fine"}}, "run", False),
        ("POST", f"/api/runs/{run}/resume", {}, "run", True),
        ("POST", f"/api/runs/{run}/pause", {}, "run", True),
        ("POST", f"/api/runs/{run}/retry/cancel", {}, "run", True),
        ("GET", f"/api/runs/{run}/actions", None, "run", True),
        ("POST", f"/api/runs/{run}/actions", {"action_id": "missing", "decision": "done"}, "run", False),
        ("POST", f"/api/runs/{run}/actions/check", {"action_id": "missing"}, "run", False),
        ("GET", f"/api/runs/{run}/tool-approvals", None, "run", True),
        ("POST", f"/api/runs/{run}/tool-approvals", {"approval_id": "missing", "approved": True}, "run", False),
        ("GET", f"/api/runs/{run}/tool-approvals/{n['approval']}", None, "run", False),
        ("GET", f"/api/runs/{run}/files/{n['file']}", None, "view", False),
        ("GET", f"/api/runs/compare?a={run}&b={run}", None, "view", True),
        ("POST", f"/api/runs/{run}/cancel", {}, "run", True),
    ]


ROUTE_NAMES = [f"{method} {path.split('?')[0]}" for method, path, *_ in
               routes("{id}", "{id}", 1, {"version": "{n}", "person": "{person}", "fixture": "{fixture_id}", "test": "{test_id}",
                                          "file": "{file}", "approval": "{approval_id}"})]


@pytest.mark.parametrize("index", range(len(ROUTE_NAMES)), ids=ROUTE_NAMES)
def test_permission_every_application_and_run_route(tmp_path, index):
    async def exercise():
        api = make(tmp_path)
        saved, run_id = await team(api)
        method, path, body, permission, exists = routes(saved["id"], run_id, saved["revision"])[index]
        needed = ROLES.index(PERMISSIONS[permission])

        async def call(principal):
            return await api.dispatch(method, path, json.loads(json.dumps(body)) if body is not None else None, principal=principal)
        status, answer = await call(OUTSIDER)
        assert status == 404, f"someone with no role was answered {status}: {answer}"
        assert "role" not in json.dumps(answer)
        for role in ROLES[:needed]:
            status, answer = await call(BY_ROLE[role])
            assert status == 403, f"a {role} was answered {status}: {answer}"
            assert answer["error"].startswith(f"Your role on this application is {role};")
        status, answer = await call(BY_ROLE[ROLES[needed]])
        assert status != 403, f"a {ROLES[needed]} was refused: {answer}"
        if exists:
            assert status != 404, f"a {ROLES[needed]} was told it does not exist: {answer}"
        await api.host.close()
    asyncio.run(exercise())


def test_permission_route_table_covers_every_documented_route():
    """A route added later goes into this table too: it names every application and run route REST_API.md lists."""
    from pathlib import Path
    documented = set()
    for line in Path("REST_API.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) > 3 and cells[1] in {"GET", "POST", "PUT", "DELETE"}:
            path = cells[2].strip("`").split("?")[0]
            if path.startswith(("/api/applications/{id}", "/api/runs", "/api/notifications/preview")):
                documented.add(f"{cells[1]} {path}")
    missing = sorted(documented - set(ROUTE_NAMES))
    assert missing == [], f"documented routes this table does not check: {missing}"


def test_permission_live_stream_needs_a_role(tmp_path, monkeypatch):
    from conclave.asgi import application
    from test_asgi import call

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))

    async def exercise():
        api = make(tmp_path)
        saved, run_id = await team(api)
        who = {"principal": OUTSIDER}
        services = SimpleNamespace(for_principal=lambda principal: api, close=api.host.close)
        app = application(apis=services, token="secret", principal_for=lambda scope, headers: who["principal"])
        status, _, _ = await call(app, "GET", f"/api/runs/{run_id}/stream", headers=[("authorization", "Bearer secret")])
        assert status == 404
        await api.host.close()
    asyncio.run(exercise())


# --- Work that runs as someone needs them to still be able to run it ----------------------------------


def test_member_event_owner_without_a_run_role_is_refused(tmp_path):
    from test_event_runs import endpoint_of, event_application, signed

    async def exercise():
        api = make(tmp_path)
        saved = await created(api, OWNER, event_application())
        await grant(api, saved["id"], RAJ.name, "owner")
        _, endpoint = await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "create"}, principal=OWNER)
        await grant(api, saved["id"], OWNER.name, "viewer", by=RAJ)
        status, answer = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "x"}))
        assert status == 403 and "role" in answer["error"]
        assert api.host.repository.list(saved["id"]) == []
        assert "role" in api.host.repository.events.recent(saved["id"])[0]["reason"]
        await api.host.close()
    asyncio.run(exercise())


def test_member_schedule_owner_without_a_run_role_does_not_start_it(tmp_path):
    from schedule_support import definition, hourly
    from conclave.application.scheduler import ScheduleService
    from test_schedule_security import later

    async def exercise():
        api = make(tmp_path)
        saved = await created(api, OWNER, definition(hourly()))
        await grant(api, saved["id"], RAJ.name, "owner")
        await grant(api, saved["id"], OWNER.name, "viewer", by=RAJ)
        service = ScheduleService(api.host, api.workspace, clock=later(hours=1, minutes=1))
        outcomes = await service.tick()
        assert [item["outcome"] for item in outcomes if item["outcome"] != "missed"] == ["failed"]
        status = api.host.repository.schedules.status(saved["id"])
        assert "can no longer run this application" in status["last"]["detail"]["reason"]
        assert status["owner"] is None and api.host.repository.list(saved["id"]) == []
        await api.host.close()
    asyncio.run(exercise())


def test_member_run_owner_without_a_run_role_is_not_continued_after_a_review(tmp_path):
    from test_review_policy import answer, application

    async def exercise():
        api = make(tmp_path)
        base = application({"reviewers": [PRIYA.name], "allow_starter": False})
        saved = await created(api, OWNER, base["definition"])
        await grant(api, saved["id"], RAJ.name, "owner")
        value = {**base, "application_id": saved["id"], "revision": saved["revision"], "definition": saved["definition"]}
        run_id = api.host.repository.create(value, actor=OWNER.name)
        await api.host._schedule(run_id)
        await api.host.until_idle(run_id)
        await grant(api, saved["id"], OWNER.name, "viewer", by=RAJ)
        review = api.host.repository.get(run_id)["reviews"][0]
        status, _ = await api.dispatch("POST", f"/api/runs/{run_id}/reviews", {"review_id": review["id"], "output": answer("approved")},
                                       principal=PRIYA)
        assert status == 200, f"a named reviewer answers without a role: {_}"
        await api.host.until_idle(run_id)
        withheld = [item["event"] for item in api.host.repository.get(run_id)["events"] if item["event"]["type"] == "ContinueWithheld"]
        assert [event["reason"] for event in withheld] == ["owner_lacks_role"]
        await api.host.close()
    asyncio.run(exercise())


def test_member_roles_feature_is_reported(tmp_path):
    async def exercise():
        api = make(tmp_path)
        _, runtime = await api.dispatch("GET", "/api/runtime", principal=OWNER)
        assert runtime["features"]["application_roles"] is True
        await api.host.close()
    asyncio.run(exercise())


# --- Security -----------------------------------------------------------------------------------


def test_member_names_match_whatever_their_capitals(tmp_path):
    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        await grant(api, saved["id"], "Raj@Acme.com", "viewer")
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=RAJ))[0] == 200, "the grant applies to raj@acme.com"
        await grant(api, saved["id"], "RAJ@acme.com", "editor")
        people = api.workspace.access(saved["id"])
        assert [(item["person"].casefold(), item["role"]) for item in people] == [(OWNER.name, "owner"), (RAJ.name, "editor")], "one person, once"
        status, answer = await api.dispatch("POST", f"/api/applications/{saved['id']}/access", {"person": "ANA@ACME.COM", "role": "editor"}, principal=OWNER)
        assert status == 400 and "only owner" in answer["error"], "the last owner, whatever their capitals"
        # Leaving is leaving, however the name is written.
        assert (await api.dispatch("DELETE", f"/api/applications/{saved['id']}/access/Raj@ACME.com", {}, principal=RAJ))[0] == 200
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}", principal=RAJ))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_member_list_is_bounded_and_its_log_is_pruned(tmp_path, monkeypatch):
    from conclave.application import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "MAX_PEOPLE", 3)
    monkeypatch.setattr(workspace_module, "LOG_KEEP", 4)

    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        await grant(api, saved["id"], "a@acme.com", "viewer")
        await grant(api, saved["id"], "b@acme.com", "viewer")
        status, answer = await api.dispatch("POST", f"/api/applications/{saved['id']}/access", {"person": "c@acme.com", "role": "viewer"}, principal=OWNER)
        assert status == 400 and "at most 3 people" in answer["error"]
        await grant(api, saved["id"], "b@acme.com", "runner")  # a change to someone listed is not another person
        for _ in range(5):
            await grant(api, saved["id"], "a@acme.com", "runner")
            await grant(api, saved["id"], "a@acme.com", "viewer")
        with api.workspace.connect() as connection:
            kept = connection.execute("SELECT COUNT(*) FROM application_access_log WHERE application_id = ?", (saved["id"],)).fetchone()[0]
        assert kept == 4
        await api.host.close()
    asyncio.run(exercise())


def test_member_hook_owner_without_a_run_role_starts_nothing(tmp_path):
    from types import SimpleNamespace

    async def exercise():
        api = make(tmp_path)
        saved = await created(api)
        await grant(api, saved["id"], "hooker@acme.com", "viewer")
        started = []

        class Store:
            def hook(self, identifier):
                return {"applications": [saved["id"]], "owner": identifier}

        async def handle(command, value, **options):
            started.append(value)
            return 201, {"id": "run"}
        api.host.connections = Store()
        original = api.host.handle
        api.host.handle = handle
        hook = Principal("hook", source="hook")
        for owner in ("hooker@acme.com", "stranger@acme.com"):
            status, answer = await api.dispatch("POST", f"/api/hooks/{owner}", {"payload": {"task": "x"}}, principal=hook)
            assert status == 403 and "can no longer run" in answer["error"]
        assert started == []
        await grant(api, saved["id"], "hooker@acme.com", "runner")
        assert (await api.dispatch("POST", "/api/hooks/hooker@acme.com", {"payload": {"task": "x"}}, principal=hook))[0] == 201
        api.host.handle, api.host.connections = original, None
        await api.host.close()
    asyncio.run(exercise())
