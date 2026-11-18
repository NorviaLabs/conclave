"""A step can run only when an earlier step's output says so.

`condition` on a step names one of the steps it depends on, a field of that
step's output, an operator and a value. When it does not hold, the step is
skipped, and so is every step only it leads to; a step that joins a skipped
branch with one that ran still runs. A skip costs nothing, asks nobody, and is
recorded in words.
"""

import asyncio
import copy
import json

import pytest

from conclave import RunBudget, SharedStore, StoreSchema, ToolRegistry
from conclave.application.definition import (_request_fingerprint, _restore_checkpoint, condition_holds,
                                             run_application, validate_definition)
from conclave.application.runs import RunRepository
from conftest import CONFIG, ScriptedProvider
from test_studio_runs import collect

TYPES = ["bug", "feature", "question", "regression"]


def triage(kind, status="open"):
    return {"type": "final", "content": "Done",
            "records": [{"id": "t1", "title": "Issue", "body": "Triaged", "status": status, "type": kind, "source_refs": []}]}


def text(value):
    return {"type": "final", "content": "Done", "text": value}


def plan(**conditions):
    """triage -> reproduce -> fix -> summary, and triage -> proposal -> summary."""
    agents = ["triager", "engineer", "writer", "fixer", "summarizer"]
    definition = {
        "version": 1, "name": "Issue routing",
        "agents": [{"id": agent, "instructions": "Work", "tools": []} for agent in agents],
        "state": [
            {"key": "triage", "type": "records", "merge": "upsert", "statuses": ["open", "closed"],
             "fields": [{"key": "type", "label": "Type", "type": "enum", "required": True, "options": list(TYPES)},
                        {"key": "severity", "label": "Severity", "type": "number", "required": False},
                        {"key": "urgent", "label": "Urgent", "type": "boolean", "required": False},
                        {"key": "area", "label": "Area", "type": "string", "required": False}]},
            {"key": "notes", "type": "text", "merge": "replace", "statuses": []},
        ],
        "workflow": [
            {"id": "triage", "name": "Triage the issue", "agent": "triager", "task": "Triage", "output": "triage"},
            {"id": "reproduce", "name": "Reproduce the bug", "agent": "engineer", "task": "Reproduce", "output": "notes",
             "condition": {"step": "triage", "field": "type", "operator": "in", "value": ["bug", "regression"]}},
            {"id": "proposal", "name": "Draft a proposal", "agent": "writer", "task": "Propose", "output": "notes",
             "condition": {"step": "triage", "field": "type", "operator": "equals", "value": "feature"}},
            {"id": "fix", "name": "Write the fix", "agent": "fixer", "task": "Fix", "output": "notes"},
            {"id": "summary", "name": "Summarize", "agent": "summarizer", "task": "Summarize", "output": "notes"},
        ],
        "connections": [{"from": "triage", "to": "reproduce"}, {"from": "triage", "to": "proposal"},
                        {"from": "reproduce", "to": "fix"}, {"from": "fix", "to": "summary"}, {"from": "proposal", "to": "summary"}],
        "views": [{"id": "triage_view", "collection": "triage", "layout": "cards"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    steps = {step["id"]: step for step in definition["workflow"]}
    for step_id, condition in conditions.items():
        if condition is None:
            steps[step_id].pop("condition", None)
        else:
            steps[step_id]["condition"] = condition
    return definition


def provider_for(kind, **extra):
    return ScriptedProvider({"triager": [triage(kind, **extra)], "engineer": [text("Reproduced")], "writer": [text("Proposal")],
                             "fixer": [text("Fixed")], "summarizer": [text("Summary")]})


def run(definition, provider, **options):
    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Handle the issue", **options)]
    return asyncio.run(go())


def started(events):
    return [event["stage"] for event in events if event["type"] == "StageStarted"]


def skipped(events):
    return {event["stage"]: event for event in events if event["type"] == "StageSkipped"}


def asked(provider):
    return sorted({name for name, _ in provider.prompts})


# --- What a condition says ---------------------------------------------------------


RECORDS = [{"id": "a", "title": "A", "body": "", "status": "Open", "type": "feature", "severity": 3, "urgent": True, "area": "Billing page"},
           {"id": "b", "title": "B", "body": "", "status": "closed", "type": "bug", "severity": 1, "urgent": False}]


@pytest.mark.parametrize("condition, output, holds", [
    ({"field": "type", "operator": "equals", "value": "bug"}, RECORDS, True),          # any record
    ({"field": "type", "operator": "equals", "value": " BUG "}, RECORDS, True),        # case and spaces
    ({"field": "type", "operator": "equals", "value": "question"}, RECORDS, False),
    ({"field": "type", "operator": "not_equals", "value": "feature"}, RECORDS, True),   # b is not a feature
    ({"field": "type", "operator": "not_equals", "value": "feature"}, RECORDS[:1], False),
    ({"field": "type", "operator": "in", "value": ["bug", "regression"]}, RECORDS, True),
    ({"field": "type", "operator": "in", "value": ["question"]}, RECORDS, False),
    ({"field": "type", "operator": "not_in", "value": ["feature"]}, RECORDS[:1], False),
    ({"field": "status", "operator": "equals", "value": "open"}, RECORDS, True),
    ({"field": "area", "operator": "contains", "value": "billing"}, RECORDS, True),
    ({"field": "area", "operator": "contains", "value": "login"}, RECORDS, False),
    ({"field": "severity", "operator": "equals", "value": "3"}, RECORDS, True),        # numbers by their text
    ({"field": "severity", "operator": "equals", "value": "3.0"}, RECORDS, True),
    ({"field": "urgent", "operator": "equals", "value": "true"}, RECORDS, True),
    ({"field": "area", "operator": "equals", "value": "x"}, [RECORDS[1]], False),      # missing field
    ({"field": "type", "operator": "not_in", "value": ["bug"]}, [], False),             # nothing to check
    ({"field": "text", "operator": "contains", "value": "crash"}, "It CRASHES on save", True),
    ({"field": "text", "operator": "equals", "value": "yes"}, " Yes \n", True),
    ({"field": "text", "operator": "not_equals", "value": "yes"}, "no", True),
    ({"field": "text", "operator": "contains", "value": "crash"}, "", False),
    ({"field": "text", "operator": "contains", "value": "crash"}, None, False),          # never produced
])
def test_condition_holds_for_each_operator(condition, output, holds):
    assert condition_holds({"step": "triage", **condition}, output) is holds


# --- Routing -------------------------------------------------------------------------


def test_condition_skips_the_step_and_every_step_only_it_leads_to():
    provider = provider_for("feature")
    events = run(plan(), provider)
    assert events[-1]["status"] == "completed"
    assert started(events) == ["triage", "proposal", "summary"]
    gone = skipped(events)
    assert set(gone) == {"reproduce", "fix"}
    assert gone["reproduce"]["condition"]["step"] == "triage" and "condition" not in gone["fix"]
    assert asked(provider) == ["summarizer", "triager", "writer"], "a skipped step never reaches a model"
    assert events[-1]["state"]["notes"] == "Summary"


def test_condition_routes_a_bug_down_the_other_branch():
    provider = provider_for("bug")
    events = run(plan(), provider)
    assert events[-1]["status"] == "completed"
    assert started(events) == ["triage", "reproduce", "fix", "summary"]
    assert set(skipped(events)) == {"proposal"}


def test_skip_reasons_are_in_words():
    events = run(plan(), provider_for("feature"))
    gone = skipped(events)
    assert gone["reproduce"]["reason"] == "Triage the issue: type was “feature”, not bug or regression"
    assert gone["fix"]["reason"] == "Only Reproduce the bug leads here, and it was skipped"


def test_a_join_with_every_branch_skipped_is_skipped_too():
    definition = plan(proposal={"step": "triage", "field": "type", "operator": "equals", "value": "question"})
    events = run(definition, provider_for("feature"))
    assert events[-1]["status"] == "completed"
    assert started(events) == ["triage"]
    assert skipped(events)["summary"]["reason"] == "Only Write the fix and Draft a proposal lead here, and they were skipped"


def test_condition_on_a_skipped_step_does_not_hold():
    definition = plan(summary={"step": "reproduce", "field": "text", "operator": "contains", "value": "crash"})
    events = run(definition, provider_for("feature"))
    assert "summary" in skipped(events)
    assert skipped(events)["summary"]["reason"] == "Reproduce the bug was skipped, so its result is unknown"
    assert events[-1]["status"] == "completed"


def test_condition_on_a_status_and_a_not_in_list():
    definition = plan(proposal={"step": "triage", "field": "status", "operator": "not_in", "value": ["closed"]})
    events = run(definition, provider_for("bug", status="closed"))
    assert "proposal" in skipped(events)
    assert skipped(events)["proposal"]["reason"] == "Triage the issue: status was “closed”, which is excluded"


def test_skipped_review_asks_nobody():
    definition = plan()
    definition["agents"].append({"id": "lead", "kind": "human", "instructions": "Approve", "tools": []})
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert", "statuses": ["approved", "rejected"]})
    definition["workflow"].append({"id": "approve", "name": "Approve the fix", "agent": "lead", "task": "Approve", "output": "decision"})
    definition["connections"].append({"from": "fix", "to": "approve"})
    events = run(definition, provider_for("feature"))
    assert not any(event["type"] == "HumanInputRequested" for event in events)
    assert "approve" in skipped(events) and events[-1]["status"] == "completed"


def test_skipping_costs_no_budget():
    definition = plan()
    definition["run_budget"]["max_delegations"] = 3   # triage, proposal and summary: nothing for skipped steps
    events = run(definition, provider_for("feature"))
    assert events[-1]["status"] == "completed"


def test_condition_on_text_output_routes_work():
    definition = plan(fix={"step": "reproduce", "field": "text", "operator": "contains", "value": "reproduced"})
    provider = provider_for("bug")
    events = run(definition, provider)
    assert "fix" in started(events) and events[-1]["status"] == "completed"


# --- Definitions ---------------------------------------------------------------------


@pytest.mark.parametrize("step, condition, message", [
    ("reproduce", {"step": "proposal", "field": "text", "operator": "equals", "value": "x"}, "depends on"),
    ("reproduce", {"step": "missing", "field": "type", "operator": "equals", "value": "bug"}, "depends on"),
    ("reproduce", {"step": "triage", "field": "colour", "operator": "equals", "value": "red"}, "field"),
    ("reproduce", {"step": "triage", "field": "text", "operator": "equals", "value": "x"}, "field"),
    ("fix", {"step": "reproduce", "field": "type", "operator": "equals", "value": "x"}, "field"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "matches", "value": "bug"}, "operator"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "equals", "value": "crash"}, "option"),
    ("reproduce", {"step": "triage", "field": "status", "operator": "equals", "value": "done"}, "option"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "in", "value": "bug"}, "value"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "equals", "value": ["bug"]}, "value"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "in", "value": []}, "value"),
    ("reproduce", {"step": "triage", "field": "area", "operator": "in", "value": [str(n) for n in range(21)]}, "value"),
    ("reproduce", {"step": "triage", "field": "area", "operator": "contains", "value": "x" * 201}, "value"),
    ("reproduce", {"step": "triage", "field": "area", "operator": "contains", "value": " "}, "value"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "in", "value": ["bug", "bug"]}, "value"),
    ("reproduce", {"step": "triage", "field": "type", "operator": "equals", "value": "bug", "note": "x"}, "condition"),
    ("reproduce", "type is bug", "condition"),
])
def test_invalid_conditions_are_refused(step, condition, message):
    with pytest.raises(ValueError, match=message):
        validate_definition(plan(**{step: condition}))


