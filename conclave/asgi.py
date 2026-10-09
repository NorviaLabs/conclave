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

`conclave.identity` is one such verified identity a deployment can switch on:
Google sign-in with a session cookie, which a browser can present where it
cannot present a bearer token. With it configured the API accepts either, and
either way the principal is established here, before application code runs.

No ASGI framework is required: `application()` returns a plain ASGI callable for
whatever server the deployment already runs.
"""

import hmac
import json

from urllib.parse import (
    parse_qs,
    unquote,
    urlsplit,
)

from .api import TenantAPIs, reject_nonfinite
from .identity import GoogleLoginError, cookie_value
from .application.principal import Principal, resolve_principal
from .application.service import setting, request_body_limit


JSON_TYPE = "application/json"

STREAM_TYPE = "text/event-stream"

PREFLIGHT_HEADERS = "authorization, content-type"

METHODS = {"GET", "POST", "PUT", "DELETE", "OPTIONS"}

# Where a browser starts and ends a Google sign-in, and what the page reads to
# find out who it is. All three are served by this adapter, not the API.
LOGIN_START = "/auth/google/start"

LOGIN_CALLBACK = "/auth/google/callback"

LOGOUT = "/auth/logout"

# The page's module graph loads these two: `definition.js` and `catalogue.js` are
# served rewritten to import this deployment's defaults and catalogue over REST.
# A module that cannot load takes the page down with it, so gating them would
# leave a signed-out visitor with a broken page instead of the sign-in screen
# that gets them in. They carry this deployment's own configuration rather than
# anybody's data, which is what lets the bundle itself be served unauthenticated.
PUBLIC_MODULES = frozenset({"/api/config/defaults", "/api/config/catalogue"})


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
        # A page on another origin sends its session cookie only if the answer
        # says credentials are allowed, so a cookied deployment needs this even
        # though the origin itself is echoed rather than wildcarded.
        return [("access-control-allow-origin", origin), ("vary", "Origin"),
                ("access-control-allow-credentials", "true"),
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


def redirect_uri(scope, headers) -> str:
    """The callback address this request claims.

    A browser is told where it is returning to, but a proxy or a forged Host
    could name another site. Nothing here is trusted on its own: the login only
    accepts a redirect URI the deployment listed, so a value invented here is
    refused before an authorization URL is built.
    """

    host = headers.get("host", "").split(",")[0].strip()
    scheme = headers.get("x-forwarded-proto", "").split(",")[0].strip() or scope.get("scheme", "http")
    return f"{scheme}://{host}{LOGIN_CALLBACK}"


def secure_origin(scope, headers) -> bool:
    """Whether this request arrived over HTTPS, behind a proxy or not."""

    return (headers.get("x-forwarded-proto", "").split(",")[0].strip()
            or scope.get("scheme", "http")) == "https"


def own_origin(scope, headers) -> str:
    """The origin this request was sent to, which is not cross-origin at all.

    A page the deployment serves itself posts with its own origin attached, and
    comparing that against the origins other pages were allowed from would refuse
    its own writes. The origin has to match the host the request arrived on, so an
    attacker's page cannot claim it.
    """

    host = headers.get("host", "").split(",")[0].strip()
    return f"{'https' if secure_origin(scope, headers) else 'http'}://{host}"


def signed_in(headers, login) -> bool:
    """Whether this request carries the deployment's session cookie at all."""

    return login is not None and cookie_value(headers.get("cookie", ""), login.session_name) is not None


async def sign_in_route(login, scope, target, method, headers, extra, send):
    """The browser's half of sign-in: start, callback and sign out.

    Answered before the API's own authentication, because a page cannot present
    a bearer token and the callback arrives with no cookie yet. The failure
    message carries no token and no other person's address.
    """

    path = urlsplit(target).path
    if path == LOGIN_START and method == "GET":
        try:
            location = login.begin(redirect_uri(scope, headers))
        except GoogleLoginError as error:
            return await reply(send, response(400, {"error": str(error)}, headers=extra))
        return await reply(send, response(302, None, headers=[*extra, ("location", location)]))
    if path == LOGIN_CALLBACK and method == "GET":
        query = parse_qs(urlsplit(target).query)
        try:
            principal = await login.callback(query.get("state", [""])[0], query.get("code", [""])[0],
                                             redirect_uri(scope, headers))
        except GoogleLoginError:
            # A person sees a page, not a payload: the page reports the failure.
            return await reply(send, response(302, None, headers=[*extra, ("location", login.return_url(failed=True)),
                                                                   ("cache-control", "no-store")]))
        return await reply(send, response(302, None,
                                          headers=[*extra, ("location", login.return_url()),
                                                   ("set-cookie", login.cookie(login.issue(principal), secure=secure_origin(scope, headers))),
                                                   ("cache-control", "no-store")]))
    if path == LOGOUT and method == "POST":
        return await reply(send, response(204, None, headers=[*extra, ("set-cookie",
                                                                      login.cleared_cookie(secure=secure_origin(scope, headers)))]))
    return await reply(send, response(404, {"error": "Endpoint not found"}, headers=extra))


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


