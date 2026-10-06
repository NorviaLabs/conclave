"""Local REST adapter for Conclave's shared application service.

HTTP presentation stays outside the reusable multi-agent runtime. This local,
single-user server uses asyncio and SQLite without adding core dependencies.
"""
import asyncio
import hmac
import json
import signal
from typing import NamedTuple
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .application.catalogue import configured_data, starter_definitions
from .application.definition import validate_definition
from .application.service import configured_host, setting
from .application.workspace import Workspace, Conflict
from .application.resources import load_resources


SDK_SOURCE = '"""Compatibility facade for the installed Conclave application adapter."""\nfrom conclave.application import run_application, validate_definition\n\n__all__ = ["run_application", "validate_definition"]\n'

CONTENT_TYPES = {
    ".css": "text/css;charset=utf-8",
    ".html": "text/html;charset=utf-8",
    ".js": "text/javascript;charset=utf-8",
    ".json": "application/json;charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

# The browser loads its modules from absolute paths, so the page and its
# sources live beside the application data they import over REST.
SHARED_MODULES = {
    "definition.js": ("'../../conclave/application/data/defaults.json'", "/api/config/defaults"),
    "catalogue.js": ("'../../conclave/application/data/catalogue.json'", "/api/config/catalogue"),
}


class StaticResponse(NamedTuple):
    status: int
    body: bytes
    content_type: str
    filename: str | None = None


def studio_assets(root=None) -> dict[str, tuple[bytes, str]]:
    """The Studio page and its modules, keyed by request path.

    Read once at startup: the server never touches the filesystem for a
    request path, so no crafted path can reach a file outside this bundle.
    A checkout without Studio serves the REST API alone.
    """

    studio = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "studio"
    web, shared = studio / "web", studio / "shared"

    if not web.is_dir() or not shared.is_dir():
        return {}

    assets = {
        "/" + path.relative_to(web).as_posix(): (
            path.read_bytes(),
            CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        )
        for path in sorted(web.rglob("*"))
        if path.is_file()
    }

    for name, (imported, endpoint) in SHARED_MODULES.items():
        source = (shared / name).read_text().replace(imported, f"'{endpoint}'")

        assets["/" + name] = (source.encode(), CONTENT_TYPES[".js"])

    return assets


def static_page(api, method, target, assets) -> StaticResponse | None:
    """A served page, module or downloaded adapter, or None for no match."""

    path = unquote(urlsplit(target).path)

    if method not in {"GET", "HEAD"}:
        return StaticResponse(405, b"", "text/plain;charset=utf-8")

    if path == "/downloads/application.py":
        return StaticResponse(200, SDK_SOURCE.encode(), "text/x-python;charset=utf-8", "conclave_application.py")

    catalogue = {"/catalogue/defaults.json": api.defaults, "/catalogue/bundled.json": api.catalogue}

    if path in catalogue:
        return StaticResponse(200, json.dumps(catalogue[path]).encode(), CONTENT_TYPES[".json"])

    asset = assets.get("/index.html" if path == "/" else path)

    if asset is None:
        return None

    return StaticResponse(200, asset[0], asset[1])


def startup_lines(api, address, assets, token) -> list[str]:
    """What an operator needs at startup: where it is, and what runs it."""

    lines = [f"Conclave REST API: {address}/api/health"]

    if assets:
        lines.append(f"Studio: {address}")
    elif token:
        # A browser page cannot present a bearer token, so a token
        # deployment keeps the Studio dev server and this API separate.
        lines.append("Studio is not served while CONCLAVE_API_TOKEN is set; start the Studio dev server instead.")
    elif not api.host.profile.serve_ui:
        lines.append(f"This {api.host.profile.name} host serves the API only; deploy the Studio bundle separately.")

    if api.host.provider is not None:
        lines.append(f"AI backend: {api.host.provider_name or 'configured'}"
                     + (" (found on this computer)" if api.host.provider_source == "detected" else ""))
    elif api.host.unavailable:
        lines.append(f"AI backend: unavailable. {api.host.unavailable}")

    return lines


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON numbers are unsupported")


class ApplicationAPI:
    def __init__(self, host, workspace, *, defaults=None, catalogue=None):
        self.host, self.workspace = host, workspace
        self.defaults = defaults if defaults is not None else configured_data("defaults")
        self.catalogue = catalogue if catalogue is not None else configured_data("catalogue")
        self.proposal_lock = asyncio.Lock()

    async def dispatch(self, method, target, value=None, *, principal=None, streaming=False):
        """Answer one request. `principal` is who acted, when the transport
        established one; the host's own principal answers otherwise. `streaming`
        is what the transport can do, which decides whether this host reports
        that a run's events can be pushed."""
        try:
            return await self._dispatch(method, target, value, principal, streaming)
        except Conflict as error:
            return 409, {"error": str(error)}
        except KeyError:
            return 404, {"error": "Resource not found"}
        except ValueError as error:
            return 400, {"error": str(error)}
        except (TypeError, AttributeError):
            return 400, {"error": "Invalid request fields"}
        except Exception:
            # Backend exceptions may contain credentials; never expose them.
            return 503, {"error": "The Conclave service is unavailable"}

    @staticmethod
    def fields(value, required):
        if not isinstance(value, dict) or set(value) != set(required):
            raise ValueError("Use the supported request fields")

    async def _dispatch(self, method, target, value, principal=None, streaming=False):
        url = urlsplit(target)
        parts = [unquote(part) for part in url.path.strip('/').split('/')]
        query = parse_qs(url.query)
        if parts[:1] != ["api"]:
            return 404, {"error": "Endpoint not found"}
        path = url.path
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            return 405, {"error": "Method not allowed"}
        if method == "GET" and path == "/api/health":
            return 200, {"status": "ok", "api_version": 1}
        if method == "GET" and path == "/api/runtime":
            return await self.host.handle("runtime", {}, streaming=streaming)
        if path == "/api/settings/sources":
            if method == "GET":
                return 200, {"sources": {key: {"name": item["name"], "path": str(item["path"]),
                                              "applications": item["applications"]}
                                          for key, item in (self.host.resources or {}).items()}}
            if method == "PUT":
                self.fields(value, {"sources"})
                if not isinstance(value["sources"], dict):
                    raise ValueError("Source settings must contain a resource object")
                if self.host.profile.name != "local":
                    # A tenant of a hosted deployment does not name a path on
                    # the server: its sources come from the deployment, which is
                    # also what keeps one tenant's folders away from another.
                    raise ValueError("This host's sources are configured by the deployment")
                resources = load_resources(None, configured=value["sources"])
                filename = self.host.paths.root / "sources.json"
                filename.parent.mkdir(parents=True, exist_ok=True)
                temporary = filename.with_suffix('.json.new')
                temporary.write_text(json.dumps(value["sources"], indent=2) + '\n')
                temporary.replace(filename)
                self.host.resources = resources
                return 200, {"saved": True}
        if method == "GET" and path == "/api/sdk/application":
            return 200, {"filename": "conclave_application.py", "source": SDK_SOURCE}
        if method == "GET" and len(parts) == 3 and parts[1] == "config":
            data = {"defaults": self.defaults, "catalogue": self.catalogue}
            if parts[2] in data:
                return 200, data[parts[2]]
        if path == "/api/definitions/validate" and method == "POST":
            self.fields(value, {"definition"})
            validate_definition(value["definition"])
            return 200, {"valid": True}
        if path == "/api/catalogue":
            if method == "GET":
                return 200, self.workspace.catalogue()
            if method == "PUT":
                self.fields(value, {"catalogue", "revision"})
                return 200, self.workspace.save_catalogue(value["catalogue"], value["revision"], self.catalogue)
        if path == "/api/applications":
            if method == "GET":
                return 200, {"applications": self.workspace.applications()}
            if method == "POST":
                self.fields(value, {"definition"})
                return 201, self.workspace.save(value["definition"])
        if len(parts) == 3 and parts[1] == "applications":
            if method == "GET":
                return 200, self.workspace.get(parts[2])
            if method == "PUT":
                self.fields(value, {"definition", "revision"})
                return 200, self.workspace.save(value["definition"], identifier=parts[2], revision=value["revision"])
            if method == "DELETE":
                self.fields(value, {"revision"})
                self.workspace.delete(parts[2], value["revision"])
                return 204, None
        if path == "/api/members":
            if method == "GET":
                return 200, {"members": self.workspace.members()}
            if method == "POST":
                self.fields(value, {"member"})
                return 201, self.workspace.add_member(value["member"])
        if len(parts) == 3 and parts[1] == "members" and method == "DELETE":
            self.fields(value, set())
            self.workspace.delete_member(parts[2])
            return 204, None
        if path == "/api/proposals" and method == "POST":
            if self.proposal_lock.locked():
                return 409, {"error": "A proposal is already being generated"}
            async with self.proposal_lock:
                return await self.host.handle("propose", value)
        if path == "/api/runs":
            if method == "GET":
                identifiers = query.get("application_id", [])
                if len(identifiers) != 1:
                    raise ValueError("Choose an application")
                return await self.host.handle("list", {"application_id": identifiers[0]})
            if method == "POST":
                self.fields(value, {"version", "application_id", "revision", "task", "inputs"} | ({"resources"} if isinstance(value, dict) and "resources" in value else set()))
                if "resources" in value and (not isinstance(value["resources"], dict) or set(value["resources"]) - {"source"}):
                    raise ValueError("Select a named source resource")
                saved = self.workspace.get(value["application_id"])
                if type(value["revision"]) is not int or value["revision"] != saved["revision"]:
                    raise Conflict("This application changed. Reload its saved version before starting.")
                # The backend supplies the durable snapshot; callers cannot swap it.
                return await self.host.handle("start", {**value, "definition": saved["definition"]})
        if len(parts) in {3, 4} and parts[1] == "runs":
            run_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return await self.host.handle("get", {"run_id": run_id})
            if len(parts) == 4:
                action = parts[3]
                if method == "GET" and action in {"events", "reviews"}:
                    _, record = await self.host.handle("get", {"run_id": run_id})
                    if action == "reviews":
                        return 200, {"reviews": record["reviews"]}
                    cursor = query.get("after", ["0"])
                    if len(cursor) != 1 or not cursor[0].isdigit():
                        raise ValueError("Event cursor must be a nonnegative integer")
                    events = [event for event in record["events"] if event["sequence"] > int(cursor[0])]
                    return 200, {"events": events, "next_cursor": record["events"][-1]["sequence"] if record["events"] else 0, "status": record["status"]}
                if method == "POST" and action in {"resume", "cancel", "reviews"}:
                    self.fields(value, {"review_id", "output"} if action == "reviews" else set())
                    return await self.host.handle("review" if action == "reviews" else action, {**value, "run_id": run_id},
                                                  principal=principal)
        return 404, {"error": "Endpoint not found"}


def configured_api(*, workspace=None, principal=None):
    host = configured_host(workspace=workspace, principal=principal)
    defaults, catalogue = (configured_data(name) for name in ("defaults", "catalogue"))
    # One tenant's applications and catalogue; the host's principal decided
    # which, and a request cannot reach another tenant's store.
    store = Workspace(host.paths.applications, starters=starter_definitions(catalogue, defaults),
                      member_limit=int(setting("MEMBER_LIMIT", "200")))
    return ApplicationAPI(host, store, defaults=defaults, catalogue=catalogue)


class TenantAPIs:
    """One application service per tenant, built on first use.

    Storage is per tenant, so a request can only ever reach the tenant its
    service was built for. The acting principal is still passed per request,
    because several people may act inside one tenant.
    """

    def __init__(self, workspace=None, *, factory=None):
        self.workspace, self.factory, self.apis = workspace, factory or configured_api, {}

    def for_principal(self, principal) -> ApplicationAPI:
        service = self.apis.get(principal.tenant)

        if service is None:
            service = self.apis[principal.tenant] = self.factory(workspace=self.workspace, principal=principal)

        return service

    async def close(self) -> None:
        services, self.apis = list(self.apis.values()), {}

        for service in services:
            await service.host.close()


async def start_server(api, port=8080, *, token=None, assets=None):
    """Start a loopback HTTP/1.1 server; one bounded request per connection.

    `assets` serves the Studio bundle from this same origin, so one
    process and one address run the whole local application.
    """
    assets = assets or {}

    async def respond(reader, writer):
        status, result = 400, {"error": "Invalid HTTP request"}
        head = False

        try:
            async with asyncio.timeout(15):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 16384:
                    raise ValueError()
                lines = raw.decode("ascii").split("\r\n")
                method, target, version = lines[0].split(' ')
                head = method == "HEAD"
                if version != "HTTP/1.1" or not target.startswith('/'):
                    raise ValueError()
                headers = {}
                for line in lines[1:-2]:
                    key, item = line.split(':', 1)
                    key = key.lower()
                    if key in headers:
                        raise ValueError()
                    headers[key] = item.strip()
                local_port = writer.get_extra_info("sockname")[1]
                valid_hosts = {f"127.0.0.1:{local_port}", f"localhost:{local_port}"}
                length = int(headers.get("content-length", "0"))
                if length < 0 or "transfer-encoding" in headers:
                    raise ValueError()
                if length > 200000:
                    status, result = 413, {"error": "Request too large"}
                elif headers.get("host") not in valid_hosts or headers.get("origin") and headers["origin"] not in {"http://" + host for host in valid_hosts}:
                    status, result = 403, {"error": "Use the local Conclave address"}
                elif token and not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + token):
                    status, result = 401, {"error": "Authentication required"}
                elif method in {"POST", "PUT", "DELETE"} and not headers.get("content-type", "").startswith("application/json"):
                    status, result = 415, {"error": "Use a JSON request"}
                else:
                    content = await reader.readexactly(length)
                    value = json.loads(content, parse_constant=reject_nonfinite) if content else None
                    status, result = None, (method, target, value)
            if status is None:
                method, target, value = result
                page = None if urlsplit(target).path.startswith("/api/") else static_page(api, method, target, assets)
                if page is not None:
                    status, result = page.status, page
                else:
                    # Provider work is independent of the request-read timeout.
                    status, result = await api.dispatch(method, target, value)
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status, result = 400, {"error": "Invalid HTTP or JSON request"}
        payload, content_type, filename = b"", "application/json", None
        if isinstance(result, StaticResponse):
            status, payload, content_type, filename = result
        elif status != 204:
            payload = json.dumps(result, allow_nan=False).encode()
        response = [f"HTTP/1.1 {status} Response", f"Content-Type: {content_type}", f"Content-Length: {len(payload)}"]
        if filename:
            response.append(f'Content-Disposition: attachment; filename="{filename}"')
        response += ["Cache-Control: no-store", "X-Content-Type-Options: nosniff", "Connection: close"]
        try:
            writer.write("\r\n".join(response).encode() + b"\r\n\r\n" + (b"" if head else payload))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.start_server(respond, "127.0.0.1", port, limit=16384)


async def serve(port=8080):
    api = configured_api(workspace=Path.cwd())
    token = setting("API_TOKEN")
    # Serving the page from this process is a local convenience: a hosted
    # deployment ships the bundle separately, and a bearer token cannot be
    # presented by a browser page anyway.
    assets = studio_assets() if api.host.profile.serve_ui and not token else {}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        server = await start_server(api, port, token=token, assets=assets)
        async with server:
            address = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            for line in startup_lines(api, address, assets, token):
                print(line, flush=True)
            await stopped.wait()
    finally:
        await api.host.close()