@pytest.mark.parametrize("condition", [
    {"step": "triage", "field": "severity", "operator": "equals", "value": "3"},
    {"step": "triage", "field": "urgent", "operator": "equals", "value": "true"},
    {"step": "triage", "field": "area", "operator": "contains", "value": "billing"},
    {"step": "triage", "field": "status", "operator": "not_in", "value": ["closed"]},
])
def test_valid_conditions_are_accepted(condition):
    validate_definition(plan(fix={**condition}))


def test_a_definition_without_conditions_is_unchanged():
    definition = plan(reproduce=None, proposal=None)
    events = run(definition, provider_for("feature"))
    assert not skipped(events) and events[-1]["status"] == "completed"


# --- Durable skips ---------------------------------------------------------------------


def with_final_review():
    definition = plan()
    definition["agents"].append({"id": "lead", "kind": "human", "instructions": "Approve", "tools": []})
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert", "statuses": ["approved", "rejected"]})
    definition["workflow"].append({"id": "approve", "name": "Approve", "agent": "lead", "task": "Approve", "output": "decision"})
    definition["connections"].append({"from": "summary", "to": "approve"})
    return {"version": 1, "application_id": "routing", "revision": 1, "definition": definition, "task": "Handle the issue", "inputs": {}}


def test_skipped_steps_survive_a_stop_and_resume(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    run_id = repository.create(with_final_review())
    provider = provider_for("feature")
    events = collect(repository.execute(run_id, provider, ToolRegistry(20000)))
    assert events[-1]["status"] == "waiting_for_human"
    assert events[-1]["checkpoint"]["skipped"] == ["reproduce", "fix"]
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="ana",
                             output={"records": [{"id": "d", "title": "Ok", "body": "Approved", "status": "approved", "source_refs": []}]})
    repository = RunRepository(path)
    events = collect(repository.resume(run_id, provider, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed"
    assert not any(event["type"] in {"StageStarted", "StageSkipped"} for event in events), "skips are not decided again"
    assert asked(provider) == ["summarizer", "triager", "writer"]


def test_a_tampered_skipped_checkpoint_is_refused():
    value = with_final_review()
    definition = value["definition"]
    events = run(definition, provider_for("feature"))
    checkpoint = next(event for event in reversed(events) if "checkpoint" in event)["checkpoint"]
    fingerprint = _request_fingerprint(definition, "Handle the issue", {})

    def restore(changed):
        async def go():
            budget = RunBudget(**definition["run_budget"])
            store = SharedStore(StoreSchema(keys=frozenset(item["key"] for item in definition["state"])),
                                {item["key"]: "" if item["type"] == "text" else [] for item in definition["state"]})
            return await _restore_checkpoint(changed, fingerprint, definition, store, budget)
        return asyncio.run(go())

    restore(checkpoint)
    for tamper in (lambda c: c.update(skipped=["triage"]),           # it has an output
                   lambda c: c.update(skipped=["missing"]),
                   lambda c: c.update(skipped="reproduce"),
                   lambda c: c.update(skipped=["fix"]),               # its prerequisite is neither done nor skipped
                   lambda c: c.update(skipped=["reproduce", "reproduce", "fix"])):
        changed = copy.deepcopy(checkpoint)
        tamper(changed)
        with pytest.raises(ValueError, match="skip"):
            restore(changed)


def test_condition_is_checked_again_after_a_revision():
    definition = plan(proposal=None)
    definition["workflow"] = [step for step in definition["workflow"] if step["id"] not in {"proposal", "fix", "summary"}]
    definition["agents"].append({"id": "lead", "kind": "human", "instructions": "Review", "tools": []})
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert",
                                "statuses": ["approved", "changes_requested"]})
    definition["workflow"].append({"id": "review", "name": "Review triage", "agent": "lead", "task": "Review", "output": "decision",
                                   "revises": ["triage"]})
    definition["connections"] = [{"from": "triage", "to": "reproduce"}, {"from": "triage", "to": "review"},
                                 {"from": "reproduce", "to": "review"}]
    provider = ScriptedProvider({"triager": [triage("feature"), triage("bug")], "engineer": [text("Reproduced")]})
    answers = [{"records": [{"id": "d", "title": "R", "body": "This is a bug", "status": "changes_requested", "source_refs": []}]},
               {"records": [{"id": "d", "title": "R", "body": "Good", "status": "approved", "source_refs": []}]}]
    events = run(definition, provider, human_handler=lambda review: answers.pop(0))
    assert events[-1]["status"] == "completed"
    kinds = [(event["type"], event["stage"]) for event in events if event["type"] in {"StageSkipped", "StageStarted"} and event["stage"] == "reproduce"]
    assert kinds == [("StageSkipped", "reproduce"), ("StageStarted", "reproduce")]
    revision = next(event for event in events if event["type"] == "RevisionRequested")
    assert "reproduce" in revision["reset"]
    assert "skipped" not in revision["checkpoint"] or "reproduce" not in revision["checkpoint"]["skipped"]


