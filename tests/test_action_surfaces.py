"""T10 — the same decisions over REST and in a terminal.

Studio calls these routes; a terminal uses the commands. Both go through the
host, so the rules are the host's: owner-scoped, explicit, and final once the
run continues.
"""

import asyncio
import json

import pytest

from actions_support import Recorder, final, registry, write_request
from conclave.api import ApplicationAPI
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conclave.application.workspace import Workspace
from conftest import ScriptedProvider
from test_action_host import crashed_mid_write, wait_for_status


def api_for(tmp_path, recorder, provider=None):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider or ScriptedProvider({"writer": [final()]}),
                        registry(recorder), reviewer="Ana Ruiz")
    return ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))


def test_rest_lists_checks_settles_and_undoes(tmp_path):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        api = api_for(tmp_path, recorder)
        status, listed = await api.dispatch("GET", f"/api/runs/{run_id}/actions")
        assert status == 200 and listed["actions"][0]["arguments"] == {"text": "hello"}
        action_id = listed["actions"][0]["id"]
        status, checked = await api.dispatch("POST", f"/api/runs/{run_id}/actions/check", {"action_id": action_id})
        assert status == 200 and checked["actions"][0]["check"]["result"] == "unavailable"
        status, decided = await api.dispatch("POST", f"/api/runs/{run_id}/actions",
                                             {"action_id": action_id, "decision": "done", "note": "Seen in Slack"})
        assert status == 200 and decided["actions"][0]["state"] == "done"
        status, undone = await api.dispatch("POST", f"/api/runs/{run_id}/actions", {"action_id": action_id, "decision": "undo"})
        assert status == 200 and undone["actions"][0]["state"] == "uncertain"
        await api.host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("body", [
    {"action_id": "x", "decision": "done", "reviewer": "spoof"},
    {"decision": "done"},
    {"action_id": "x", "decision": "approve"},
    {"action_id": "x", "decision": "done", "note": 5},
])
def test_rest_refuses_anything_but_an_explicit_decision(tmp_path, body):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        api = api_for(tmp_path, recorder)
        action_id = (await api.dispatch("GET", f"/api/runs/{run_id}/actions"))[1]["actions"][0]["id"]
        value = {**body, **({"action_id": action_id} if "action_id" in body else {})}
        status, _ = await api.dispatch("POST", f"/api/runs/{run_id}/actions", value)
        assert status == 400
        assert (await api.dispatch("GET", f"/api/runs/{run_id}/actions"))[1]["actions"][0]["state"] == "uncertain"
        await api.host.close()

    asyncio.run(exercise())


def test_rest_resume_names_what_still_needs_a_decision(tmp_path):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        api = api_for(tmp_path, recorder)
        action_id = (await api.dispatch("GET", f"/api/runs/{run_id}/actions"))[1]["actions"][0]["id"]
        status, body = await api.dispatch("POST", f"/api/runs/{run_id}/resume", {})
        assert status == 409 and body["actions"] == [action_id] and "Settle 1 action" in body["error"]
        await api.dispatch("POST", f"/api/runs/{run_id}/actions", {"action_id": action_id, "decision": "done"})
        status, _ = await api.dispatch("POST", f"/api/runs/{run_id}/resume", {})
        assert status == 200
        await wait_for_status(api.host, run_id, "completed")
        await api.host.close()

    asyncio.run(exercise())


def test_the_run_record_and_history_carry_counts_never_details(tmp_path):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        api = api_for(tmp_path, recorder)
        status, record = await api.dispatch("GET", f"/api/runs/{run_id}")
        assert status == 200 and record["actions"] == {"to_check": 0, "needs_decision": 1, "unknown": 0}
        assert '"arguments"' not in json.dumps(record) and "hello" not in json.dumps(record["events"])
        status, history = await api.dispatch("GET", "/api/runs?application_id=announce")
        assert history["runs"][0]["actions"] == {"to_check": 0, "needs_decision": 1, "unknown": 0}
        await api.host.close()

    asyncio.run(exercise())


# --- The terminal ---------------------------------------------------------------


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    from applications import cli

    recorder = Recorder()
    run_id = asyncio.run(crashed_mid_write(tmp_path / "runs.sqlite", recorder))
    provider = ScriptedProvider({"writer": [final()]})
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: LocalRunHost(
        RunRepository(tmp_path / "runs.sqlite"), provider, registry(recorder), reviewer="Ana Ruiz"))
    return cli, run_id, recorder


def test_the_terminal_lists_and_decides(terminal, capsys):
    cli, run_id, recorder = terminal
    cli.run(["actions", run_id])
    listed = json.loads(capsys.readouterr().out)["actions"]
    action_id = listed[0]["id"]
    assert listed[0]["state"] == "uncertain" and listed[0]["arguments"] == {"text": "hello"}
    cli.run(["settle", run_id, action_id, "--done", "--note", "Seen in Slack"])
    assert json.loads(capsys.readouterr().out)["actions"][0]["note"] == "Seen in Slack"
    cli.run(["settle", run_id, action_id, "--undo"])
    assert json.loads(capsys.readouterr().out)["actions"][0]["state"] == "uncertain"
    cli.run(["check", run_id, action_id])
    assert json.loads(capsys.readouterr().out)["actions"][0]["check"]["result"] == "unavailable"
    cli.run(["settle", run_id, action_id, "--resend"])
    capsys.readouterr()
    cli.run(["resume", run_id])
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert len(recorder.calls) == 2


def test_the_terminal_says_what_to_settle_before_resuming(terminal, capsys):
    cli, run_id, _ = terminal
    with pytest.raises(SystemExit) as stopped:
        cli.run(["resume", run_id])
    assert stopped.value.code == 3
    error = capsys.readouterr().err
    assert "1 action still needs a decision" in error and "Send note" in error
    assert f"conclave settle {run_id}" in error and "--done" in error and "--resend" in error


@pytest.mark.parametrize("flags", [[], ["--done", "--resend"], ["--undo", "--note", "x"]])
def test_the_terminal_asks_for_exactly_one_decision(terminal, flags, capsys):
    cli, run_id, _ = terminal
    with pytest.raises(SystemExit) as refused:
        cli.run(["settle", run_id, "some-action", *flags])
    assert refused.value.code == 2
