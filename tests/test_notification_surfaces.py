"""N6 and N7 — what the host says about notifications (runtime, the Studio
address) and the REST endpoints: status and preview."""

import asyncio

import pytest

from notification_support import ANA, BEA, STUDIO, FakeConnections, api, definition, email, slack, webhook


def principal(name=ANA, tenant="local"):
    from conclave.application.principal import Principal
    return Principal(name, tenant)


def connections():
    return FakeConnections().add("team_slack", owner=ANA, integration="slack").add("ana_gmail", owner=ANA, integration="gmail")


# --- N6: runtime and the Studio address ---------------------------------------------


@pytest.mark.parametrize("store, address, notifications, link", [
    (None, STUDIO, False, True), (connections(), STUDIO, True, True), (connections(), None, True, False)])
def test_the_runtime_says_whether_notifications_can_be_used(tmp_path, store, address, notifications, link):
    async def exercise():
        service = api(tmp_path, connections=store, studio_url=address)
        code, body = await service.dispatch("GET", "/api/runtime")
        assert code == 200
        assert (body["features"]["notifications"], body["features"]["studio_url"]) == (notifications, link)

    asyncio.run(exercise())


@pytest.mark.parametrize("value, accepted", [
    ("https://studio.acme.example", "https://studio.acme.example"),
    ("https://studio.acme.example/team/", "https://studio.acme.example/team"),
    ("http://127.0.0.1:8123", "http://127.0.0.1:8123"),
    ("ftp://studio.acme.example", None), ("studio.acme.example", None),
    ("https://studio.acme.example/?x=1", None), ("https://studio.acme.example/#top", None)])
def test_the_studio_address_setting_is_checked_when_the_host_starts(tmp_path, monkeypatch, value, accepted):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    monkeypatch.setenv("CONCLAVE_STUDIO_URL", value)
    if accepted is None:
        with pytest.raises(ValueError, match="CONCLAVE_STUDIO_URL"):
            configured_host()
    else:
        made = configured_host()
        assert made.studio_url == accepted
        asyncio.run(made.close())


def test_a_host_has_no_studio_address_unless_it_is_set(tmp_path, monkeypatch):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    monkeypatch.delenv("CONCLAVE_STUDIO_URL", raising=False)
    made = configured_host()
    assert made.studio_url is None and made.notifier is not None
    asyncio.run(made.close())


def test_local_serving_links_to_its_own_address(tmp_path, monkeypatch):
    from conclave import api as module

    service = api(tmp_path, connections=None, studio_url=None)
    monkeypatch.setattr(module, "configured_api", lambda **options: service)

    async def exercise():
        serving = asyncio.create_task(module.serve(0))
        async with asyncio.timeout(10):
            while not service.host.studio_url:
                await asyncio.sleep(0.02)
        assert service.host.studio_url.startswith("http://127.0.0.1:")
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)

    asyncio.run(exercise())


# --- N7: status and preview --------------------------------------------------------


def test_the_status_is_readable_by_the_tenant_and_not_by_another(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await service.dispatch("POST", "/api/applications", {"definition": definition(slack())}, principal=principal())
        code, body = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(BEA))
        assert code == 200 and body["owner"] == ANA and body["configured"] is True
        code, _ = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(BEA, "other"))
        assert code == 403
        code, _ = await service.dispatch("GET", "/api/applications/missing/notifications", principal=principal())
        assert code == 404

    asyncio.run(exercise())


@pytest.mark.parametrize("block", [slack(), email(), webhook()], ids=["slack", "email", "webhook"])
@pytest.mark.parametrize("reason", ["review", "pause"])
def test_the_preview_is_the_message_the_host_would_send(tmp_path, block, reason):
    from conclave.application.notices import PREVIEW_RUN, waiting_message

    async def exercise():
        service = api(tmp_path, connections=connections())
        value = definition(block)
        code, body = await service.dispatch("POST", "/api/notifications/preview",
                                            {"definition": value, "application_id": "digest", "reason": reason}, principal=principal())
        assert code == 200, body
        event = ({"status": "waiting_for_human", "stage": "review"} if reason == "review"
                 else {"status": "paused", "next_stage": "write"})
        message = waiting_message(value, application_id="digest", run_id=PREVIEW_RUN,
                                  event={"type": "ApplicationFinished", "sequence": 1, "timestamp": body.get("waiting_since", ""), **event},
                                  studio_url=STUDIO)
        assert (body["via"], body["target"], body["link"]) == (message.via, message.target, message.link)
        if block["via"] == "slack":
            assert body["text"] == message.arguments["text"]
        elif block["via"] == "email":
            assert body["subject"] == ("Support digest is waiting for you" if reason == "review" else "Support digest is paused")
            assert "Support digest" in body["text"] and message.link in body["text"]
        else:
            assert body["payload"] | {"waiting_since": None} == message.arguments["payload"] | {"waiting_since": None}

    asyncio.run(exercise())


@pytest.mark.parametrize("value, status, error", [
    ({"definition": definition(slack(channel="no spaces allowed"))}, 400, "Name the Slack channel"),
    ({"definition": definition(None)}, 400, "Say where to send the message when a run waits"),
    ({"definition": definition(slack()), "reason": "later"}, 400, "review or pause"),
    ({"definition": definition(slack()), "extra": 1}, 400, "supported request fields")])
def test_the_preview_refuses_what_it_cannot_build(tmp_path, value, status, error):
    async def exercise():
        service = api(tmp_path, connections=connections())
        code, body = await service.dispatch("POST", "/api/notifications/preview", value, principal=principal())
        assert code == status and error in body["error"]

    asyncio.run(exercise())


def test_the_preview_needs_a_studio_address(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections(), studio_url=None)
        code, body = await service.dispatch("POST", "/api/notifications/preview", {"definition": definition(slack())}, principal=principal())
        assert code == 400 and "Studio address" in body["error"]

    asyncio.run(exercise())
