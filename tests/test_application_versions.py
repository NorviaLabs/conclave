"""Every saved change to an application is kept as a version.

A save that changes what an application does is a new version, with who saved
it and when; moving cards is not. Any two versions can be compared, area by area,
in words. An older version can be restored, as a new version. A run names the
version it ran. A store from before versions were kept starts from the current
version, and recovers older ones from the runs that used them.
"""

import asyncio
import copy
import json
import sqlite3
from contextlib import closing

import pytest

from conclave.application.versions import compare_definitions, summarize_changes
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider
from test_api import make_api
from test_studio_runs import request

BASE = request()["definition"]


def changed(**edits):
    definition = copy.deepcopy(BASE)
    for path, value in edits.items():
        target, *keys = path.split("__")
        node = definition
        for key in [target, *keys][:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        last = [target, *keys][-1]
        node[int(last) if isinstance(node, list) else last] = value
    return definition


def store(tmp_path):
    return Workspace(tmp_path / "applications.sqlite")


# --- Keeping versions --------------------------------------------------------------


def test_version_history_keeps_every_saved_change_with_who_saved_it(tmp_path):
    workspace = store(tmp_path)
    saved = workspace.save(BASE, by="ana@acme.com")
    second = workspace.save(changed(name="Renamed"), identifier=saved["id"], revision=1, by="ben@acme.com")
    assert second["revision"] == 2
    history = workspace.versions(saved["id"])
    assert [(item["version"], item["saved_by"]) for item in history] == [(2, "ben@acme.com"), (1, "ana@acme.com")]
    assert all(item["saved_at"] and item["restored_from"] is None and item["recovered_from_run"] is None for item in history)
    assert workspace.version(saved["id"], 1)["definition"]["name"] == BASE["name"]
    assert workspace.version(saved["id"], 2)["definition"]["name"] == "Renamed"


def test_version_is_not_made_by_moving_cards(tmp_path):
    workspace = store(tmp_path)
    saved = workspace.save(BASE, by="ana@acme.com")
    moved = {**copy.deepcopy(BASE), "canvas": {"positions": {"draft": {"x": 40, "y": 80}}}}
    after = workspace.save(moved, identifier=saved["id"], revision=1, by="ana@acme.com")
    assert after["revision"] == 1 and after["layout_revision"] == 1
    assert [item["version"] for item in workspace.versions(saved["id"])] == [1]
    assert workspace.version(saved["id"], 1)["definition"]["canvas"] == moved["canvas"], "the version keeps where its cards sit"


def test_version_history_is_untouched_by_a_conflicting_save(tmp_path):
    from conclave.application.errors import Conflict

    workspace = store(tmp_path)
    saved = workspace.save(BASE, by="ana@acme.com")
    workspace.save(changed(name="Two"), identifier=saved["id"], revision=1, by="ana@acme.com")
    with pytest.raises(Conflict):
        workspace.save(changed(name="Stale"), identifier=saved["id"], revision=1, by="ben@acme.com")
    assert [item["version"] for item in workspace.versions(saved["id"])] == [2, 1]


def test_version_history_is_deleted_with_its_application(tmp_path):
    workspace = store(tmp_path)
    saved = workspace.save(BASE, by="ana@acme.com")
    workspace.save(changed(name="Two"), identifier=saved["id"], revision=1, by="ana@acme.com")
    workspace.delete(saved["id"], 2)
    with closing(sqlite3.connect(tmp_path / "applications.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM application_versions WHERE application_id = ?", (saved["id"],)).fetchone()[0] == 0
    with pytest.raises(KeyError):
        workspace.versions(saved["id"])


def test_version_of_an_unknown_number_is_not_found(tmp_path):
    workspace = store(tmp_path)
    saved = workspace.save(BASE, by="ana@acme.com")
    with pytest.raises(KeyError):
        workspace.version(saved["id"], 9)


# --- A store from before versions were kept -----------------------------------------------


def old_store(tmp_path, definition, revision):
    path = tmp_path / "applications.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE applications (id TEXT PRIMARY KEY, definition TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                layout_revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        """)
        connection.execute("INSERT INTO applications VALUES ('app', ?, ?, 0, '2026-10-01T00:00:00+00:00')", (json.dumps(definition), revision))
    return path


def test_version_history_of_an_older_store_starts_from_its_current_version(tmp_path):
    old_store(tmp_path, changed(name="Current"), 4)
    workspace = store(tmp_path)
    Workspace(tmp_path / "applications.sqlite")   # opening again adds nothing
    history = workspace.versions("app")
    assert [(item["version"], item["saved_by"], item["saved_at"]) for item in history] == [(4, None, "2026-10-01T00:00:00+00:00")]
    assert workspace.version("app", 4)["definition"]["name"] == "Current"


def test_version_history_is_recovered_from_the_runs_that_used_it(tmp_path):
    old_store(tmp_path, changed(name="Current"), 4)

    async def exercise():
        api = make_api(tmp_path)
        runs = api.host.repository
        for revision, name in ((2, "Second"), (2, "Second"), (3, "Third")):
            value = request()
            value.update(application_id="app", revision=revision, definition=changed(name=name))
            runs.create(value)
        status, listed = await api.dispatch("GET", "/api/applications/app/versions")
        assert status == 200
        assert [(item["version"], bool(item["recovered_from_run"]), item["saved_by"]) for item in listed["versions"]] == [
            (4, False, None), (3, True, None), (2, True, None)]
        assert listed["current"] == 4 and [item["runs"] for item in listed["versions"]] == [0, 1, 2]
        assert (await api.dispatch("GET", "/api/applications/app/versions/2"))[1]["definition"]["name"] == "Second"
        assert (await api.dispatch("GET", "/api/applications/app/versions/1"))[0] == 404, "nothing is invented for version 1"
        await api.host.close()
    asyncio.run(exercise())


# --- What changed ---------------------------------------------------------------------------


def areas(comparison):
    return {area["area"]: [(change["kind"], change["text"]) for change in area["changes"]] for area in comparison["areas"]}


def test_version_comparison_of_identical_definitions_is_empty():
    comparison = compare_definitions(BASE, copy.deepcopy(BASE))
    assert comparison == {"areas": [], "count": 0, "layout_changed": False, "truncated": 0}


def test_version_comparison_names_steps_added_removed_and_changed():
    after = copy.deepcopy(BASE)
    after["workflow"].append({"id": "check", "name": "Check the changelog", "agent": "writer", "task": "Check", "output": "brief"})
    after["workflow"][1]["task"] = "Review carefully"
    after["workflow"][1]["answers_required"] = 1
    before = copy.deepcopy(BASE)
    before["workflow"].append({"id": "gone", "name": "Old step", "agent": "writer", "task": "Old", "output": "brief"})
    found = areas(compare_definitions(before, after))["Steps"]
    assert ("added", "Step “Check the changelog” (writer)") in found
    assert ("removed", "Step “Old step”") in found
    assert ("changed", "Step “review”: task was “Review”, now “Review carefully”") in found


@pytest.mark.parametrize("edit, area, text", [
    ({"agents__0__instructions": "Write tersely"}, "Team", "Member “writer”: instructions changed"),
    ({"name": "Release brief"}, "Application", "Name was “Research brief”, now “Release brief”"),
    ({"run_budget__tokens": 5000}, "Limits and approvals", "Token limit was none, now 5000"),
    ({"autonomous_execution": True}, "Limits and approvals", "Autonomous execution was off, now on"),
    ({"review_policy": {"reviewers": ["ana@acme.com"]}}, "Review policy", "Review policy added: only ana@acme.com may answer; the person who started a run may not"),
    ({"inputs__0__label": "Evidence"}, "Request form", "Field “Sources”: label was “Sources”, now “Evidence”"),
    ({"views__0__layout": "prose"}, None, None),
    ({"state__0__label": "The brief"}, "Results", "Result “brief”: label was none, now “The brief”"),
])
def test_version_comparison_covers_each_area(edit, area, text):
    found = areas(compare_definitions(BASE, changed(**edit)))
    if area is None:
        assert found == {}
    else:
        assert ("changed" if "added:" not in text else "added", text) in found[area] or ("changed", text) in found[area], found


def test_version_comparison_of_connections_and_schedules():
    before = changed(connections=[{"from": "draft", "to": "review"}])
    after = changed(connections=[])
    assert areas(compare_definitions(before, after))["Connections"] == [("removed", "Connection “draft” → “review”")]


def test_version_comparison_notes_card_positions_separately():
    moved = {**copy.deepcopy(BASE), "canvas": {"positions": {"draft": {"x": 1, "y": 2}}}}
    comparison = compare_definitions(BASE, moved)
    assert comparison["areas"] == [] and comparison["layout_changed"] is True


def test_version_comparison_is_bounded():
    before = copy.deepcopy(BASE)
    after = copy.deepcopy(BASE)
    after["requirements"] = [{"question": f"Question {n}?", "answer": "Yes"} for n in range(20)]
    before["requirements"] = [{"question": f"Question {n}?", "answer": "No"} for n in range(20)]
    comparison = compare_definitions(before, after, limit=5)
    assert comparison["count"] == 20 and comparison["truncated"] == 15
    assert sum(len(area["changes"]) for area in comparison["areas"]) == 5


def test_version_summary_says_the_first_change_and_how_many_more():
    after = changed(name="Release brief")
    after["autonomous_execution"] = True
    assert summarize_changes(BASE, after) == "Name was “Research brief”, now “Release brief” · 1 more change"
    assert summarize_changes(BASE, copy.deepcopy(BASE)) == "No change to what it does"
    assert summarize_changes(None, BASE) == "First saved version"


# --- Restore ------------------------------------------------------------------------------------


def saved_three(api):
    async def go():
        status, first = await api.dispatch("POST", "/api/applications", {"definition": BASE})
        assert status == 201
        _, second = await api.dispatch("PUT", f"/api/applications/{first['id']}", {"definition": changed(name="Two"), "revision": 1})
        _, third = await api.dispatch("PUT", f"/api/applications/{first['id']}", {"definition": changed(name="Three"), "revision": 2})
        return first["id"]
    return go()


def test_restore_makes_a_new_version_with_the_old_content(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        status, restored = await api.dispatch("POST", f"/api/applications/{application}/versions/1/restore", {"revision": 3})
        assert status == 200 and restored["revision"] == 4
        assert restored["definition"]["name"] == BASE["name"]
        _, listed = await api.dispatch("GET", f"/api/applications/{application}/versions")
        assert [(item["version"], item["restored_from"]) for item in listed["versions"]] == [(4, 1), (3, None), (2, None), (1, None)]
        assert listed["versions"][0]["saved_by"] == "operator"
        assert (await api.dispatch("GET", f"/api/applications/{application}"))[1]["definition"]["name"] == BASE["name"]
        await api.host.close()
    asyncio.run(exercise())


def test_restore_is_refused_when_the_application_changed_or_the_version_is_unknown(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        assert (await api.dispatch("POST", f"/api/applications/{application}/versions/1/restore", {"revision": 2}))[0] == 409
        assert (await api.dispatch("POST", f"/api/applications/{application}/versions/9/restore", {"revision": 3}))[0] == 404
        assert (await api.dispatch("POST", f"/api/applications/{application}/versions/1/restore", {}))[0] == 400
        assert (await api.dispatch("POST", f"/api/applications/{application}/versions/x/restore", {"revision": 3}))[0] == 404
        _, listed = await api.dispatch("GET", f"/api/applications/{application}/versions")
        assert len(listed["versions"]) == 3, "a refused restore changes nothing"
        await api.host.close()
    asyncio.run(exercise())


def test_restore_of_the_current_content_is_refused_as_no_change(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        status, body = await api.dispatch("POST", f"/api/applications/{application}/versions/3/restore", {"revision": 3})
        assert status == 400 and "already" in body["error"]
        await api.host.close()
    asyncio.run(exercise())


def test_restore_keeps_running_runs_on_their_version(tmp_path):
    async def exercise():
        api = make_api(tmp_path, ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"}))
        application = await saved_three(api)
        value = request()
        status, run = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": application, "revision": 3,
                                                               "task": value["task"], "inputs": value["inputs"]})
        assert status == 201
        await api.host.until_idle(run["id"])
        await api.dispatch("POST", f"/api/applications/{application}/versions/1/restore", {"revision": 3})
        _, shown = await api.dispatch("GET", f"/api/runs/{run['id']}")
        assert shown["request"]["definition"]["name"] == "Three" and shown["request"]["revision"] == 3
        assert shown["version"] == {"number": 3, "application_id": application,
                                    "link": f"/api/applications/{application}/versions/3"}
        await api.host.close()
    asyncio.run(exercise())


# --- REST ------------------------------------------------------------------------------------------


def test_version_rest_list_compare_and_summaries(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        status, listed = await api.dispatch("GET", f"/api/applications/{application}/versions")
        assert status == 200 and listed["current"] == 3
        assert [item["summary"] for item in listed["versions"]] == [
            "Name was “Two”, now “Three”", f"Name was “{BASE['name']}”, now “Two”", "First saved version"]
        status, comparison = await api.dispatch("GET", f"/api/applications/{application}/versions/compare?from=1&to=3")
        assert status == 200 and comparison["from"] == 1 and comparison["to"] == 3
        assert areas(comparison)["Application"] == [("changed", f"Name was “{BASE['name']}”, now “Three”")]
        for query in ("from=1", "from=1&to=9", "from=a&to=2", "from=1&to=2&to=3"):
            assert (await api.dispatch("GET", f"/api/applications/{application}/versions/compare?{query}"))[0] in {400, 404}
        assert (await api.dispatch("GET", "/api/applications/missing/versions"))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_version_history_belongs_to_its_tenant(tmp_path, monkeypatch):
    from conclave.api import configured_api
    from conclave.application.principal import Principal

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CONCLAVE_RUN_DATABASE", raising=False)
    monkeypatch.delenv("CONCLAVE_DATABASE", raising=False)

    async def exercise():
        first = configured_api(workspace=tmp_path, principal=Principal("ada", "acme"))
        second = configured_api(workspace=tmp_path, principal=Principal("bob", "beta"))
        _, saved = await first.dispatch("POST", "/api/applications", {"definition": BASE}, principal=Principal("ada", "acme"))
        _, listed = await first.dispatch("GET", f"/api/applications/{saved['id']}/versions")
        assert listed["versions"][0]["saved_by"] == "ada"
        assert (await second.dispatch("GET", f"/api/applications/{saved['id']}/versions"))[0] == 404
        await first.host.close()
        await second.host.close()
    asyncio.run(exercise())


def test_version_comparison_gives_was_and_now_apart_for_display():
    change = areas_with_parts(compare_definitions(BASE, changed(name="Release brief")))["Application"][0]
    assert change == {"kind": "changed", "text": "Name was “Research brief”, now “Release brief”", "item": "",
                      "subject": "Name", "was": "“Research brief”", "now": "“Release brief”"}
    prose = areas_with_parts(compare_definitions(BASE, changed(agents__0__instructions="Write tersely")))["Team"][0]
    assert "was" not in prose and prose["text"] == "Member “writer”: instructions changed"


def areas_with_parts(comparison):
    return {area["area"]: area["changes"] for area in comparison["areas"]}


def test_version_comparison_gives_review_policy_changes_apart_too():
    before, after = changed(review_policy={"reviewers": ["ana@acme.com"], "allow_starter": True}), changed(review_policy={"reviewers": ["@acme.com"]})
    found = areas_with_parts(compare_definitions(before, after))["Review policy"]
    assert [(item["subject"], item["was"], item["now"]) for item in found] == [
        ("Who may answer", "ana@acme.com", "anyone at @acme.com"),
        ("The person who started a run may answer its reviews", "yes", "no")]


# --- Security audit (W1–W3) -----------------------------------------------------------------


SECRET = "sk-live-1234567890abcdef"


def test_version_history_never_keeps_a_secret_someone_removed(tmp_path):
    workspace = store(tmp_path)
    leaked = changed(agents__0__instructions=f"Write a brief. Use api_key={SECRET} and Bearer abc.def.ghi")
    saved = workspace.save(leaked, by="ana@acme.com")
    assert SECRET in workspace.get(saved["id"])["definition"]["agents"][0]["instructions"], "the application itself is as saved"
    kept = workspace.version(saved["id"], 1)["definition"]["agents"][0]["instructions"]
    assert SECRET not in kept and "abc.def.ghi" not in kept and "[REDACTED]" in kept
    moved = {**leaked, "canvas": {"positions": {"draft": {"x": 1, "y": 1}}}}
    workspace.save(moved, identifier=saved["id"], revision=1, by="ana@acme.com")   # cards only
    assert SECRET not in json.dumps(workspace.version(saved["id"], 1)["definition"])
    workspace.recover(saved["id"], {})
    with closing(sqlite3.connect(tmp_path / "applications.sqlite")) as connection:
        assert SECRET not in json.dumps(connection.execute("SELECT definition FROM application_versions").fetchall())


def test_version_history_recovered_from_a_run_keeps_no_secret(tmp_path):
    old_store(tmp_path, changed(name="Current"), 3)
    workspace = store(tmp_path)
    workspace.recover("app", {1: (changed(agents__0__instructions=f"password={SECRET}"), "run-1")})
    assert SECRET not in json.dumps(workspace.version("app", 1)["definition"])


def test_version_recovered_from_a_run_must_be_a_valid_definition(tmp_path):
    old_store(tmp_path, changed(name="Current"), 4)

    async def exercise():
        api = make_api(tmp_path)
        broken = changed(name="Broken")
        broken["workflow"][0]["agent"] = "nobody"
        for revision, definition, actor in ((2, changed(name="Second"), "ana@acme.com"), (3, broken, "eve@acme.com")):
            value = request()
            value.update(application_id="app", revision=revision, definition=definition)
            with api.host.repository._connect() as connection:   # as a store from before validation would hold it
                run_id = api.host.repository._insert_run(connection, value, actor=actor)
        _, listed = await api.dispatch("GET", "/api/applications/app/versions")
        assert [item["version"] for item in listed["versions"]] == [4, 2], "an invalid definition is not recovered"
        assert listed["versions"][1]["recovered_started_by"] == "ana@acme.com"
        await api.host.close()
    asyncio.run(exercise())


def test_restore_of_a_recovered_version_needs_its_own_confirmation(tmp_path):
    old_store(tmp_path, changed(name="Current"), 3)

    async def exercise():
        api = make_api(tmp_path)
        value = request()
        value.update(application_id="app", revision=2, definition=changed(name="Recovered"))
        api.host.repository.create(value)
        await api.dispatch("GET", "/api/applications/app/versions")
        status, body = await api.dispatch("POST", "/api/applications/app/versions/2/restore", {"revision": 3})
        assert status == 400 and "cannot confirm it was ever saved here" in body["error"]
        status, restored = await api.dispatch("POST", "/api/applications/app/versions/2/restore", {"revision": 3, "recovered": True})
        assert status == 200 and restored["definition"]["name"] == "Recovered" and restored["revision"] == 4
        assert (await api.dispatch("POST", "/api/applications/app/versions/4/restore", {"revision": 4, "recovered": "yes"}))[0] == 400
        await api.host.close()
    asyncio.run(exercise())


# --- Performance audit (Y1, Y2) ---------------------------------------------------------------


def test_version_list_does_no_recovery_work_once_history_is_kept(tmp_path, monkeypatch):
    import conclave.application.workspace as workspace_module

    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        for revision in (1, 2, 3):
            value = request()
            value.update(application_id=application, revision=revision, definition=changed(name=f"Run {revision}"))
            api.host.repository.create(value)
        counted = {"kept": 0, "validated": 0}
        real_kept, real_valid = workspace_module.kept_form, api._valid_definition
        monkeypatch.setattr(workspace_module, "kept_form", lambda definition: (counted.__setitem__("kept", counted["kept"] + 1), real_kept(definition))[1])
        monkeypatch.setattr(api, "_valid_definition", lambda definition: (counted.__setitem__("validated", counted["validated"] + 1), real_valid(definition))[1])
        _, listed = await api.dispatch("GET", f"/api/applications/{application}/versions")
        assert [item["runs"] for item in listed["versions"]] == [1, 1, 1]
        assert counted == {"kept": 0, "validated": 0}, counted
        await api.host.close()
    asyncio.run(exercise())


def test_version_list_reads_no_definitions_once_summaries_are_kept(tmp_path, monkeypatch):
    async def exercise():
        api = make_api(tmp_path)
        application = await saved_three(api)
        counted = {"loads": 0}
        real = api.workspace.version
        monkeypatch.setattr(api.workspace, "version", lambda *args: (counted.__setitem__("loads", counted["loads"] + 1), real(*args))[1])
        _, listed = await api.dispatch("GET", f"/api/applications/{application}/versions")
        assert [item["summary"] for item in listed["versions"]] == [
            "Name was “Two”, now “Three”", f"Name was “{BASE['name']}”, now “Two”", "First saved version"]
        assert counted["loads"] == 0, counted
        await api.host.close()
    asyncio.run(exercise())


def test_version_summaries_for_history_kept_before_are_filled_in_once(tmp_path):
    old_store(tmp_path, changed(name="Current"), 4)

    async def exercise():
        api = make_api(tmp_path)
        value = request()
        value.update(application_id="app", revision=3, definition=changed(name="Third"))
        api.host.repository.create(value)
        _, listed = await api.dispatch("GET", "/api/applications/app/versions")
        assert [item["summary"] for item in listed["versions"]] == ["Name was “Third”, now “Current”", "Earlier versions were not kept"]
        with closing(sqlite3.connect(tmp_path / "applications.sqlite")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM application_versions WHERE summary IS NULL").fetchone()[0] == 0
        await api.host.close()
    asyncio.run(exercise())


def test_version_summary_keeps_no_secret(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        _, first = await api.dispatch("POST", "/api/applications", {"definition": BASE})
        await api.dispatch("PUT", f"/api/applications/{first['id']}", {"definition": changed(name=f"Use api_key={SECRET}"), "revision": 1})
        _, listed = await api.dispatch("GET", f"/api/applications/{first['id']}/versions")
        assert "[REDACTED]" in listed["versions"][0]["summary"] and SECRET not in json.dumps(listed)
        with closing(sqlite3.connect(tmp_path / "applications.sqlite")) as connection:
            assert SECRET not in json.dumps(connection.execute("SELECT summary FROM application_versions").fetchall())
        await api.host.close()
    asyncio.run(exercise())
