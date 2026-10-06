"""Local REST adapter for Conclave's shared application service.

HTTP presentation stays outside the reusable multi-agent runtime. This local,
single-user server uses asyncio and SQLite without adding core dependencies.
"""
import asyncio
import hmac
import json
import signal
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .application.catalogue import configured_data, starter_definitions
from .application.definition import validate_definition
from .application.service import configured_host, setting
from .application.workspace import Workspace, Conflict


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON numbers are unsupported")


class ApplicationAPI:
    def __init__(self, host, workspace, *, defaults=None, catalogue=None):
        self.host, self.workspace = host, workspace
        self.defaults = defaults if defaults is not None else configured_data("defaults")
        self.catalogue = catalogue if catalogue is not None else configured_data("catalogue")
        self.proposal_lock = asyncio.Lock()

    async def dispatch(self, method, target, value=None):
        try:
            return await self._dispatch(method, target, value)
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

    async def _dispatch(self, method, target, value):
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
            return await self.host.handle("runtime", {})
        if method == "GET" and path == "/api/sdk/application":
            return 200, {"filename": "conclave_application.py", "source": '"""Compatibility facade for the installed Conclave application adapter."""\nfrom conclave.application import run_application, validate_definition\n\n__all__ = ["run_application", "validate_definition"]\n'}
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
                self.fields(value, {"version", "application_id", "revision", "task", "inputs"})
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
                    return await self.host.handle("review" if action == "reviews" else action, {**value, "run_id": run_id})
        return 404, {"error": "Endpoint not found"}


def configured_api(*, workspace=None):
    host = configured_host(workspace=workspace)
    defaults, catalogue = (configured_data(name) for name in ("defaults", "catalogue"))
    database = setting("DATABASE", host.repository.path.parent / "studio.sqlite")
    store = Workspace(database, starters=starter_definitions(catalogue, defaults), member_limit=int(setting("MEMBER_LIMIT", "200")))
    return ApplicationAPI(host, store, defaults=defaults, catalogue=catalogue)


async def start_server(api, port=8080, *, token=None):
    """Start a loopback HTTP/1.1 server; one bounded request per connection."""
    async def respond(reader, writer):
        status, result = 400, {"error": "Invalid HTTP request"}
        try:
            async with asyncio.timeout(15):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 16384:
                    raise ValueError()
                lines = raw.decode("ascii").split("\r\n")
                method, target, version = lines[0].split(' ')
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
                # Provider work is independent of the request-read timeout.
                status, result = await api.dispatch(*result)
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status, result = 400, {"error": "Invalid HTTP or JSON request"}
        try:
            payload = b"" if status == 204 else json.dumps(result, allow_nan=False).encode()
            writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.start_server(respond, "127.0.0.1", port, limit=16384)


async def serve(port=8080):
    api = configured_api(workspace=Path.cwd())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        server = await start_server(api, port, token=setting("API_TOKEN"))
        async with server:
            print(f"Conclave REST API: http://127.0.0.1:{server.sockets[0].getsockname()[1]}", flush=True)
            await stopped.wait()
    finally:
        await api.host.close()
