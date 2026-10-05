import asyncio

import pytest

from conclave import Completion, ToolRegistry
from conftest import ScriptedProvider
from test_studio_runs import request, with_final_stage
from studio.runtime import host as host_module
from studio.runtime.host import LocalRunHost
from studio.runtime.runs import RunRepository


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
    assert list(host.tools.tools) == ["read_file"]

    async def exercise():
        assert "Supplied real evidence" in await host.tools.execute("read_file", {"path": "brief.md"})
        for path in (".secret.md", "../outside.md", "escape.md", ".env"):
            with pytest.raises((ValueError, RuntimeError)):
                await host.tools.execute("read_file", {"path": path})
        await host.close()

    asyncio.run(exercise())
