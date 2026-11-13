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
    # A code checkout keeps the files its tools read through a settings round trip.
    sources = {'documents': {'name': 'Documents', 'path': str(tmp_path), 'applications': []},
               'code': {'name': 'Code', 'path': str(tmp_path), 'applications': None, 'extensions': '*'},
               'rust': {'name': 'Rust', 'path': str(tmp_path), 'applications': None, 'extensions': ['.rs', '.toml']}}

    async def exercise():
        api = make_api(tmp_path)
        assert (await api.dispatch('PUT', '/api/settings/sources', {'sources': sources}))[0] == 200
        assert (await api.dispatch('GET', '/api/settings/sources'))[1]['sources'] == sources
        assert json.loads((tmp_path / 'sources.json').read_text()) == sources
        invalid = {'broken': {'path': str(tmp_path / 'missing')}}
        assert (await api.dispatch('PUT', '/api/settings/sources', {'sources': invalid}))[0] == 400
        assert set(api.host.resources) == {'documents', 'code', 'rust'}
        await api.host.close()
    asyncio.run(exercise())
    host = configured_host()
    assert set(host.resources) == {'documents', 'code', 'rust'}
    assert host.resources['code']['extensions'] is None and host.resources['rust']['extensions'] == {'.rs', '.toml'}


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


def test_local_host_answers_who_the_page_is_without_a_sign_in(tmp_path):
    # Studio asks /api/me first and shows Google sign-in when nobody answers;
    # a local host has one operator and no login, so it must say so.
    status, payload = asyncio.run(make_api(tmp_path).dispatch("GET", "/api/me"))
    assert status == 200
    assert payload["login"] is None
    assert payload["principal"]["tenant"] == "local" and payload["principal"]["label"]


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


def respond_once(monkeypatch, api, request, *, body=b"", token=None, assets=None, app=None):
    """The server's bytes for one raw request, without binding a socket."""
    from types import SimpleNamespace
    from conclave import api as module

    captured = {}
    server = SimpleNamespace(close=lambda: None)

    async def capture(handler, host, port, **kwargs):
        captured["respond"] = handler
        return server

    monkeypatch.setattr(module.asyncio, "start_server", capture)
    asyncio.run(module.start_server(api, 0, token=token, assets=assets, app=app))
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
        # Binding a real port is the one step here an environment can refuse. A
        # server that never starts leaves the event unset and keeps its failure
        # inside the task, so the wait is bounded and that failure is surfaced
        # instead of blocking the suite forever.
        try:
            await asyncio.wait_for(started.wait(), timeout=10)
        except TimeoutError:
            if task.done():
                task.result()
            raise
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


# --- Serving a deployment's adapter from this machine ------------------------

def hosted_app(*, login_result, origins=()):
    from conclave.api import TenantAPIs
    from conclave.asgi import application

    return application(apis=TenantAPIs(), login=login_result, origins=origins)


def header(head, name):
    """One response header, whatever case the writer spelled it in."""

    for line in head.split("\r\n")[1:]:
        key, _, value = line.partition(": ")
        if key.lower() == name.lower():
            return value
    raise AssertionError(f"{name} is missing from {head!r}")


def request_head(method, target, *headers):
    return f"{method} {target} HTTP/1.1\r\n" + "".join(f"{line}\r\n" for line in headers) + "\r\n"


def hosted_login(monkeypatch, **overrides):
    """A login whose callback this deployment listed for a loopback address."""

    from test_identity import claims, config, id_token, keypair
    from conclave.identity import GoogleLogin

    key, _ = keypair()
    settings = {"redirect_uris": ["http://127.0.0.1:8080/auth/google/callback"],
                "after_login": "http://127.0.0.1:8080/", **overrides}
    result = GoogleLogin(config(**settings), _HostedHTTP(key), session_key="k" * 40)
    return key, result


