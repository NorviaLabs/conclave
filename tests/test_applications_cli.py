import json

import pytest

from applications import cli
from conclave import ToolRegistry
from conftest import ScriptedProvider
from conclave.application.service import LocalRunHost
from conclave.application.runs import RunRepository
from test_studio_runs import with_final_stage


def test_create_and_run_custom_application(tmp_path, monkeypatch, capsys):
    path = tmp_path / "application.json"
    cli.run(["create", str(path), "--name", "Release writer", "--instructions", "Write release notes for the supplied changes."])
    definition = json.loads(path.read_text())
    assert definition["name"] == "Release writer"
    assert definition["agents"][0]["role"] == "custom"
    assert definition["agents"][0]["tools"] == []
    with pytest.raises(SystemExit) as refused:
        cli.run(["create", str(path), "--name", "Overwrite", "--instructions", "Replace"])
    assert refused.value.code == 2
    assert json.loads(path.read_text())["name"] == "Release writer"
    capsys.readouterr()
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Release notes for version one"})
    repository = RunRepository(tmp_path / "runs.sqlite")
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: LocalRunHost(repository, provider, ToolRegistry(20000), reviewer="operator"))
    cli.run(["run", str(path), "--task", "Version one added export"])
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "completed"
    assert record["request"]["definition"] == definition
    assert any(event["event"].get("patch") == "Release notes for version one" for event in record["events"])
    assert "Version one added export" in str(provider.prompts)
    cli.run(["status", record["id"]])
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_exported_application_review_and_resume(tmp_path, monkeypatch, capsys):
    request = with_final_stage()
    path = tmp_path / "export.json"
    path.write_text(json.dumps(request["definition"]))
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(request["inputs"]))
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Custom result"})
    repository = RunRepository(tmp_path / "runs.sqlite")
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: LocalRunHost(repository, provider, ToolRegistry(20000), reviewer="operator"))
    cli.run(["run", str(path), "--task", request["task"], "--inputs", str(inputs)])
    waiting = json.loads(capsys.readouterr().out)
    assert waiting["status"] == "waiting_for_human"
    output = tmp_path / "review.json"
    output.write_text(json.dumps({"text": "Approved by operator"}))
    cli.run(["review", waiting["id"], waiting["reviews"][0]["id"], "--output", str(output)])
    capsys.readouterr()
    cli.run(["resume", waiting["id"]])
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "completed"
    assert len(provider.prompts) == 2


def test_run_selects_or_authorizes_a_source(tmp_path, monkeypatch, capsys):
    """The CLI can answer the source question without an environment setting."""
    path = tmp_path / "application.json"
    cli.run(["create", str(path), "--name", "Reader", "--instructions", "Read the supplied notes."])
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Read"})
    repository = RunRepository(tmp_path / "runs.sqlite")
    seen = {}

    def host(**kwargs):
        seen.update(kwargs)
        return LocalRunHost(repository, provider, ToolRegistry(20000), reviewer="operator")

    monkeypatch.setattr(cli, "configured_host", host)

    cli.run(["run", str(path), "--task", "Read the notes", "--source", "case-a"])
    record = json.loads(capsys.readouterr().out)
    assert record["request"]["resources"] == {"source": "case-a"}
    assert seen["source_dir"] is None

    cli.run(["run", str(path), "--task", "Read the notes", "--source-dir", str(tmp_path)])
    capsys.readouterr()
    assert seen["source_dir"] == str(tmp_path)


def test_invalid_definition_does_not_create_run(tmp_path, monkeypatch):
    path = tmp_path / "invalid.json"
    path.write_text('{}')
    repository = RunRepository(tmp_path / "runs.sqlite")
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: LocalRunHost(repository, ScriptedProvider(), ToolRegistry(20000), reviewer="operator"))
    with pytest.raises(SystemExit) as failed:
        cli.run(["run", str(path), "--task", "Test"])
    assert failed.value.code == 2
    assert repository.list(str(path.resolve())) == []


def test_cli_serve_uses_conclave_provider_settings(monkeypatch):
    from conclave import api
    calls = []

    async def serve(port):
        import os
        calls.append((port, os.environ.get("CONCLAVE_PROVIDER"), os.environ.get("CONCLAVE_MODEL")))

    monkeypatch.setattr(api, "serve", serve)
    monkeypatch.delenv("CONCLAVE_PROVIDER", raising=False)
    monkeypatch.delenv("CONCLAVE_MODEL", raising=False)
    cli.run(["serve", "--port", "9000", "--provider", "custom", "--model", "exact/model"])
    import os
    assert calls == [(9000, "custom", "exact/model")]
    assert "CONCLAVE_PROVIDER" not in os.environ
    assert "CONCLAVE_MODEL" not in os.environ
    for port in ("-1", "65536"):
        with pytest.raises(SystemExit) as invalid:
            cli.run(["serve", "--port", port])
        assert invalid.value.code == 2
