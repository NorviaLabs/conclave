"""T9 — the host: list uncertain actions, check them, let a person decide or
undo, and check again before a run continues.

The host is what Studio, REST and the CLI all call. It binds each run's tools
afresh (so grants are rechecked), records who decided, and refuses to continue
while anything is still uncertain.
"""

import asyncio

import pytest

from actions_support import Recorder, checker, final, note, registry, write_request
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost
from conftest import ScriptedProvider


async def wait_for_status(host, run_id, *statuses):
    async with asyncio.timeout(5):
        while host.repository.get(run_id)["status"] not in statuses:
            await asyncio.sleep(0.005)


def host_for(path, tools, provider=None, **options):
    return LocalRunHost(RunRepository(path), provider or ScriptedProvider({"writer": [note(), final()]}), tools,
                        reviewer="Ana Ruiz", **options)


async def crashed_mid_write(path, *recorders, request=None):
    """Start a run on one host, stop that host while the writes are in flight."""
    for recorder in recorders:
        recorder.gate = asyncio.Event()
    scripts = {"writer": [note(), final("Announced")],
               "builder": [{"type": "tool", "name": "open_change", "arguments": {"text": "change"}}, final("Built")]}
    host = host_for(path, registry(*recorders), ScriptedProvider(scripts))
    _, run = await host.handle("start", request or write_request(autonomous=True))
    async with asyncio.timeout(5):
        for recorder in recorders:
            await recorder.started.wait()
    await host.close()
    for recorder in recorders:
        recorder.gate = None
    assert host.repository.get(run["id"])["status"] == "interrupted"
    return run["id"]


def by_tool(actions, tool="send_note"):
    return next(action for action in actions if action["tool"] == tool)


def test_a_person_sees_what_was_being_sent_and_whether_it_can_be_checked(tmp_path):
    async def exercise():
        unchecked, checked = Recorder(), Recorder("open_change", check=checker("found"))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", unchecked, checked, request=write_request(parallel=True, autonomous=True))
        host = host_for(tmp_path / "runs.sqlite", registry(unchecked, checked))
        code, listed = await host.handle("actions", {"run_id": run_id})
        assert code == 200
        decide, will_check = by_tool(listed["actions"]), by_tool(listed["actions"], "open_change")
        assert decide["label"] == "Send note" and decide["state"] == "uncertain"
        assert decide["arguments"] == {"text": "hello"}
        assert decide["check"]["available"] is False and decide["undoable"] is False and decide["final"] is False
        assert will_check["check"]["available"] is True
        assert will_check["check"]["description"] == "Will look for the note"
        assert will_check["check"]["result"] is None
        await host.close()

    asyncio.run(exercise())


def test_a_person_can_decide_and_undo_until_the_run_continues(tmp_path):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder), ScriptedProvider({"writer": [final()]}))
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        _, listed = await host.handle("settle", {"run_id": run_id, "action_id": action_id, "decision": "done",
                                                 "note": "Posted at 10:41"})
        decided = by_tool(listed["actions"])
        assert (decided["state"], decided["settled_by"], decided["note"], decided["undoable"]) == (
            "done", "Ana Ruiz", "Posted at 10:41", True)
        assert "arguments" not in decided, "a write marked done will not be sent, so its details are not offered"
        _, listed = await host.handle("settle", {"run_id": run_id, "action_id": action_id, "decision": "undo"})
        assert by_tool(listed["actions"])["state"] == "uncertain" and by_tool(listed["actions"])["note"] is None
        _, listed = await host.handle("settle", {"run_id": run_id, "action_id": action_id, "decision": "resend"})
        assert by_tool(listed["actions"])["arguments"] == {"text": "hello"}, "a resend shows exactly what will be sent"
        await host.handle("resume", {"run_id": run_id})
        await wait_for_status(host, run_id, "completed")
        assert len(recorder.calls) == 2
        with pytest.raises(ValueError, match="already used when the run continued"):
            await host.handle("settle", {"run_id": run_id, "action_id": action_id, "decision": "undo"})
        await host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("value, message", [
    ({"decision": "done", "note": "x" * 501}, "500 characters"),
    ({"decision": "maybe"}, "Choose done, resend or undo"),
    ({"decision": "done", "reviewer": "spoof"}, None),
])
def test_a_decision_is_explicit_and_bounded(tmp_path, value, message):
    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        with pytest.raises(ValueError, match=message):
            await host.handle("settle", {"run_id": run_id, "action_id": action_id, **value})
        assert by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["state"] == "uncertain"
        await host.close()

    asyncio.run(exercise())


def test_a_completed_write_cannot_be_decided(tmp_path):
    async def exercise():
        recorder = Recorder()
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        _, run = await host.handle("start", write_request(autonomous=True))
        await wait_for_status(host, run["id"], "completed")
        action = by_tool((await host.handle("actions", {"run_id": run["id"]}))[1]["actions"])
        assert action["state"] == "completed" and "arguments" not in action
        with pytest.raises(ValueError):
            await host.handle("settle", {"run_id": run["id"], "action_id": action["id"], "decision": "resend"})
        await host.close()

    asyncio.run(exercise())


