"""An application started by an event: its own address and secret, one run per event.

An application whose start method is "event" can be given an incoming address
and a signing secret (shown once). A signed event whose data fits the
application's request starts one run, acting as whoever turned events on; the
same event again starts nothing and answers with the first run.
"""

import asyncio
import copy
import json
import sqlite3
import time
from contextlib import closing

import pytest

from conclave.application.definition import validate_definition
from conclave.application.hooks import signature
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository, validate_run_request
from conftest import ScriptedProvider, share
from test_api import make_api
from test_studio_runs import request

ANA = Principal("ana@acme.com")


def event_application(**event):
    value = request()
    definition = value["definition"]
    definition["agents"] = definition["agents"][:1]
    definition["workflow"] = definition["workflow"][:1]
    definition["start_mode"] = "event"
    definition["event"] = {"task": "Triage the issue in this event", **event}
    return definition


def signed(secret, payload, *, timestamp=None, event_id=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    headers = {"content-type": "application/json", "x-conclave-timestamp": timestamp,
               "x-conclave-signature": signature(secret, timestamp, body)}
    if event_id is not None:
        headers["x-conclave-event-id"] = event_id
    return headers, body


async def turned_on(api, definition=None, principal=ANA):
    status, saved = await api.dispatch("POST", "/api/applications", {"definition": definition or event_application()}, principal=principal)
    assert status == 201, saved
    status, endpoint = await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "create"}, principal=principal)
    assert status == 200, endpoint
    return saved, endpoint


def endpoint_of(endpoint):
    return endpoint["address"].rsplit("/", 1)[1]


def provider():
    return ScriptedProvider(default={"type": "final", "content": "Done", "text": "Triaged"})


# --- Definitions --------------------------------------------------------------------


@pytest.mark.parametrize("change, message", [
    ({"event": None}, "event"),                                  # event mode needs its block
    ({"event": {"task": " "}}, "event"),
    ({"event": {"task": "x" * 20001}}, "event"),
    ({"event": {"task": "Go", "allow_task_override": "yes"}}, "event"),
    ({"event": {"task": "Go", "retries": 3}}, "event"),
    ({"start_mode": "manual"}, "event"),                         # a block without event mode
])
def test_event_settings_are_validated(change, message):
    definition = event_application()
    for key, value in change.items():
        if value is None:
            definition.pop(key)
        else:
            definition[key] = value
    with pytest.raises(ValueError, match=message):
        validate_definition(definition)


def test_event_trigger_is_accepted_on_a_run_request():
    value = request()
    value["trigger"] = {"kind": "event", "event_id": "gh-1432", "received_at": "2026-10-20T14:30:00+00:00"}
    assert validate_run_request(value)["trigger"]["event_id"] == "gh-1432"
    for trigger in ({"kind": "event", "event_id": "", "received_at": "2026-10-20T14:30:00+00:00"},
                    {"kind": "event", "event_id": "x" * 201, "received_at": "2026-10-20T14:30:00+00:00"},
                    {"kind": "event", "event_id": "a", "received_at": "yesterday"},
                    {"kind": "event", "event_id": "a"}):
        value["trigger"] = trigger
        with pytest.raises(ValueError, match="trigger"):
            validate_run_request(value)


# --- The address and its secret --------------------------------------------------------------


def test_event_address_and_secret_are_shown_once(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        assert endpoint["address"].startswith("/api/events/ev_") and endpoint["secret"].startswith("ces_")
        assert len(endpoint["secret"]) >= 40
        status, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert status == 200 and shown["enabled"] is True and shown["owner"] == "ana@acme.com"
        assert "secret" not in shown and endpoint["secret"] not in json.dumps(shown)
        assert (await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "create"}, principal=ANA))[0] == 400
        await api.host.close()
    asyncio.run(exercise())


def test_event_secret_rotation_replaces_the_old_one_at_once(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        _, rotated = await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "rotate"}, principal=ANA)
        assert rotated["address"] == endpoint["address"] and rotated["secret"] != endpoint["secret"]
        headers, body = signed(endpoint["secret"], {"sources": "Old secret"})
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 401
        headers, body = signed(rotated["secret"], {"sources": "New secret"})
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 201
        await api.host.close()
    asyncio.run(exercise())


def test_event_address_needs_an_application_saved_in_event_mode(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        manual = request()["definition"]
        _, saved = await api.dispatch("POST", "/api/applications", {"definition": manual}, principal=ANA)
        status, body = await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "create"}, principal=ANA)
        assert status == 400 and "event" in body["error"]
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA))[1]["enabled"] is False
        await api.host.close()
    asyncio.run(exercise())


