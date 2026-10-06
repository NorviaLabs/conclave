"""Being told about a run instead of asking, and who may name a source.

A hosted transport can push a run's events, so it says so in `features.events`;
a caller that asked for the stream gets one. A hosted tenant also does not get
to name a path on the server.
"""

import asyncio
import json

import pytest

from conclave.api import configured_api
from conclave.application.principal import Principal
from conclave.application.runs import RunRepository
from conclave.asgi import application, streamed_run
from test_asgi import call, make_services
from test_studio_runs import request


def run_record(tmp_path, **overrides):
    value = request()
    value.update(overrides)
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value)

    return repository, run_id


def test_a_watcher_is_told_when_a_run_changes(tmp_path):
    repository, run_id = run_record(tmp_path)

    async def exercise():
        watching = repository.watch(run_id)
        first = await anext(watching)
        assert first["id"] == run_id and first["events"] == []

        repository._append(run_id, {"type": "StageStarted", "stage": "draft"})
        changed = await asyncio.wait_for(anext(watching), timeout=1)

        assert [item["event"]["type"] for item in changed["events"]] == ["StageStarted"]

        # A slow reader coalesces: a second change while it was busy is not lost,
        # and does not queue up behind the first.
        repository._append(run_id, {"type": "StageStarted", "stage": "review"})
        repository._append(run_id, {"type": "ApplicationFinished", "status": "completed"})
        latest = await asyncio.wait_for(anext(watching), timeout=1)

        assert latest["status"] == "completed"
        assert len(latest["events"]) == 3, "the record is read again, not replayed"

        await watching.aclose()
        assert repository.watchers == {}, "closing the watcher unsubscribed it"

    asyncio.run(exercise())


def test_a_watcher_is_told_when_a_review_is_recorded(tmp_path):
    repository, run_id = run_record(tmp_path)

    async def exercise():
        repository._append(run_id, {"type": "HumanInputRequested", "stage": "review", "agent": "reviewer",
                                    "request": {"stage": "review"}, "output": {"type": "text", "key": "brief"}})
        repository._append(run_id, {"type": "ApplicationFinished", "status": "waiting_for_human", "reason": "Waiting for review"})
        review = repository.get(run_id)["reviews"][0]
        watching = repository.watch(run_id)
        await anext(watching)

        repository.submit_review(run_id, review["id"], reviewer="ada", output={"text": "Reviewed"})
        changed = await asyncio.wait_for(anext(watching), timeout=1)

        assert changed["reviews"][0]["status"] == "submitted"

        repository.cancel_waiting(run_id)
        cancelled = await asyncio.wait_for(anext(watching), timeout=1)

        assert cancelled["status"] == "cancelled"
        await watching.aclose()

    asyncio.run(exercise())


def test_the_hosted_transport_says_it_can_push(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, _ = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        status, _, body = await call(app, "GET", "/api/runtime")

        assert status == 200
        assert json.loads(body)["features"]["events"] is True, "the hosted transport can push"

        await app.close()

    asyncio.run(exercise())


def test_the_loopback_transport_does_not_promise_push(tmp_path, monkeypatch):
    """The local adapter answers one bounded request per connection."""
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    api = configured_api(workspace=tmp_path, principal=Principal("ada", "local"))

    async def exercise():
        status, runtime = await api.dispatch("GET", "/api/runtime")

        assert status == 200 and runtime["features"]["events"] is False
        await api.host.close()

    asyncio.run(exercise())


def test_the_run_stream_reports_each_change(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, _ = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))
    api = services.for_principal(Principal("ada", "acme"))
    run_id = api.host.repository.create(request())
    seen = []

    async def exercise():
        async def receive():
            await asyncio.sleep(0)
            return {"type": "http.request", "body": b""}

        async def send(message):
            seen.append(message)

        scope = {"type": "http", "method": "GET", "path": f"/api/runs/{run_id}/stream",
                 "raw_path": f"/api/runs/{run_id}/stream".encode(), "headers": []}
        task = asyncio.ensure_future(app(scope, receive, send))
        while not seen:
            await asyncio.sleep(0)

        api.host.repository._append(run_id, {"type": "StageStarted", "stage": "draft"})
        while len([m for m in seen if m["type"] == "http.response.body"]) < 2:
            await asyncio.sleep(0)

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        await app.close()

    asyncio.run(exercise())

    start = seen[0]
    assert start["status"] == 200 and dict((k.decode(), v.decode()) for k, v in start["headers"])["content-type"].startswith("text/event-stream")
    bodies = [message["body"] for message in seen if message["type"] == "http.response.body"]
    records = [json.loads(body.decode().removeprefix("data: ")) for body in bodies]

    assert [record["id"] for record in records] == [run_id, run_id]
    assert records[0]["events"] == [] and len(records[1]["events"]) == 1
    assert all(message.get("more_body") for message in seen if message["type"] == "http.response.body")


def test_the_stream_refuses_a_run_it_does_not_have(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    services, _ = make_services(tmp_path)
    app = application(apis=services, fallback=Principal("ada", "acme"))

    async def exercise():
        status, _, body = await call(app, "GET", "/api/runs/nothing/stream")

        assert status == 404 and json.loads(body)["error"] == "No such run"
        await app.close()

    asyncio.run(exercise())


def test_only_a_run_stream_is_a_stream():
    assert streamed_run("/api/runs/abc/stream") == "abc"
    assert streamed_run("/api/runs/abc/stream?since=0") == "abc"

    for target in ("/api/runs/abc", "/api/runs", "/api/runtime", "/api/runs/abc/events", "/api/runs/abc/stream/extra"):
        assert streamed_run(target) is None


def test_a_hosted_tenant_does_not_name_a_source_path(tmp_path, monkeypatch):
    """Sources come from the deployment, which keeps tenants' folders apart."""
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_SOURCE_DIR", str(tmp_path))
    api = configured_api(workspace=tmp_path, principal=Principal("ada", "acme"))

    async def exercise():
        status, result = await api.dispatch("PUT", "/api/settings/sources", {"sources": {"docs": {"name": "Docs", "path": str(tmp_path)}}})

        assert status == 400 and result["error"] == "This host's sources are configured by the deployment"
        assert not (tmp_path / "data" / "tenants" / "acme" / "sources.json").exists()

        # Reading them still works: the deployment's sources are the tenant's.
        status, result = await api.dispatch("GET", "/api/settings/sources")

        assert status == 200 and set(result["sources"]) == {"default"}
        await api.host.close()

    asyncio.run(exercise())


def test_a_local_host_still_chooses_its_own_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CONCLAVE_PROFILE", raising=False)
    api = configured_api(workspace=tmp_path, principal=Principal("ada", "local"))

    async def exercise():
        status, result = await api.dispatch("PUT", "/api/settings/sources", {"sources": {"docs": {"name": "Docs", "path": str(tmp_path)}}})

        assert status == 200 and result["saved"] is True
        assert json.loads((tmp_path / "data" / "sources.json").read_text()) == {"docs": {"name": "Docs", "path": str(tmp_path)}}
        await api.host.close()

    asyncio.run(exercise())
