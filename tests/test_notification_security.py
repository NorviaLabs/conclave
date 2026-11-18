"""Step 8 — the security fixes for run notifications.

NS1: a run is told from the account of whoever owned the version it runs.
NS2: limits on how often one run, and one application, send.
NS3: only the owner sees the details of the owner's connection.
"""

import asyncio

import pytest

from notification_support import ANA, BEA, FakeConnections, api, definition, events, settle, slack, webhook
from conftest import share


def principal(name=ANA):
    from conclave.application.principal import Principal
    return Principal(name)


async def save(service, value, by, saved=None):
    if saved is None:
        code, body = await service.dispatch("POST", "/api/applications", {"definition": value}, principal=principal(by))
        if code == 201:
            # Ana and Bea work on the same applications.
            share(service, body["id"], *({ANA, BEA} - {by}))
    else:
        code, body = await service.dispatch("PUT", f"/api/applications/{saved['id']}",
                                            {"definition": value, "revision": saved["revision"]}, principal=principal(by))
    assert code in {200, 201}, body
    return body


async def start(service, saved, by=ANA, *, pause=False):
    code, run = await service.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                             "task": "x", "inputs": {"team": "x"}}, principal=principal(by))
    assert code == 201, run
    if pause:
        service.host.pauses.add(run["id"])
    return run["id"]


def store():
    return (FakeConnections().add("team_slack", owner=ANA, integration="slack")
            .add("ops_hook", owner=ANA, integration="webhooks", name="Ana's private incident hook (PagerDuty, prod)"))


# --- NS1: the sender is pinned with the run ------------------------------------------


