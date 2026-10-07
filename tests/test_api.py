import asyncio
import copy
import json
import pytest

from conclave.api import ApplicationAPI, start_server, studio_assets
from conclave.application.catalogue import configured_data, starter_definitions
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conclave.application.runs import RunRepository
from conclave import ToolRegistry
from conftest import ScriptedProvider
from test_studio_runs import with_final_stage


def make_api(tmp_path, provider=None):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider or ScriptedProvider(default={"type": "final", "content": "Done", "text": "REST result"}), ToolRegistry(20000), reviewer="operator")
    return ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))


def test_source_settings_are_validated_persisted_and_applied(tmp_path, monkeypatch):
    from conclave.application.service import configured_host
    monkeypatch.setenv('CONCLAVE_DATA_DIR', str(tmp_path))
    sources = {'documents': {'name': 'Documents', 'path': str(tmp_path), 'applications': []}}

    async def exercise():
        api = make_api(tmp_path)
        assert (await api.dispatch('PUT', '/api/settings/sources', {'sources': sources}))[0] == 200
        assert (await api.dispatch('GET', '/api/settings/sources'))[1]['sources'] == sources
        assert json.loads((tmp_path / 'sources.json').read_text()) == sources
        invalid = {'broken': {'path': str(tmp_path / 'missing')}}
        assert (await api.dispatch('PUT', '/api/settings/sources', {'sources': invalid}))[0] == 400
        assert set(api.host.resources) == {'documents'}
        await api.host.close()
    asyncio.run(exercise())
    host = configured_host()
    assert set(host.resources) == {'documents'}


def test_rest_named_resource_binding_is_pinned_and_paths_are_not_accepted(tmp_path):
    from test_application_resources import setup_resources, source_request

    async def exercise():
        tools, resources, _, _ = setup_resources(tmp_path)
        api = make_api(tmp_path)
        api.host.tools, api.host.resources, api.host.source_extensions = tools, resources, {'.md'}
        value = source_request('beta')
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': value['definition']})
        value = {key: item for key, item in value.items() if key != 'definition'}
        value.update(application_id=saved['id'], revision=saved['revision'])
        for invalid in ({'path': str(tmp_path)}, {'source': 'missing'}, {'source': 'alpha'}, {'source': 'beta', 'source_revision': 'fake'}):
            assert (await api.dispatch('POST', '/api/runs', {**value, 'resources': invalid}))[0] == 400
        assert api.host.repository.list(saved['id']) == []
        status, run = await api.dispatch('POST', '/api/runs', value)
        assert status == 201
        assert run['request']['resources'] == {'source': 'beta', 'source_revision': resources['beta']['revision']}
        assert str(resources['beta']['path']) not in json.dumps(run)
        await api.host.close()

    asyncio.run(exercise())


def test_saved_application_source_is_pinned_without_runtime_selection(tmp_path):
    from test_application_resources import setup_resources, source_request

    async def exercise():
        tools, resources, _, _ = setup_resources(tmp_path)
        api = make_api(tmp_path)
        api.host.tools, api.host.resources, api.host.source_extensions = tools, resources, {'.md'}
        value = source_request('beta')
        value['definition']['resources'] = value.pop('resources')
        _, saved = await api.dispatch('POST', '/api/applications', {'definition': value['definition']})
        value.pop('definition')
        value.update(application_id=saved['id'], revision=saved['revision'])
        status, record = await api.dispatch('POST', '/api/runs', value)
        assert status == 201
        assert record['request']['resources'] == {'source': 'beta', 'source_revision': resources['beta']['revision']}
        await api.host.close()
    asyncio.run(exercise())


