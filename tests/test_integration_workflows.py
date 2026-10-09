"""Representative end-to-end workflows A–F, offline and deterministic.

Each one runs a real application through the real runtime with a scripted
provider and a fake HTTP transport, so what is exercised is the actual binding,
authorization, approval and activity path — not a mocked integration layer.
None of these proves a live provider works.
"""
import asyncio
import json

import pytest
from cryptography.fernet import Fernet
from conclave import ToolRegistry
from conclave.application.definition import run_application
from conclave.integrations import ConnectionStore, IntegrationError
from conftest import ScriptedProvider
from test_studio_runs import request as base_request


class Recorder:
    """A provider-shaped HTTP boundary that records what each call would send."""

    def __init__(self):
        self.calls = []

    def __call__(self, hosts):
        return self

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "body": kwargs.get("body"), "params": kwargs.get("params")})
        if "slack.com/api/search.messages" in url:
            return {"ok": True, "messages": {"matches": [{"text": "Billing export failed", "channel": {"name": "support"}}]}}
        if "zendesk.com" in url and "search" in url:
            return {"results": [{"id": 41, "subject": "Invoice dispute"}, {"id": 42, "subject": "Refund request"}]}
        if "sheets.googleapis.com" in url:
            return {"values": [["month", "total"], ["2026-09", "1200"]]}
        if "docs.googleapis.com" in url:
            return {"documentId": "doc-live-1"}
        if "slack.com/api/chat.postMessage" in url:
            return {"ok": True, "ts": "1.1"}
        return {}


def definition_for(tools, *, steps=1, human=False):
    value = base_request()
    definition = value["definition"]
    definition["workflow"] = definition["workflow"][:1]
    definition["agents"] = definition["agents"][:1]
    definition["views"] = [view for view in definition["views"] if view["collection"] == definition["workflow"][0]["output"]] or definition["views"][:1]
    definition["agents"][0]["tools"] = list(tools)
    return definition


def run(definition, tool_calls, tools, *, approve=False, inputs=None):
    script = [{"type": "tool", **call} for call in tool_calls]
    script.append({"type": "final", "content": "Done", "text": "Result"})
    provider = ScriptedProvider(scripts={"writer": script})
    approver = (lambda request: True) if approve else None

    async def collect():
        return [event async for event in run_application(definition, provider, tools, "Do the work",
                                                         inputs=inputs or {"sources": "Ticket export"},
                                                         approver=approver)]
    return asyncio.run(collect())


def store_with(tmp_path, value, *, hosts=()):
    recorder = Recorder()
    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(),
                            hosts=set(hosts), transport_factory=recorder)
    store.create({**value, "applications": ["app"]}, "tenant", "alice")
    return store, recorder


def bound(store, definition):
    return store.bind(ToolRegistry(1000), definition, "tenant", "alice", "app")


def events_of(events, kind):
    return [event for event in events if event["type"] == kind]


def test_workflow_a_reads_support_tickets_and_slack_without_approval(tmp_path):
    """A read-only investigation: both reads run, nothing needs approval."""
    store, recorder = store_with(tmp_path, {
        "id": "support", "integration": "zendesk", "name": "Support", "operations": ["search_tickets"],
        "config": {"base_url": "https://company.zendesk.com"},
        "credentials": {"email": "agent@example.com", "token": "PRIVATE"}},
        hosts={"company.zendesk.com"})
    store.create({"id": "team", "integration": "slack", "name": "Team", "applications": ["app"],
                  "operations": ["search_messages"], "scopes": ["search:read"],
                  "credentials": {"token": "PRIVATE"}}, "tenant", "alice")
    definition = definition_for(["connection.support.search_tickets", "connection.team.search_messages"])
    events = run(definition, [
        {"name": "connection.support.search_tickets", "arguments": {"query": "invoice"}},
        {"name": "connection.team.search_messages", "arguments": {"query": "billing"}}],
        bound(store, definition))
    called = events_of(events, "ToolCalled")
    assert [event["ok"] for event in called] == [True, True]
    assert [(event["connection_id"], event["operation"]) for event in called] == [
        ("support", "search_tickets"), ("team", "search_messages")]
    assert not events_of(events, "ToolDenied")
    assert "PRIVATE" not in json.dumps(events)
    assert [call["url"].split("/api")[0] for call in recorder.calls] == [
        "https://company.zendesk.com", "https://slack.com"]


