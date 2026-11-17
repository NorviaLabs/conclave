"""N5 and N6 — the notifier: one message per waiting point, sent after the run
has stopped, never failing or holding up the run; and what happens across
restarts, at start-up and from the CLI."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from notification_support import (ANA, BEA, CANARY, STUDIO, FakeConnections, api, definition, email, events, host,
                                  run_request, settle, slack, waiting_event, webhook)


def principal(name=ANA):
    from conclave.application.principal import Principal
    return Principal(name)


def connections():
    return (FakeConnections()
            .add("team_slack", owner=ANA, integration="slack")
            .add("ana_gmail", owner=ANA, integration="gmail")
            .add("ops_hook", owner=ANA, integration="webhooks", name="Ops incident hook"))


async def started(service, block=None, *, by=ANA, grant=True, pause=False):
    """Save the digest, grant its connection, and start a run that will wait."""
    block = slack() if block is None else block
    code, saved = await service.dispatch("POST", "/api/applications", {"definition": definition(block)}, principal=principal(by))
    assert code == 201, saved
    if grant and service.host.connections is not None:
        service.host.connections.grant(block["connection"], saved["id"])
    code, run = await service.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                             "task": "Summarise " + CANARY["task"], "inputs": {"team": CANARY["input"]}},
                                       principal=principal(by))
    assert code == 201, run
    if pause:
        service.host.pauses.add(run["id"])
    return saved, run["id"]


async def review_and_resume(service, run_id, *, pause=False):
    review = service.host.repository.get(run_id)["reviews"][-1]
    code, body = await service.dispatch("POST", f"/api/runs/{run_id}/reviews",
                                        {"review_id": review["id"], "output": {"text": "Looks right"}}, principal=principal())
    assert code == 200, body
    code, body = await service.dispatch("POST", f"/api/runs/{run_id}/resume", {}, principal=principal())
    assert code == 200, body
    if pause:
        service.host.pauses.add(run_id)


def expected(service, saved, run_id):
    from conclave.application.notices import waiting_message

    row = waiting_event(service.host, run_id)
    return waiting_message(saved["definition"], application_id=saved["id"], run_id=run_id,
                           event={**row["event"], "sequence": row["sequence"]}, studio_url=STUDIO), row["sequence"]


# --- N5: sending -------------------------------------------------------------------


def test_a_review_stop_sends_one_message_after_the_run_has_stopped(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        saved, run_id = await started(service)
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "waiting_for_human"
        message, sequence = expected(service, saved, run_id)
        sent = service.host.connections.sent
        assert len(sent) == 1
        assert (sent[0]["connection"], sent[0]["operation"], sent[0]["owner"], sent[0]["application"]) == (
            "team_slack", "send_message", ANA, saved["id"])
        assert sent[0]["arguments"] == message.arguments
        told = events(service.host, run_id, "NotificationSent")
        assert [(item["waiting_sequence"], item["via"], item["target"], item["step"]) for item in told] == [
            (sequence, "slack", "#reviews", {"id": "review", "name": "Review the digest"})]
        assert service.host.repository.get(run_id)["status"] == "waiting_for_human"
        await service.host.close()

    asyncio.run(exercise())


def test_a_pause_names_the_step_it_stopped_before(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, run_id = await started(service, pause=True)
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "paused"
        assert "is paused before “Write the digest”" in service.host.connections.sent[0]["arguments"]["text"]
        assert events(service.host, run_id, "NotificationSent")[0]["step"] == {"id": "write", "name": "Write the digest"}
        await service.host.close()

    asyncio.run(exercise())


def test_email_and_webhook_are_sent_with_their_own_arguments(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        saved, mailed = await started(service, email())
        await settle(service.host)
        assert service.host.connections.sent[0]["operation"] == "send_email"
        assert service.host.connections.sent[0]["arguments"] == expected(service, saved, mailed)[0].arguments
        saved, hooked = await started(service, webhook())
        await settle(service.host)
        hook = service.host.connections.sent[1]
        message, sequence = expected(service, saved, hooked)
        assert (hook["operation"], hook["arguments"]) == ("send", message.arguments)
        assert hook["action"] == f"notice-{hooked}-{sequence}", "the webhook gets an Idempotency-Key"
        # A connection's own name is its owner's to see (security review NS3): the run names it by id.
        assert events(service.host, hooked, "NotificationSent")[0]["target"] == "ops_hook"
        await service.host.close()

    asyncio.run(exercise())


def test_a_failed_send_is_recorded_and_the_run_carries_on(tmp_path):
    from conclave.integrations.transport import IntegrationError

    async def exercise():
        store = connections()
        store.fail = IntegrationError("Slack rejected the operation")
        service = api(tmp_path, connections=store)
        _, run_id = await started(service)
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "waiting_for_human"
        failed = events(service.host, run_id, "NotificationFailed")
        assert [item["reason"] for item in failed] == ["Slack rejected the operation"]
        store.fail = None
        await review_and_resume(service, run_id)
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "completed"
        await service.host.close()

    asyncio.run(exercise())


def test_without_a_studio_address_nothing_is_sent_and_the_run_says_why(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections(), studio_url=None)
        _, run_id = await started(service)
        await settle(service.host)
        assert service.host.connections.sent == []
        assert [item["reason"] for item in events(service.host, run_id, "NotificationFailed")] == [
            "This host has no Studio address (CONCLAVE_STUDIO_URL)"]
        assert service.host.repository.get(run_id)["status"] == "waiting_for_human"
        await service.host.close()

    asyncio.run(exercise())


def test_a_slow_send_never_holds_up_the_run(tmp_path):
    async def exercise():
        store = connections()
        store.gate = asyncio.Event()
        service = api(tmp_path, connections=store)
        _, run_id = await started(service)
        async with asyncio.timeout(5):
            while not store.sent:
                await asyncio.sleep(0.01)
        await review_and_resume(service, run_id)
        async with asyncio.timeout(5):
            while service.host.repository.get(run_id)["status"] != "completed":
                await asyncio.sleep(0.01)
        store.gate.set()
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "completed"
        assert len(events(service.host, run_id, "NotificationSent")) == 1
        await service.host.close()

    asyncio.run(exercise())


def test_an_owner_without_access_sends_nothing_and_the_run_says_why(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, run_id = await started(service, grant=False)
        await settle(service.host)
        assert service.host.connections.sent == []
        assert [item["reason"] for item in events(service.host, run_id, "NotificationFailed")] == [
            "team_slack is not granted send_message for this application"]
        # Bea's change makes the notification hers; team_slack is Ana's.
        saved = service.workspace.applications()[0]
        service.host.connections.grant("team_slack", saved["id"])
        changed = definition(slack())
        changed["description"] = "Bea's wording"
        code, _ = await service.dispatch("PUT", f"/api/applications/{saved['id']}",
                                         {"definition": changed, "revision": saved["revision"]}, principal=principal(BEA))
        assert code == 200
        code, run = await service.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"] + 1,
                                                                 "task": "Again", "inputs": {"team": "x"}}, principal=principal(BEA))
        assert code == 201, run
        await settle(service.host)
        assert service.host.connections.sent == []
        assert [item["reason"] for item in events(service.host, run["id"], "NotificationFailed")] == [
            f"team_slack is not {BEA}'s connection"]
        await service.host.close()

    asyncio.run(exercise())


def test_a_run_that_moved_on_before_the_send_is_not_told(tmp_path):
    from conclave.application.notifier import Notifier

    async def exercise():
        made = host(tmp_path, connections=connections())
        run_id = made.repository.create(run_request(slack()), actor=ANA)
        made.repository._claim(run_id)
        made.repository._append(run_id, {"type": "ApplicationFinished", "status": "waiting_for_human", "stage": "review",
                                         "state": {}, "views": [], "checkpoint": {"version": 1, "patches": {}}})
        made.repository._append(run_id, {"type": "ApplicationFinished", "status": "cancelled", "reason": "Stopped"})
        await Notifier(made).deliver(run_id)
        assert made.connections.sent == []
        assert [item["reason"] for item in events(made, run_id, "NotificationFailed")] == [
            "The run had moved on before the message could be sent"]

    asyncio.run(exercise())


def test_resuming_and_waiting_again_sends_a_second_message(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        # No wait between a run's messages here; the default ten minutes is
        # security review NS2 (test_notification_security.py).
        service.host.notify_run_interval = 0
        _, run_id = await started(service)
        await settle(service.host)
        await review_and_resume(service, run_id, pause=True)
        await settle(service.host)
        assert service.host.repository.get(run_id)["status"] == "paused"
        told = events(service.host, run_id, "NotificationSent")
        assert len(service.host.connections.sent) == 2 and len({item["waiting_sequence"] for item in told}) == 2
        # Paused straight after resuming: the submitted review is applied when
        # the run continues, so the step it waits before is still the review.
        assert "is paused before “Review the digest”" in service.host.connections.sent[1]["arguments"]["text"]
        await service.host.close()

    asyncio.run(exercise())


def test_an_unexpected_error_is_reduced_to_a_plain_reason(tmp_path):
    async def exercise():
        store = connections()
        store.fail = RuntimeError("boom " + CANARY["secret"])
        service = api(tmp_path, connections=store)
        _, run_id = await started(service)
        await settle(service.host)
        assert [item["reason"] for item in events(service.host, run_id, "NotificationFailed")] == ["The message could not be sent"]
        assert CANARY["secret"] not in json.dumps(service.host.repository.get(run_id))
        await service.host.close()

    asyncio.run(exercise())


def test_a_definition_run_from_a_file_is_sent_as_whoever_started_it(tmp_path):
    from conclave.application.notifier import Notifier

    async def exercise():
        made = host(tmp_path, connections=connections())
        made.connections.grant("team_slack", "digest")
        run_id = made.repository.create(run_request(slack()), actor=ANA)
        made.repository._claim(run_id)
        made.repository._append(run_id, {"type": "ApplicationFinished", "status": "waiting_for_human", "stage": "review",
                                         "state": {}, "views": [], "checkpoint": {"version": 1, "patches": {}}})
        await Notifier(made).deliver(run_id)
        assert [item["owner"] for item in made.connections.sent] == [ANA]

    asyncio.run(exercise())


# --- N6: restarts, start-up, the CLI -------------------------------------------------


def stored_waiting_run(tmp_path):
    from conclave.application.runs import RunRepository

    store = RunRepository(tmp_path / "runs.sqlite")
    run_id = store.create(run_request(slack()), actor=ANA)
    store._claim(run_id)
    store._append(run_id, {"type": "ApplicationFinished", "status": "waiting_for_human", "stage": "review",
                           "state": {}, "views": [], "checkpoint": {"version": 1, "patches": {}}})
    return store, run_id, store.get(run_id)["events"][-1]["sequence"]


def test_a_send_cut_off_by_a_restart_is_marked_uncertain_once_and_never_resent(tmp_path):
    from conclave.application.runs import RunRepository

    store, run_id, sequence = stored_waiting_run(tmp_path)
    assert store.notices.claim(run_id, sequence)
    RunRepository(tmp_path / "runs.sqlite").sweep_abandoned()
    RunRepository(tmp_path / "runs.sqlite").sweep_abandoned()
    reopened = RunRepository(tmp_path / "runs.sqlite")
    assert reopened.notices.state(run_id, sequence) == "unknown"
    uncertain = [row["event"] for row in reopened.get(run_id)["events"] if row["event"]["type"] == "NotificationUncertain"]
    assert len(uncertain) == 1 and uncertain[0]["waiting_sequence"] == sequence
    assert "may not have been sent" in uncertain[0]["reason"]

    async def exercise():
        made = host(tmp_path, connections=connections())
        made.connections.grant("team_slack", "digest")
        assert await made.notifier.deliver_all() == []
        assert made.connections.sent == []

    asyncio.run(exercise())


def test_a_notice_never_attempted_is_sent_once_at_start_up(tmp_path):
    stored_waiting_run(tmp_path)

    async def exercise():
        made = host(tmp_path, connections=connections())
        made.connections.grant("team_slack", "digest")
        assert [item["state"] for item in await made.notifier.deliver_all()] == ["sent"]
        assert await made.notifier.deliver_all() == []
        assert len(made.connections.sent) == 1

    asyncio.run(exercise())


def test_serving_sends_what_was_left_due(tmp_path):
    stored_waiting_run(tmp_path)

    async def exercise():
        service = api(tmp_path, connections=connections())
        service.host.connections.grant("team_slack", "digest")
        await service.start_schedules()
        await settle(service.host)
        assert len(service.host.connections.sent) == 1
        await service.stop_schedules()
        await service.host.close()

    asyncio.run(exercise())


def test_closing_the_host_waits_for_a_send_and_a_send_it_gives_up_on_stays_uncertain(tmp_path):
    from conclave.application.runs import RunRepository

    async def exercise():
        service = api(tmp_path, connections=connections())
        _, run_id = await started(service)
        task = service.host.tasks[run_id][0]
        await task
        await service.host.close()
        assert len(service.host.connections.sent) == 1 and events(service.host, run_id, "NotificationSent")

        stuck = api(tmp_path / "stuck", connections=connections())
        stuck.host.connections.gate = asyncio.Event()
        stuck.host.NOTICE_GRACE = 0.1
        _, stuck_run = await started(stuck)
        await stuck.host.tasks[stuck_run][0]
        async with asyncio.timeout(2):
            await stuck.host.close()
        sequence = waiting_event(stuck.host, stuck_run)["sequence"]
        assert stuck.host.repository.notices.state(stuck_run, sequence) == "sending"
        RunRepository(tmp_path / "stuck" / "runs.sqlite").sweep_abandoned()
        assert stuck.host.repository.notices.state(stuck_run, sequence) == "unknown"

    (tmp_path / "stuck").mkdir()
    asyncio.run(exercise())


def test_the_cli_sends_before_it_exits(tmp_path, monkeypatch, capsys):
    from conclave import cli

    path = tmp_path / "digest.json"
    path.write_text(json.dumps(definition(slack())))
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"team": "billing"}))
    application_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    made = host(tmp_path, connections=connections())
    made.connections.grant("team_slack", application_id)
    monkeypatch.setattr(cli, "configured_host", lambda **options: made)
    args = SimpleNamespace(command="run", definition=str(path), application_id=None, revision=1, task="Summarise",
                           inputs=str(inputs), source=None, format="json", source_dir=None)
    asyncio.run(cli.execute(args))
    assert len(made.connections.sent) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "waiting_for_human"


def test_applications_without_the_setting_send_nothing_and_record_nothing(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        code, saved = await service.dispatch("POST", "/api/applications", {"definition": definition(None)}, principal=principal())
        code, run = await service.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                                 "task": "x", "inputs": {"team": "x"}}, principal=principal())
        await settle(service.host)
        assert service.host.connections.sent == []
        assert service.host.repository.notices.due(run["id"]) == []
        assert not [event for event in events(service.host, run["id"]) if event["type"].startswith("Notification")]
        await service.host.close()

    asyncio.run(exercise())
