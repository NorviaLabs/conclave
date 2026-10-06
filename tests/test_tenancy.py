"""Identity and tenancy: one deployment, many tenants, no shared storage."""

import asyncio
import json

import pytest

from conclave.api import configured_api
from conclave.application.principal import Principal, resolve_principal, require_tenant
from conclave.application.service import configured_host, configured_paths
from conclave.application.storage import tenant_paths
from test_studio_runs import request


def test_a_principal_names_an_actor_in_a_tenant(monkeypatch):
    monkeypatch.delenv("CONCLAVE_PRINCIPAL", raising=False)
    monkeypatch.delenv("CONCLAVE_TENANT", raising=False)

    local = resolve_principal()

    assert local.name and local.tenant == "local"
    assert local.label == local.name, "the local tenant is not repeated in the UI label"
    assert resolve_principal("ada", "acme").label == "ada · acme"
    assert require_tenant("case-a_2") == "case-a_2"

    with pytest.raises(ValueError, match="lowercase identifiers"):
        resolve_principal("ada", "Acme Inc")
    with pytest.raises(ValueError, match="at most 100"):
        resolve_principal("x" * 101)
    with pytest.raises(ValueError, match="required"):
        resolve_principal("  ")


def test_the_local_tenant_keeps_the_files_this_checkout_already_has(tmp_path):
    local = tenant_paths(tmp_path, "local")

    assert local.root == tmp_path
    assert local.runs == tmp_path / "runs.sqlite"
    assert local.applications == tmp_path / "studio.sqlite"

    # Another tenant gets its own directory, so no existing row has to learn
    # a tenant it never had.
    other = tenant_paths(tmp_path, "acme")

    assert other.root == tmp_path / "tenants" / "acme"
    assert other.runs.parent == other.root and other.applications.parent == other.root


def test_explicit_databases_belong_to_the_local_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONCLAVE_RUN_DATABASE", str(tmp_path / "one-file.sqlite"))

    local = configured_paths(Principal("ada"))

    assert local.runs == tmp_path / "one-file.sqlite"
    assert local.applications == tmp_path / "studio.sqlite"

    # One file cannot hold two tenants, so the override does not follow them.
    acme = configured_paths(Principal("ada", "acme"))

    assert acme.runs == tmp_path / "data" / "tenants" / "acme" / "runs.sqlite"
    assert acme.applications == tmp_path / "data" / "tenants" / "acme" / "applications.sqlite"


def test_two_tenants_share_a_deployment_without_sharing_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CONCLAVE_RUN_DATABASE", raising=False)
    monkeypatch.delenv("CONCLAVE_DATABASE", raising=False)
    acme, beta = Principal("ada", "acme"), Principal("bob", "beta")

    async def exercise():
        first = configured_api(workspace=tmp_path, principal=acme)
        second = configured_api(workspace=tmp_path, principal=beta)
        assert first.host.repository.path != second.host.repository.path
        assert first.host.paths.root == tmp_path / "data" / "tenants" / "acme"
        assert first.host.principal.tenant == "acme" and second.host.reviewer == "bob"

        definition = request()["definition"]
        _, saved = await first.dispatch("POST", "/api/applications", {"definition": definition})
        assert saved["id"] in [item["id"] for item in (await first.dispatch("GET", "/api/applications"))[1]["applications"]]

        # The other tenant has its own starters and cannot see the first one's work.
        listed = (await second.dispatch("GET", "/api/applications"))[1]["applications"]
        assert saved["id"] not in [item["id"] for item in listed]
        assert (await second.dispatch("GET", "/api/applications/" + saved["id"]))[0] == 404

        # Saved source settings are per tenant as well.
        sources = {"documents": {"name": "Documents", "path": str(tmp_path)}}
        assert (await first.dispatch("PUT", "/api/settings/sources", {"sources": sources}))[0] == 200
        assert json.loads((tmp_path / "data" / "tenants" / "acme" / "sources.json").read_text()) == sources
        assert not (tmp_path / "data" / "tenants" / "beta" / "sources.json").exists()
        assert "documents" not in (await second.dispatch("GET", "/api/settings/sources"))[1]["sources"]

        await first.host.close()
        await second.host.close()

    asyncio.run(exercise())


def test_a_run_created_by_one_tenant_is_not_readable_by_another(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    acme = configured_host(workspace=tmp_path, principal=Principal("ada", "acme"))
    beta = configured_host(workspace=tmp_path, principal=Principal("bob", "beta"))

    run_id = acme.repository.create(request())

    assert acme.repository.get(run_id)["id"] == run_id
    with pytest.raises(KeyError):
        beta.repository.get(run_id)
    assert beta.repository.list(request()["application_id"]) == []

    _, runtime = asyncio.run(beta.handle("runtime", {}))
    assert runtime["tenant"] == "beta" and runtime["reviewer"] == "bob"
