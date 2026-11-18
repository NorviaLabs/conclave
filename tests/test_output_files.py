"""A step whose output is a document, dataset or artifact leaves a real file.

The host writes it from the step's validated output — Markdown, CSV or JSON —
stores it beside the run with its size and SHA-256, and serves it only from a
checked download link: the file must belong to the run and still be exactly
what was stored. The model never names a file, a path or a link.
"""

import asyncio
import copy
import csv
import hashlib
import io
import json
import stat

import pytest

from conclave import ToolRegistry
from conclave.application import output_files
from conclave.application.definition import validate_definition
from conclave.application.output_files import (MAX_FILE_BYTES, FileDownload, file_format, file_name, preview,
                                               render_file)
from conclave.application.presentation import render_run
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, public_record
from conftest import CONFIG, ScriptedProvider
from test_api import make_api
from test_revision_loop import decision
from test_studio_runs import collect, request

EVIDENCE_FIELDS = [{"key": "check", "label": "Check", "type": "string", "required": False},
                   {"key": "passed", "label": "Passed", "type": "number", "required": False}]
NOTES = "# Release notes\n\n- Faster sync\n- Compare runs\n"
ROWS = [{"id": "unit", "title": "Unit tests", "body": "412 passed", "status": "validated", "source_refs": ["ci/881"], "check": "pytest", "passed": 412},
        {"id": "evil", "title": "=HYPERLINK(\"http://x\")", "body": "+1, \"quoted\"\nline", "status": "unverified", "source_refs": [],
         "check": "@sum", "passed": -5}]
BUILD = [{"id": "pr-866", "title": "Fix the login loop", "body": "https://github.com/acme/forge/pull/866", "status": "draft", "source_refs": []}]