def test_rest_application_revision_run_events_reviews_and_restart(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        request = with_final_stage()
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": request["definition"]})
        assert status == 201
        path = "/api/applications/" + saved["id"]
        assert (await api.dispatch("GET", path))[1] == saved
        revised = copy.deepcopy(saved["definition"])
        revised["name"] = "Changed custom application"
        status, updated = await api.dispatch("PUT", path, {"definition": revised, "revision": 1})
        assert status == 200 and updated["revision"] == 2
        assert (await api.dispatch("PUT", path, {"definition": revised, "revision": 1}))[0] == 409
        start = {key: value for key, value in request.items() if key != "definition"}
        start["application_id"] = saved["id"]
        assert (await api.dispatch("POST", "/api/runs", start))[0] == 409
        start["revision"] = 2
        assert (await api.dispatch("POST", "/api/runs", {**start, "definition": revised}))[0] == 400
        status, run = await api.dispatch("POST", "/api/runs", start)
        assert status == 201 and run["request"]["definition"] == revised
        active = api.host.tasks.get(run["id"])
        if active:
            await active[0]
        prefix = "/api/runs/" + run["id"]
        _, events = await api.dispatch("GET", prefix + "/events?after=0")
        assert events["events"] and events["status"] == "waiting_for_human"
        assert (await api.dispatch("GET", prefix + "/events?after=" + str(events["next_cursor"])))[1]["events"] == []
        assert (await api.dispatch("GET", prefix + "/events?after=-1"))[0] == 400
        _, reviews = await api.dispatch("GET", prefix + "/reviews")
        assert len(reviews["reviews"]) == 1
        await api.host.close()
        # Restart the actual shared repositories and continue from a durable review.
        api = make_api(tmp_path)
        review = {"review_id": reviews["reviews"][0]["id"], "output": {"text": "Approved"}}
        assert (await api.dispatch("POST", prefix + "/reviews", {**review, "reviewer": "spoof"}))[0] == 400
        assert (await api.dispatch("POST", prefix + "/reviews", review))[0] == 200
        assert (await api.dispatch("POST", prefix + "/resume", {}))[0] == 200
        active = api.host.tasks.get(run["id"])
        if active:
            await active[0]
        status, completed = await api.dispatch("GET", prefix)
        assert status == 200 and completed["status"] == "completed"
        assert completed["reviews"][0]["reviewer"] == "operator"
        assert len(api.host.provider.prompts) == 1  # Completed work was not replayed.
        assert (await api.dispatch("GET", "/api/runs?application_id=" + saved["id"]))[1]["runs"][0]["id"] == run["id"]
        assert (await api.dispatch("DELETE", path, {"revision": 1}))[0] == 409
        assert (await api.dispatch("DELETE", path, {"revision": 2}))[0] == 204
        assert (await api.dispatch("GET", path))[0] == 404
        assert (await api.dispatch("GET", prefix))[1]["request"]["definition"] == revised
        await api.host.close()
    asyncio.run(exercise())


def test_an_arranged_application_keeps_its_version_and_tracks_the_layout(tmp_path):
    """Where the cards sit is a change of its own, not a new application version."""

    async def exercise():
        api = make_api(tmp_path)
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": with_final_stage()["definition"]})
        assert status == 201
        assert (saved["revision"], saved["layout_revision"]) == (1, 0)
        path = "/api/applications/" + saved["id"]

        # The same plan, arranged: the layout advances and the version a request
        # reads does not, so moving a card cannot make a request look older.
        arranged = copy.deepcopy(saved["definition"])
        arranged["canvas"] = {"positions": {step["id"]: {"x": 40 * index, "y": 20 * index} for index, step in enumerate(arranged["workflow"])}}
        status, layout = await api.dispatch("PUT", path, {"definition": arranged, "revision": 1})
        assert status == 200 and (layout["revision"], layout["layout_revision"]) == (1, 1)
        assert layout["definition"]["canvas"] == arranged["canvas"]
        assert (await api.dispatch("GET", path))[1]["layout_revision"] == 1

        # A content edit still needs the version it read, and still advances it.
        revised = copy.deepcopy(layout["definition"])
        revised["workflow"][0]["task"] = "Read the supplied evidence once more"
        status, updated = await api.dispatch("PUT", path, {"definition": revised, "revision": 1})
        assert status == 200 and (updated["revision"], updated["layout_revision"]) == (2, 1)

        # Rearranging from the version before that edit is refused like any save,
        # and nothing of it is written.
        assert (await api.dispatch("PUT", path, {"definition": arranged, "revision": 1}))[0] == 409
        stored = (await api.dispatch("GET", path))[1]
        assert (stored["revision"], stored["layout_revision"]) == (2, 1)
        assert stored["definition"]["canvas"] == arranged["canvas"]
        await api.host.close()

    asyncio.run(exercise())


def test_legacy_application_layout_migration_preserves_content(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    definition = with_final_stage()["definition"]
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, definition TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO applications VALUES (?, ?, ?, ?)", ("legacy", json.dumps(definition), 7, "before"))
    store = Workspace(path)
    saved = store.get("legacy")
    assert (saved["revision"], saved["layout_revision"]) == (7, 0)
    assert saved["definition"] == definition
    definition["canvas"] = {"positions": {"draft": {"x": 100, "y": 80}}}
    store.save(definition, identifier="legacy", revision=7)
    restored = Workspace(path).get("legacy")
    assert (restored["revision"], restored["layout_revision"]) == (7, 1)
    assert restored["definition"] == definition
    assert store.save(with_final_stage()["definition"])["layout_revision"] == 0


def test_rest_catalogue_members_config_and_bad_inputs(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        for name in ("defaults", "catalogue"):
            assert (await api.dispatch("GET", "/api/config/" + name))[1] == configured_data(name)
        assert (await api.dispatch("GET", "/api/config/fixtures"))[0] == 404
        status, sdk = await api.dispatch("GET", "/api/sdk/application")
        assert status == 200 and sdk["filename"] == "conclave_application.py"
        compile(sdk["source"], sdk["filename"], "exec")
        _, catalogue = await api.dispatch("GET", "/api/catalogue")
        assert catalogue["revision"] == 0
        catalogue["catalogue"]["roles"].append({"id": "custom_operator", "name": "Operator", "kind": "human", "instructions": "Review the proposed result", "tools": []})
        assert (await api.dispatch("PUT", "/api/catalogue", catalogue))[0] == 200
        assert (await api.dispatch("PUT", "/api/catalogue", catalogue))[0] == 409
        member = catalogue["catalogue"]["roles"][0]
        status, saved = await api.dispatch("POST", "/api/members", {"member": member})
        assert status == 201
        assert (await api.dispatch("GET", "/api/members"))[1]["members"][0] == saved
        assert (await api.dispatch("DELETE", "/api/members/" + saved["id"], {}))[0] == 204
        assert (await api.dispatch("GET", "/api/members"))[1]["members"] == []
        assert (await api.dispatch("POST", "/api/applications", {"definition": []}))[0] == 400
        assert (await api.dispatch("POST", "/api/applications", {"definition": {}}))[0] == 400
        assert (await api.dispatch("GET", "/api/runs"))[0] == 400
        assert (await api.dispatch("PATCH", "/api/applications"))[0] == 405
        assert (await api.dispatch("GET", "/api/unknown"))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_http_boundary_token_origin_size_json_and_real_application(tmp_path):
    async def exercise():
        api = make_api(tmp_path)
        server = await start_server(api, 0, token="test-token")
        port = server.sockets[0].getsockname()[1]

        async def call(method, path, payload=b"", extra="", authenticate=True):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            auth = "Authorization: Bearer test-token\r\n" if authenticate else ""
            writer.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n{extra}\r\n".encode() + payload)
            await writer.drain()
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b':', 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            body = await reader.readexactly(length)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass
            return int(headers.split(b' ')[1]), json.loads(body) if body else None

        async with server:
            assert (await call("GET", "/api/health", authenticate=False))[0] == 401
            assert (await call("GET", "/api/health"))[0] == 200
            assert (await call("POST", "/api/applications", b"{}", "Origin: https://untrusted.example\r\n"))[0] == 403
            assert (await call("POST", "/api/applications", b"{"))[0] == 400
            assert (await call("POST", "/api/applications", b'x' * 200001))[0] == 413
            assert (await call("POST", "/api/applications", b'{"definition":NaN}'))[0] == 400
            definition = with_final_stage()["definition"]
            status, saved = await call("POST", "/api/applications", json.dumps({"definition": definition}).encode())
            assert status == 201
            assert (await call("GET", "/api/applications/" + saved["id"]))[1]["definition"] == definition
        await api.host.close()
    asyncio.run(exercise())


def test_canonical_starters_are_valid_and_do_not_overwrite_saved_edits(tmp_path):
    starters = starter_definitions(configured_data("catalogue"), configured_data("defaults"))
    assert len(starters) == 18
    store = Workspace(tmp_path / "workspace.sqlite", starters=starters)
    original = store.get("research-brief")
    original["definition"]["name"] = "User edits"
    store.save(original["definition"], identifier=original["id"], revision=1)
    restored = Workspace(store.path, starters=starters)
    assert restored.get("research-brief")["definition"]["name"] == "User edits"


def test_rest_validation_proposals_cancellation_and_redacted_errors(tmp_path):
    from conclave.providers import Completion

    async def exercise():
        api = make_api(tmp_path)
        definition = with_final_stage()["definition"]
        assert (await api.dispatch("POST", "/api/definitions/validate", {"definition": definition}))[1] == {"valid": True}
        member = {"id": "custom", "name": "Custom", "instructions": "Work", "tools": []}
        assert (await api.dispatch("DELETE", "/api/members/missing", {}))[0] == 404
        assert (await api.dispatch("PUT", "/api/applications/missing", {"definition": definition, "revision": 1}))[0] == 404
        assert (await api.dispatch("DELETE", "/api/applications/missing", {"revision": 1}))[0] == 404
        assert (await api.dispatch("DELETE", "/api/applications/missing", {"revision": 0}))[0] == 400
        assert (await api.dispatch("PUT", "/api/catalogue", {"catalogue": {"version": 1, "roles": [], "starters": [], "outputs": []}, "revision": -1}))[0] == 400
        _, saved = await api.dispatch("POST", "/api/applications", {"definition": definition})
        prefix = "/api/applications/" + saved["id"]
        assert (await api.dispatch("PUT", prefix, {"definition": definition, "revision": 0}))[0] == 400
        _, run = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": 1, "task": "Task", "inputs": {"sources": "Source"}})
        active = api.host.tasks.get(run["id"])
        if active:
            await active[0]
        assert (await api.dispatch("POST", "/api/runs/" + run["id"] + "/cancel", {}))[0] == 200
        started, release = asyncio.Event(), asyncio.Event()

        class Provider:
            async def complete(self, messages):
                started.set()
                await release.wait()
                return Completion(json.dumps({"definition": definition, "summary": "Proposal"}))

        api.host.provider = Provider()
        proposal = {"prompt": "Custom work", "definition": definition, "catalogue": {}}
        pending = asyncio.create_task(api.dispatch("POST", "/api/proposals", proposal))
        await started.wait()
        assert (await api.dispatch("POST", "/api/proposals", proposal))[0] == 409
        release.set()
        assert (await pending)[0] == 200
        original = api.workspace.members
        api.workspace.members = lambda: (_ for _ in ()).throw(RuntimeError("secret backend token"))
        status, error = await api.dispatch("GET", "/api/members")
        assert status == 503 and "secret" not in str(error)
        api.workspace.members = original
        assert (await api.dispatch("GET", "/outside"))[0] == 404
        await api.host.close()
    asyncio.run(exercise())


def test_the_studio_page_modules_and_download_are_served_from_this_origin(tmp_path):
    """One command, one address: the page and its API share an origin."""
    from conclave.api import static_page

    api = make_api(tmp_path)
    assets = studio_assets()
    assert assets, "This checkout ships the Studio bundle"

    page = static_page(api, "GET", "/", assets)
    assert page.status == 200 and page.content_type.startswith("text/html") and b"<title>Conclave" in page.body

    module = static_page(api, "GET", "/studio.js", assets)
    assert module.status == 200 and module.content_type.startswith("text/javascript") and len(module.body) > 1000

    # The page's modules import REST endpoints, never a file path.
    shared = static_page(api, "GET", "/definition.js", assets)
    assert b"/api/config/defaults" in shared.body and b"../../conclave" not in shared.body

    download = static_page(api, "GET", "/downloads/application.py", assets)
    assert download.content_type.startswith("text/x-python") and b"run_application" in download.body
    assert download.filename == "conclave_application.py"

    bundled = static_page(api, "GET", "/catalogue/defaults.json", assets)
    assert json.loads(bundled.body) == api.defaults

    # Anything else is not a page: the transport falls through to the API.
    assert static_page(api, "GET", "/not-a-page.js", assets) is None
    assert static_page(api, "GET", "/", {}) is None
    assert static_page(api, "GET", "/api/health", assets) is None
    assert static_page(api, "POST", "/", assets).status == 405
    assert studio_assets(tmp_path / "no-checkout") == {}

    asyncio.run(api.host.close())


class FakeWriter:
    """A connection that records the bytes the server writes."""

    def __init__(self):
        self.written = b""

    def get_extra_info(self, name):
        return ("127.0.0.1", 8080)

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class FakeReader:
    """The request head and body the handler reads."""

    def __init__(self, head, body=b""):
        self.head, self.body = head.encode(), body

    async def readuntil(self, separator):
        return self.head

    async def readexactly(self, count):
        assert count == len(self.body)
        return self.body


def respond_once(monkeypatch, api, request, *, body=b"", token=None, assets=None):
    """The server's bytes for one raw request, without binding a socket."""
    from types import SimpleNamespace
    from conclave import api as module

    captured = {}
    server = SimpleNamespace(close=lambda: None)

    async def capture(handler, host, port, **kwargs):
        captured["respond"] = handler
        return server

    monkeypatch.setattr(module.asyncio, "start_server", capture)
    asyncio.run(module.start_server(api, 0, token=token, assets=assets))
    writer = FakeWriter()
    asyncio.run(captured["respond"](FakeReader(request, body), writer))
    head, _, payload = writer.written.partition(b"\r\n\r\n")
    return head.decode(), payload


def test_the_transport_serves_pages_and_json_from_one_address(tmp_path, monkeypatch):
    api = make_api(tmp_path)
    assets = studio_assets()
    host = "Host: 127.0.0.1:8080\r\n"

    head, payload = respond_once(monkeypatch, api, f"GET / HTTP/1.1\r\n{host}\r\n", assets=assets)
    assert head.startswith("HTTP/1.1 200") and "Content-Type: text/html" in head
    assert f"Content-Length: {len(payload)}" in head and b"<title>Conclave" in payload

    head, payload = respond_once(monkeypatch, api, f"GET /api/health HTTP/1.1\r\n{host}\r\n", assets=assets)
    assert head.startswith("HTTP/1.1 200") and "Content-Type: application/json" in head
    assert json.loads(payload) == {"status": "ok", "api_version": 1}

    # A page path is never a write, and the API still answers for itself.
    head, _ = respond_once(monkeypatch, api, f"POST / HTTP/1.1\r\n{host}Content-Type: application/json\r\nContent-Length: 0\r\n\r\n", assets=assets)
    assert head.startswith("HTTP/1.1 405")
    head, payload = respond_once(monkeypatch, api, f"GET /api/nothing HTTP/1.1\r\n{host}\r\n", assets=assets)
    assert head.startswith("HTTP/1.1 404") and json.loads(payload)["error"]

    # The adapter download keeps its filename and type.
    head, payload = respond_once(monkeypatch, api, f"GET /downloads/application.py HTTP/1.1\r\n{host}\r\n", assets=assets)
    assert 'Content-Disposition: attachment; filename="conclave_application.py"' in head
    assert b"run_application" in payload

    # A token guards the API, and a page is not served without it.
    head, _ = respond_once(monkeypatch, api, f"GET /api/health HTTP/1.1\r\n{host}\r\n", token="secret")
    assert head.startswith("HTTP/1.1 401")
    head, _ = respond_once(monkeypatch, api, f"GET / HTTP/1.1\r\n{host}\r\n")
    assert head.startswith("HTTP/1.1 404")

    # A HEAD response describes the page without sending it.
    head, payload = respond_once(monkeypatch, api, f"HEAD / HTTP/1.1\r\n{host}\r\n", assets=assets)
    assert payload == b"" and "Content-Length: 0" not in head

    asyncio.run(api.host.close())


def test_startup_lines_say_where_the_page_and_the_backend_are(tmp_path):
    from conclave.api import startup_lines

    api = make_api(tmp_path)
    api.host.provider_name, api.host.provider_source = "opencode", "detected"
    address = "http://127.0.0.1:8080"

    assert startup_lines(api, address, {"/index.html": (b"page", "text/html")}, None) == [
        f"Conclave REST API: {address}/api/health",
        f"Studio: {address}",
        "AI backend: opencode (found on this computer)",
    ]

    # A token deployment keeps the Studio dev server; a page cannot send it.
    lines = startup_lines(api, address, {}, "secret")
    assert "CONCLAVE_API_TOKEN" in lines[1] and "Studio:" not in lines[1]

    # With nothing installed, the line is the next action rather than a blank.
    api.host.provider, api.host.provider_name, api.host.provider_source = None, "codex", "configured"
    api.host.unavailable = "The configured codex command is unavailable on this host"
    assert startup_lines(api, address, {}, None)[-1] == "AI backend: unavailable. The configured codex command is unavailable on this host"

    # A hosted host serves the API only, and says so instead of staying silent.
    from conclave.application.profile import HOSTED

    api.host.profile = HOSTED
    lines = startup_lines(api, address, {}, None)
    assert lines[1] == "This hosted host serves the API only; deploy the Studio bundle separately."


def test_a_token_deployment_does_not_serve_the_page(tmp_path, monkeypatch, capsys):
    """A browser page cannot present a bearer token, so it is not offered."""
    import signal
    from types import SimpleNamespace
    from conclave import api as module

    class FakeServer:
        """A started server, without binding a socket."""

        sockets = [SimpleNamespace(getsockname=lambda: ("127.0.0.1", 8080))]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(module, "configured_api", lambda **kwargs: make_api(tmp_path))
    monkeypatch.setenv("CONCLAVE_API_TOKEN", "test-token")
    monkeypatch.setattr(module, "studio_assets", lambda *args: {"/index.html": (b"page", "text/html")})

    async def exercise():
        loop = asyncio.get_running_loop()
        callbacks = {}
        monkeypatch.setattr(loop, "add_signal_handler", lambda signal, callback: callbacks.update({signal: callback}))
        started, served = asyncio.Event(), {}
        server = FakeServer()

        async def start(*args, **kwargs):
            served.update(kwargs)
            started.set()
            return server

        monkeypatch.setattr(module, "start_server", start)
        task = asyncio.create_task(module.serve(0))
        await asyncio.wait_for(started.wait(), timeout=10)
        assert served["assets"] == {} and served["token"] == "test-token"
        output = capsys.readouterr().out
        assert "CONCLAVE_API_TOKEN" in output and "Studio:" not in output
        callbacks[signal.SIGTERM]()
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(exercise())


def test_serve_lifecycle_starts_http_and_closes_on_signal(tmp_path, monkeypatch, capsys):
    import signal
    from conclave import api as module

    async def exercise():
        api = make_api(tmp_path)
        monkeypatch.setattr(module, "configured_api", lambda **kwargs: api)
        callbacks = {}
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda signal, callback: callbacks.update({signal: callback}))
        started = asyncio.Event()
        real_start = module.start_server
        servers = []

        async def start(*args, **kwargs):
            server = await real_start(*args, **kwargs)
            servers.append(server)
            started.set()
            return server

        monkeypatch.setattr(module, "start_server", start)
        task = asyncio.create_task(module.serve(0))
        await started.wait()
        assert servers[0].is_serving()
        assert "Conclave REST API" in capsys.readouterr().out
        callbacks[signal.SIGTERM]()
        await task
        assert not servers[0].is_serving()
    asyncio.run(exercise())


def test_deleted_starter_stays_deleted_after_restart_and_legacy_samples_are_hidden(tmp_path):
    starters = starter_definitions(configured_data("catalogue"), configured_data("defaults"))
    assert all(not any(key.startswith("sample_") for key in item["definition"]) for item in starters)
    store = Workspace(tmp_path / "workspace.sqlite", starters=starters)
    saved = store.get("research-brief")
    legacy = {**saved["definition"], "sample_request": {"task": "Old sample"}, "sample_outputs": {"report": "Fictional"}, "sample_sources": []}
    with store.connect() as connection:
        connection.execute("UPDATE applications SET definition = ? WHERE id = ?", (json.dumps(legacy), saved["id"]))
    assert store.get(saved["id"])["definition"] == saved["definition"]
    assert next(item for item in store.applications() if item["id"] == saved["id"])["definition"] == saved["definition"]
    store.delete(saved["id"], saved["revision"])
    restored = Workspace(store.path, starters=starters)
    with pytest.raises(KeyError):
        restored.get(saved["id"])
    assert len(restored.applications()) == len(starters) - 1
