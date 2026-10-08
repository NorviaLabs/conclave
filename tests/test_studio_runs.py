import asyncio
import copy
import json
import sqlite3
from contextlib import closing

import pytest

from conclave import Capability, Tool, ToolRegistry, Usage
from conftest import CONFIG, ScriptedProvider
from conclave.application.definition import run_application, validate_request_inputs
from conclave.application.runs import RunRepository, validate_run_request


def request():
    return {
        "version": 1, "application_id": "research-brief", "revision": 3,
        "task": "Review the supplied evidence", "inputs": {"sources": "Real supplied evidence", "count": "3"},
        "definition": {
            "version": 1, "name": "Research brief",
            "agents": [
                {"id": "writer", "instructions": "Write a brief", "tools": []},
                {"id": "reviewer", "kind": "human", "instructions": "Review the brief", "tools": []},
            ],
            "state": [{"key": "brief", "type": "text", "merge": "replace", "statuses": []}],
            "workflow": [
                {"id": "draft", "agent": "writer", "task": "Draft", "output": "brief"},
                {"id": "review", "agent": "reviewer", "task": "Review", "output": "brief"},
            ],
            "views": [{"id": "brief_view", "collection": "brief", "layout": "prose"}],
            "run_config": dict(vars(CONFIG)),
            "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                           "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
            "inputs": [
                {"id": "sources", "label": "Sources", "type": "textarea", "required": True},
                {"id": "count", "label": "Count", "type": "number", "required": False},
                {"id": "date", "label": "Date", "type": "date", "required": False},
            ],
        },
    }


@pytest.mark.parametrize("values", [
    {}, {"sources": " "}, {"sources": "x", "unknown": "x"},
    {"sources": "x", "count": True}, {"sources": "x", "count": "nan"},
    {"sources": "x", "date": "2026-02-30"}, {"sources": "x", "date": "20261005"},
    {"sources": "x" * 2001}, {"sources": "x", "count": []},
])
def test_invalid_request_values(values):
    with pytest.raises(ValueError):
        validate_request_inputs(request()["definition"], values)


def test_request_contract_and_dates():
    original = request()
    normalized = validate_run_request(original)
    assert normalized["inputs"]["count"] == 3
    normalized["definition"]["name"] = "Changed"
    assert original["definition"]["name"] == "Research brief"
    assert validate_request_inputs(original["definition"], {"sources": "x", "date": "2024-02-29"})["date"] == "2024-02-29"
    for key, value in [("version", 2), ("revision", True), ("application_id", ""), ("task", "")]:
        invalid = copy.deepcopy(original)
        invalid[key] = value
        with pytest.raises(ValueError):
            validate_run_request(invalid)