def test_a_check_settles_what_it_finds_and_cannot_be_undone(tmp_path):
    async def exercise():
        recorder = Recorder(check=checker("found", summary="Found the note"))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        _, listed = await host.handle("check", {"run_id": run_id, "action_id": action_id})
        action = by_tool(listed["actions"])
        assert (action["state"], action["settled_by"], action["check"]["result"], action["check"]["summary"]) == (
            "done", "check", "found", "Found the note")
        assert action["undoable"] is False
        with pytest.raises(ValueError):
            await host.handle("settle", {"run_id": run_id, "action_id": action_id, "decision": "undo"})
        await host.close()

    asyncio.run(exercise())


def test_continue_checks_first_and_carries_on_when_everything_is_settled(tmp_path):
    async def exercise():
        recorder = Recorder(check=checker("found", detail="The note is in #eng"))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        provider = ScriptedProvider({"writer": [final()]})
        host = host_for(tmp_path / "runs.sqlite", registry(recorder), provider)
        await host.handle("resume", {"run_id": run_id})
        await wait_for_status(host, run_id, "completed")
        assert len(recorder.calls) == 1 and "The note is in #eng" in provider.prompts[0][1]
        await host.close()

    asyncio.run(exercise())


def test_continue_stops_when_a_check_needs_a_person_and_keeps_earlier_choices_open(tmp_path):
    from conclave.application.runs import ActionsUnsettled

    async def exercise():
        decided, inconclusive = Recorder(), Recorder("open_change", check=checker("inconclusive"))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", decided, inconclusive,
                                         request=write_request(parallel=True, autonomous=True))
        host = host_for(tmp_path / "runs.sqlite", registry(decided, inconclusive))
        actions = (await host.handle("actions", {"run_id": run_id}))[1]["actions"]
        await host.handle("settle", {"run_id": run_id, "action_id": by_tool(actions)["id"], "decision": "done"})
        with pytest.raises(ActionsUnsettled) as refused:
            await host.handle("resume", {"run_id": run_id})
        assert refused.value.actions == [by_tool(actions, "open_change")["id"]]
        assert host.repository.get(run_id)["status"] == "interrupted"
        listed = (await host.handle("actions", {"run_id": run_id}))[1]["actions"]
        assert by_tool(listed, "open_change")["check"]["result"] == "inconclusive"
        assert by_tool(listed)["undoable"] is True, "the run never continued, so the choice is still provisional"
        await host.handle("settle", {"run_id": run_id, "action_id": by_tool(actions)["id"], "decision": "undo"})
        await host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("exact, resent", [(True, True), (False, False)])
def test_continue_resends_only_what_a_check_proved_absent(tmp_path, exact, resent):
    from conclave.application.runs import ActionsUnsettled

    async def exercise():
        recorder = Recorder(check=checker("absent", exact=exact))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder), ScriptedProvider({"writer": [final()]}))
        if resent:
            await host.handle("resume", {"run_id": run_id})
            await wait_for_status(host, run_id, "completed")
            assert len(recorder.calls) == 2
        else:
            with pytest.raises(ActionsUnsettled):
                await host.handle("resume", {"run_id": run_id})
            assert len(recorder.calls) == 1
        await host.close()

    asyncio.run(exercise())


def test_a_tool_this_host_no_longer_offers_cannot_be_checked(tmp_path):
    async def exercise():
        recorder = Recorder(check=checker("found"))
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        tools = registry(Recorder("open_change"))
        host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), ScriptedProvider(), tools, reviewer="Ana Ruiz")
        action_id = (await host.handle("actions", {"run_id": run_id}))[1]["actions"][0]["id"]
        _, listed = await host.handle("check", {"run_id": run_id, "action_id": action_id})
        assert listed["actions"][0]["check"]["result"] == "unavailable"
        assert listed["actions"][0]["state"] == "uncertain"
        await host.close()

    asyncio.run(exercise())


def test_stopping_a_run_mid_write_cancels_it_and_keeps_the_write_for_the_record(tmp_path):
    async def exercise():
        recorder = Recorder()
        recorder.gate = asyncio.Event()
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        _, run = await host.handle("start", write_request(autonomous=True))
        await recorder.started.wait()
        _, record = await host.handle("cancel", {"run_id": run["id"]})
        assert record["status"] == "cancelled"
        assert record["actions"] == {"to_check": 0, "needs_decision": 0, "unknown": 1}
        with pytest.raises(ValueError, match="Only a waiting, paused or stopped run"):
            await host.handle("resume", {"run_id": run["id"]})
        await host.close()

    asyncio.run(exercise())


def test_another_principal_cannot_see_or_decide_a_connection_run(tmp_path):
    from cryptography.fernet import Fernet
    from conclave.application.principal import Principal
    from conclave.integrations.connections import ConnectionStore

    async def exercise():
        recorder = Recorder()
        run_id = await crashed_mid_write(tmp_path / "runs.sqlite", recorder)
        host = host_for(tmp_path / "runs.sqlite", registry(recorder))
        host.connections = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key())
        host.connections.pin_run(run_id, "local", "Ana Ruiz")
        other = Principal("Mallory")
        action_id = by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["id"]
        for command, value in [("actions", {}), ("check", {"action_id": action_id}),
                               ("settle", {"action_id": action_id, "decision": "resend"})]:
            with pytest.raises(ValueError, match="belongs to another principal"):
                await host.handle(command, {"run_id": run_id, **value}, principal=other)
        assert by_tool((await host.handle("actions", {"run_id": run_id}))[1]["actions"])["state"] == "uncertain"
        await host.close()

    asyncio.run(exercise())
