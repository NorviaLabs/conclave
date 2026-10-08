"""Serve the application service to a real HTTP server.

`conclave/api.py`'s loopback server is the local adapter: it binds 127.0.0.1 and
refuses any other Host. A hosted deployment supplies its own ASGI server and the
application below, so the same `ApplicationAPI.dispatch` contract answers real
traffic without the loopback assumptions.

Authentication stays the deployment's job. This adapter establishes the
principal in one of two ways: from a `principal_for` callable the deployment
supplies (its verified identity, mapped to a tenant), or — when nothing is
supplied — from the host's own settings, which serves exactly one tenant. Tenant
headers are never trusted on their own: a caller that can name any tenant can
read any tenant.

No ASGI framework is required: `application()` returns a plain ASGI callable for
whatever server the deployment already runs.
"""

import hmac
import json

from urllib.parse import (
    unquote,
    urlsplit,
)

from .api import TenantAPIs, reject_nonfinite
from .application.principal import Principal, resolve_principal
from .application.service import setting, request_body_limit


JSON_TYPE = "application/json"

STREAM_TYPE = "text/event-stream"

PREFLIGHT_HEADERS = "authorization, content-type"

METHODS = {"GET", "POST", "PUT", "DELETE", "OPTIONS"}


class TooLarge(ValueError):
    """A request body larger than this adapter accepts."""


def response(status: int, payload=None, *, headers=()) -> dict:
    body = b"" if payload is None else json.dumps(payload, allow_nan=False).encode()

    return {
        "status": status,
        "headers": [("content-type", JSON_TYPE), ("cache-control", "no-store"),
                    ("x-content-type-options", "nosniff"), *headers],
        "body": body,
    }


def cors_headers(origin, allowed) -> list[tuple[str, str]]:
    """Only the origins a deployment named may read a response."""

    if origin and origin in allowed:
        return [("access-control-allow-origin", origin), ("vary", "Origin"),
                ("access-control-allow-headers", PREFLIGHT_HEADERS),
                ("access-control-allow-methods", "GET, POST, PUT, DELETE, OPTIONS")]

    return []


def request_headers(scope) -> dict[str, str]:
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}


def principal_from(scope, headers, *, principal_for=None, fallback=None) -> Principal:
    """Who acted, established before any application code runs."""

    if principal_for is not None:
        return principal_for(scope, headers)

    return fallback or resolve_principal()


async def read_body(receive) -> bytes:
    chunks, total = [], 0
    limit = request_body_limit()

    while True:
        message = await receive()
        chunk = message.get("body", b"")
        total += len(chunk)

        if limit is not None and total > limit:
            raise TooLarge(f"Request exceeds host limit of {limit} bytes")

        chunks.append(chunk)

        if not message.get("more_body"):
            return b"".join(chunks)


def application(*, apis=None, token=None, origins=(), principal_for=None, fallback=None):
    """An ASGI callable over one or more tenants' application services."""

    services = apis if apis is not None else TenantAPIs()
    allowed = {origin.rstrip("/") for origin in origins}
    token = token if token is not None else setting("API_TOKEN")

    async def app(scope, receive, send):
        if scope.get("type") != "http":
            raise RuntimeError("This application answers HTTP requests")

        method = scope.get("method", "GET").upper()
        raw = scope.get("raw_path")
        target = raw.decode("latin-1") if isinstance(raw, bytes) else scope.get("path", "/")
        headers = request_headers(scope)
        origin = headers.get("origin")
        extra = cors_headers(origin, allowed)

        if method not in METHODS:
            return await reply(send, response(405, {"error": "Method not allowed"}, headers=extra))

        if method == "OPTIONS":
            return await reply(send, response(204, None, headers=extra))

        if token and not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + token):
            return await reply(send, response(401, {"error": "Authentication required"}, headers=extra))

        if origin and origin not in allowed:
            return await reply(send, response(403, {"error": "Use an allowed origin"}, headers=extra))

        if method in {"POST", "PUT", "DELETE"} and not headers.get("content-type", "").startswith(JSON_TYPE):
            return await reply(send, response(415, {"error": "Use a JSON request"}, headers=extra))

        try:
            principal = principal_from(scope, headers, principal_for=principal_for, fallback=fallback)
            body = await read_body(receive) if method in {"POST", "PUT", "DELETE"} else b""
            value = json.loads(body, parse_constant=reject_nonfinite) if body else None
        except TooLarge as error:
            return await reply(send, response(413, {"error": str(error)}, headers=extra))
        except ValueError as error:
            return await reply(send, response(400, {"error": str(error)}, headers=extra))

        api = services.for_principal(principal)

        # A run's events can be pushed here, so the host says so, and a caller
        # that asks for the stream gets one instead of polling.
        if method == "GET" and (run_id := streamed_run(target)) is not None:
            return await stream_run(send, api, run_id, extra)

        status, payload = await api.dispatch(method, target, value, principal=principal, streaming=True)

        return await reply(send, response(status, payload, headers=extra))

    app.services = services
    app.close = services.close

    return app


def configured_application():
    """The hosted app as this deployment's settings describe it.

    Tenants are built on demand, `CONCLAVE_API_TOKEN` authenticates callers,
    and `CONCLAVE_UI_ORIGINS` names the origins allowed to read responses. A
    deployment whose users share a tenant supplies its own `principal_for` to
    `application()` instead, so a verified caller decides the tenant.

    Serve it with whatever ASGI server the deployment runs, for example
    `uvicorn --factory conclave.asgi:configured_application`.
    """

    origins = [item.strip() for item in setting("UI_ORIGINS", "").split(",") if item.strip()]

    return application(origins=origins)


async def reply(send, message) -> None:
    body = message["body"]
    status = message["status"]
    headers = list(message["headers"]) + [("content-length", str(len(body)))]

    await send({"type": "http.response.start", "status": status, "headers": [(key.encode(), value.encode()) for key, value in headers]})
    await send({"type": "http.response.body", "body": body})


def streamed_run(target: str) -> str | None:
    """The run a caller asked to be told about, if that is what this is."""

    parts = [unquote(part) for part in urlsplit(target).path.strip("/").split("/")]

    if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "stream":
        return parts[2]

    return None


async def stream_run(send, api, run_id: str, extra) -> None:
    """One run's record, sent again whenever it changes.

    Server-sent events, so an ordinary HTTP client can watch a run with curl and
    a browser can watch it with a reader. A caller that disconnects stops the
    watch: the repository drops the subscription when the generator is closed.
    """

    try:
        api.host.repository.get(run_id)

    except KeyError:
        return await reply(send, response(404, {"error": "No such run"}, headers=extra))

    await send({"type": "http.response.start", "status": 200,
                "headers": [(key.encode(), value.encode()) for key, value in
                            [("content-type", STREAM_TYPE), ("cache-control", "no-store"),
                             ("x-content-type-options", "nosniff"), *extra]]})

    watching = api.host.repository.watch(run_id)

    try:
        async for record in watching:
            body = b"data: " + json.dumps(record, allow_nan=False).encode() + b"\n\n"
            await send({"type": "http.response.body", "body": body, "more_body": True})

    finally:
        await watching.aclose()