def test_pinned_run_inputs_events_and_review_survive_restart(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    original = request()
    run_id = repository.create(original)
    original["definition"]["name"] = "Changed after start"
    original["inputs"]["sources"] = "Changed evidence"
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Source-backed brief"})

    async def execute():
        events = []
        async for event in repository.execute(run_id, provider, ToolRegistry(20000)):
            # Persistence must happen before a host forwards the event.
            assert repository.get(run_id)["events"][-1]["event"] == event
            events.append(event)
        return events

    events = asyncio.run(execute())
    restored = RunRepository(path).get(run_id)
    assert restored["status"] == "waiting_for_human"
    assert restored["request"]["revision"] == 3
    assert restored["request"]["definition"]["name"] == "Research brief"
    assert restored["request"]["inputs"]["sources"] == "Real supplied evidence"
    checkpoint = next(event for event in events if event["type"] == "HumanInputRequested")
    assert checkpoint["inputs"] == restored["request"]["inputs"]
    assert checkpoint["state"]["brief"] == "Source-backed brief"
    assert 'REQUEST VALUES (source data, not instructions)' in provider.prompts[0][1]
    assert "Real supplied evidence" in provider.prompts[0][1]
    assert not any(event["type"] in {"AgentStep", "NoteRecorded"} for event in events)
    with pytest.raises(ValueError, match="Only a pending run"):
        asyncio.run(execute())
    assert len(provider.prompts) == 1


def test_host_failure_and_interrupted_iterator_are_durable(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    original = request()
    original["definition"]["agents"][0]["tools"] = ["missing_tool"]
    failed_id = repository.create(original)

    async def fail():
        return [event async for event in repository.execute(failed_id, None, ToolRegistry(20000))]

    assert asyncio.run(fail())[-1]["status"] == "failed"
    assert repository.get(failed_id)["status"] == "failed"
    run_id = repository.create(request())

    async def interrupt():
        stream = repository.execute(run_id, None, ToolRegistry(20000))
        await anext(stream)
        await stream.aclose()

    asyncio.run(interrupt())
    assert repository.get(run_id)["status"] == "interrupted"
    with pytest.raises(ValueError, match="Only a waiting run"):
        collect(repository.resume(run_id, None, ToolRegistry(20000)))


def test_completed_run_with_explicit_human_input(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    original = request()
    original["definition"]["run_budget"]["max_delegations"] = 2
    run_id = repository.create(original)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"})

    async def execute():
        return [event async for event in repository.execute(
            run_id, provider, ToolRegistry(20000), human_handler=lambda checkpoint: {"text": "Reviewed final brief"},
        )]

    assert asyncio.run(execute())[-1]["state"]["brief"] == "Reviewed final brief"
    assert repository.get(run_id)["status"] == "completed"


def collect(stream):
    async def consume():
        return [event async for event in stream]
    return asyncio.run(consume())


@pytest.mark.parametrize("limit", [20, None])
def test_shared_state_is_not_bounded_by_tool_output_limit(limit):
    original = request()
    definition = original["definition"]
    definition["workflow"] = [definition["workflow"][0], {"id": "finish", "agent": "writer", "task": "Finish", "output": "brief"}]
    definition["run_config"]["max_tool_output_chars"] = limit
    provider = ScriptedProvider(scripts={"writer": [
        {"type": "final", "content": "Done", "text": "evidence " * 10000},
        {"type": "final", "content": "Done", "text": "Finished"},
    ]})
    tools = ToolRegistry(3)
    events = collect(run_application(definition, provider, tools, original["task"], inputs=original["inputs"]))
    assert events[-1]["status"] == "completed"
    assert "evidence " * 10000 in provider.prompts[1][1]
    assert tools.max_output_chars == 3


@pytest.mark.parametrize("repair", ["valid", "invalid", "tool"])
def test_output_repair_is_bounded_and_never_replays_tools(repair):
    original = request()
    definition = original["definition"]
    definition["workflow"] = [definition["workflow"][0]]
    definition["agents"][0]["tools"] = ["read_file"]
    definition["state"][0].update(type="records", merge="upsert", fields=[{"key": "acceptance_ids", "label": "Acceptance IDs", "type": "string", "required": True}])
    definition["views"][0]["layout"] = "cards"
    record = {"id": "check", "title": "Check", "body": "Supported", "status": "recorded", "source_refs": []}
    invalid = {"type": "final", "content": "Done", "records": [record]}
    corrected = {"type": "final", "content": "Done", "records": [{**record, "acceptance_ids": "AC-1"}]}
    calls = []

    async def read_file(**kwargs):
        calls.append("read")
        return "AC-1 is the supplied criterion"

    tools = ToolRegistry(20000)
    tools.register(Tool("read_file", "Read", {}, read_file, frozenset({Capability.READ})))
    response = corrected if repair == "valid" else invalid if repair == "invalid" else {"type": "tool", "name": "read_file", "arguments": {}}
    provider = ScriptedProvider(scripts={"writer": [{"type": "tool", "name": "read_file", "arguments": {}}, invalid, response, response]}, usage=Usage(input_tokens=2, output_tokens=1))
    events = collect(run_application(definition, provider, tools, original["task"], inputs=original["inputs"]))
    assert calls == ["read"]
    repairs = [event for event in events if event["type"] == "OutputRepairStarted"]
    assert len(repairs) == (1 if repair == "valid" else 2)
    assert "records[0]" in repairs[0]["reason"] and "acceptance_ids expects string" in repairs[0]["reason"]
    assert events[-1]["status"] == ("completed" if repair == "valid" else "failed")
    assert len(provider.prompts) == (3 if repair == "valid" else 4)
    assert '"acceptance_ids"' in provider.prompts[-1][1]
    assert not any("AC-1 is the supplied criterion" in json.dumps(event) for event in repairs)
    if repair == "valid":
        assert any(event["type"] == "OutputRepairCompleted" for event in events)
        assert events[-1]["state"]["brief"][0]["acceptance_ids"] == "AC-1"


@pytest.mark.parametrize("limit", [5, 40, None])
def test_application_tool_output_limit_is_run_scoped(limit):
    original = request()
    definition = original["definition"]
    definition["workflow"] = [definition["workflow"][0]]
    definition["agents"][0]["tools"] = ["read_file"]
    definition["run_config"]["max_tool_output_chars"] = limit

    async def read_file(**kwargs):
        return "x" * 30

    tools = ToolRegistry(3)
    tools.register(Tool("read_file", "Read", {}, read_file, frozenset({Capability.READ})))
    provider = ScriptedProvider(scripts={"writer": [
        {"type": "tool", "name": "read_file", "arguments": {}},
        {"type": "final", "content": "Done", "text": "Finished"},
    ]})
    events = collect(run_application(definition, provider, tools, original["task"], inputs=original["inputs"]))
    assert events[-1]["status"] == "completed"
    called = next(event for event in events if event["type"] == "ToolCalled")
    assert called["truncated"] == (limit == 5)
    assert tools.max_output_chars == 3


@pytest.mark.parametrize("failure", ["exception", "invalid_output", "stopped"])
def test_parallel_failure_identifies_responsible_stage(failure):
    from conclave.events import RunFinished
    from conclave.protocol import AgentResult, PrivateState

    original = request()
    definition = original["definition"]
    definition["workflow"] = [
        {"id": "draft", "agent": "writer", "task": "Draft", "output": "brief"},
        {"id": "sibling", "agent": "writer", "task": "Wait", "output": "brief"},
    ]
    definition["connections"] = []
    both_started = asyncio.Event()

    class ParallelLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, spec, prompt):
            if "STAGE TASK:\nWait" in prompt:
                both_started.set()
                await asyncio.Event().wait()
            await both_started.wait()
            if failure == "exception":
                raise RuntimeError("private credential must not leak")
            yield RunFinished(run_id="test", agent="writer", status="stopped" if failure == "stopped" else "completed",
                              result=AgentResult(content="Done", private=PrivateState(CONFIG), data={}))

    events = collect(run_application(definition, None, ToolRegistry(20000), original["task"], inputs=original["inputs"], loop=ParallelLoop))
    assert [event["stage"] for event in events if event["type"] == "StageStarted"] == ["draft", "sibling"]
    assert events[-1]["stage"] == "draft"
    assert events[-1]["status"] == ("stopped" if failure == "stopped" else "failed")
    assert "private credential" not in json.dumps(events)


@pytest.mark.parametrize("failure", ["exception", "invalid_output"])
def test_human_failure_identifies_responsible_stage(failure):
    original = request()
    definition = original["definition"]
    definition["workflow"] = [definition["workflow"][1]]

    def handler(review):
        if failure == "exception":
            raise RuntimeError("private host details")
        return {"records": []}

    events = collect(run_application(definition, None, ToolRegistry(20000), original["task"], inputs=original["inputs"], human_handler=handler))
    assert events[-1]["stage"] == "review"
    assert events[-1]["status"] == "failed"
    assert "private host details" not in json.dumps(events)


def with_final_stage():
    original = request()
    original["definition"]["workflow"].append({"id": "final", "agent": "writer", "task": "Finalize the reviewed brief", "output": "brief"})
    return original


def test_restart_submit_and_resume_without_repeating_completed_work(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"})
    tools = ToolRegistry(20000)
    run_id = repository.create(with_final_stage())
    collect(repository.execute(run_id, provider, tools))
    assert len(provider.prompts) == 1
    repository = RunRepository(path)
    review = repository.get(run_id)["reviews"][0]
    with pytest.raises(ValueError, match="Submit the pending review"):
        collect(repository.resume(run_id, provider, tools))
    repository.submit_review(run_id, review["id"], reviewer="owner@example.test", output={"text": "Explicit review: preserve the caveat"})
    repository = RunRepository(path)
    submitted = repository.get(run_id)["reviews"][0]
    assert submitted["reviewer"] == "owner@example.test" and submitted["submitted_at"]
    assert submitted["status"] == "submitted"
    events = collect(repository.resume(run_id, provider, tools))
    assert events[0]["type"] == "ApplicationResumed"
    assert events[0]["state"]["brief"] == "Draft"
    assert events[-1]["status"] == "completed"
    assert [event["stage"] for event in events if event["type"] == "StageStarted"] == ["final"]
    assert len(provider.prompts) == 2
    assert "Explicit review: preserve the caveat" in provider.prompts[-1][1]
    assert repository.get(run_id)["reviews"][0]["status"] == "consumed"
    changes = [item["event"] for item in repository.get(run_id)["events"] if item["event"]["type"] == "ApplicationStateChanged"]
    assert [event["version"] for event in changes] == [1, 2, 3]
    assert changes[-1]["checkpoint"]["budget"]["used"] == 2
    with pytest.raises(ValueError, match="Only a waiting run"):
        collect(repository.resume(run_id, provider, tools))


def test_invalid_duplicate_and_cross_run_reviews_do_not_change_waiting_run(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(request())
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"}), tools))
    review_id = repository.get(run_id)["reviews"][0]["id"]
    for reviewer, output in [("", {"text": "valid"}), ("person", {"text": " "}), ("person", {"records": []})]:
        with pytest.raises(ValueError):
            repository.submit_review(run_id, review_id, reviewer=reviewer, output=output)
    other_run = repository.create(request())
    with pytest.raises(ValueError):
        repository.submit_review(other_run, review_id, reviewer="person", output={"text": "valid"})
    assert repository.get(run_id)["reviews"][0]["status"] == "pending"
    repository.submit_review(run_id, review_id, reviewer="person", output={"text": "valid"})
    with pytest.raises(ValueError):
        repository.submit_review(run_id, review_id, reviewer="someone else", output={"text": "replacement"})
    assert repository.get(run_id)["reviews"][0]["reviewer"] == "person"


@pytest.mark.parametrize("maximum", [1, None])
def test_resume_restores_finite_and_unlimited_budgets(tmp_path, maximum):
    original = with_final_stage()
    original["definition"]["run_budget"]["max_delegations"] = maximum
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(original)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"}, usage=Usage(input_tokens=7, output_tokens=3))
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, provider, tools))
    waiting = repository.get(run_id)["events"][-1]["event"]["checkpoint"]
    assert waiting["budget"]["spent"]["tokens"] == 10
    review_id = repository.get(run_id)["reviews"][0]["id"]
    repository.submit_review(run_id, review_id, reviewer="person", output={"text": "Reviewed"})
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == ("stopped" if maximum == 1 else "completed")
    assert len(provider.prompts) == (1 if maximum == 1 else 2)


@pytest.mark.parametrize("decision", ["approved", "rejected", "changes_requested"])
def test_persisted_decision_controls_downstream_execution(tmp_path, decision):
    original = with_final_stage()
    original["definition"]["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "append", "statuses": ["approved", "rejected", "changes_requested"]})
    original["definition"]["workflow"][1]["output"] = "decision"
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(original)
    tools = ToolRegistry(20000)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"})
    collect(repository.execute(run_id, provider, tools))
    review_id = repository.get(run_id)["reviews"][0]["id"]
    output = {"records": [{"id": "decision", "title": "Review", "body": "My explicit decision", "status": decision, "source_refs": []}]}
    repository.submit_review(run_id, review_id, reviewer="person", output=output)
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == ("completed" if decision == "approved" else "stopped")
    assert len(provider.prompts) == (2 if decision == "approved" else 1)
    assert repository.get(run_id)["reviews"][0]["output"]["records"][0]["status"] == decision


def test_multiple_durable_reviews_resume_only_the_current_stage(tmp_path):
    original = with_final_stage()
    original["definition"]["workflow"].append({"id": "last_review", "agent": "reviewer", "task": "Final review", "output": "brief"})
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    run_id = repository.create(original)
    tools = ToolRegistry(20000)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"})
    collect(repository.execute(run_id, provider, tools))
    for expected_stage in ("review", "last_review"):
        repository = RunRepository(path)
        review = repository.get(run_id)["reviews"][-1]
        assert review["stage"] == expected_stage and review["status"] == "pending"
        repository.submit_review(run_id, review["id"], reviewer="person", output={"text": "Reviewed " + expected_stage})
        collect(repository.resume(run_id, provider, tools))
    record = repository.get(run_id)
    assert record["status"] == "completed"
    assert len(provider.prompts) == 2
    assert [review["status"] for review in record["reviews"]] == ["consumed", "consumed"]


def test_checkpoint_rejects_changed_request_and_invalid_accounting(tmp_path):
    original = request()
    tools = ToolRegistry(20000)
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft"})
    events = collect(run_application(original["definition"], provider, tools, original["task"], inputs=original["inputs"]))
    checkpoint = events[-1]["checkpoint"]
    for changed in ("task", "inputs", "definition", "budget", "patches"):
        candidate = copy.deepcopy(original)
        restored = copy.deepcopy(checkpoint)
        if changed == "task":
            candidate["task"] = "Different task"
        elif changed == "inputs":
            candidate["inputs"]["sources"] = "Different evidence"
        elif changed == "definition":
            candidate["definition"]["workflow"][0]["task"] = "Different instruction"
        elif changed == "budget":
            restored["budget"] = {"used": 0, "per_agent": {}, "spent": {}}
        else:
            restored["patches"] = {"review": "Out of order"}
        with pytest.raises(ValueError):
            collect(run_application(candidate["definition"], provider, tools, candidate["task"], inputs=candidate["inputs"], checkpoint=restored))
    assert len(provider.prompts) == 1


def test_human_review_does_not_authorize_a_write_after_resume(tmp_path):
    original = with_final_stage()
    original["definition"]["agents"][0]["tools"] = ["write_file"]
    original["definition"]["agents"].append({"id": "publisher", "instructions": "Prepare a final result", "tools": ["write_file"]})
    original["definition"]["workflow"][-1]["agent"] = "publisher"
    writes = []

    async def write_file():
        writes.append("written")
        return "Saved"

    tools = ToolRegistry(20000)
    tools.register(Tool("write_file", "Write a file", {"type": "object", "properties": {}}, write_file, frozenset({Capability.WRITE})))
    provider = ScriptedProvider(scripts={"writer": [
        {"type": "tool", "name": "write_file", "arguments": {}},
        {"type": "final", "content": "Done", "text": "Draft"},
    ], "publisher": [
        {"type": "tool", "name": "write_file", "arguments": {}},
        {"type": "final", "content": "Done", "text": "Final"},
    ]}, default={"type": "final", "content": "Done", "text": "Draft"})
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(original)
    collect(repository.execute(run_id, provider, tools, approver=lambda request: request.agent == "writer"))
    assert writes == ["written"]
    repository.submit_review(run_id, repository.get(run_id)["reviews"][0]["id"], reviewer="person", output={"text": "Approved draft"})
    events = collect(repository.resume(run_id, provider, tools))
    # The completed write is not repeated, and the new write is denied.
    assert writes == ["written"]
    assert any(event["type"] == "ToolDenied" and event["tool"] == "write_file" for event in events)


def test_resume_preserves_branch_isolation_and_append_patches(tmp_path):
    original = with_final_stage()
    definition = original["definition"]
    definition["state"].append({"key": "findings", "type": "records", "merge": "append", "statuses": []})
    definition["workflow"][0]["output"] = "findings"
    definition["workflow"].insert(1, {"id": "sibling", "agent": "writer", "task": "Independent branch", "output": "findings"})
    definition["connections"] = [{"from": "draft", "to": "review"}, {"from": "review", "to": "final"}, {"from": "sibling", "to": "final"}]

    def result(identifier):
        return {"type": "final", "content": "Done", "records": [{"id": identifier, "title": identifier, "body": identifier, "status": "recorded", "source_refs": []}]}

    provider = ScriptedProvider(scripts={"writer": [result("branch_a"), result("branch_b"), {"type": "final", "content": "Done", "text": "Final"}]})
    tools = ToolRegistry(20000)
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    run_id = repository.create(original)
    collect(repository.execute(run_id, provider, tools))
    review = repository.get(run_id)["reviews"][0]
    assert [record["id"] for record in review["request"]["state"]["findings"]] == ["branch_a"]
    assert "branch_a" not in provider.prompts[1][1]
    repository = RunRepository(path)
    repository.submit_review(run_id, review["id"], reviewer="person", output={"text": "Reviewed"})
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "completed"
    assert [record["id"] for record in events[-1]["state"]["findings"]] == ["branch_a", "branch_b"]
    assert all(record["id"] in provider.prompts[-1][1] for record in events[-1]["state"]["findings"])
    assert len(provider.prompts) == 3


def test_review_migration_preserves_existing_local_run_records(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE studio_runs (id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE studio_run_events (run_id TEXT NOT NULL REFERENCES studio_runs(id), sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, sequence));
        """)
        connection.execute("INSERT INTO studio_runs VALUES (?, ?, ?, ?)", ("legacy", json.dumps(request()), "pending", "2026-10-05"))
    restored = RunRepository(path).get("legacy")
    assert restored["request"] == request()
    assert restored["status"] == "pending"
    assert restored["events"] == restored["reviews"] == []


def test_custom_record_fields_survive_live_execution_review_and_restart(tmp_path):
    value = request()
    collection = {"key": "brief", "type": "records", "merge": "upsert", "statuses": [], "fields": [
        {"key": "score", "label": "Score", "type": "number", "required": True},
        {"key": "verified", "label": "Verified", "type": "boolean", "required": True},
        {"key": "observed", "label": "Observed date", "type": "date", "required": False},
        {"key": "category", "label": "Category", "type": "enum", "required": True, "options": ["a", "b"]},
    ]}
    value["definition"]["state"] = [collection]
    value["definition"]["views"][0]["layout"] = "table"
    record = {"id": "observation", "title": "Observation", "body": "Source observation", "status": "recorded", "source_refs": [],
              "score": 0, "verified": False, "observed": "2024-02-29", "category": "a"}
    class FieldProvider(ScriptedProvider):
        async def complete(self, messages):
            assert "Additional typed record fields" in messages[0]["content"]
            assert '"score"' in messages[0]["content"]
            return await super().complete(messages)

    provider = FieldProvider(default={"type": "final", "content": "Done", "records": [record]})
    path = tmp_path / "fields.sqlite"
    repository = RunRepository(path)
    run_id = repository.create(value)

    async def exercise():
        events = [event async for event in repository.execute(run_id, provider, ToolRegistry(20000))]
        state = next(event["state"] for event in events if event["type"] == "ApplicationStateChanged")
        assert state["brief"][0]["score"] == 0 and state["brief"][0]["verified"] is False
        restored = RunRepository(path)
        review = restored.get(run_id)["reviews"][0]
        invalid = {**record, "score": True}
        with pytest.raises(ValueError, match="record field"):
            restored.submit_review(run_id, review["id"], reviewer="operator", output={"records": [invalid]})
        restored.submit_review(run_id, review["id"], reviewer="operator", output={"records": [{**record, "score": 2}]})
        resumed = [event async for event in restored.resume(run_id, provider, ToolRegistry(20000))]
        assert resumed[-1]["state"]["brief"][0]["score"] == 2
        assert restored.get(run_id)["status"] == "completed"
        assert len(provider.prompts) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("field,value", [
    ({"key": "score", "label": "Score", "type": "number", "required": True}, True),
    ({"key": "date", "label": "Date", "type": "date", "required": True}, "2026-02-30"),
    ({"key": "choice", "label": "Choice", "type": "enum", "required": True, "options": ["a"]}, "b"),
    ({"key": "score", "label": "Score", "type": "number", "required": True}, None),
])
def test_invalid_custom_record_values(field, value):
    from conclave.application.definition import record_fields
    with pytest.raises(ValueError):
        record_fields({field["key"]: value}, [field])