def plan(**files):
    """draft (document) -> collect (dataset) -> build (artifact) -> triage (findings)."""
    agents = ["writer", "analyst", "builder", "triager"]
    definition = {
        "version": 1, "name": "Release",
        "agents": [{"id": agent, "instructions": "Work", "tools": []} for agent in agents],
        "state": [
            {"key": "notes", "label": "Release notes: 4.2 / final!!", "kind": "document", "type": "text", "merge": "replace", "statuses": []},
            {"key": "evidence", "label": "Test evidence", "kind": "dataset", "type": "records", "merge": "upsert",
             "statuses": ["unverified", "validated"], "fields": EVIDENCE_FIELDS},
            {"key": "implementation", "label": "Implementation", "kind": "artifact", "type": "records", "merge": "upsert", "statuses": ["draft", "reviewed"]},
            {"key": "findings", "label": "Findings", "kind": "findings", "type": "records", "merge": "upsert", "statuses": []},
        ],
        "workflow": [{"id": "draft", "name": "Draft the notes", "agent": "writer", "task": "Draft", "output": "notes"},
                     {"id": "collect", "name": "Collect evidence", "agent": "analyst", "task": "Collect", "output": "evidence"},
                     {"id": "build", "name": "Build it", "agent": "builder", "task": "Build", "output": "implementation"},
                     {"id": "triage", "name": "Triage", "agent": "triager", "task": "Triage", "output": "findings"}],
        "connections": [{"from": "draft", "to": "collect"}, {"from": "collect", "to": "build"}, {"from": "build", "to": "triage"}],
        "views": [{"id": "notes_view", "collection": "notes", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    for key, setting in files.items():
        next(item for item in definition["state"] if item["key"] == key)["file"] = setting
    return definition


def records(*items):
    return {"type": "final", "content": "Done", "records": [copy.deepcopy(item) for item in items]}


def provider():
    return ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": NOTES}], "analyst": [records(*ROWS)],
                             "builder": [records(*BUILD)], "triager": [records({"id": "f1", "title": "Fine", "body": "", "status": "", "source_refs": []})]})


def collection(key, **changes):
    return {**next(item for item in plan()["state"] if item["key"] == key), **changes}


def wrapped(definition, application="release"):
    return {"version": 1, "application_id": application, "revision": 1, "task": "Release", "inputs": {}, "definition": definition}


def stored(tmp_path, definition=None, script=None):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(wrapped(definition or plan()))
    collect(repository.execute(run_id, script or provider(), ToolRegistry(20000)))
    return repository, run_id


def by_stage(repository, run_id):
    return {item["stage"]: item for item in repository.files(run_id)}


# --- Which outputs become files, and what they are called -----------------------------------------


@pytest.mark.parametrize("key, changes, expected", [
    ("notes", {}, "markdown"), ("evidence", {}, "csv"), ("implementation", {}, "json"), ("findings", {}, None),
    ("evidence", {"file": {"format": "json"}}, "json"), ("evidence", {"file": False}, None),
    ("notes", {"file": False}, None), ("evidence", {"kind": None}, None)])
def test_output_file_format_follows_the_kind(key, changes, expected):
    item = {name: value for name, value in collection(key, **changes).items() if value is not None}
    assert file_format(item) == expected


@pytest.mark.parametrize("key, setting", [
    ("notes", {"format": "csv"}), ("implementation", {"format": "csv"}), ("findings", {"format": "json"}),
    ("evidence", {"format": "xlsx"}), ("evidence", {"format": "csv", "name": "x"}), ("evidence", True), ("evidence", "csv"),
    ("evidence", None)])
def test_output_file_setting_is_checked(key, setting):
    with pytest.raises(ValueError, match="file"):
        validate_definition(plan(**{key: setting}))


def test_output_file_settings_that_are_allowed():
    validate_definition(plan(evidence={"format": "json"}, notes=False, implementation={"format": "json"}))


@pytest.mark.parametrize("label, key, expected", [
    ("Release notes: 4.2 / final!!", "notes", "release-notes-4-2-final.md"),
    (None, "notes", "notes.md"), ("¿¡!!", "notes", "notes.md"), ("Éclair café", "notes", "eclair-cafe.md"),
    ("x" * 100, "notes", "x" * 60 + ".md"), ('a"b\r\nc', "notes", "a-b-c.md")])
def test_output_file_name_is_made_by_conclave(label, key, expected):
    item = collection(key)
    if label is None:
        item.pop("label")
    else:
        item["label"] = label
    assert file_name(item) == expected


# --- What a file holds -------------------------------------------------------------------------------


def test_output_file_markdown_is_the_text_as_written():
    name, media, body = render_file(collection("notes"), NOTES)
    assert (name, media, body) == ("release-notes-4-2-final.md", "text/markdown; charset=utf-8", NOTES.encode())


def test_output_file_csv_has_a_column_each_and_cannot_run_formulas():
    name, media, body = render_file(collection("evidence"), copy.deepcopy(ROWS))
    assert name == "test-evidence.csv" and media == "text/csv; charset=utf-8"
    rows = list(csv.reader(io.StringIO(body.decode())))
    assert rows[0] == ["id", "title", "body", "status", "sources", "Check", "Passed"]
    assert rows[1] == ["unit", "Unit tests", "412 passed", "validated", "ci/881", "pytest", "412"]
    assert rows[2] == ["evil", "'=HYPERLINK(\"http://x\")", "'+1, \"quoted\"\nline", "unverified", "", "'@sum", "-5"], \
        "text that a spreadsheet would run is marked as text; a number stays a number"


def test_output_file_json_is_the_records_without_provenance():
    with_provenance = [{**BUILD[0], "contributions": [{"agent": "builder", "stage": "build", "run_id": "r"}], "source_checks": []}]
    name, media, body = render_file(collection("implementation"), with_provenance)
    assert name == "implementation.json" and media == "application/json"
    assert json.loads(body) == [{**BUILD[0], "source_checks": []}]


def test_output_file_dataset_as_json_when_chosen():
    name, _, body = render_file(collection("evidence", file={"format": "json"}), copy.deepcopy(ROWS[:1]))
    assert name == "test-evidence.json" and json.loads(body) == ROWS[:1]


def test_output_file_preview_is_the_first_lines():
    lines, total = preview("\n".join(f"line {index}" for index in range(50)).encode())
    assert lines == "\n".join(f"line {index}" for index in range(20)) and total == 50
    long, _ = preview(("x" * 5000).encode())
    assert len(long) == 2000


def test_output_file_too_large_is_refused():
    with pytest.raises(ValueError, match="too large"):
        render_file(collection("notes"), "x" * (MAX_FILE_BYTES + 1))


# --- Stored when a step finishes ---------------------------------------------------------------------


def test_output_file_is_stored_when_a_step_finishes(tmp_path):
    repository, run_id = stored(tmp_path)
    files = by_stage(repository, run_id)
    assert set(files) == {"draft", "collect", "build"}, "findings have no file"
    draft = files["draft"]
    assert (draft["name"], draft["format"], draft["output"], draft["round"]) == ("release-notes-4-2-final.md", "markdown", "notes", 1)
    path = tmp_path / "run-files" / run_id / draft["id"]
    assert path.read_bytes() == NOTES.encode() and draft["size"] == len(NOTES.encode())
    assert draft["sha256"] == hashlib.sha256(NOTES.encode()).hexdigest()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert draft["preview"] == NOTES.rstrip("\n") and draft["lines"] == 4
    events = [item["event"] for item in repository.get(run_id)["events"] if item["event"]["type"] == "OutputFileStored"]
    assert [(event["stage"], event["name"]) for event in events] == [
        ("draft", "release-notes-4-2-final.md"), ("collect", "test-evidence.csv"), ("build", "implementation.json")]
    assert set(events[0]) == {"type", "stage", "output", "file_id", "name", "format", "size", "round", "timestamp"}


def test_output_file_name_is_never_the_storage_path(tmp_path):
    definition = plan()
    definition["state"][0]["label"] = "../../etc/passwd"
    repository, run_id = stored(tmp_path, definition)
    draft = by_stage(repository, run_id)["draft"]
    assert draft["name"] == "etc-passwd.md"
    # Stored under ids Conclave chose, and nothing else.
    assert sorted(path.name for path in (tmp_path / "run-files" / run_id).iterdir()) == sorted(item["id"] for item in repository.files(run_id))
    assert not (tmp_path / "etc").exists()


def test_output_file_turned_off_is_not_stored(tmp_path):
    repository, run_id = stored(tmp_path, plan(notes=False, evidence=False))
    assert set(by_stage(repository, run_id)) == {"build"}


def test_output_file_too_large_is_skipped_and_said(tmp_path, monkeypatch):
    monkeypatch.setattr(output_files, "MAX_FILE_BYTES", 10)
    repository, run_id = stored(tmp_path)
    record = repository.get(run_id)
    assert record["status"] == "completed"
    skipped = [item["event"] for item in record["events"] if item["event"]["type"] == "OutputFileSkipped"]
    assert {event["stage"] for event in skipped} == {"draft", "collect", "build"}
    assert "too large" in skipped[0]["reason"] and repository.files(run_id) == []


def test_output_file_is_removed_when_it_could_not_be_recorded(tmp_path, monkeypatch):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(wrapped(plan(evidence=False, implementation=False)))
    original = repository._insert_event

    def failing(connection, run, event):
        if event["type"] == "OutputFileStored":
            raise RuntimeError("disk full")
        return original(connection, run, event)
    monkeypatch.setattr(repository, "_insert_event", failing)
    collect(repository.execute(run_id, provider(), ToolRegistry(20000)))
    # The run records that it failed; the file it could not record is gone.
    assert repository.get(run_id)["status"] == "failed"
    folder = tmp_path / "run-files" / run_id
    assert not folder.exists() or list(folder.iterdir()) == []
    assert repository.files(run_id) == []


def reviewed_document():
    value = request()
    definition = value["definition"]
    definition["state"][0].update(kind="document", label="Brief")
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert",
                                "statuses": ["proposed", "approved", "rejected", "changes_requested"]})
    definition["workflow"][1]["output"] = "decision"
    return value


