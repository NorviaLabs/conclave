"""Every source a record cites is checked against what its step read.

A step's successful reads are its evidence: the files it read or searched, the
pages it fetched, the issues, pull requests and commits it opened, and the
records it was given. Each cited source is then `found`, `not_found`, or
`not_checkable` when it names nothing a check can look for (or the step read
through a tool whose reads cannot be interpreted). Tool output is used while the
step runs and is never recorded.
"""

import asyncio
import json

import pytest

from conclave import Capability, Tool, ToolRegistry
from conclave.application.definition import run_application, validate_fields
from conclave.application.runs import RunRepository
from conclave.application.sources import source_kind
from conftest import CONFIG, ScriptedProvider
from test_studio_runs import collect

SECRET = "SECRET-DOCUMENT-TEXT-1234"
ISSUE = json.dumps({"number": 12, "title": "Bug", "url": "https://github.com/acme/forge/issues/12", "body": SECRET})
PR = json.dumps({"number": 866, "title": "Fix", "url": "https://github.com/acme/forge/pull/866", "headRefOid": "f" * 40})
SHOW = "commit 3f9c2a1b7e0d4c5b6a798877665544332211aabb\nAuthor: Ana\n\n    Fix the counter\n"
SEARCH = json.dumps([{"title": "Rumour", "href": "https://example.com/rumour", "body": "Snippet"}])


def tool(name, output, *, fails=False):
    async def call(**arguments):
        if fails:
            raise RuntimeError("cannot read")
        return output
    return Tool(name=name, description=name, parameters={}, func=call, capabilities=frozenset({Capability.READ}))


def registry(*extra):
    tools = ToolRegistry(20000)
    for item in (tool("read_file", "1: " + SECRET), tool("web_fetch", "Page text " + SECRET), tool("web_search", SEARCH),
                 tool("list_files", "docs/listed.md\ndocs/a.md"), tool("search_files", "docs/hit.md:4: a matching line"),
                 tool("github_issue", ISSUE), tool("github_pr", PR), tool("git_show", SHOW), tool("git_log", "abc1234 one"),
                 *extra):
        tools.register(item)
    return tools


def call(name, **arguments):
    return {"type": "tool", "name": name, "arguments": arguments}


def final(*records):
    return {"type": "final", "content": "Done", "records": [
        {"id": record_id, "title": record_id, "body": "Claim", "status": "open", "source_refs": list(refs), **extra}
        for record_id, refs, extra in records]}