def test_event_secret_is_sealed_when_the_host_has_a_key(tmp_path):
    from cryptography.fernet import Fernet

    async def exercise():
        api = make_api(tmp_path, provider())
        api.host.repository = RunRepository(tmp_path / "sealed.sqlite", key=Fernet.generate_key())
        _, endpoint = await turned_on(api)
        with closing(sqlite3.connect(tmp_path / "sealed.sqlite")) as connection:
            stored = json.dumps([list(map(str, row)) for row in connection.execute("SELECT * FROM studio_event_endpoints")])
        assert endpoint["secret"] not in stored
        await api.host.close()
    asyncio.run(exercise())


# --- Receiving one ---------------------------------------------------------------------------------


def test_event_starts_one_run_with_its_data_as_input(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"event_id": "gh-1432", "sources": "Login fails on Safari", "count": 3,
                                                    "ignored": "not a declared field", "task": "Do something else"})
        status, answer = await api.receive_event(endpoint_of(endpoint), headers, body)
        assert status == 201 and answer["event_id"] == "gh-1432"
        run = api.host.repository.get(answer["run_id"])
        assert run["request"]["task"] == "Triage the issue in this event", "an event does not replace the task unless allowed"
        assert run["request"]["inputs"] == {"sources": "Login fails on Safari", "count": 3}
        assert run["request"]["trigger"]["kind"] == "event" and run["request"]["trigger"]["event_id"] == "gh-1432"
        assert run["started_by"] == "ana@acme.com"
        await api.host.until_idle(answer["run_id"])
        assert api.host.repository.get(answer["run_id"])["status"] == "completed"
        _, listed = await api.dispatch("GET", f"/api/runs?application_id={saved['id']}", principal=ANA)
        assert listed["runs"][0]["trigger"]["kind"] == "event"
        await api.host.close()
    asyncio.run(exercise())


def test_event_may_replace_the_task_only_when_the_application_allows_it(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api, event_application(allow_task_override=True))
        headers, body = signed(endpoint["secret"], {"sources": "x", "task": "Summarize this release"})
        _, answer = await api.receive_event(endpoint_of(endpoint), headers, body)
        assert api.host.repository.get(answer["run_id"])["request"]["task"] == "Summarize this release"
        await api.host.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("tamper, status", [
    (lambda headers, body: (headers | {"x-conclave-signature": "0" * 64}, body), 401),
    (lambda headers, body: (headers, body + b" "), 401),                              # the body changed after signing
    (lambda headers, body: ({key: value for key, value in headers.items() if key != "x-conclave-signature"}, body), 401),
])
def test_event_with_a_bad_signature_starts_nothing_and_is_not_listed(tmp_path, tamper, status):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        headers, body = tamper(*signed(endpoint["secret"], {"sources": "x"}))
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == status
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert shown["recent"] == [] and api.host.repository.list(saved["id"]) == []
        await api.host.close()
    asyncio.run(exercise())


def test_event_outside_the_signature_window_or_too_large_is_refused(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"sources": "x"}, timestamp=int(time.time()) - 3600)
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 401
        headers, body = signed(endpoint["secret"], {"sources": "x" * 100_001})
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 413
        await api.host.close()
    asyncio.run(exercise())


def test_event_to_an_unknown_or_switched_off_address_is_not_found(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"sources": "x"})
        assert (await api.receive_event("ev_unknown", headers, body))[0] == 404
        manual = copy.deepcopy(saved["definition"])
        manual["start_mode"] = "manual"
        manual.pop("event")
        await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": manual, "revision": saved["revision"]}, principal=ANA)
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 404
        # Set back, the same address and secret work again.
        evented = copy.deepcopy(saved["definition"])
        await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": evented, "revision": saved["revision"] + 1}, principal=ANA)
        assert (await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "y"})))[0] == 201
        await api.host.close()
    asyncio.run(exercise())


def test_event_missing_a_required_field_is_refused_with_the_reason(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"event_id": "gh-9", "count": 2})
        status, answer = await api.receive_event(endpoint_of(endpoint), headers, body)
        assert status == 400 and answer["error"] == "Missing required field “sources”"
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert shown["recent"][0]["outcome"] == "refused" and shown["recent"][0]["reason"] == "Missing required field “sources”"
        # Refused, it does not hold its identifier: sent again, corrected, it starts a run.
        headers, body = signed(endpoint["secret"], {"event_id": "gh-9", "sources": "now present"})
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 201
        await api.host.close()
    asyncio.run(exercise())


