"""T2 — was it sent? Every boundary says whether a failed write left the host.

A clear failure (nothing left, or the service refused it) is reported to the
agent as today. Anything else is an unknown outcome that must be checked.
"""

import asyncio
import subprocess

import pytest

from conclave.integrations.transport import IntegrationError, Transport


class Response:
    status = 200
    data = b'{"ok":true}'
    headers = {}

    def read(self, limit):
        return self.data[:limit]

    def getheader(self, name):
        return self.headers.get(name)


class Connection:
    fail_before_send = None
    fail_after_send = None

    def __init__(self, host, address, timeout):
        pass

    def request(self, method, path, body=None, headers=None):
        if self.fail_before_send:
            raise self.fail_before_send

    def getresponse(self):
        if self.fail_after_send:
            raise self.fail_after_send
        return Response()

    def close(self):
        pass


@pytest.fixture
def pinned(monkeypatch):
    from conclave.integrations import transport as module

    monkeypatch.setattr(module, "public_addresses", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(module, "PinnedHTTPS", Connection)
    monkeypatch.setattr(Connection, "fail_before_send", None)
    monkeypatch.setattr(Connection, "fail_after_send", None)
    monkeypatch.setattr(Response, "status", 200)
    return Transport({"example.com"})


def caught(coroutine):
    with pytest.raises(IntegrationError) as error:
        asyncio.run(coroutine)
    return error.value


def test_a_refused_endpoint_never_left_the_host():
    from conclave.actions import classify

    error = caught(Transport({"example.com"}).request("POST", "https://other.example/hook"))
    assert error.sent is False and classify(error) == "failed"


def test_a_host_that_does_not_resolve_never_left_the_host(monkeypatch):
    from conclave.actions import classify
    from conclave.integrations import transport as module

    def unresolved(host):
        raise IntegrationError("Host name could not be resolved")

    monkeypatch.setattr(module, "public_addresses", unresolved)
    error = caught(Transport({"example.com"}).request("POST", "https://example.com/hook"))
    assert error.sent is False and classify(error) == "failed"


def test_a_connection_that_fails_before_sending_never_left_the_host(pinned, monkeypatch):
    from conclave.actions import classify

    monkeypatch.setattr(Connection, "fail_before_send", ConnectionRefusedError("refused"))
    error = caught(pinned.request("POST", "https://example.com/hook", body={"a": 1}))
    assert error.sent is False and classify(error) == "failed"
    assert str(error) == "Provider request could not be completed", "the public text is unchanged"


def test_a_connection_that_drops_after_sending_is_unknown(pinned, monkeypatch):
    from conclave.actions import classify

    monkeypatch.setattr(Connection, "fail_after_send", ConnectionResetError("reset"))
    error = caught(pinned.request("POST", "https://example.com/hook", body={"a": 1}))
    assert error.sent is True and classify(error) == "uncertain"


@pytest.mark.parametrize("status", [400, 404, 409, 422, 429])
def test_a_service_that_refuses_the_request_is_a_clear_failure(pinned, monkeypatch, status):
    from conclave.actions import classify

    monkeypatch.setattr(Response, "status", status)
    error = caught(pinned.request("POST", "https://example.com/hook", body={}))
    assert error.rejected is True and error.status == status and classify(error) == "failed"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_after_sending_is_unknown(pinned, monkeypatch, status):
    from conclave.actions import classify

    monkeypatch.setattr(Response, "status", status)
    error = caught(pinned.request("POST", "https://example.com/hook", body={}))
    assert error.sent is True and error.rejected is False and error.status == status
    assert classify(error) == "uncertain"


def test_a_timeout_is_unknown_because_the_worker_may_still_send(pinned, monkeypatch):
    from conclave.actions import classify
    import time

    def slow(self):
        time.sleep(0.3)
        return Response()

    monkeypatch.setattr(Connection, "getresponse", slow)
    pinned.timeout = -1.9  # wait_for(timeout + 2) gives the call 0.1s
    error = caught(pinned.request("POST", "https://example.com/hook", body={}))
    assert str(error) == "Provider request timed out"
    assert error.sent is True and classify(error) == "uncertain"


def test_integration_error_keeps_its_old_shape():
    error = IntegrationError("Connection operation is not granted to this application")
    assert str(error) == "Connection operation is not granted to this application"
    assert error.sent is None and error.rejected is False and error.status is None


def test_connection_refusals_before_the_call_never_left_the_host(tmp_path):
    from cryptography.fernet import Fernet
    from conclave.actions import classify
    from conclave.integrations.connections import ConnectionStore

    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), hosts=["hooks.example.com"])
    store.create({"id": "hook", "integration": "webhooks", "name": "Deploy hook", "applications": ["app"],
                  "operations": ["send"], "config": {"base_url": "https://hooks.example.com/deploy"},
                  "credentials": {"token": "secret-token"}}, "local", "owner")

    async def refused(operation, arguments, application="app"):
        with pytest.raises(IntegrationError) as error:
            await store.invoke("hook", operation, arguments, "local", "owner", application)
        return error.value

    async def exercise():
        ungranted = await refused("send", {"payload": {}}, application="other")
        invalid = await refused("send", {"unexpected": 1})
        for error in (ungranted, invalid):
            assert error.sent is False and classify(error) == "failed"

    asyncio.run(exercise())