def application(*, apis=None, token=None, origins=(), principal_for=None, fallback=None, hooks=None, login=None):
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

        # Sign-in is a browser flow and is answered first: the page that starts it
        # cannot present a token, and the callback arrives with no cookie yet.
        if login is not None and urlsplit(target).path in {LOGIN_START, LOGIN_CALLBACK, LOGOUT}:
            return await sign_in_route(login, scope, target, method, headers, extra, send)

        # `/api/hooks/` authenticates by signature instead, and only when this
        # deployment configured a hook resolver at all.
        is_hook = hooks is not None and method == "POST" and target.startswith("/api/hooks/")
        is_module = method in {"GET", "HEAD"} and urlsplit(target).path in PUBLIC_MODULES
        presented_token = bool(token) and hmac.compare_digest(headers.get("authorization", ""), "Bearer " + token)
        if login is not None and principal_for is None:
            # A browser cannot present a bearer token, so a session cookie is the
            # other credential this deployment accepts. Either way the principal
            # is established here, and a cookie that does not verify is no
            # credential at all.
            if not is_hook and not is_module and not presented_token and login.authenticated(headers) is None:
                return await reply(send, response(401, {"error": "Sign in to continue", "login": LOGIN_START}, headers=extra))
        elif token and not is_hook and not presented_token:
            return await reply(send, response(401, {"error": "Authentication required"}, headers=extra))

        if origin and origin not in allowed and origin != own_origin(scope, headers):
            return await reply(send, response(403, {"error": "Use an allowed origin"}, headers=extra))

        if method in {"POST", "PUT", "DELETE"} and not headers.get("content-type", "").startswith(JSON_TYPE):
            return await reply(send, response(415, {"error": "Use a JSON request"}, headers=extra))

        try:
            # The credential decides where the principal comes from: a deployment
            # that supplies its own verifier wins, then a session, then this host.
            verifier = principal_for if principal_for is not None else (login.principal_for if signed_in(headers, login) else None)
            principal = principal_from(scope, headers, principal_for=verifier, fallback=fallback)
            body = await read_body(receive) if method in {"POST", "PUT", "DELETE"} else b""
            value = json.loads(body, parse_constant=reject_nonfinite) if body else None
        except TooLarge as error:
            return await reply(send, response(413, {"error": str(error)}, headers=extra))
        except GoogleLoginError as error:
            return await reply(send, response(401, {"error": str(error), "login": LOGIN_START}, headers=extra))
        except ValueError as error:
            return await reply(send, response(400, {"error": str(error)}, headers=extra))

        # Who the page is, and how to stop being them. Read before dispatch, so
        # the answer comes from this transport rather than from application code.
        if urlsplit(target).path == "/api/me" and method == "GET":
            return await reply(send, response(200, {"principal": {"name": principal.name, "tenant": principal.tenant,
                                                                  "source": principal.source, "label": principal.label},
                                                     "login": None if login is None else {"start": LOGIN_START, "logout": LOGOUT}},
                                               headers=extra))

        # A signed hook is its own authentication: an external service cannot
        # present this deployment's bearer token, and nothing else may reach it.
        if hooks is not None and method == "POST" and target.startswith("/api/hooks/"):
            delivery = target[len("/api/hooks/"):].split("?")[0]
            try:
                principal, payload = await hooks(delivery, headers, body)
            except ValueError as error:
                return await reply(send, response(400, {"error": str(error)}, headers=extra))
            api = services.for_principal(principal)
            status, answer = await api.dispatch("POST", target, payload, principal=principal, streaming=False)
            return await reply(send, response(status, answer, headers=extra))

        api = services.for_principal(principal)

        # A run's events can be pushed here, so the host says so, and a caller
        # that asks for the stream gets one instead of polling.
        if method == "GET" and (run_id := streamed_run(target)) is not None:
            return await stream_run(send, api, run_id, extra)

        status, payload = await api.dispatch(method, target, value, principal=principal, streaming=True)

        return await reply(send, response(status, payload, headers=extra))

    app.services = services
    app.close = services.close
    app.login = login

    return app


def configured_application():
    """The hosted app as this deployment's settings describe it.

    Tenants are built on demand, `CONCLAVE_API_TOKEN` authenticates callers,
    and `CONCLAVE_UI_ORIGINS` names the origins allowed to read responses. A
    deployment whose users share a tenant supplies its own `principal_for` to
    `application()` instead, so a verified caller decides the tenant.

    `CONCLAVE_GOOGLE_LOGIN_FILE` and `CONCLAVE_SESSION_KEY` turn on Google
    sign-in for browsers, which cannot present a bearer token; with no login
    file nothing about this app changes.

    Serve it with whatever ASGI server the deployment runs, for example
    `uvicorn --factory conclave.asgi:configured_application`.
    """

    origins = [item.strip() for item in setting("UI_ORIGINS", "").split(",") if item.strip()]

    # Sign-in is opt-in and deny-by-default: `conclave.identity` refuses to build
    # a login that admits nobody, so a deployment cannot open itself by accident.
    from .identity import configured_login
    login = configured_login()

    # Inbound hooks are opt-in: without this resolver `/api/hooks/` stays behind
    # the ordinary bearer token and nothing can trigger a run from outside.
    if setting("HOOKS", "").lower() in {"1", "true", "yes"}:
        from .application.hooks import resolver
        from .application.principal import resolve_principal
        services = TenantAPIs()
        return application(apis=services, origins=origins, login=login,
                           hooks=resolver(services, resolve_principal(source="hook")))
    return application(origins=origins, login=login)


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