# --- The same event again ---------------------------------------------------------------------------------


@pytest.mark.parametrize("identify", ["header", "body", "content"])
def test_event_dedup_starts_one_run_for_the_same_event(tmp_path, identify):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        payload = {"sources": "Same"} | ({"event_id": "gh-7"} if identify == "body" else {})
        first = signed(endpoint["secret"], payload, event_id="gh-7" if identify == "header" else None)
        status, started = await api.receive_event(endpoint_of(endpoint), *first)
        assert status == 201
        again = signed(endpoint["secret"], payload, event_id="gh-7" if identify == "header" else None)
        status, repeated = await api.receive_event(endpoint_of(endpoint), *again)
        assert status == 200 and repeated == {"duplicate": True, "run_id": started["run_id"], "event_id": started["event_id"]}
        assert len(api.host.repository.list(saved["id"])) == 1
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert [item["outcome"] for item in shown["recent"]] == ["duplicate", "started"]
        if identify == "content":
            assert started["event_id"].startswith("sha256:")
        await api.host.close()
    asyncio.run(exercise())


def test_event_dedup_survives_a_restart(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"event_id": "gh-8", "sources": "x"})
        _, started = await api.receive_event(endpoint_of(endpoint), headers, body)
        await api.host.close()
        again = make_api(tmp_path, provider())
        headers, body = signed(endpoint["secret"], {"event_id": "gh-8", "sources": "x"})
        status, repeated = await again.receive_event(endpoint_of(endpoint), headers, body)
        assert status == 200 and repeated["run_id"] == started["run_id"]
        await again.host.close()
    asyncio.run(exercise())


def test_event_dedup_claims_expire_after_seven_days(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        _, first = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"event_id": "gh-1", "sources": "x"}))
        with api.host.repository._connect() as connection:
            connection.execute("UPDATE studio_event_claims SET received_at = '2000-01-01T00:00:00+00:00'")
        status, second = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"event_id": "gh-1", "sources": "x"}))
        assert status == 201 and second["run_id"] != first["run_id"]
        await api.host.close()
    asyncio.run(exercise())


# --- Limits, and who the run acts as ---------------------------------------------------------------------


def test_event_runs_beyond_the_active_limit_are_refused(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        api.event_run_limit = 2
        with api.host.repository._connect() as connection:   # two event runs still going
            for n in range(2):
                value = validate_run_request({**request(), "application_id": saved["id"], "revision": saved["revision"],
                                              "definition": saved["definition"], "inputs": {"sources": "x"},
                                              "trigger": {"kind": "event", "event_id": f"busy-{n}", "received_at": "2026-10-20T14:30:00+00:00"}})
                run_id = api.host.repository._insert_run(connection, value)
                connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
        status, answer = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"event_id": "gh-2", "sources": "x"}))
        assert status == 429 and answer["error"] == "2 runs started by events are still going"
        assert (await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"event_id": "gh-2", "sources": "x"})))[0] == 429, \
            "refused, it held no claim"
        await api.host.close()
    asyncio.run(exercise())


def test_event_run_refuses_writes_nobody_authorized_up_front(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        calls = []
        schedule = api.host._schedule

        async def recording(run, **options):
            calls.append(options)
            return await schedule(run, **options)
        api.host._schedule = recording
        await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "x"}))
        assert calls == [{"approvals": "refuse"}]
        await api.host.close()
    asyncio.run(exercise())


def test_event_for_an_owner_the_deployment_no_longer_admits_is_refused(tmp_path):
    from types import SimpleNamespace

    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        api.host.schedules = SimpleNamespace(admits=lambda owner: False)
        status, answer = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "x"}))
        assert status == 403 and "can no longer" in answer["error"]
        assert api.host.repository.list(saved["id"]) == []
        api.host.schedules = None
        await api.host.close()
    asyncio.run(exercise())


def test_event_log_keeps_the_latest_hundred(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        for n in range(105):
            await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"event_id": f"e-{n}", "count": 1}))
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert len(shown["recent"]) == 100 and shown["recent"][0]["event_id"] == "e-104"
        await api.host.close()
    asyncio.run(exercise())


# --- Transports -----------------------------------------------------------------------------------------