def test_an_unexpected_failure_inside_a_connection_call_is_unknown(tmp_path):
    from cryptography.fernet import Fernet
    from conclave.actions import classify
    from conclave.integrations.connections import ConnectionStore

    class Broken:
        def __init__(self, hosts):
            pass

        async def request(self, *args, **kwargs):
            raise KeyError("unexpected shape")

    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), hosts=["hooks.example.com"],
                            transport_factory=Broken)
    store.create({"id": "hook", "integration": "webhooks", "name": "Deploy hook", "applications": ["app"],
                  "operations": ["send"], "config": {"base_url": "https://hooks.example.com/deploy"},
                  "credentials": {"token": "secret-token"}}, "local", "owner")
    with pytest.raises(IntegrationError, match="Integration operation failed") as error:
        asyncio.run(store.invoke("hook", "send", {"payload": {}}, "local", "owner", "app"))
    assert classify(error.value) == "uncertain"


def test_a_host_adapter_refuses_an_unapproved_operation_before_sending():
    from conclave.actions import classify
    from conclave.integrations.host import HostAdapters

    adapters = HostAdapters({})
    adapters.instances["crm"] = type("Service", (), {"id": "mcp", "operations": ()})()
    adapters.clients["crm"] = object()
    with pytest.raises(IntegrationError) as error:
        asyncio.run(adapters.invoke("crm", "delete_everything", {}))
    assert error.value.sent is False and classify(error.value) == "failed"


def test_a_local_write_refused_for_its_arguments_did_nothing(tmp_path):
    from conclave.actions import classify
    from conclave.tools.changes import Changes

    changes = Changes(tmp_path)
    for coroutine in (changes.write_file("../outside.txt", "x"), changes.write_file(".git/config", "x"),
                      changes.edit_file("missing.txt", "a", "b"), changes.push_branch("-x"),
                      changes.git_commit("   ")):
        with pytest.raises(ValueError) as error:
            asyncio.run(coroutine)
        assert classify(error.value) == "failed", error.value


def test_a_local_git_command_that_fails_on_its_own_did_nothing(monkeypatch, tmp_path):
    from conclave.actions import classify
    from conclave.tools.changes import Changes

    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "nothing to commit"))
    with pytest.raises(RuntimeError, match="nothing to commit") as error:
        asyncio.run(Changes(tmp_path).git_commit("Fix"))
    assert classify(error.value) == "failed", "a commit is atomic: a refused commit made none"


def test_a_remote_command_that_fails_is_left_to_its_check(monkeypatch, tmp_path):
    from conclave.actions import classify
    from conclave.tools.changes import Changes

    def push_fails(command, **kwargs):
        if command[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(command, 128, "", "fatal: the remote end hung up unexpectedly")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", push_fails)
    with pytest.raises(RuntimeError, match="hung up") as error:
        asyncio.run(Changes(tmp_path).push_branch("fix/issue-7"))
    assert classify(error.value) == "uncertain", "the remote may have taken the push before hanging up"


def test_a_timed_out_command_is_unknown(monkeypatch, tmp_path):
    from conclave.actions import classify
    from conclave.tools.changes import Changes

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        asyncio.run(Changes(tmp_path).merge_pull_request(12))
    assert classify(error.value) == "uncertain"