def test_workflow_b_reports_a_mutation_until_the_human_approves(tmp_path):
    """A publish step: denied by default, then exactly one send once approved."""
    store, recorder = store_with(tmp_path, {
        "id": "team", "integration": "slack", "name": "Team", "operations": ["send_message"],
        "scopes": ["chat:write"], "credentials": {"token": "PRIVATE"}})
    definition = definition_for(["connection.team.send_message"])
    call = {"name": "connection.team.send_message", "arguments": {"channel": "support", "text": "Summary"}}
    denied = run(definition, [call], bound(store, definition))
    assert events_of(denied, "ToolDenied") and not recorder.calls
    approved = run(definition, [call], bound(store, definition), approve=True)
    assert events_of(approved, "ToolCalled")[0]["ok"] is True
    assert len(recorder.calls) == 1


def test_workflow_c_two_connections_one_service_stay_separate(tmp_path):
    """Two Slack accounts: each call uses the account it was bound to."""
    recorder = Recorder()
    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), transport_factory=recorder)
    for identifier, token in [("team_a", "TOKEN_A"), ("team_b", "TOKEN_B")]:
        store.create({"id": identifier, "integration": "slack", "name": identifier, "applications": ["app"],
                      "operations": ["search_messages"], "scopes": ["search:read"],
                      "credentials": {"token": token}}, "tenant", "alice")
    definition = definition_for(["connection.team_b.search_messages"])
    events = run(definition, [{"name": "connection.team_b.search_messages", "arguments": {"query": "billing"}}],
                 bound(store, definition))
    assert events_of(events, "ToolCalled")[0]["ok"] is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["url"].startswith("https://slack.com")


def test_workflow_d_an_agent_cannot_reach_another_application_connection(tmp_path):
    """Cross-application and cross-owner attempts are refused at bind time."""
    store, recorder = store_with(tmp_path, {
        "id": "finance", "integration": "google_sheets", "name": "Finance", "operations": ["read_range"],
        "scopes": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        "credentials": {"access_token": "PRIVATE"}})
    definition = definition_for(["connection.finance.read_range"])
    with pytest.raises(IntegrationError):
        store.bind(ToolRegistry(1000), definition, "tenant", "bob", "app")
    with pytest.raises(IntegrationError):
        store.bind(ToolRegistry(1000), definition, "tenant", "alice", "another-app")
    assert store.list("tenant", "bob") == []


def test_workflow_e_revocation_stops_an_already_bound_run(tmp_path):
    """A tool bound to a live run stops working the moment access is revoked."""
    store, recorder = store_with(tmp_path, {
        "id": "reports", "integration": "google_sheets", "name": "Reports", "operations": ["read_range"],
        "scopes": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        "credentials": {"access_token": "PRIVATE"}})
    definition = definition_for(["connection.reports.read_range"])
    tools = bound(store, definition)
    call = {"name": "connection.reports.read_range", "arguments": {"spreadsheet_id": "s1", "range": "A1:B2"}}
    assert events_of(run(definition, [call], tools), "ToolCalled")[0]["ok"] is True
    store.disconnect("reports", "tenant", "alice")
    after = run(definition, [call], tools)
    assert events_of(after, "ToolCalled")[0]["ok"] is False
    assert len(recorder.calls) == 1


def test_workflow_f_a_write_is_never_retried_after_failure(tmp_path):
    """A failed mutation is reported, not silently repeated."""
    class Failing(Recorder):
        async def request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url})
            raise IntegrationError("provider rejected the write")

    recorder = Failing()
    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), transport_factory=recorder)
    store.create({"id": "docs", "integration": "google_docs", "name": "Docs", "applications": ["app"],
                  "operations": ["create_document"], "scopes": ["https://www.googleapis.com/auth/documents"],
                  "credentials": {"access_token": "PRIVATE"}}, "tenant", "alice")
    definition = definition_for(["connection.docs.create_document"])
    events = run(definition, [{"name": "connection.docs.create_document",
                               "arguments": {"title": "Report", "text": "Body"}}],
                 bound(store, definition), approve=True)
    assert events_of(events, "ToolCalled")[0]["ok"] is False
    assert len(recorder.calls) == 1
    assert events_of(events, "ApplicationFinished")[0]["status"] == "completed"