# --- Step tests and REST ------------------------------------------------------------------


def test_a_step_test_runs_a_conditional_step_and_reports_the_condition(tmp_path):
    from conclave.application.step_execution import execute_test
    from conclave.application.step_testing import StepTestStore, authored_fixture

    definition = plan()
    record_triage = [{"id": "t1", "title": "Issue", "body": "Triaged", "status": "open", "type": "feature", "source_refs": []}]
    fixture = authored_fixture(definition, "routing", 1, "reproduce", upstream={"triage": record_triage})
    store = StepTestStore(tmp_path / "tests.sqlite")
    record = {"id": "test-1", "scope": "step", "events": [], "request": {"definition": definition}}
    provider = ScriptedProvider({"engineer": [text("Reproduced")]})
    asyncio.run(execute_test(store, "routing", "ana", record, fixture, provider, ToolRegistry(20000)))
    assert record["status"] == "completed" and record["output"] == "Reproduced", "the tested step runs anyway"
    assert record["condition"] == {"holds": False, "reason": "Triage the issue: type was “feature”, not bug or regression"}


def test_a_skipped_branch_completes_over_rest(tmp_path):
    from test_api import make_api

    async def exercise():
        api = make_api(tmp_path, provider_for("feature"))
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": plan()})
        assert status == 201, saved
        status, record = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"],
                                                                   "revision": saved["revision"], "task": "Handle it", "inputs": {}})
        assert status == 201
        await api.host.until_idle(record["id"])
        _, record = await api.dispatch("GET", f"/api/runs/{record['id']}")
        assert record["status"] == "completed"
        assert [item["event"]["stage"] for item in record["events"] if item["event"]["type"] == "StageSkipped"] == ["reproduce", "fix"]
        await api.host.close()
    asyncio.run(exercise())