class _HostedHTTP:
    """The token endpoint and key set, without a network."""

    def __init__(self, key):
        self.key, self.token = key, None

    async def request(self, method, url, *, headers=None, body=None, params=None, raw=False):
        if url.endswith("/token"):
            return self.token or {}
        from test_identity import keypair
        from conclave.identity import public_key_from_jwk
        _, jwk = keypair()
        numbers = self.key.public_key().public_numbers()
        from test_identity import encode
        jwk = {**jwk, "n": encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
               "e": encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))}
        return {"keys": [jwk]}


def test_the_hosted_adapter_runs_on_this_machine(tmp_path, monkeypatch):
    """`conclave serve --hosted`: the deployment's own adapter, over loopback."""

    from test_identity import claims, id_token

    key, login_result = hosted_login(monkeypatch)
    app = hosted_app(login_result=login_result, origins=("http://localhost:5173",))
    assets = studio_assets()
    host = "Host: 127.0.0.1:8080"

    # The Studio page and the API are served from one address, as locally.
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/", host), assets=assets, app=app)
    assert head.startswith("HTTP/1.1 200") and b"<title>Conclave" in payload

    # Nothing is served to an unauthenticated API caller but a way to sign in.
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/me", host), app=app)
    assert head.startswith("HTTP/1.1 401") and json.loads(payload) == {"error": "Sign in to continue",
                                                                      "login": "/auth/google/start"}

    # The redirect URI Google is given is the loopback one this login listed.
    head, _ = respond_once(monkeypatch, None, request_head("GET", "/auth/google/start", host), app=app)
    assert head.startswith("HTTP/1.1 302")
    location = header(head, "location")
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Fauth%2Fgoogle%2Fcallback" in location
    state = location.split("state=")[1].split("&")[0]

    # Google answers with a token for the nonce this deployment asked for.
    login_result.transport.token = {"id_token": id_token(key, claims(nonce=login_result.pending[state][2]))}
    head, _ = respond_once(monkeypatch, None, request_head("GET", f"/auth/google/callback?code=code-1&state={state}", host), app=app)
    assert head.startswith("HTTP/1.1 302") and header(head, "location") == "http://127.0.0.1:8080/"
    cookie = header(head, "set-cookie")
    assert cookie.startswith("conclave_session=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie

    # That cookie is the credential: it reaches the API, and the page may read it.
    value = cookie.split(";")[0].split("=", 1)[1]
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/me", host,
                                                                 f"Cookie: conclave_session={value}",
                                                                 "Origin: http://localhost:5173"), app=app)
    assert head.startswith("HTTP/1.1 200") and head.lower().count("access-control-allow-credentials: true") == 1
    assert json.loads(payload)["principal"]["name"] == "person@example.com"

    # The adapter's own rules decide now, not the loopback check: a credential
    # that exists is still not readable from an origin this deployment never named.
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/me", host,
                                                                 f"Cookie: conclave_session={value}",
                                                                 "Origin: http://elsewhere.test"), app=app)
    assert head.startswith("HTTP/1.1 403") and json.loads(payload)["error"] == "Use an allowed origin"
    # And without a credential the answer is the same wherever it came from.
    head, _ = respond_once(monkeypatch, None, request_head("GET", "/api/me", host, "Origin: http://elsewhere.test"), app=app)
    assert head.startswith("HTTP/1.1 401")


def test_a_hosted_page_may_be_turned_off_and_the_api_still_runs(tmp_path, monkeypatch):
    """A deployment that ships the bundle elsewhere serves the API alone."""

    _, login_result = hosted_login(monkeypatch)
    app = hosted_app(login_result=login_result, origins=("https://studio.example.com",))
    head, _ = respond_once(monkeypatch, None, request_head("GET", "/", "Host: 127.0.0.1:8080"), app=app)
    assert header(head, "content-type").startswith("application/json")
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/health", "Host: 127.0.0.1:8080"), app=app)
    assert "HTTP/1.1 401" in head
    # A non-browser caller still has to be on this machine: only loopback Hosts.
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/health", "Host: api.example.com"), app=app)
    assert "HTTP/1.1 403" in head and json.loads(payload)["error"] == "Use the local Conclave address"