def test_a_run_is_told_from_the_account_of_whoever_owned_the_version_it_runs(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        saved = await save(service, definition(slack(), name="URGENT: sign in at evil.example to approve"), BEA, saved)
        # Bea runs her version; before it reaches the review, Ana saves a fix.
        run_id = await start(service, saved, BEA)
        assert service.host.repository.notices.sender(run_id) == BEA
        await save(service, definition(slack()), ANA, saved)
        await settle(service.host)
        assert service.host.connections.sent == [], "Ana's account never sends Bea's words"
        failed = events(service.host, run_id, "NotificationFailed")
        assert [(item["sent_as"], item["reason"]) for item in failed] == [(BEA, f"team_slack is not {BEA}'s connection")]
        await service.host.close()

    asyncio.run(exercise())


def test_the_sender_is_recorded_when_the_run_is_created_and_never_changes(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        saved = await save(service, definition(slack()), ANA)
        run_id = await start(service, saved, BEA)
        assert service.host.repository.notices.sender(run_id) == ANA, "Bea runs Ana's version: Ana's words, Ana's account"
        changed = definition(slack())
        changed["description"] = "Bea's wording"
        await save(service, changed, BEA, saved)
        assert service.host.repository.notices.sender(run_id) == ANA
        plain = await save(service, definition(None), ANA)
        assert service.host.repository.notices.sender(await start(service, plain)) is None
        await settle(service.host)
        await service.host.close()

    asyncio.run(exercise())


# --- NS2: limits -------------------------------------------------------------------------


async def review_and_resume(service, run_id, *, pause):
    review = service.host.repository.get(run_id)["reviews"][-1]
    await service.dispatch("POST", f"/api/runs/{run_id}/reviews", {"review_id": review["id"], "output": {"text": "ok"}}, principal=principal())
    code, body = await service.dispatch("POST", f"/api/runs/{run_id}/resume", {}, principal=principal())
    assert code == 200, body
    if pause:
        service.host.pauses.add(run_id)


def test_a_run_that_waits_again_soon_is_not_told_again(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        assert service.host.notify_run_interval == 600, "ten minutes by default"
        saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        run_id = await start(service, saved)
        await settle(service.host)
        await review_and_resume(service, run_id, pause=True)
        await settle(service.host)
        assert len(service.host.connections.sent) == 1
        [failed] = events(service.host, run_id, "NotificationFailed")
        assert failed["reason"] == "Not sent: #reviews was told about this run less than a minute ago"
        await service.host.close()

    asyncio.run(exercise())


def test_after_the_interval_a_run_is_told_again(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        service.host.notify_run_interval = 0
        saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        run_id = await start(service, saved)
        await settle(service.host)
        await review_and_resume(service, run_id, pause=True)
        await settle(service.host)
        assert len(service.host.connections.sent) == 2
        await service.host.close()

    asyncio.run(exercise())


def test_resuming_and_pausing_over_and_over_cannot_flood_the_owners_channel(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        run_id = await start(service, saved, BEA, pause=True)
        await settle(service.host)
        for _ in range(10):
            await service.dispatch("POST", f"/api/runs/{run_id}/resume", {}, principal=principal(BEA))
            service.host.pauses.add(run_id)
            await settle(service.host)
        assert len(service.host.connections.sent) == 1
        assert len(events(service.host, run_id, "NotificationFailed")) == 10
        await service.host.close()

    asyncio.run(exercise())


def test_an_application_sends_at_most_its_hourly_limit(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        assert service.host.notify_hourly_limit == 20, "twenty an hour by default"
        service.host.notify_hourly_limit = 3
        saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        runs = [await start(service, saved, BEA) for _ in range(5)]
        await settle(service.host)
        assert len(service.host.connections.sent) == 3
        reasons = [item["reason"] for run_id in runs for item in events(service.host, run_id, "NotificationFailed")]
        assert reasons == ["Not sent: this application has sent 3 messages in the last hour"] * 2
        other = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", other["id"])
        await start(service, other)
        await settle(service.host)
        assert len(service.host.connections.sent) == 4, "the limit is per application"
        await service.host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("interval, limit, ok", [("30", "5", True), ("0", "1", True), ("-1", "5", False), ("x", "5", False), ("30", "0", False)])
def test_the_limits_are_host_settings(tmp_path, monkeypatch, interval, limit, ok):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    monkeypatch.setenv("CONCLAVE_NOTIFY_RUN_INTERVAL", interval)
    monkeypatch.setenv("CONCLAVE_NOTIFY_HOURLY_LIMIT", limit)
    if not ok:
        with pytest.raises(ValueError, match="CONCLAVE_NOTIFY"):
            configured_host()
        return
    made = configured_host()
    assert (made.notify_run_interval, made.notify_hourly_limit) == (int(interval), int(limit))
    asyncio.run(made.close())


# --- NS3: the owner's connection is the owner's -------------------------------------------


def test_only_the_owner_sees_the_details_of_the_owners_connection(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        saved = await save(service, definition(webhook()), ANA)
        code, theirs = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(BEA))
        assert code == 200
        assert (theirs["owner"], theirs["target"], theirs["usable"]) == (ANA, "ops_hook", False)
        assert theirs["reason"] == f"Notifications cannot be sent as set up; {ANA} can see why"
        code, mine = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(ANA))
        assert mine["target"] == "Ana's private incident hook (PagerDuty, prod)"
        assert mine["reason"] == "ops_hook is not granted send for this application"

    asyncio.run(exercise())


def test_what_is_true_of_the_host_is_shown_to_everyone(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store(), studio_url=None)
        saved = await save(service, definition(slack()), ANA)
        code, theirs = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(BEA))
        assert theirs["reason"] == "This host has no Studio address (CONCLAVE_STUDIO_URL)"
        assert theirs["target"] == "#reviews", "the definition's own channel is no secret"

    asyncio.run(exercise())


def test_a_run_names_a_webhook_by_its_id_not_its_owners_name_for_it(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=store())
        saved = await save(service, definition(webhook()), ANA)
        service.host.connections.grant("ops_hook", saved["id"])
        run_id = await start(service, saved)
        await settle(service.host)
        assert [item["target"] for item in events(service.host, run_id, "NotificationSent")] == ["ops_hook"]
        await service.host.close()

    asyncio.run(exercise())