# --- Security audit (C1, C2, C3) --------------------------------------------------------


def gated_plan():
    """triage -> security review (only for bugs) -> merge, and triage -> tests -> merge -> announce."""
    definition = plan(reproduce=None, proposal=None)
    definition["workflow"] = [step for step in definition["workflow"] if step["id"] in {"triage", "fix", "summary"}]
    definition["workflow"][1]["name"], definition["workflow"][2]["name"] = "Merge", "Announce"
    definition["agents"].append({"id": "lead", "kind": "human", "instructions": "Approve", "tools": []})
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert", "statuses": ["approved", "rejected"]})
    definition["workflow"].insert(1, {"id": "review", "name": "Security review", "agent": "lead", "task": "Approve", "output": "decision",
                                      "condition": {"step": "triage", "field": "type", "operator": "equals", "value": "bug"}})
    definition["workflow"].insert(1, {"id": "tests", "name": "Run tests", "agent": "engineer", "task": "Test", "output": "notes"})
    definition["connections"] = [{"from": "triage", "to": "review"}, {"from": "triage", "to": "tests"}, {"from": "review", "to": "fix"},
                                 {"from": "tests", "to": "fix"}, {"from": "fix", "to": "summary"}, {"from": "tests", "to": "summary"}]
    return definition