def test_serving_the_hosted_adapter_prints_where_to_sign_in(tmp_path, monkeypatch, capsys):
    """`conclave serve --hosted` is how sign-in is tried before deploying."""

    import json as _json
    from types import SimpleNamespace

    from conclave import api as module
    from conclave.identity import MINIMUM_SESSION_KEY
    from test_identity import keypair

    _, jwk = keypair()
    login_file = tmp_path / "login.json"
    login_file.write_text(_json.dumps({
        "client_id": "client-1", "client_secret": "secret-1",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "hosts": ["accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"],
        "redirect_uris": ["http://127.0.0.1:8123/auth/google/callback"],
        "tenant": "acme", "emails": ["person@example.com"]}))
    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(login_file))
    monkeypatch.setenv("CONCLAVE_SESSION_KEY", "k" * MINIMUM_SESSION_KEY)
    monkeypatch.setenv("CONCLAVE_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.delenv("CONCLAVE_API_TOKEN", raising=False)

    captured = {}

    class Server:
        """A socket that is served, then closed, without binding one."""

        sockets = []

        async def __aenter__(self):
            self.sockets = [SimpleNamespace(getsockname=lambda: ("127.0.0.1", captured["port"]))]
            return self

        async def __aexit__(self, *exception):
            return False

    async def capture(api, port, *, token=None, assets=None, app=None):
        captured.update(api=api, port=port, app=app, assets=assets)
        return Server()

    class Stopped(asyncio.Event):
        async def wait(self):
            return None

    monkeypatch.setattr(module, "start_server", capture)
    monkeypatch.setattr(module.asyncio, "Event", Stopped)
    asyncio.run(module.serve(8123, hosted=True))

    printed = capsys.readouterr().out
    assert captured["port"] == 8123 and captured["api"] is None
    assert captured["app"].login is not None and captured["app"].login.config.tenant == "acme"
    assert "http://127.0.0.1:8123/api/health" in printed
    assert "Google sign-in: http://127.0.0.1:8123/auth/google/start" in printed
    # An unnamed origin may read but not write, so the command says so outright.
    assert "Page origins allowed to call this API: none." in printed
    assert "Set CONCLAVE_UI_ORIGINS=http://127.0.0.1:8123" in printed
    monkeypatch.setenv("CONCLAVE_UI_ORIGINS", "http://127.0.0.1:8123, https://studio.example.com")
    asyncio.run(module.serve(8123, hosted=True))
    assert "Page origins allowed to call this API: http://127.0.0.1:8123 https://studio.example.com" in capsys.readouterr().out
    monkeypatch.delenv("CONCLAVE_UI_ORIGINS", raising=False)

    # Without a login file the same command serves the API and says so.
    monkeypatch.delenv("CONCLAVE_GOOGLE_LOGIN_FILE", raising=False)
    asyncio.run(module.serve(8123, hosted=True))
    assert "Google sign-in: off." in capsys.readouterr().out
    assert captured["assets"], "a local profile serves the page"

    # On a hosted profile the page is served elsewhere, as in a deployment.
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    asyncio.run(module.serve(8123, hosted=True))
    assert captured["assets"] == {} and "Studio:" not in capsys.readouterr().out
    monkeypatch.delenv("CONCLAVE_PROFILE", raising=False)


def test_an_adapter_that_fails_is_not_reported_as_a_bad_request(tmp_path, monkeypatch):
    """A broken deployment is a 500 here, and it still says nothing private."""

    async def broken(scope, receive, send):
        raise RuntimeError("private host detail")

    async def silent(scope, receive, send):
        return None

    for app in (broken, silent):
        head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/health", "Host: 127.0.0.1:8080"), app=app)
        assert "HTTP/1.1 500" in head and "private host detail" not in payload.decode()


def test_a_misconfigured_login_is_reported_as_a_setting(tmp_path, monkeypatch, capsys):
    """The command says which setting is wrong, in one line."""

    from conclave import api as module
    from conclave import cli

    monkeypatch.setenv("CONCLAVE_GOOGLE_LOGIN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("CONCLAVE_SESSION_KEY", "k" * 32)
    with pytest.raises(ValueError, match="login file could not be read"):
        asyncio.run(module.serve(8123, hosted=True))

    with pytest.raises(SystemExit) as exit_code:
        cli.run(["serve", "--hosted", "--port", "8123"])
    assert exit_code.value.code == 2
    assert "clave: The login file could not be read: FileNotFoundError" in capsys.readouterr().err


def test_the_deployments_own_page_may_write_to_it(tmp_path, monkeypatch):
    """A page the adapter serves itself is not a foreign origin.

    Browsers attach an origin to every write, including same-origin ones, so a
    deployment that judged origins only by the list of *other* pages would refuse
    its own page. The origin has to match the host the request arrived on.
    """

    from conclave.api import TenantAPIs
    from conclave.asgi import application
    from conclave.cli import create_definition

    # This test writes an application, so the store is its own directory rather
    # than the one this checkout uses.
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    app = application(apis=TenantAPIs(), token="a-token", origins=("http://localhost:5173",))
    host = "Host: 127.0.0.1:8080"
    definition = json.dumps({"definition": create_definition("one", "Do the thing the person asked for, carefully.")}).encode()

    def write(origin):
        return respond_once(monkeypatch, None,
                            request_head("POST", "/api/applications", host, "Authorization: Bearer a-token",
                                         f"Origin: {origin}", "Content-Type: application/json",
                                         f"Content-Length: {len(definition)}"),
                            body=definition, app=app)

    # The deployment's own page, posting to the address it was served from.
    head, payload = write("http://127.0.0.1:8080")
    assert head.startswith("HTTP/1.1 201"), payload

    # Every other page is still refused, including one claiming this port's host
    # from another host name, and one claiming this host from another port.
    for origin in ("https://elsewhere.test", "http://localhost:8080", "http://127.0.0.1:9999"):
        head, payload = write(origin)
        assert head.startswith("HTTP/1.1 403") and json.loads(payload)["error"] == "Use an allowed origin", origin


def test_the_pages_own_modules_load_before_signing_in(tmp_path, monkeypatch):
    """The page cannot reach its sign-in screen if its modules are gated.

    `definition.js` and `catalogue.js` are served rewritten to import this
    deployment's defaults and catalogue over REST, so they are part of the page's
    module graph: a 401 there is a page that never loads, which is what a
    signed-out visitor would see instead of a way in. They hold the deployment's
    own configuration, not anybody's data, and the rest of the API stays gated.
    """

    from conclave.api import TenantAPIs
    from conclave.asgi import application

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    key, login_result = hosted_login(monkeypatch)
    app = hosted_app(login_result=login_result, origins=("http://localhost:5173",))
    host = "Host: 127.0.0.1:8080"

    for module in ("/api/config/defaults", "/api/config/catalogue"):
        head, payload = respond_once(monkeypatch, None, request_head("GET", module, host), app=app)
        assert head.startswith("HTTP/1.1 200"), (module, head)
        assert json.loads(payload), module

    # Everything that is somebody's data is still gated, including a write.
    head, payload = respond_once(monkeypatch, None, request_head("GET", "/api/me", host), app=app)
    assert head.startswith("HTTP/1.1 401") and json.loads(payload)["login"] == "/auth/google/start"
    head, _ = respond_once(monkeypatch, None, request_head("GET", "/api/applications", host), app=app)
    assert head.startswith("HTTP/1.1 401")

    # A deployment that authenticates machines instead keeps its configuration
    # to itself: no browser flow means no page to boot.
    machine = application(apis=TenantAPIs(), token="a-token", origins=("http://localhost:5173",))
    head, _ = respond_once(monkeypatch, None, request_head("GET", "/api/config/catalogue", host), app=machine)
    assert head.startswith("HTTP/1.1 401")


def test_a_streamed_response_reaches_the_socket_frame_by_frame(monkeypatch):
    """A run's events must reach a watching page while the run is still working.

    The adapter's messages are written as they arrive. A server that collected
    them first would hold an endless event stream until the run finished, and the
    page would sit on whatever snapshot it started with — with no error to fall
    back on, which is exactly how a working step looks stuck.
    """

    from types import SimpleNamespace

    from conclave import api as module

    writer = FakeWriter()
    seen = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        seen.append(writer.written)
        await send({"type": "http.response.body", "body": b"data: one\n\n", "more_body": True})
        seen.append(writer.written)
        await send({"type": "http.response.body", "body": b"data: two\n\n", "more_body": True})
        seen.append(writer.written)

    async def capture(handler, host, port, **kwargs):
        capture.respond = handler
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(module.asyncio, "start_server", capture)
    asyncio.run(module.start_server(None, 0, app=app))
    asyncio.run(capture.respond(FakeReader(request_head("GET", "/api/runs/abc/events", "Host: 127.0.0.1:8080")), writer))

    # The head is out before the first event, and each event is out before the
    # adapter is asked for the next one.
    assert seen[0].startswith(b"HTTP/1.1 200 Response\r\ncontent-type: text/event-stream")
    assert seen[0].endswith(b"\r\n\r\n") and b"data:" not in seen[0]
    assert b"data: one\n\n" in seen[1] and b"data: two" not in seen[1]
    assert b"data: one\n\ndata: two\n\n" in seen[2]
    assert writer.written.endswith(b"data: two\n\n")


def test_a_client_that_went_away_is_not_an_error(tmp_path, monkeypatch):
    """Closing a stream you are watching is not this server failing.

    A browser that navigates away mid-stream leaves a broken pipe behind. The
    response is still written where it can be, and the connection is closed
    quietly: the run carries on in its own task either way.
    """

    from types import SimpleNamespace

    from conclave import api as module

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))

    class ClosedEarly(FakeWriter):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            raise BrokenPipeError(32, "Broken pipe")

    writer = ClosedEarly()

    async def capture(handler, host, port, **kwargs):
        capture.respond = handler
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(module.asyncio, "start_server", capture)
    asyncio.run(module.start_server(make_api(tmp_path), 0))
    asyncio.run(capture.respond(FakeReader(request_head("GET", "/api/runtime", "Host: 127.0.0.1:8080")), writer))

    assert writer.written.startswith(b"HTTP/1.1 200 Response"), "the answer is still written"
    assert writer.closed, "and the connection is still closed"



def test_studio_serves_images_and_fonts_with_their_types(tmp_path):
    # Images and fonts under studio/web are served with the type a browser
    # needs to draw them.
    web, shared = tmp_path / "web", tmp_path / "shared"
    (web / "images").mkdir(parents=True)
    shared.mkdir()
    for module in ("definition.js", "catalogue.js"):
        (shared / module).write_text("export {};")
    for name in ("manifest.json", "mark.webp", "mark.png", "mark.jpg", "mark.avif"):
        (web / "images" / name).write_bytes(b"x")
    (web / "fonts").mkdir()
    (web / "fonts" / "jost.woff2").write_bytes(b"wOF2")
    assets = studio_assets(tmp_path)
    # Studio's fonts are self-hosted and served as fonts.
    assert assets["/fonts/jost.woff2"] == (b"wOF2", "font/woff2")
    assert assets["/images/manifest.json"][1].startswith("application/json")
    assert {assets[f"/images/mark.{ext}"][1] for ext in ("webp", "png", "jpg", "avif")} == {
        "image/webp", "image/png", "image/jpeg", "image/avif"}
