"""The hosted transport: identity, tenancy and CORS without a loopback server.

The ASGI callable is driven directly — a fake scope, a fake receive and a
collector for send — so no server, port or network is involved.
"""

import asyncio
import json

import pytest

from conclave.api import ApplicationAPI, TenantAPIs
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conclave.asgi import application, response
from conclave import ToolRegistry
from conftest import ScriptedProvider
from test_studio_runs import with_final_stage


def make_services(tmp_path, *, tenants=("local",)):
    """One application service per tenant, sharing a factory."""
    built = {}

    def factory(*, workspace=None, principal=None):
        host = LocalRunHost(RunRepository(tmp_path / f"runs-{principal.tenant}.sqlite"),
                            ScriptedProvider(default={"type": "final", "content": "Done", "text": "Result"}),
                            ToolRegistry(20000), reviewer=principal.name, principal=principal)
        service = ApplicationAPI(host, Workspace(tmp_path / f"applications-{principal.tenant}.sqlite"))
        built[principal.tenant] = service
        return service

    return TenantAPIs(tmp_path, factory=factory), built


async def call(app, method="GET", path="/api/health", *, body=None, headers=(), chunks=None):
    """Drive one ASGI request and return (status, headers, payload)."""
    raw = b"" if body is None else json.dumps(body).encode()
    messages = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks or []) - 1}
                for index, chunk in enumerate(chunks)] if chunks else [{"type": "http.request", "body": raw}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "headers": [(key.lower().encode(), value.encode()) for key, value in headers]}
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], dict((key.decode(), value.decode()) for key, value in start["headers"]), body


def test_the_hosted_transport_answers_the_same_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        status, headers, body = await call(app)
        assert status == 200 and json.loads(body) == {"status": "ok", "api_version": 1}
        assert headers["content-type"].startswith("application/json")

        # The same routes as the loopback adapter, without its Host check.
        definition = with_final_stage()["definition"]
        status, _, body = await call(app, "POST", "/api/applications", body={"definition": definition},
                                     headers=[("content-type", "application/json")])
        assert status == 201
        saved = json.loads(body)
        assert saved["definition"] == definition
        assert (await call(app, "GET", "/api/applications/" + saved["id"]))[0] == 200
        assert (await call(app, "GET", "/api/nothing"))[0] == 404
        assert (await call(app, "PATCH", "/api/applications"))[0] == 405

        await app.close()

    asyncio.run(exercise())
    assert list(built) == ["acme"], "the tenant's service was built for the caller's tenant"


def test_the_hosted_transport_requires_a_token_and_names_its_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, _ = make_services(tmp_path)
    app = application(apis=services, token="secret", origins=["https://studio.example"],
                      fallback=Principal("ada", "acme"))

    async def exercise():
        assert (await call(app))[0] == 401
        assert (await call(app, headers=[("authorization", "Bearer wrong")]))[0] == 401

        status, headers, _ = await call(app, headers=[("authorization", "Bearer secret")])
        assert status == 200

        # A browser origin that was not named is refused, and a named one is
        # answered with the headers its page needs.
        assert (await call(app, headers=[("authorization", "Bearer secret"), ("origin", "https://elsewhere.example")]))[0] == 403
        status, headers, _ = await call(app, headers=[("authorization", "Bearer secret"), ("origin", "https://studio.example")])
        assert status == 200 and headers["access-control-allow-origin"] == "https://studio.example"

        status, headers, _ = await call(app, "OPTIONS", "/api/applications",
                                        headers=[("origin", "https://studio.example"), ("access-control-request-method", "POST")])
        assert status == 204 and "POST" in headers["access-control-allow-methods"]

        await app.close()

    asyncio.run(exercise())


def test_the_hosted_transport_rejects_bad_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, _ = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        assert (await call(app, "POST", "/api/applications", body={"definition": {}}))[0] == 415

        status, _, body = await call(app, "POST", "/api/applications", body={"definition": {}},
                                     headers=[("content-type", "application/json")])
        assert status == 400 and json.loads(body)["error"]

        status, _, body = await call(app, "POST", "/api/applications", chunks=[b"x" * 200_001],
                                     headers=[("content-type", "application/json")])
        assert status == 413

        await app.close()

    asyncio.run(exercise())


def test_a_tenant_is_established_by_the_deployment_not_by_a_header(tmp_path, monkeypatch):
    """Headers are only trusted when the deployment maps a verified caller."""
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, built = make_services(tmp_path)

    # No mapping supplied: the host serves its own tenant, and a caller that
    # names another one changes nothing.
    plain = application(apis=services, token="secret", fallback=Principal("ada", "local"))

    async def exercise():
        assert (await call(plain))[0] == 401
        status, _, _ = await call(plain, headers=[("authorization", "Bearer secret"), ("x-conclave-tenant", "acme")])
        assert status == 200
        assert list(built) == ["local"], "the claimed tenant was ignored"

        # A deployment that verifies callers itself may map them to a tenant.
        seen = []

        def principal_for(scope, headers):
            seen.append(headers.get("authorization"))
            return Principal(headers.get("x-verified-user", "unknown"), "acme")

        mapped = application(apis=services, token="secret", principal_for=principal_for)
        status, _, body = await call(mapped, headers=[("authorization", "Bearer secret"), ("x-verified-user", "ada")])
        assert status == 200 and json.loads(body)["api_version"] == 1
        assert seen == ["Bearer secret"]

        await plain.close()
        await mapped.close()

    asyncio.run(exercise())
    assert list(built) == ["local", "acme"], "each tenant got its own service"


def test_the_hosted_app_reads_its_settings(tmp_path, monkeypatch):
    """A deployment can serve `configured_application` without writing code."""
    from conclave.asgi import configured_application

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONCLAVE_API_TOKEN", "secret")
    monkeypatch.setenv("CONCLAVE_UI_ORIGINS", "https://studio.example, https://admin.example/")
    app = configured_application()

    async def exercise():
        assert (await call(app))[0] == 401
        status, headers, _ = await call(app, headers=[("authorization", "Bearer secret"), ("origin", "https://admin.example")])
        assert status == 200 and headers["access-control-allow-origin"] == "https://admin.example"
        assert (await call(app, headers=[("authorization", "Bearer secret"), ("origin", "https://elsewhere.example")]))[0] == 403
        await app.close()

    asyncio.run(exercise())


def test_the_response_helper_bounds_what_it_will_serialize():
    message = response(200, {"ok": True})

    assert message["status"] == 200 and json.loads(message["body"]) == {"ok": True}
    assert message["headers"][0] == ("content-type", "application/json")

    with pytest.raises(ValueError):
        response(200, {"value": float("nan")})
