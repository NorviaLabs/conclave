import asyncio

import pytest

from conclave import Completion, ToolRegistry
from conftest import ScriptedProvider
from test_studio_runs import request, with_final_stage
from conclave.application import service as host_module
from conclave.application.service import LocalRunHost
from conclave.application.runs import RunRepository


async def wait_for_status(host, run_id, status):
    async with asyncio.timeout(3):
        while host.repository.get(run_id)["status"] != status:
            await asyncio.sleep(0.001)


def test_local_host_start_review_restart_resume_and_public_status(tmp_path):
    path = tmp_path / "runs.sqlite"
    tools = ToolRegistry(20000)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Real host result from a fake provider"})

    async def exercise():
        host = LocalRunHost(RunRepository(path), provider, tools, reviewer="local-operator")
        code, run = await host.handle("start", with_final_stage())
        assert code == 201
        await wait_for_status(host, run["id"], "waiting_for_human")
        await host.close()
        restored = LocalRunHost(RunRepository(path), provider, tools, reviewer="local-operator")
        code, record = await restored.handle("get", {"run_id": run["id"]})
        assert code == 200 and record["status"] == "waiting_for_human"
        assert all("checkpoint" not in item["event"] for item in record["events"])
        assert next(item["event"] for item in record["events"] if item["event"]["type"] == "ApplicationStateChanged")["patch"] == "Real host result from a fake provider"
        assert "cost_usd" not in record["metrics"]
        review_id = record["reviews"][0]["id"]
        with pytest.raises(ValueError):
            await restored.handle("review", {"run_id": run["id"], "review_id": review_id, "output": {"text": "Review"}, "reviewer": "browser-spoof"})
        code, reviewed = await restored.handle("review", {"run_id": run["id"], "review_id": review_id, "output": {"text": "Explicit review"}})
        assert reviewed["reviews"][0]["reviewer"] == "local-operator"
        await restored.handle("resume", {"run_id": run["id"]})
        await wait_for_status(restored, run["id"], "completed")
        code, history = await restored.handle("list", {"application_id": "research-brief"})
        assert history["runs"][0]["status"] == "completed"
        assert len(provider.prompts) == 2
        await restored.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("immediate", [False, True])
def test_local_host_cancel_closes_active_iterator(tmp_path, immediate):
    started = asyncio.Event()

    class WaitingProvider:
        async def complete(self, messages):
            started.set()
            await asyncio.Event().wait()
            return Completion(text="{}")

    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), WaitingProvider(), ToolRegistry(20000), reviewer="operator")
        _, run = await host.handle("start", request())
        if not immediate:
            await started.wait()
        _, record = await host.handle("cancel", {"run_id": run["id"]})
        assert record["status"] == "interrupted"
        with pytest.raises(ValueError, match="Only a waiting run"):
            await host.handle("resume", {"run_id": run["id"]})
        await host.close()

    asyncio.run(exercise())


def test_cancel_waiting_review_and_missing_provider(tmp_path):
    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(default={"type": "final", "content": "Done", "text": "Brief"}), ToolRegistry(20000), reviewer="operator")
        _, run = await host.handle("start", request())
        await wait_for_status(host, run["id"], "waiting_for_human")
        _, record = await host.handle("cancel", {"run_id": run["id"]})
        assert record["status"] == "cancelled"
        with pytest.raises(ValueError):
            await host.handle("review", {"run_id": run["id"], "review_id": record["reviews"][0]["id"], "output": {"text": "Late review"}})
        host.provider = None
        _, runtime = await host.handle("runtime", {})
        assert runtime["local"] and not runtime["available"]
        with pytest.raises(ValueError, match="Configure an AI provider"):
            await host.handle("start", request())
        assert len(host.repository.list("research-brief")) == 1
        await host.close()

    asyncio.run(exercise())


def test_missing_tool_fails_before_a_run_is_created(tmp_path):
    async def exercise():
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), ToolRegistry(20000), reviewer="operator")
        value = request()
        value["definition"]["agents"][0]["tools"] = ["read_file"]
        with pytest.raises(ValueError, match="Host must register tools"):
            await host.handle("start", value)
        assert host.repository.list("research-brief") == []
        await host.close()

    asyncio.run(exercise())


