"""T7 — checking with the service before anything is sent again.

Each check makes one strongly consistent read where the service offers one, and
answers found, absent (exact only where absence is reliable) or inconclusive. A
check may only read through an operation the connection already grants. GitHub
issue and pull request bodies carry an invisible marker; webhook and OpenAPI
writes carry an idempotency key.
"""

import asyncio
import base64
import json
from datetime import datetime, timezone

import pytest

from conclave.integrations.services import integrations, invoke
from conclave.integrations.transport import IntegrationError

STARTED = "2026-11-16T10:40:00+00:00"
EPOCH = datetime.fromisoformat(STARTED).timestamp()


class Routes:
    """A recording transport answering by URL fragment; a status raises."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, answer in self.routes.items():
            if fragment in url:
                if isinstance(answer, int):
                    raise IntegrationError(f"Provider request failed (HTTP {answer})", sent=True, rejected=answer < 500,
                                           status=answer)
                if isinstance(answer, Exception):
                    raise answer
                return answer(kwargs) if callable(answer) else answer
        raise AssertionError(f"unexpected request {method} {url}")


def context(arguments, action_id="a1"):
    from conclave.actions import ActionContext

    return ActionContext(action_id=action_id, started_at=STARTED, arguments=arguments)


def verify(service_id, operation, arguments, routes, *, config=None, credentials=None):
    from conclave.integrations.checks import verify as check

    transport = Routes(routes)
    service = integrations()[service_id]
    config = config or {"base_url": service.base_url or "https://company.example.com"}
    result = asyncio.run(check(service, operation, config, credentials or {"token": "token", "access_token": "token",
                                                                       "api_key": "key", "email": "me@example.com"},
                               arguments, context(arguments), transport))
    return result, transport.calls


# --- GitHub ------------------------------------------------------------------

GITHUB = {"owner": "NorviaLabs", "repo": "forge"}


def test_github_branch_found_at_the_commit_it_was_created_from():
    arguments = {**GITHUB, "branch": "fix/duplicates", "sha": "abc1234def"}
    result, calls = verify("github", "create_branch", arguments,
                           {"/git/ref/heads/fix/duplicates": {"object": {"sha": "abc1234def"}}})
    assert result.result == "found" and "fix/duplicates" in result.summary
    assert calls[0][0] == "GET" and calls[0][1] == "https://api.github.com/repos/NorviaLabs/forge/git/ref/heads/fix/duplicates"
    result, _ = verify("github", "create_branch", arguments, {"/git/ref/heads/": {"object": {"sha": "0000000"}}})
    assert result.result == "inconclusive", "a branch of that name that points elsewhere is not proof either way"
    result, _ = verify("github", "create_branch", arguments, {"/git/ref/heads/": 404})
    assert result.result == "absent" and result.exact


def test_github_file_found_only_with_the_text_that_was_written():
    arguments = {**GITHUB, "path": "src/notify.py", "message": "Fix", "text": "print('once')\n", "branch": "fix/duplicates"}
    encoded = base64.b64encode(b"print('once')\n").decode()
    result, calls = verify("github", "write_content", arguments,
                           {"/contents/src/notify.py": {"encoding": "base64", "content": encoded, "sha": "f1"}})
    assert result.result == "found"
    assert calls[0][2]["params"] == {"ref": "fix/duplicates"}
    other = base64.b64encode(b"print('twice')\n").decode()
    result, _ = verify("github", "write_content", arguments, {"/contents/": {"encoding": "base64", "content": other}})
    assert result.result == "absent" and result.exact
    result, _ = verify("github", "write_content", arguments, {"/contents/": 404})
    assert result.result == "absent" and result.exact


def test_github_pull_request_found_by_its_branches_or_marker():
    arguments = {**GITHUB, "title": "Fix duplicates", "head": "fix/duplicates", "base": "main"}
    pull = {"number": 866, "title": "Fix duplicates", "state": "open", "html_url": "https://github.com/NorviaLabs/forge/pull/866",
            "head": {"ref": "fix/duplicates"}, "base": {"ref": "main"}, "body": "Body\n\n<!-- conclave-action:a1 -->"}
    result, calls = verify("github", "create_pull_request", arguments, {"/pulls": [pull]})
    assert result.result == "found" and "#866" in result.summary and "#866" in result.detail
    assert calls[0][1].endswith("/repos/NorviaLabs/forge/pulls")
    assert calls[0][2]["params"] == {"head": "NorviaLabs:fix/duplicates", "base": "main", "state": "all", "per_page": 100}
    result, _ = verify("github", "create_pull_request", arguments, {"/pulls": []})
    assert result.result == "absent" and result.exact


def test_github_merge_found_when_merged_and_absent_while_open():
    arguments = {**GITHUB, "number": "866", "merge_method": "squash"}
    result, calls = verify("github", "merge_pull_request", arguments, {"/pulls/866": {"merged": True, "state": "closed"}})
    assert result.result == "found" and calls[0][0] == "GET"
    result, _ = verify("github", "merge_pull_request", arguments, {"/pulls/866": {"merged": False, "state": "open"}})
    assert result.result == "absent" and result.exact
    result, _ = verify("github", "merge_pull_request", arguments, {"/pulls/866": {"merged": False, "state": "closed"}})
    assert result.result == "inconclusive"


def test_github_issue_found_only_by_the_marker_this_action_added():
    arguments = {**GITHUB, "title": "Duplicate notifications", "body": "Seen twice"}
    issues = [{"number": 12, "title": "Duplicate notifications", "body": "Seen twice"},
              {"number": 13, "title": "Duplicate notifications", "body": "Seen twice\n\n<!-- conclave-action:a1 -->",
               "html_url": "https://github.com/NorviaLabs/forge/issues/13"}]
    result, calls = verify("github", "create_issue", arguments, {"/issues": issues})
    assert result.result == "found" and "#13" in result.summary
    assert calls[0][2]["params"]["state"] == "all" and calls[0][2]["params"]["since"] == "2026-11-16T10:39:00Z"
    result, _ = verify("github", "create_issue", arguments, {"/issues": issues[:1]})
    assert result.result == "absent" and result.exact, "a same-titled issue without the marker is someone else's"


# --- Other services -----------------------------------------------------------


def test_sheets_read_back_decides_only_when_it_matches():
    arguments = {"spreadsheet_id": "s1", "range": "A1:B1", "values": [["a", 1]]}
    result, calls = verify("google_sheets", "update_range", arguments, {"/values/A1%3AB1": {"values": [["a", "1"]]}})
    assert result.result == "found"
    result, _ = verify("google_sheets", "update_range", arguments, {"/values/": {"values": [["b", "2"]]}})
    assert result.result == "inconclusive", "someone may have changed it since"


def test_hubspot_and_jira_updates_read_back_exactly():
    arguments = {"object_type": "contacts", "record_id": "7", "properties": {"lifecyclestage": "customer"}}
    result, calls = verify("hubspot", "update_record", arguments,
                           {"/crm/v3/objects/contacts/7": {"properties": {"lifecyclestage": "customer"}}})
    assert result.result == "found" and calls[0][2]["params"] == {"properties": "lifecyclestage"}
    result, _ = verify("hubspot", "update_record", arguments, {"/crm/v3/objects/": {"properties": {"lifecyclestage": "lead"}}})
    assert result.result == "absent" and result.exact
    arguments = {"issue_id": "ENG-7", "summary": "Fix duplicates"}
    result, _ = verify("jira", "update_issue", arguments, {"/rest/api/3/issue/ENG-7": {"fields": {"summary": "Fix duplicates"}}})
    assert result.result == "found"
    result, _ = verify("jira", "update_issue", arguments, {"/rest/api/3/issue/ENG-7": {"fields": {"summary": "Old"}}})
    assert result.result == "absent" and result.exact


def test_searches_that_lag_can_find_but_never_prove_absence():
    arguments = {"project": "ENG", "summary": "Fix duplicates", "issue_type": "Bug"}
    one = {"issues": [{"key": "ENG-9", "fields": {"summary": "Fix duplicates"}}]}
    result, calls = verify("jira", "create_issue", arguments, {"/search/jql": one})
    assert result.result == "found" and "ENG-9" in result.summary
    jql = calls[0][2]["params"]["jql"]
    assert 'project = "ENG"' in jql and 'summary ~ "Fix duplicates"' in jql and "created >=" in jql
    for answer in ({"issues": []}, {"issues": one["issues"] * 2}):
        result, _ = verify("jira", "create_issue", arguments, {"/search/jql": answer})
        assert result.result == "inconclusive"
    arguments = {"team_id": "t1", "title": "Fix duplicates"}
    found = {"data": {"issues": {"nodes": [{"identifier": "ENG-3", "title": "Fix duplicates"}]}}}
    result, _ = verify("linear", "create_issue", arguments, {"/graphql": found})
    assert result.result == "found"
    result, _ = verify("linear", "create_issue", arguments, {"/graphql": {"data": {"issues": {"nodes": []}}}})
    assert result.result == "inconclusive"
    arguments = {"channel": "C1", "text": "Heads up"}
    result, calls = verify("slack", "send_message", arguments,
                           {"conversations.history": {"ok": True, "messages": [{"text": "Heads up", "ts": "1"}]}})
    assert result.result == "found" and calls[0][2]["params"]["channel"] == "C1"
    assert float(calls[0][2]["params"]["oldest"]) == EPOCH - 60
    result, _ = verify("slack", "send_message", arguments, {"conversations.history": {"ok": True, "messages": []}})
    assert result.result == "inconclusive", "Slack may have rewritten the text"


def test_a_trello_card_is_dated_by_its_own_id():
    created = format(int(EPOCH) + 5, "x") + "0" * 16
    older = format(int(EPOCH) - 3600, "x") + "0" * 16
    arguments = {"list_id": "L1", "name": "Fix duplicates"}
    result, _ = verify("trello", "create_card", arguments, {"/1/lists/L1/cards": [{"id": created, "name": "Fix duplicates"}]})
    assert result.result == "found"
    result, _ = verify("trello", "create_card", arguments, {"/1/lists/L1/cards": [{"id": older, "name": "Fix duplicates"}]})
    assert result.result == "absent" and result.exact, "an older card of the same name is not this one"


def test_a_zendesk_note_is_found_among_the_tickets_comments():
    arguments = {"ticket_id": "42", "text": "Refund issued"}
    comments = {"comments": [{"body": "Refund issued", "public": False, "created_at": "2026-11-16T10:40:30Z"}]}
    result, _ = verify("zendesk", "prepare_reply", arguments, {"/tickets/42/comments.json": comments})
    assert result.result == "found"
    result, _ = verify("zendesk", "prepare_reply", arguments, {"/tickets/42/comments.json": {"comments": []}})
    assert result.result == "absent" and result.exact


def test_gmail_is_checked_only_by_a_message_id_the_message_already_has():
    raw = base64.urlsafe_b64encode(b"Message-ID: <abc@example.com>\r\nSubject: Hi\r\n\r\nBody").decode()
    result, calls = verify("gmail", "send_email", {"raw": raw}, {"/messages": {"messages": [{"id": "m1"}]}})
    assert result.result == "found" and calls[0][2]["params"]["q"] == "rfc822msgid:<abc@example.com>"
    result, _ = verify("gmail", "send_email", {"raw": raw}, {"/messages": {}})
    assert result.result == "inconclusive"
    bare = base64.urlsafe_b64encode(b"Subject: Hi\r\n\r\nBody").decode()
    result, calls = verify("gmail", "send_email", {"raw": bare}, {})
    assert result.result == "inconclusive" and not calls and "Message-ID" in result.summary


def test_a_check_that_cannot_reach_the_service_is_inconclusive_never_an_error():
    arguments = {**GITHUB, "number": "866"}
    for failure in (500, IntegrationError("Provider request timed out", sent=True), KeyError("shape")):
        result, _ = verify("github", "merge_pull_request", arguments, {"/pulls/866": failure})
        assert result.result == "inconclusive" and "GitHub" in result.summary


def test_a_check_never_repeats_a_credential_the_service_echoed():
    arguments = {**GITHUB, "title": "t", "head": "h", "base": "main"}
    pull = {"number": 1, "title": "token-secret-value", "state": "open", "head": {"ref": "h"}, "base": {"ref": "main"}}
    result, _ = verify("github", "create_pull_request", arguments, {"/pulls": [pull]},
                       credentials={"token": "token-secret-value"})
    assert "token-secret-value" not in result.summary + result.detail and "[redacted]" in result.summary + result.detail


# --- What each check needs, and says -------------------------------------------


@pytest.mark.parametrize("service, operation, needs", [
    ("github", "create_branch", "read_branches"), ("github", "write_content", "read_content"),
    ("github", "create_pull_request", "read_pull_request"), ("github", "merge_pull_request", "read_pull_request"),
    ("github", "create_issue", "read_issue"), ("google_sheets", "update_range", "read_range"),
    ("hubspot", "update_record", "read_record"), ("jira", "update_issue", "read_issue"),
    ("jira", "create_issue", "search_issues"), ("linear", "create_issue", "search_issues"),
    ("trello", "create_card", "read_cards"), ("zendesk", "prepare_reply", "read_history"),
    ("slack", "send_message", "read_conversation"), ("gmail", "send_email", "search_messages"),
    ("gmail", "create_draft", None), ("google_docs", "create_document", None), ("trello", "update_card", None),
    ("webhooks", "send", None),
])
def test_each_check_names_the_read_it_needs(service, operation, needs):
    from conclave.integrations.checks import describe, requirement

    assert requirement(service, operation) == needs
    if needs:
        assert describe(service, operation, {**GITHUB, "head": "fix/x", "base": "main", "channel": "C1"})


def test_bound_tools_carry_a_check_only_when_its_read_is_granted(tmp_path):
    from cryptography.fernet import Fernet
    from conclave import ToolRegistry
    from conclave.integrations.connections import ConnectionStore

    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), hosts=["hooks.example.com"])
    store.create({"id": "forge", "integration": "github", "name": "Forge", "applications": ["app"],
                  "operations": ["create_pull_request", "read_pull_request", "create_issue"],
                  "credentials": {"token": "t"}}, "local", "owner")
    store.create({"id": "team_slack", "integration": "slack", "name": "Team Slack", "applications": ["app"],
                  "operations": ["send_message"], "scopes": ["chat:write"], "credentials": {"token": "t"}}, "local", "owner")
    store.create({"id": "hook", "integration": "webhooks", "name": "Deploy", "applications": ["app"], "operations": ["send"],
                  "config": {"base_url": "https://hooks.example.com/x"}, "credentials": {"token": "t"}}, "local", "owner")
    definition = {"agents": [{"id": "a", "tools": ["connection.forge.create_pull_request", "connection.forge.create_issue",
                                                   "connection.team_slack.send_message", "connection.hook.send"]}]}
    tools = store.bind(ToolRegistry(1000), definition, "local", "owner", "app")
    assert tools.tool("connection.forge.create_pull_request").check is not None
    issue = tools.tool("connection.forge.create_issue")
    assert issue.check is None and "read issue" in issue.check_unavailable and "forge" in issue.check_unavailable
    slack = tools.tool("connection.team_slack.send_message")
    assert slack.check is None and "team_slack" in slack.check_unavailable and "read conversation" in slack.check_unavailable
    hook = tools.tool("connection.hook.send")
    assert hook.check is None and "cannot confirm" in hook.check_unavailable
    described = tools.tool("connection.forge.create_pull_request").check.describe({"head": "fix/x", "base": "main"})
    assert "fix/x" in described and "main" in described


def test_a_bound_check_reads_through_the_connection_and_rechecks_the_grant(tmp_path):
    from cryptography.fernet import Fernet
    from conclave import ToolRegistry
    from conclave.integrations.connections import ConnectionStore

    transports = []

    def factory(hosts):
        transport = Routes({"/pulls": []})
        transports.append(transport)
        return transport

    store = ConnectionStore(tmp_path / "connections.sqlite", Fernet.generate_key(), transport_factory=factory)
    store.create({"id": "forge", "integration": "github", "name": "Forge", "applications": ["app"],
                  "operations": ["create_pull_request", "read_pull_request"], "credentials": {"token": "t"}}, "local", "owner")
    tools = store.bind(ToolRegistry(1000), {"agents": [{"id": "a", "tools": ["connection.forge.create_pull_request"]}]},
                       "local", "owner", "app")
    check = tools.tool("connection.forge.create_pull_request").check
    arguments = {**GITHUB, "title": "t", "head": "h", "base": "main"}
    result = asyncio.run(check.run(arguments, context(arguments)))
    assert result.result == "absent" and transports[0].calls[0][2]["headers"]["Authorization"] == "Bearer t"
    store.update_grants("forge", {"operations": ["create_pull_request"]}, "local", "owner")
    result = asyncio.run(check.run(arguments, context(arguments)))
    assert result.result == "unavailable", "a read revoked since binding is not used"


# --- Marker and idempotency key -------------------------------------------------


def sent(service_id, operation_name, arguments, *, action_id="a1", config=None):
    from conclave.actions import current_action

    transport = Routes({"": {"number": 1, "ok": True}})
    service = integrations()[service_id]
    operation = next(item for item in service.operations if item.name == operation_name)

    async def exercise():
        token = current_action.set(action_id)
        try:
            await invoke(service, operation, config or {"base_url": service.base_url or "https://hooks.example.com/x"},
                         {"token": "t"}, arguments, transport)
        finally:
            current_action.reset(token)

    asyncio.run(exercise())
    return transport.calls[-1][2]


def test_github_bodies_carry_an_invisible_marker():
    body = sent("github", "create_issue", {**GITHUB, "title": "Bug", "body": "Seen twice"})["body"]
    assert body["body"] == "Seen twice\n\n<!-- conclave-action:a1 -->"
    body = sent("github", "create_pull_request", {**GITHUB, "title": "Fix", "head": "h", "base": "main"})["body"]
    assert body["body"] == "<!-- conclave-action:a1 -->"
    body = sent("github", "create_issue", {**GITHUB, "title": "Bug", "body": "Seen"}, action_id=None)["body"]
    assert body["body"] == "Seen", "outside an action nothing is added"


def test_no_marker_reaches_messages_people_read():
    body = sent("slack", "send_message", {"channel": "C1", "text": "Heads up"})["body"]
    assert "conclave-action" not in json.dumps(body)
    body = sent("webhooks", "send", {"payload": {"event": "deploy"}})["body"]
    assert body == {"event": "deploy"}


def test_webhooks_and_openapi_writes_carry_an_idempotency_key():
    from conclave.actions import current_action
    from conclave.integrations.protocols import OpenAPI

    headers = sent("webhooks", "send", {"payload": {"event": "deploy"}}, action_id="a7")["headers"]
    assert headers["Idempotency-Key"] == "a7"
    spec = {"paths": {"/items": {"post": {"operationId": "create", "parameters": []},
                                 "get": {"operationId": "list", "parameters": []}}}}
    transport = Routes({"": {}})
    adapter = OpenAPI("https://approved.example", spec, ["create", "list"], transport, headers={"X-Team": "t"})

    async def exercise():
        token = current_action.set("a7")
        try:
            await adapter.invoke("create", {})
            await adapter.invoke("list", {})
        finally:
            current_action.reset(token)

    asyncio.run(exercise())
    assert transport.calls[0][2]["headers"] == {"X-Team": "t", "Idempotency-Key": "a7"}
    assert "Idempotency-Key" not in transport.calls[1][2]["headers"], "reads are not keyed"
    assert adapter.headers == {"X-Team": "t"}, "the shared headers are not changed"