def test_a_skipped_review_never_lets_work_past_it():
    """A condition decided by model output must not route work around a person."""
    provider = ScriptedProvider({"triager": [triage("question")], "engineer": [text("ok")], "fixer": [text("merged")], "summarizer": [text("sent")]})
    events = run(gated_plan(), provider)
    assert events[-1]["status"] == "completed"
    assert started(events) == ["triage", "tests"], "nothing after the skipped review runs, even where another branch joins it"
    gone = skipped(events)
    assert set(gone) == {"review", "fix", "summary"}
    assert gone["fix"]["reason"] == "Security review was skipped, and nothing runs after a review that did not happen"
    assert gone["summary"]["reason"] == "Security review was skipped, and nothing runs after a review that did not happen"
    assert "fixer" not in asked(provider) and "summarizer" not in asked(provider)


def test_a_review_that_runs_lets_the_join_run():
    provider = ScriptedProvider({"triager": [triage("bug")], "engineer": [text("ok")], "fixer": [text("merged")], "summarizer": [text("sent")]})
    answer = {"records": [{"id": "d", "title": "Ok", "body": "Approved", "status": "approved", "source_refs": []}]}
    events = run(gated_plan(), provider, human_handler=lambda review: answer)
    assert events[-1]["status"] == "completed" and started(events) == ["triage", "tests", "fix", "summary"]