def test_host_sources_stay_in_explicit_folder_and_hide_credentials(tmp_path, monkeypatch):
    source = tmp_path / "sources"
    source.mkdir()
    (source / "brief.md").write_text("Supplied real evidence")
    (source / ".secret.md").write_text("Do not expose")
    (tmp_path / "outside.md").write_text("Outside selected folder")
    (source / "escape.md").symlink_to(tmp_path / "outside.md")
    monkeypatch.setenv("STUDIO_SOURCE_DIR", str(source))
    monkeypatch.delenv("STUDIO_PROVIDER", raising=False)
    monkeypatch.delenv("STUDIO_WEB", raising=False)
    monkeypatch.setattr(host_module, "RunRepository", lambda path: RunRepository(tmp_path / "test.sqlite"))
    host = host_module.configured_host()
    assert set(host.tools.tools) == {"list_files", "read_file", "search_files", "knowledge_search"}

    async def exercise():
        assert "Supplied real evidence" in await host.tools.execute("read_file", {"path": "brief.md"})
        for path in (".secret.md", "../outside.md", "escape.md", ".env"):
            with pytest.raises((ValueError, RuntimeError)):
                await host.tools.execute("read_file", {"path": path})
        await host.close()

    asyncio.run(exercise())


def test_runtime_discovers_custom_tools_and_approval_requirements(tmp_path):
    from conclave import Tool, Capability

    tools = ToolRegistry(20000)
    tools.register(Tool("custom_lookup", "Look up a supplied record", {"key": "string"}, lambda **kwargs: None, frozenset({Capability.READ})))
    tools.register(Tool("custom_update", "Update a record", {}, lambda **kwargs: None, frozenset({Capability.WRITE})))
    tools.register(Tool("unknown_access", "Unclassified access", {}, lambda **kwargs: None, frozenset()))
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), None, tools, reviewer="operator")
    _, runtime = asyncio.run(host.handle("runtime", {}))
    descriptors = {tool["id"]: tool for tool in runtime["tool_descriptors"]}
    assert descriptors["custom_lookup"]["parameters"] == {"key": "string"}
    assert not descriptors["custom_lookup"]["requires_approval"]
    assert descriptors["custom_update"]["requires_approval"]
    assert descriptors["unknown_access"]["requires_approval"]
    assert runtime["features"]["dag"] and not runtime["features"]["schedules"]


def test_host_custom_provider_and_source_suffixes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from conclave.application import extensions

    source = tmp_path / "sources"
    source.mkdir()
    (source / "events.log").write_text("Custom source")
    (source / ".secret.log").write_text("Hidden")
    (source / "brief.md").write_text("Unselected format")
    provider = ScriptedProvider(default={"type": "final", "content": "Done"})
    arguments = []

    def factory(**kwargs):
        arguments.append(kwargs)
        return provider

    monkeypatch.setattr(extensions.importlib, "import_module", lambda name: SimpleNamespace(create=factory))
    monkeypatch.setenv("STUDIO_PROVIDER_FACTORY", "custom_backend:create")
    monkeypatch.setenv("STUDIO_MODEL", "backend/model")
    monkeypatch.setenv("STUDIO_SOURCE_DIR", str(source))
    monkeypatch.setenv("STUDIO_SOURCE_EXTENSIONS", ".log")
    monkeypatch.setenv("STUDIO_RUN_DATABASE", str(tmp_path / "runs.sqlite"))
    monkeypatch.delenv("STUDIO_EXTENSIONS", raising=False)
    monkeypatch.delenv("STUDIO_WEB", raising=False)
    host = host_module.configured_host()
    assert host.provider is provider
    assert arguments[0]["model"] == "backend/model"

    async def exercise():
        assert "Custom source" in await host.tools.execute("read_file", {"path": "events.log"})
        for path in (".secret.log", "brief.md", "../events.log"):
            with pytest.raises((ValueError, RuntimeError)):
                await host.tools.execute("read_file", {"path": path})
        await host.close()

    asyncio.run(exercise())
    monkeypatch.setenv("STUDIO_SOURCE_EXTENSIONS", "*")
    with pytest.raises(ValueError, match="file suffixes"):
        host_module.configured_host()
    with pytest.raises(ValueError, match="module:callable"):
        extensions.load_provider_factory("unsafe/path:create")


def test_run_history_limit_is_configurable(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite", history_limit=1)
    repository.create(request())
    latest = repository.create(request())
    assert [run["id"] for run in repository.list("research-brief")] == [latest]
    with pytest.raises(ValueError, match="positive integer"):
        RunRepository(tmp_path / "invalid.sqlite", history_limit=0)


def test_host_planning_proposes_without_creating_or_running_application(tmp_path):
    import copy
    import json

    value = request()["definition"]
    proposed = copy.deepcopy(value)
    proposed["name"] = "Domain-specific application"
    provider = ScriptedProvider(default={"definition": proposed, "summary": "Use a domain-specific team"})
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="operator")
    code, result = asyncio.run(host.handle("propose", {"prompt": "Create a domain-specific team", "definition": value, "catalogue": {}}))
    assert code == 200 and result["definition"] == proposed
    assert value["name"] != proposed["name"]
    assert host.repository.list("research-brief") == []
    assert len(provider.prompts) == 1
    assert "available_tools" in json.dumps(provider.prompts)


