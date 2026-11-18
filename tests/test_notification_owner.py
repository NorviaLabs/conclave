"""N4 — whose account sends the message, and what a save may change.

The message quotes names anyone in the tenant can edit, so it is always sent
from the account of whoever last saved a change to what runs, never from
someone else's.
"""

import asyncio

import pytest

from notification_support import ANA, BEA, FakeConnections, api, definition, email, slack
from conftest import share

ana_principal = None


def principal(name):
    from conclave.application.principal import Principal
    return Principal(name)


def connections():
    return (FakeConnections()
            .add("team_slack", owner=ANA, integration="slack")
            .add("ana_gmail", owner=ANA, integration="gmail")
            .add("bea_slack", owner=BEA, integration="slack"))


async def save(service, value, by, saved=None):
    if saved is None:
        code, body = await service.dispatch("POST", "/api/applications", {"definition": value}, principal=principal(by))
        if code == 201:
            # Ana and Bea work on the same applications.
            share(service, body["id"], *({ANA, BEA} - {by}))
        return code, body
    return await service.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": value, "revision": saved["revision"]},
                                  principal=principal(by))


async def status(service, saved, by=ANA):
    code, body = await service.dispatch("GET", f"/api/applications/{saved['id']}/notifications", principal=principal(by))
    assert code == 200, body
    return body


def run(coroutine):
    return asyncio.run(coroutine)


def test_the_first_person_to_save_it_owns_it(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        code, saved = await save(service, definition(slack()), ANA)
        assert code == 201, saved
        service.host.connections.grant("team_slack", saved["id"])
        body = await status(service, saved)
        assert (body["configured"], body["owner"], body["via"], body["connection"], body["target"]) == (True, ANA, "slack", "team_slack", "#reviews")
        assert body["owner_since"] and body["usable"] is True and not body["reason"]

    run(exercise())


def test_someone_elses_change_makes_it_theirs_and_says_why_it_cannot_send(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await save(service, definition(slack()), ANA)
        service.host.connections.grant("team_slack", saved["id"])
        changed = definition(slack())
        changed["description"] = "Bea's better description"
        code, saved = await save(service, changed, BEA, saved)
        assert code == 200, saved
        body = await status(service, saved, by=BEA)
        assert body["owner"] == BEA and body["usable"] is False
        assert body["reason"] == f"team_slack is not {BEA}'s connection"

    run(exercise())


def test_moving_cards_does_not_change_the_owner(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await save(service, definition(slack()), ANA)
        moved = definition(slack())
        moved["canvas"] = {"positions": {"write": {"x": 40, "y": 80}}}
        code, saved = await save(service, moved, BEA, saved)
        assert code == 200
        assert (await status(service, saved))["owner"] == ANA

    run(exercise())


@pytest.mark.parametrize("block, message", [
    (slack(connection="team_slack", channel="#bea"), "Choose one of your own Slack connections"),
    (slack(connection="ana_gmail"), "Choose one of your own Slack connections"),
    (email(connection="team_slack"), "Choose one of your own Email connections"),
    (slack(connection="nobody_has_this"), "Choose one of your own Slack connections"),
], ids=["someone else's connection", "another service", "another service for email", "missing"])
def test_choosing_a_connection_that_is_not_yours_or_not_that_service_is_refused(tmp_path, block, message):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await save(service, definition(slack(connection="bea_slack")), BEA)
        code, body = await save(service, definition(block), BEA, saved)
        assert code == 400 and body["error"] == message

    run(exercise())


def test_a_save_that_leaves_the_setting_alone_is_never_refused_for_it(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await save(service, definition(slack()), ANA)
        renamed = definition(slack(), name="Support digest (billing)")
        code, body = await save(service, renamed, BEA, saved)
        assert code == 200, body

    run(exercise())


def test_turning_it_off_clears_the_owner(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=connections())
        _, saved = await save(service, definition(slack()), ANA)
        _, saved = await save(service, definition(None), ANA, saved)
        body = await status(service, saved)
        assert (body["configured"], body["owner"], body["usable"]) == (False, None, False)
        _, saved = await save(service, definition(slack()), ANA, saved)
        assert (await status(service, saved))["owner"] == ANA

    run(exercise())


def test_each_reason_a_message_cannot_be_sent(tmp_path):
    async def exercise():
        store = connections()
        service = api(tmp_path, connections=store)
        _, saved = await save(service, definition(slack()), ANA)
        assert (await status(service, saved))["reason"] == "team_slack is not granted send_message for this application"
        store.grant("team_slack", saved["id"])
        assert (await status(service, saved))["usable"] is True
        store.records["team_slack"]["integration"] = "gmail"
        assert (await status(service, saved))["reason"] == "team_slack is not a Slack connection"
        store.records["team_slack"]["integration"] = "slack"
        service.host.studio_url = None
        assert (await status(service, saved))["reason"] == "This host has no Studio address (CONCLAVE_STUDIO_URL)"

    run(exercise())


def test_a_host_without_connections_saves_the_setting_and_says_it_cannot_send(tmp_path):
    async def exercise():
        service = api(tmp_path, connections=None)
        code, saved = await save(service, definition(slack()), ANA)
        assert code == 201, saved
        body = await status(service, saved)
        assert body["configured"] is True and body["usable"] is False and body["reason"] == "This host has no connections"

    run(exercise())