def test_event_reaches_the_local_server_signed_from_any_host(tmp_path):
    from conclave.api import start_server

    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        server = await start_server(api, 0, token="test-token")
        port = server.sockets[0].getsockname()[1]

        async def post(path, headers, body, host="events.example.com"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            lines = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
            writer.write(f"POST {path} HTTP/1.1\r\nHost: {host}\r\n{lines}Content-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            payload = json.loads(await reader.readexactly(length))
            writer.close()
            return int(head.split(b" ")[1]), payload

        async with server:
            status, answer = await post(endpoint["address"], *signed(endpoint["secret"], {"sources": "x"}))
            assert status == 201 and answer["run_id"]
            assert (await post(endpoint["address"], {"content-type": "application/json"}, b'{"sources":"x"}'))[0] == 401
            assert (await post("/api/health", {"content-type": "application/json"}, b"{}"))[0] == 403, "everything else still needs the local address"
        await api.host.close()
    asyncio.run(exercise())


def test_event_reaches_the_hosted_transport_without_a_bearer_token(tmp_path):
    from conclave.asgi import application
    from test_asgi import call, make_services

    async def exercise():
        services, built = make_services(tmp_path)
        api = services.for_principal(Principal("local"))
        _, endpoint = await turned_on(api)
        app = application(apis=services, token="secret-token")
        headers, body = signed(endpoint["secret"], {"sources": "x"})
        status, _, answer = await call(app, "POST", endpoint["address"], chunks=[body], headers=list(headers.items()))
        assert status == 201 and json.loads(answer)["run_id"]
        status, _, _ = await call(app, "POST", endpoint["address"], chunks=[b'{"sources":"x"}'], headers=[("content-type", "application/json")])
        assert status == 401
        await api.host.close()
    asyncio.run(exercise())


def test_event_triggers_are_a_host_feature(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        _, runtime = await api.dispatch("GET", "/api/runtime")
        assert runtime["features"]["event_triggers"] is True
        await api.host.close()
    asyncio.run(exercise())


# --- Security audit (Z1–Z4) ---------------------------------------------------------------


def test_event_replayed_with_a_new_identifier_is_a_dedup_duplicate(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"sources": "captured"}, event_id="gh-1")
        status, first = await api.receive_event(endpoint_of(endpoint), headers, body)
        assert status == 201
        for replayed in ({**headers, "x-conclave-event-id": "gh-2"}, {key: value for key, value in headers.items() if key != "x-conclave-event-id"}):
            status, answer = await api.receive_event(endpoint_of(endpoint), replayed, body)
            assert status == 200 and answer["duplicate"] is True and answer["run_id"] == first["run_id"]
        assert len(api.host.repository.list(saved["id"])) == 1, "one signed request, one run"
        # A new request for the same content, signed afresh, is the sender's own
        # business: its header says which event it is.
        status, _ = await api.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "captured"}, event_id="gh-3",
                                                                          timestamp=int(time.time()) + 1))
        assert status == 201
        await api.host.close()
    asyncio.run(exercise())


BOB = Principal("bob@acme.com")


def test_event_secret_rotated_by_someone_else_makes_them_the_owner(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        share(api, saved["id"], BOB.name, role="owner")  # turning events on and rotating the secret is an owner's
        _, rotated = await api.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "rotate"}, principal=BOB)
        assert rotated["owner"] == "bob@acme.com"
        status, answer = await api.receive_event(endpoint_of(endpoint), *signed(rotated["secret"], {"sources": "bob's event"}))
        assert api.host.repository.get(answer["run_id"])["started_by"] == "bob@acme.com", "whoever holds the secret is who runs act as"
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert shown["owner"] == "bob@acme.com"
        assert any(item["outcome"] == "owner" and item["reason"] == "Owner is now bob@acme.com (rotated the secret)" for item in shown["recent"])
        await api.host.close()
    asyncio.run(exercise())


def test_event_application_edited_by_someone_else_makes_them_the_trigger_owner(tmp_path):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        share(api, saved["id"], BOB.name)
        edited = copy.deepcopy(saved["definition"])
        edited["event"]["task"] = "Do what bob says"
        _, after = await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": edited, "revision": saved["revision"]}, principal=BOB)
        _, shown = await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA)
        assert shown["owner"] == "bob@acme.com", "editing what event runs do never borrows someone else's accounts"
        assert any(item["reason"] == "Owner is now bob@acme.com (changed what runs)" for item in shown["recent"])
        # Moving cards is not a change to what runs.
        moved = {**copy.deepcopy(edited), "canvas": {"positions": {"draft": {"x": 1, "y": 1}}}}
        await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": moved, "revision": after["revision"]}, principal=ANA)
        assert (await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA))[1]["owner"] == "bob@acme.com"
        # The owner saving their own change keeps it theirs, and says nothing new.
        again = copy.deepcopy(edited)
        again["event"]["task"] = "Do what bob says, carefully"
        await api.dispatch("PUT", f"/api/applications/{saved['id']}", {"definition": again, "revision": after["revision"]}, principal=BOB)
        owners = [item for item in (await api.dispatch("GET", f"/api/applications/{saved['id']}/event", principal=ANA))[1]["recent"] if item["outcome"] == "owner"]
        assert len(owners) == 1
        await api.host.close()
    asyncio.run(exercise())