@pytest.mark.parametrize("invalid", ["not json", {"definition": {}}, {"definition": {}, "summary": "Invalid"}])
def test_invalid_planning_response_is_rejected(tmp_path, invalid):
    import json
    provider = ScriptedProvider(default=invalid)
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="operator")
    with pytest.raises(ValueError, match="invalid application proposal"):
        asyncio.run(host.handle("propose", {"prompt": "Create a team", "definition": request()["definition"], "catalogue": {}}))
    assert host.repository.list("research-brief") == []


def test_connector_checks_report_actual_availability_without_private_errors(tmp_path):
    from conclave.application.extensions import ConnectorAdapter

    def fail():
        raise RuntimeError("private credential in backend error")

    adapters = [ConnectorAdapter("empty_source", "Empty source", "files", (), lambda: True),
                ConnectorAdapter("missing_tool", "Missing tool", "files", ("not_registered",), lambda: True),
                ConnectorAdapter("failed_check", "Failed check", "files", (), fail)]
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), None, ToolRegistry(20000), reviewer="operator", adapters=adapters)
    _, runtime = asyncio.run(host.handle("runtime", {}))
    assert [adapter["available"] for adapter in runtime["connectors"]] == [True, False, False]
    assert "private credential" not in str(runtime)


def test_trusted_extension_registers_custom_tools(monkeypatch):
    from types import SimpleNamespace
    from conclave import Tool, Capability
    from conclave.application.extensions import ConnectorAdapter, load_extensions
    from conclave.application import extensions

    async def lookup():
        return "Domain record"

    def register(tools):
        tools.register(Tool("domain_lookup", "Look up records", {}, lookup, frozenset({Capability.READ})))
        return [ConnectorAdapter("domain_source", "Domain source", "custom", ("domain_lookup",), lambda: True)]

    monkeypatch.setattr(extensions.importlib, "import_module", lambda name: SimpleNamespace(register=register))
    tools = ToolRegistry(20000)
    adapters = load_extensions("domain.extension", tools)
    assert adapters[0].id == "domain_source"
    assert asyncio.run(tools.execute("domain_lookup", {})) == "Domain record"
    with pytest.raises(ValueError, match="module names"):
        load_extensions("/browser/supplied/path", tools)


def test_budget_warnings_are_recorded_from_reported_usage_and_not_repeated_after_resume(tmp_path):
    from conclave import Usage

    async def exercise():
        provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Brief"}, usage=Usage(input_tokens=8, output_tokens=4))
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="operator")
        value = with_final_stage()
        value["definition"]["budget_warnings"] = {"tokens": 1, "cost_usd": 1}
        _, run = await host.handle("start", value)
        await wait_for_status(host, run["id"], "waiting_for_human")
        _, record = await host.handle("get", {"run_id": run["id"]})
        warnings = [item["event"] for item in record["events"] if item["event"]["type"] == "BudgetWarning"]
        assert [item["dimension"] for item in warnings] == ["tokens"]
        assert warnings[0]["used"] == 12 and record["metrics"]["tokens"] == 12
        await host.handle("review", {"run_id": run["id"], "review_id": record["reviews"][0]["id"], "output": {"text": "Approved"}})
        await host.handle("resume", {"run_id": run["id"]})
        await wait_for_status(host, run["id"], "completed")
        assert len([item for item in host.repository.get(run["id"])["events"] if item["event"]["type"] == "BudgetWarning"]) == 1
        await host.close()

    asyncio.run(exercise())


def test_custom_write_tool_keeps_core_approval_gate(tmp_path):
    from conclave import Tool, Capability

    calls = []

    async def write():
        calls.append("executed")
        return "Written"

    async def exercise():
        tools = ToolRegistry(20000)
        tools.register(Tool("domain_write", "Write a domain record", {}, write, frozenset({Capability.WRITE})))
        value = request()
        value["definition"]["agents"][0]["tools"] = ["domain_write"]
        provider = ScriptedProvider(scripts={"writer": [
            {"type": "tool", "name": "domain_write", "arguments": {}},
            {"type": "final", "content": "Done", "text": "Write was not approved"},
        ]})
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, tools, reviewer="operator")
        _, run = await host.handle("start", value)
        await wait_for_status(host, run["id"], "waiting_for_human")
        assert calls == []
        assert any(item["event"]["type"] == "ToolDenied" and item["event"]["tool"] == "domain_write" for item in host.repository.get(run["id"])["events"])
        await host.close()

    asyncio.run(exercise())