def test_a_checkpoint_cannot_skip_a_step_whose_condition_held(tmp_path):
    """A well-formed but wrong `skipped` (say, a review marked skipped) is refused on restore."""
    repository = RunRepository(tmp_path / "runs.sqlite")
    value = {"version": 1, "application_id": "gate", "revision": 1, "definition": gated_plan(), "task": "Handle the issue", "inputs": {}}
    run_id = repository.create(value)
    provider = ScriptedProvider({"triager": [triage("bug")], "engineer": [text("ok")], "fixer": [text("merged")], "summarizer": [text("sent")]})
    events = collect(repository.execute(run_id, provider, ToolRegistry(20000)))
    assert events[-1]["status"] == "waiting_for_human" and "skipped" not in events[-1]["checkpoint"]
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="ana",
                             output={"records": [{"id": "d", "title": "No", "body": "Hold on", "status": "rejected", "source_refs": []}]})
    with repository._connect() as connection:
        connection.execute("UPDATE studio_run_events SET payload = json_set(payload, '$.checkpoint.skipped', json('[\"review\"]')) "
                           "WHERE run_id = ? AND json_type(payload, '$.checkpoint') IS NOT NULL", (run_id,))
    events = collect(repository.resume(run_id, provider, ToolRegistry(20000)))
    assert events[-1]["status"] == "stopped" and "could not be restored" in events[-1]["reason"]
    assert "fixer" not in asked(provider), "nothing ran past the review"


def test_a_checkpoint_skip_is_derived_again_on_restore():
    definition = plan()
    events = run(definition, provider_for("feature"))
    checkpoint = next(event for event in reversed(events) if "checkpoint" in event)["checkpoint"]
    fingerprint = _request_fingerprint(definition, "Handle the issue", {})

    def restore(changed):
        async def go():
            budget = RunBudget(**definition["run_budget"])
            store = SharedStore(StoreSchema(keys=frozenset(item["key"] for item in definition["state"])),
                                {item["key"]: "" if item["type"] == "text" else [] for item in definition["state"]})
            return await _restore_checkpoint(changed, fingerprint, definition, store, budget)
        return asyncio.run(go())

    restore(checkpoint)
    wrong = copy.deepcopy(checkpoint)
    wrong["skipped"] = ["reproduce", "fix"]
    wrong["patches"].pop("proposal")
    wrong["patches"].pop("summary")
    wrong["skipped"].append("proposal")   # the condition held: triage said feature
    with pytest.raises(ValueError, match="skip"):
        restore(wrong)


def test_a_skip_reason_quotes_a_long_value_briefly():
    long = "x" * 19990
    provider = ScriptedProvider({"triager": [{"type": "final", "content": "Done", "records": [
        {"id": f"t{index}", "title": "I", "body": "b", "status": "open", "type": "feature", "area": long + str(index), "source_refs": []}
        for index in range(6)]}], "writer": [text("p")], "summarizer": [text("s")]})
    definition = plan(reproduce={"step": "triage", "field": "area", "operator": "contains", "value": "billing"})
    reason = skipped(run(definition, provider))["reproduce"]["reason"]
    assert len(reason) < 600, len(reason)
    assert reason.startswith("Triage the issue: area was “" + "x" * 79 + "…”") and reason.endswith("and more, which does not contain “billing”")