def test_output_file_each_round_keeps_its_own_file(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(reviewed_document())
    script = ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": "One"}, {"type": "final", "content": "Done", "text": "Two"}]})
    collect(repository.execute(run_id, script, ToolRegistry(20000)))
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="priya", output=decision("changes_requested"))
    collect(repository.resume(run_id, script, ToolRegistry(20000)))
    files = [item for item in repository.files(run_id) if item["stage"] == "draft"]
    assert [(item["round"], item["size"]) for item in files] == [(1, 3), (2, 3)]
    assert (tmp_path / "run-files" / run_id / files[1]["id"]).read_bytes() == b"Two"


def test_output_file_from_a_person_step(tmp_path):
    value = request()
    value["definition"]["state"][0].update(kind="document", label="Brief")
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value)
    collect(repository.execute(run_id, ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"}), ToolRegistry(20000)))
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="priya", output={"text": "Edited by a person"})
    collect(repository.resume(run_id, ScriptedProvider(), ToolRegistry(20000)))
    person = by_stage(repository, run_id)["review"]
    assert (tmp_path / "run-files" / run_id / person["id"]).read_bytes() == b"Edited by a person"


# --- The run record and the checked download ----------------------------------------------------------


def test_output_files_are_in_the_run_record(tmp_path):
    repository, run_id = stored(tmp_path)
    files = public_record(repository.get(run_id))["files"]
    draft = next(item for item in files if item["stage"] == "draft")
    assert draft["link"] == f"/api/runs/{run_id}/files/{draft['id']}" and draft["available"] is True
    assert set(draft) == {"id", "stage", "output", "round", "name", "format", "media_type", "size", "created_at",
                          "preview", "lines", "link", "available"}, "the hash stays on the host"
    (tmp_path / "run-files" / run_id / draft["id"]).write_bytes(b"short")
    assert next(item for item in public_record(repository.get(run_id))["files"] if item["stage"] == "draft")["available"] is False


def test_output_file_download_gives_the_stored_bytes(tmp_path):
    repository, run_id = stored(tmp_path)
    draft = by_stage(repository, run_id)["draft"]
    download = repository.read_file(run_id, draft["id"])
    assert download == FileDownload(body=NOTES.encode(), media_type="text/markdown; charset=utf-8", name="release-notes-4-2-final.md")


@pytest.mark.parametrize("tamper", ["change", "same_size_change", "delete"])
def test_output_file_download_refuses_a_file_that_changed(tmp_path, tamper):
    repository, run_id = stored(tmp_path)
    draft = by_stage(repository, run_id)["draft"]
    path = tmp_path / "run-files" / run_id / draft["id"]
    if tamper == "delete":
        path.unlink()
    else:
        path.write_bytes(b"x" * (draft["size"] if tamper == "same_size_change" else 3))
    with pytest.raises(output_files.FileChanged):
        repository.read_file(run_id, draft["id"])


def test_output_file_download_only_from_its_own_run(tmp_path):
    repository, run_id = stored(tmp_path)
    other = repository.create(wrapped(plan()))
    draft = by_stage(repository, run_id)["draft"]
    for run, file_id in ((other, draft["id"]), (run_id, "missing"), (run_id, "../" + draft["id"])):
        with pytest.raises(KeyError):
            repository.read_file(run, file_id)


def test_output_file_download_through_rest(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        run_id = api.host.repository.create(wrapped(plan()))
        collect_async = [event async for event in api.host.repository.execute(run_id, provider(), ToolRegistry(20000))]
        assert collect_async[-1]["status"] == "completed"
        draft = by_stage(api.host.repository, run_id)["draft"]
        status, download = await api.dispatch("GET", f"/api/runs/{run_id}/files/{draft['id']}")
        assert status == 200 and isinstance(download, FileDownload) and download.body == NOTES.encode()
        assert (await api.dispatch("GET", f"/api/runs/{run_id}/files/missing"))[0] == 404
        (api.host.repository.path.parent / "run-files" / run_id / draft["id"]).write_bytes(b"changed")
        status, answer = await api.dispatch("GET", f"/api/runs/{run_id}/files/{draft['id']}")
        assert status == 410 and "missing or was changed" in answer["error"]
        assert (await api.dispatch("POST", f"/api/runs/{run_id}/files/{draft['id']}", {}))[0] in {404, 405}
        await api.host.close()
    asyncio.run(exercise())


def test_output_file_download_through_the_hosted_transport(tmp_path, monkeypatch):
    from conclave.application.principal import Principal
    from conclave.asgi import application
    from test_asgi import call, make_services

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        assert (await call(app))[0] == 200
        repository = built["acme"].host.repository
        run_id = repository.create(wrapped(plan()))
        [event async for event in repository.execute(run_id, provider(), ToolRegistry(20000))]
        evidence = by_stage(repository, run_id)["collect"]
        status, headers, body = await call(app, "GET", f"/api/runs/{run_id}/files/{evidence['id']}")
        assert status == 200 and body.decode().startswith("id,title,body,status,sources")
        assert headers["content-type"] == "text/csv; charset=utf-8"
        assert headers["content-disposition"] == 'attachment; filename="test-evidence.csv"'
        assert headers["x-content-type-options"] == "nosniff" and headers["cache-control"] == "no-store"
        assert (await call(app, "GET", f"/api/runs/{run_id}/files/nope"))[0] == 404
        await app.close()
    asyncio.run(exercise())


def test_output_file_download_checks_access_like_opening_the_run(tmp_path):
    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer="operator")
        run_id = host.repository.create(wrapped(plan()))
        [event async for event in host.repository.execute(run_id, provider(), ToolRegistry(20000))]
        draft = by_stage(host.repository, run_id)["draft"]
        checked = []

        def owner(run, principal, *, reviewing=False):
            checked.append((run, reviewing))
            raise ValueError("Integration run belongs to another principal")
        host._require_run_owner = owner
        with pytest.raises(ValueError, match="another principal"):
            await host.handle("file", {"run_id": run_id, "file_id": draft["id"]})
        assert checked == [(run_id, True)]
        for bad in ({"run_id": run_id}, {"run_id": run_id, "file_id": 1}, {"run_id": run_id, "file_id": "x", "more": 1}):
            with pytest.raises(ValueError):
                await host.handle("file", bad)
        _, runtime = await host.handle("runtime", {})
        assert runtime["features"]["output_files"] is True
        await host.close()
    asyncio.run(exercise())


def test_output_file_older_run_has_none(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(wrapped(plan()))
    assert repository.files(run_id) == [] and public_record(repository.get(run_id))["files"] == []


def test_output_file_listed_by_the_terminal(tmp_path):
    repository, run_id = stored(tmp_path)
    text = render_run(public_record(repository.get(run_id)))
    assert "Files" in text
    draft = by_stage(repository, run_id)["draft"]
    assert f"  release-notes-4-2-final.md  Markdown · 46 B  /api/runs/{run_id}/files/{draft['id']}" in text


# --- Security ------------------------------------------------------------------------------------------


def test_output_file_csv_header_cannot_run_formulas():
    item = collection("evidence", fields=[{"key": "check", "label": "=HYPERLINK(\"http://evil\",\"Click\")", "type": "string", "required": False},
                                          {"key": "passed", "label": "@cmd", "type": "number", "required": False}])
    _, _, body = render_file(item, copy.deepcopy(ROWS[:1]))
    header = next(csv.reader(io.StringIO(body.decode())))
    assert header[-2:] == ["'=HYPERLINK(\"http://evil\",\"Click\")", "'@cmd"]


def test_output_file_that_cannot_be_written_does_not_fail_the_run(tmp_path, monkeypatch, caplog):
    import os

    real = os.open

    def refusing(path, flags, mode=0o777, **kwargs):
        if str(path).endswith(".partial"):
            raise OSError(28, "No space left on device: " + str(path))
        return real(path, flags, mode, **kwargs)
    monkeypatch.setattr(os, "open", refusing)
    repository, run_id = stored(tmp_path)
    record = repository.get(run_id)
    assert record["status"] == "completed"
    assert any(item["event"]["type"] == "ApplicationStateChanged" and item["event"]["stage"] == "draft" for item in record["events"])
    skipped = [item["event"] for item in record["events"] if item["event"]["type"] == "OutputFileSkipped"]
    assert {event["stage"] for event in skipped} == {"draft", "collect", "build"}
    assert skipped[0]["reason"] == "The file could not be written"
    assert str(tmp_path) not in json.dumps(record["events"]), "where the host stores files stays on the host"
    assert repository.files(run_id) == [] and list((tmp_path / "run-files" / run_id).iterdir()) == []


def test_output_file_download_cannot_reach_another_tenant(tmp_path, monkeypatch):
    from conclave.application.principal import Principal
    from conclave.asgi import application
    from test_asgi import call, make_services

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)
    tenant = {"value": "acme"}
    app = application(apis=services, token="secret", principal_for=lambda scope, headers: Principal("ada", tenant["value"]))
    auth = [("authorization", "Bearer secret")]

    async def exercise():
        assert (await call(app, headers=auth))[0] == 200
        repository = built["acme"].host.repository
        run_id = repository.create(wrapped(plan()))
        [event async for event in repository.execute(run_id, provider(), ToolRegistry(20000))]
        draft = by_stage(repository, run_id)["draft"]
        assert (await call(app, "GET", f"/api/runs/{run_id}/files/{draft['id']}", headers=auth))[0] == 200
        tenant["value"] = "beta"
        status, _, body = await call(app, "GET", f"/api/runs/{run_id}/files/{draft['id']}", headers=auth)
        assert status == 404 and NOTES.encode() not in body
        await app.close()
    asyncio.run(exercise())


# --- Performance -------------------------------------------------------------------------------------


def test_output_file_is_written_without_holding_up_the_host(tmp_path, monkeypatch):
    """Making and writing a large file happens beside the host's loop, not on it."""
    import os
    import time

    real = os.replace

    def slow(source, target):
        if "run-files" in str(target):
            time.sleep(0.3)
        return real(source, target)
    monkeypatch.setattr(os, "replace", slow)

    async def exercise():
        repository = RunRepository(tmp_path / "runs.sqlite")
        run_id = repository.create(wrapped(plan(evidence=False, implementation=False)))
        gaps, running = [], True

        async def ticking():
            last = time.perf_counter()
            while running:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        ticker = asyncio.create_task(ticking())
        [event async for event in repository.execute(run_id, provider(), ToolRegistry(20000))]
        running = False
        await ticker
        return repository, run_id, max(gaps)
    repository, run_id, longest = asyncio.run(exercise())
    # The write itself takes 0.3 s here; the loop may pause briefly for other work, never for that.
    assert longest < 0.2, f"the host stood still for {longest:.2f}s while a file was written"
    assert by_stage(repository, run_id)["draft"]["size"] == len(NOTES.encode())