def definition(steps=(("research", "researcher", []),), tools=None, extra_agents=()):
    names = tools or ["read_file", "web_fetch", "web_search", "list_files", "search_files", "github_issue",
                      "github_pr", "git_show", "git_log"]
    agents = {agent for _, agent, _ in steps}
    return {
        "version": 1, "name": "Sources",
        "agents": [{"id": agent, "instructions": "Research", "tools": list(names)} for agent in sorted(agents)] + list(extra_agents),
        "state": [{"key": "findings", "type": "records", "merge": "upsert", "statuses": []}],
        "workflow": [{"id": step, "agent": agent, "task": "Do " + step, "output": "findings"} for step, agent, _ in steps],
        "connections": [{"from": before, "to": step} for step, _, needs in steps for before in needs],
        "views": [{"id": "findings_view", "collection": "findings", "layout": "cards"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }


def run(value, provider, tools=None, **options):
    async def go():
        return [event async for event in run_application(value, provider, tools or registry(), "Find sources", **options)]
    return asyncio.run(go())


def checks(events, record_id=None):
    records = events[-1]["state"]["findings"]
    found = {record["id"]: record.get("source_checks") for record in records}
    return found[record_id] if record_id else found


# --- What a reference names --------------------------------------------------------


@pytest.mark.parametrize("ref, expected", [
    ("https://Example.com:443/a/?q=1#frag", ("url", "https://example.com/a?q=1")),
    ("http://example.com", ("url", "http://example.com")),
    ("HTTP://EXAMPLE.com/", ("url", "http://example.com")),
    ("http://example.com:8080/x", ("url", "http://example.com:8080/x")),
    ("docs/a.md#L3-L9", ("path", "docs/a.md")),
    ("./docs/a.md:10", ("path", "docs/a.md")),
    ("docs/a.md:10-20", ("path", "docs/a.md")),
    ("/docs/a.md", ("path", "docs/a.md")),
    ("README.md", ("path", "README.md")),
    ("src/", ("path", "src")),
    ("#12", ("issue", "12")),
    ("PR #866", ("issue", "866")),
    ("issue #12", ("issue", "12")),
    ("acme/forge#12", ("issue", "12")),
    ("3F9C2A1", ("commit", "3f9c2a1")),
    ("3f9c2a1b7e0d4c5b6a798877665544332211aabb", ("commit", "3f9c2a1b7e0d4c5b6a798877665544332211aabb")),
    ("finding-1", ("name", "finding-1")),
    ("Interview notes", None),
    ("", None),
    ("file:///etc/passwd", None),
    ("javascript:alert(1)", None),
    ("../outside.md", None),
])
def test_source_kind_names_what_a_reference_points_at(ref, expected):
    assert source_kind(ref) == expected


def test_source_checks_is_a_reserved_record_field():
    with pytest.raises(ValueError):
        validate_fields([{"key": "source_checks", "label": "Checks", "type": "string", "required": False}])


# --- What the step read ------------------------------------------------------------


def test_source_checks_mark_files_and_pages_the_step_read_as_found():
    provider = ScriptedProvider({"researcher": [
        call("read_file", path="./docs/a.md"), call("web_fetch", url="https://Example.com/page/#top"),
        final(("f1", ["docs/a.md#L3", "docs/a.md:10", "https://example.com/page", "docs/b.md",
                      "https://example.com/other", "Interview notes"], {}))]})
    events = run(definition(), provider)
    assert events[-1]["status"] == "completed"
    assert checks(events, "f1") == ["found", "found", "found", "not_found", "not_found", "not_checkable"]


def test_source_seen_only_in_search_results_or_a_listing_is_not_found():
    provider = ScriptedProvider({"researcher": [
        call("web_search", query="price"), call("list_files", path="docs"),
        final(("f1", ["https://example.com/rumour", "docs/listed.md"], {}))]})
    assert checks(run(definition(), provider), "f1") == ["not_found", "not_found"]


def test_source_search_hits_count_as_read():
    provider = ScriptedProvider({"researcher": [call("search_files", query="match"),
                                                final(("f1", ["docs/hit.md:4", "docs/miss.md"], {}))]})
    assert checks(run(definition(), provider), "f1") == ["found", "not_found"]


def test_source_issues_pull_requests_and_commits_the_step_opened_are_found():
    provider = ScriptedProvider({"researcher": [
        call("github_issue", number=12), call("github_pr", number=866), call("git_show", revision="HEAD"),
        final(("f1", ["#12", "https://github.com/acme/forge/issues/12", "PR #866", "https://github.com/acme/forge/pull/866/",
                      "3f9c2a1", "3f9c2a1b7e0d4c5b6a798877665544332211aabb", "#13", "abc1234", "deadbeef"], {}))]})
    assert checks(run(definition(), provider), "f1") == ["found"] * 6 + ["not_found"] * 3


def test_source_from_a_failed_read_is_not_found():
    provider = ScriptedProvider({"researcher": [call("read_file", path="docs/a.md"), final(("f1", ["docs/a.md"], {}))]})
    tools = registry()
    tools.register(tool("read_file", "", fails=True))
    assert checks(run(definition(), provider, tools), "f1") == ["not_found"]


def test_source_read_by_a_delegated_member_counts_for_the_step():
    value = definition(extra_agents=[{"id": "reader", "instructions": "Read", "tools": ["read_file"]}])
    provider = ScriptedProvider({
        "researcher": [{"type": "delegate", "agent": "reader", "task": "Read docs/a.md", "purpose": "Read"},
                       final(("f1", ["docs/a.md"], {}))],
        "reader": [call("read_file", path="docs/a.md"), {"type": "final", "content": "Read it"}],
    })
    events = run(value, provider)
    assert events[-1]["status"] == "completed"
    assert checks(events, "f1") == ["found"]


def test_source_read_through_an_uninterpreted_tool_is_not_checkable():
    value = definition(tools=["read_file", "connection.notion.read_page"])
    provider = ScriptedProvider({"researcher": [
        call("connection.notion.read_page", page="x"), call("read_file", path="docs/a.md"),
        final(("f1", ["https://www.notion.so/abc", "docs/a.md", "Interview notes"], {}))]})
    events = run(value, provider, registry(tool("connection.notion.read_page", "Page")))
    assert checks(events, "f1") == ["not_checkable", "found", "not_checkable"]


def test_source_naming_a_record_the_step_was_given_is_found():
    value = definition(steps=(("first", "researcher", []), ("second", "writer", ["first"])))
    provider = ScriptedProvider({
        "researcher": [final(("f1", [], {}))],
        "writer": [final(("f2", ["f1", "f9", "docs/a.md"], {}))],
    })
    assert checks(run(value, provider), "f2") == ["found", "not_checkable", "not_found"]


def test_source_checks_supplied_by_the_agent_are_ignored():
    provider = ScriptedProvider({"researcher": [final(("f1", ["docs/zzz.md"], {"source_checks": ["found"]}))]})
    assert checks(run(definition(), provider), "f1") == ["not_found"]


def test_source_checks_never_record_what_a_tool_returned():
    provider = ScriptedProvider({"researcher": [call("read_file", path="docs/a.md"), call("github_issue", number=12),
                                                final(("f1", ["docs/a.md"], {}))]})
    events = run(definition(), provider)
    assert SECRET not in json.dumps(events)


def test_source_checks_are_replaced_with_the_sources_on_upsert():
    value = definition(steps=(("first", "researcher", []), ("second", "writer", ["first"])))
    provider = ScriptedProvider({
        "researcher": [call("read_file", path="docs/a.md"), final(("f1", ["docs/a.md"], {}))],
        "writer": [final(("f1", ["docs/b.md"], {}))],
    })
    events = run(value, provider)
    first = next(event for event in events if event["type"] == "ApplicationStateChanged" and event["stage"] == "first")
    assert first["patch"][0]["source_checks"] == ["found"]
    record = events[-1]["state"]["findings"][0]
    assert record["source_refs"] == ["docs/b.md"] and record["source_checks"] == ["not_found"]


def test_source_checks_follow_an_output_repair():
    provider = ScriptedProvider({"researcher": [
        call("read_file", path="docs/a.md"),
        {"type": "final", "content": "Done", "records": [{"id": "f1", "body": "No title", "status": "open", "source_refs": ["docs/a.md"]}]},
        final(("f1", ["docs/a.md", "docs/b.md"], {}))]})
    events = run(definition(), provider)
    assert any(event["type"] == "OutputRepairCompleted" for event in events)
    assert checks(events, "f1") == ["found", "not_found"]


def with_review(value):
    value["agents"].append({"id": "reviewer", "kind": "human", "instructions": "Review", "tools": []})
    value["workflow"].append({"id": "review", "agent": "reviewer", "task": "Review", "output": "findings"})
    value["connections"] = [{"from": "research", "to": "review"}]
    return value


def test_source_cited_by_a_person_is_not_checkable():
    value = with_review(definition())
    provider = ScriptedProvider({"researcher": [call("read_file", path="docs/a.md"), final(("f1", ["docs/a.md"], {}))]})
    answer = {"records": [{"id": "h1", "title": "Review", "body": "Checked", "status": "open", "source_refs": ["docs/a.md", "#12"]}]}
    events = run(value, provider, human_handler=lambda review: answer)
    assert checks(events) == {"f1": ["found"], "h1": ["not_checkable", "not_checkable"]}


def test_source_checks_survive_a_review_and_a_restart(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    value = with_review(definition())
    run_id = repository.create({"version": 1, "application_id": "sources", "revision": 1, "definition": value,
                                "task": "Find sources", "inputs": {}})
    provider = ScriptedProvider({"researcher": [call("read_file", path="docs/a.md"), final(("f1", ["docs/a.md", "docs/b.md"], {}))]})
    collect(repository.execute(run_id, provider, registry()))
    review = repository.get(run_id)["reviews"][0]
    assert review["request"]["state"]["findings"][0]["source_checks"] == ["found", "not_found"]
    repository.submit_review(run_id, review["id"], reviewer="ana",
                             output={"records": [{"id": "h1", "title": "Review", "body": "Ok", "status": "open", "source_refs": []}]})
    repository = RunRepository(path)
    events = collect(repository.resume(run_id, provider, registry()))
    assert events[-1]["status"] == "completed"
    assert checks(events) == {"f1": ["found", "not_found"], "h1": []}


# --- Performance audit (P2) -------------------------------------------------------------


def test_source_commit_checks_stay_fast_at_their_limits():
    import hashlib
    import time
    from conclave.application.sources import StepReads, with_source_checks

    sha = lambda value: hashlib.sha1(str(value).encode()).hexdigest()
    reads = StepReads()
    for index in range(5000):
        reads._add_commit(sha(index))
    records = [{"source_refs": [sha(10**6 + record * 50 + ref)[:12] for ref in range(50)]} for record in range(199)]
    records.append({"source_refs": [sha(7)[:7], sha(8), sha(9)[:20]]})
    started = time.perf_counter()
    with_source_checks(records, reads)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"{elapsed:.2f}s for 10,000 cited commits against 5,000 read"
    assert records[-1]["source_checks"] == ["found", "found", "found"]
    assert set(records[0]["source_checks"]) == {"not_found"}