# --- Performance audit (PF1, PF2) --------------------------------------------------------


def fan_plan(middles=10, calls=10):
    """root -> m0..mN -> sink, one slot: the middles wait while each other works."""
    from conclave import Capability, Tool

    names = [f"m{index}" for index in range(middles)]
    steps = ["root", *names, "sink"]
    definition = {
        "version": 1, "name": "Fan", "agents": [{"id": "worker", "instructions": "Work", "tools": ["look"]}],
        "state": [{"key": "notes", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": step, "agent": "worker", "task": "Do " + step, "output": "notes"} for step in steps],
        "connections": [{"from": "root", "to": name} for name in names] + [{"from": name, "to": "sink"} for name in names],
        "views": [{"id": "v", "collection": "notes", "layout": "prose"}],
        "run_config": {**dict(vars(CONFIG)), "fan_out_limit": 1, "max_tool_steps": calls + 2},
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    # Every middle but the last runs only when the root said "go".
    for step in definition["workflow"][1:-2]:
        step["condition"] = {"step": "root", "field": "text", "operator": "contains", "value": "go"}

    async def look(**_):
        return "ok"
    tools = ToolRegistry(20000)
    tools.register(Tool(name="look", description="look", parameters={}, func=look, capabilities=frozenset({Capability.READ})))
    script = ([{"type": "tool", "name": "look", "arguments": {}}] * calls + [text("go on")]) * len(steps)
    return definition, tools, ScriptedProvider({"worker": script})


def count_skip_decisions(monkeypatch):
    import conclave.application.definition as module

    counted = {"calls": 0}
    real = module._skip_reason

    def counting(*args, **kwargs):
        counted["calls"] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "_skip_reason", counting)
    return counted


def test_skip_decisions_are_made_once_per_step(monkeypatch):
    definition, tools, provider = fan_plan()
    counted = count_skip_decisions(monkeypatch)

    async def go():
        return [event async for event in run_application(definition, provider, tools, "Work")]
    events = asyncio.run(go())
    assert events[-1]["status"] == "completed"
    assert len(events) > 200, "many events, each one a turn of the run loop"
    # root is decided when the run starts, then every other step once, when it becomes ready.
    assert counted["calls"] <= len(definition["workflow"]), counted


def test_a_revision_decides_its_reset_steps_again(monkeypatch):
    counted = count_skip_decisions(monkeypatch)
    test_condition_is_checked_again_after_a_revision()
    # triage, reproduce and review: once each, then again after the revision resets them.
    assert counted["calls"] <= 6, counted


def test_skip_decisions_do_not_walk_the_plan_each_time(monkeypatch):
    """A step's earlier steps are worked out once per run, not once per decision."""
    import conclave.application.definition as module

    definition, tools, provider = fan_plan()
    validate = module.validate_definition
    monkeypatch.setattr(module, "validate_definition", lambda value: None)   # count only the run's own walks
    validate(definition)
    walks = {"calls": 0}
    real = module._ancestors

    def counting(*args, **kwargs):
        walks["calls"] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "_ancestors", counting)

    async def go():
        return [event async for event in run_application(definition, provider, tools, "Work")]
    assert asyncio.run(go())[-1]["status"] == "completed"
    assert walks["calls"] == 0, "no review was skipped, so no step needs its earlier steps worked out"


def test_a_skipped_review_works_out_each_steps_earlier_steps_once(monkeypatch):
    import conclave.application.definition as module

    walks = {"calls": 0}
    real = module._ancestors

    def counting(*args, **kwargs):
        walks["calls"] += 1
        return real(*args, **kwargs)
    provider = ScriptedProvider({"triager": [triage("question")], "engineer": [text("ok")]})
    definition = gated_plan()
    validate_definition(definition)
    monkeypatch.setattr(module, "validate_definition", lambda value: None)
    monkeypatch.setattr(module, "_ancestors", counting)
    events = run(definition, provider)
    assert set(skipped(events)) == {"review", "fix", "summary"}
    assert walks["calls"] <= len(definition["workflow"]), walks