def test_unavailable_selected_adapter_fails_before_creating_run(tmp_path):
    from conclave.application.extensions import ConnectorAdapter

    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), ToolRegistry(20000), reviewer="operator",
                        adapters=[ConnectorAdapter("domain_source", "Domain source", "custom", (), lambda: False)])
    value = request()
    value["definition"]["integrations"] = [{"connector": "domain_source", "purpose": "Evidence", "scope": "", "mode": "read"}]
    with pytest.raises(ValueError, match="connector is unavailable"):
        asyncio.run(host.handle("start", value))
    assert host.repository.list("research-brief") == []


def test_host_uses_shared_configurable_defaults(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    defaults = json.loads((Path(__file__).parents[1] / "conclave/application/data/defaults.json").read_text())
    defaults["run_config"]["max_tool_output_chars"] = 123
    custom = tmp_path / "defaults.json"
    custom.write_text(json.dumps(defaults))
    monkeypatch.setenv("STUDIO_DEFAULTS_FILE", str(custom))
    monkeypatch.setenv("STUDIO_RUN_DATABASE", str(tmp_path / "runs.sqlite"))
    for key in ("STUDIO_SOURCE_DIR", "STUDIO_WEB", "STUDIO_PROVIDER", "STUDIO_EXTENSIONS"):
        monkeypatch.delenv(key, raising=False)
    host = host_module.configured_host()
    assert host.tools.max_output_chars == 123
    assert host.repository.path == tmp_path / "runs.sqlite"


@pytest.mark.parametrize("name,model,available", [("codex", None, True), ("opencode", "provider/model", True), ("ollama", "local-model", True), ("codex", None, False)])
def test_conclave_provider_configuration_and_legacy_precedence(tmp_path, monkeypatch, name, model, available):
    import os
    calls = []
    provider = ScriptedProvider()
    for key in list(os.environ):
        if key.startswith(("CONCLAVE_", "STUDIO_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CONCLAVE_RUN_DATABASE", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("CONCLAVE_PROVIDER", name)
    monkeypatch.setenv("STUDIO_PROVIDER", "ignored")
    if model:
        monkeypatch.setenv("CONCLAVE_MODEL", model)
    monkeypatch.setattr(host_module.shutil, "which", lambda name: "fake-executable" if available else None)
    for backend in ("CodexProvider", "OpenCodeProvider", "OllamaProvider"):
        monkeypatch.setattr(host_module, backend, lambda *args: calls.append(args) or provider)
    host = host_module.configured_host(workspace=tmp_path)
    assert host.provider_name == name
    if available:
        assert host.provider is provider
        if name == "opencode":
            assert calls == [(tmp_path.resolve(), model, 300.0)]
        elif name == "ollama":
            assert calls == [(model, 300.0, "http://127.0.0.1:11434")]
        else:
            assert calls == [(model, 300.0)]
    else:
        assert host.provider is None and "unavailable" in host.unavailable


def test_conclave_host_invalid_settings_and_explicit_web_tools(tmp_path, monkeypatch):
    import os
    import conclave.tools
    from conclave import Capability, Tool
    for key in list(os.environ):
        if key.startswith(("CONCLAVE_", "STUDIO_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CONCLAVE_RUN_DATABASE", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("CONCLAVE_SOURCE_DIR", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="source folder"):
        host_module.configured_host()
    monkeypatch.delenv("CONCLAVE_SOURCE_DIR")
    monkeypatch.setenv("CONCLAVE_MODEL_TIMEOUT", "nan")
    with pytest.raises(ValueError, match="finite and positive"):
        host_module.configured_host()
    monkeypatch.delenv("CONCLAVE_MODEL_TIMEOUT")
    monkeypatch.setenv("CONCLAVE_HISTORY_LIMIT", "0")
    with pytest.raises(ValueError, match="positive"):
        host_module.configured_host()
    monkeypatch.delenv("CONCLAVE_HISTORY_LIMIT")
    tools = ToolRegistry(20000)
    for name in ("web_search", "web_fetch"):
        tools.register(Tool(name, name, {}, lambda: None, frozenset({Capability.READ})))
    monkeypatch.setattr(conclave.tools, "build_tool_registry", lambda *args: tools)
    monkeypatch.setenv("CONCLAVE_WEB", "1")
    host = host_module.configured_host()
    assert set(host.tools.tools) == {"list_files", "read_file", "search_files", "knowledge_search", "web_fetch", "web_search"}
    assert [adapter.id for adapter in host.adapters] == ["web_search", "web_reader"]