def test_event_larger_than_allowed_is_refused_before_it_is_read(tmp_path):
    from conclave.api import start_server

    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        server = await start_server(api, 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            # Declares 200 KB and sends none of it: the answer must not wait for the body.
            writer.write(f"POST {endpoint['address']} HTTP/1.1\r\nHost: events.example.com\r\ncontent-type: application/json\r\n"
                         f"Content-Length: 200000\r\n\r\n".encode())
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            assert int(head.split(b" ")[1]) == 413
            writer.close()
        await api.host.close()
    asyncio.run(exercise())


def test_event_flood_of_unsigned_requests_reads_the_address_once(tmp_path, monkeypatch):
    async def exercise():
        api = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(api)
        counted = {"resolve": 0, "load": 0}
        real_resolve, real_get = api.host.repository.events.resolve, api.workspace.get
        monkeypatch.setattr(api.host.repository.events, "resolve", lambda *a: (counted.__setitem__("resolve", counted["resolve"] + 1), real_resolve(*a))[1])
        monkeypatch.setattr(api.workspace, "get", lambda *a: (counted.__setitem__("load", counted["load"] + 1), real_get(*a))[1])
        for n in range(50):
            headers, body = signed("ces_" + "x" * 43, {"sources": str(n)})   # someone who does not hold the secret
            assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 401
        assert counted == {"resolve": 1, "load": 1}, counted
        await api.host.close()
    asyncio.run(exercise())


def test_event_old_secret_is_refused_at_once_by_every_worker_after_rotation(tmp_path):
    async def exercise():
        first = make_api(tmp_path, provider())
        saved, endpoint = await turned_on(first)
        # This worker has the address cached.
        assert (await first.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "a"})))[0] == 201
        second = make_api(tmp_path, provider())   # another worker, sharing the stores
        _, rotated = await second.dispatch("POST", f"/api/applications/{saved['id']}/event", {"action": "rotate"}, principal=ANA)
        # The first worker's cache still holds the old secret; a valid signature is checked again before anything runs.
        assert (await first.receive_event(endpoint_of(endpoint), *signed(endpoint["secret"], {"sources": "b"})))[0] == 401
        assert (await second.receive_event(endpoint_of(endpoint), *signed(rotated["secret"], {"sources": "c"})))[0] == 201
        await first.host.close()
        await second.host.close()
    asyncio.run(exercise())


# --- Performance audit (Q-E1, Q-E2) ----------------------------------------------------------------


def test_event_active_run_count_reads_only_runs_still_going(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    with repository._connect() as connection:
        plan = " ".join(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + repository.ACTIVE_EVENT_RUNS, ("app",)))
        assert "studio_runs_status" in plan, plan
        assert repository.active_event_runs(connection, "app") == 0


def test_event_run_status_index_is_added_to_an_existing_store(tmp_path):
    path = tmp_path / "runs.sqlite"
    RunRepository(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP INDEX studio_runs_status")
    RunRepository(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'studio_runs_status'").fetchone()


def test_event_duplicate_does_not_prune_claims(tmp_path, monkeypatch):
    from contextlib import contextmanager

    async def exercise():
        api = make_api(tmp_path, provider())
        _, endpoint = await turned_on(api)
        headers, body = signed(endpoint["secret"], {"event_id": "gh-1", "sources": "x"})
        await api.receive_event(endpoint_of(endpoint), headers, body)
        statements, real = [], api.host.repository._connect

        @contextmanager
        def traced():
            with real() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        monkeypatch.setattr(api.host.repository, "_connect", traced)
        assert (await api.receive_event(endpoint_of(endpoint), headers, body))[0] == 200
        assert not any(statement.startswith("DELETE FROM studio_event_claims") for statement in statements), statements
        await api.host.close()
    asyncio.run(exercise())
