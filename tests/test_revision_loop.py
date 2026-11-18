"""A reviewer who asks for changes gets them, and is asked again.

`changes_requested` on a decision sends the reviewed step back to work with the
reviewer's comment, then returns the review to the same person. `rejected` still
ends the run, and so does a request for changes with no rounds left or nothing
to revise. Every round is durable: it survives a restart, a stop and a resume.
"""

import asyncio
import copy
import json
import sqlite3
from contextlib import closing

import pytest

from conclave import ProviderError, ToolRegistry
from conclave.application.definition import _restore_checkpoint, run_application, validate_definition
from conclave.application.runs import RunRepository
from conclave.application.service import public_record
from conftest import CONFIG, ScriptedProvider
from test_studio_runs import collect, request

STATUSES = ["proposed", "approved", "rejected", "changes_requested"]
COMMENT = "The pricing section cites no source. Add the 2025 price list."


def reviewed(**review):
    """draft (AI) -> review (human decision)."""
    value = request()
    definition = value["definition"]
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert", "statuses": list(STATUSES)})
    definition["views"].append({"id": "decision_view", "collection": "decision", "layout": "cards"})
    definition["workflow"][1].update(output="decision", **review)
    return value


def decision(status, body=COMMENT):
    return {"records": [{"id": "decision", "title": "Review", "body": body, "status": status, "source_refs": []}]}


def drafts(*texts):
    return ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": text} for text in texts]})


def answers(*outputs):
    queue = list(outputs)
    asked = []

    def handler(review):
        asked.append(review)
        return queue.pop(0)
    handler.asked = asked
    return handler


def run(definition, provider, handler, **options):
    async def go():
        return [event async for event in run_application(definition, provider, ToolRegistry(20000), "Write the brief",
                                                         inputs={"sources": "Evidence"}, human_handler=handler, **options)]
    return asyncio.run(go())


def writer_prompts(provider):
    return [prompt for name, prompt in provider.prompts if name == "writer"]


def test_changes_requested_reruns_the_reviewed_step_with_the_comment_and_asks_again():
    definition = reviewed()["definition"]
    provider = drafts("Draft one", "Draft two")
    handler = answers(decision("changes_requested"), decision("approved", "Good now"))
    events = run(definition, provider, handler)

    assert events[-1]["status"] == "completed"
    revision = next(event for event in events if event["type"] == "RevisionRequested")
    assert revision["stage"] == "review" and revision["round"] == 1
    assert revision["revises"] == ["draft"] and revision["comment"] == COMMENT
    assert "checkpoint" in revision
    assert [event["stage"] for event in events if event["type"] == "StageStarted"] == ["draft", "draft"]

    prompts = writer_prompts(provider)
    assert len(prompts) == 2
    assert COMMENT not in prompts[0]
    assert "REQUESTED CHANGES" in prompts[1] and COMMENT in prompts[1]
    assert "Draft one" in prompts[1], "the step sees what it wrote last time"

    # Asked again, told which round this is and what was asked before.
    assert len(handler.asked) == 2
    assert "revision" not in handler.asked[0]
    assert handler.asked[1]["revision"]["round"] == 2
    assert handler.asked[1]["revision"]["history"] == [{"round": 1, "comment": COMMENT, "revised": ["draft"]}]
    assert handler.asked[1]["state"]["brief"] == "Draft two"

    final = events[-1]["state"]
    assert final["brief"] == "Draft two"
    assert [record["status"] for record in final["decision"]] == ["approved"]


def test_changes_requested_revision_rounds_are_unlimited_without_max_revisions():
    definition = reviewed()["definition"]
    provider = drafts("One", "Two", "Three", "Four")
    handler = answers(*[decision("changes_requested", f"Round {n}") for n in (1, 2, 3)], decision("approved", "Fine"))
    events = run(definition, provider, handler)
    assert events[-1]["status"] == "completed"
    assert [event["round"] for event in events if event["type"] == "RevisionRequested"] == [1, 2, 3]
    assert len(writer_prompts(provider)) == 4
    assert [item["round"] for item in handler.asked[3]["revision"]["history"]] == [1, 2, 3]


def test_rejected_still_stops_the_run_without_a_revision():
    definition = reviewed()["definition"]
    provider = drafts("Draft one")
    events = run(definition, provider, answers(decision("rejected", "No")))
    assert events[-1]["status"] == "stopped"
    assert not any(event["type"] == "RevisionRequested" for event in events)
    assert len(writer_prompts(provider)) == 1


def test_changes_requested_stops_when_max_revisions_is_used_up():
    definition = reviewed(max_revisions=1)["definition"]
    provider = drafts("One", "Two", "Three")
    handler = answers(decision("changes_requested", "First"), decision("changes_requested", "Second"))
    events = run(definition, provider, handler)
    assert events[-1]["status"] == "stopped"
    assert "1 revision round" in events[-1]["reason"]
    assert len(writer_prompts(provider)) == 2
    assert handler.asked[1]["revision"]["max"] == 1


def test_changes_requested_with_max_revisions_zero_stops_at_once():
    definition = reviewed(max_revisions=0)["definition"]
    provider = drafts("One")
    events = run(definition, provider, answers(decision("changes_requested")))
    assert events[-1]["status"] == "stopped" and len(writer_prompts(provider)) == 1


def test_changes_requested_with_nothing_to_revise_stops():
    value = reviewed()
    definition = value["definition"]
    # The review comes first: there is no earlier AI step to send back.
    definition["workflow"] = [definition["workflow"][1], definition["workflow"][0]]
    events = run(definition, drafts("One"), answers(decision("changes_requested")))
    assert events[-1]["status"] == "stopped"
    assert "no earlier step to revise" in events[-1]["reason"]


def three_steps(**review):
    """research -> draft -> review; draft also feeds a sibling summary."""
    value = reviewed(**review)
    definition = value["definition"]
    definition["agents"].append({"id": "researcher", "instructions": "Research", "tools": []})
    definition["state"].append({"key": "notes", "type": "text", "merge": "replace", "statuses": []})
    definition["workflow"] = [
        {"id": "research", "agent": "researcher", "task": "Research", "output": "notes"},
        {"id": "draft", "agent": "writer", "task": "Draft", "output": "brief"},
        {"id": "summary", "agent": "researcher", "task": "Summarize the draft", "output": "notes"},
        {**definition["workflow"][1]},
    ]
    definition["connections"] = [{"from": "research", "to": "draft"}, {"from": "draft", "to": "summary"},
                                 {"from": "draft", "to": "review"}]
    return definition


@pytest.mark.parametrize("settings, message", [
    ({"revises": ["missing"]}, "revises"),
    ({"revises": ["review"]}, "revises"),
    ({"revises": ["summary"]}, "revises"),          # not an ancestor of the review
    ({"revises": []}, "revises"),
    ({"revises": ["draft", "draft"]}, "revises"),
    ({"revises": "draft"}, "revises"),
    ({"max_revisions": -1}, "max_revisions"),
    ({"max_revisions": True}, "max_revisions"),
    ({"max_revisions": 1.5}, "max_revisions"),
    ({"max_revisions": 101}, "max_revisions"),
])
def test_invalid_revision_settings_are_refused(settings, message):
    definition = three_steps(**settings)
    with pytest.raises(ValueError, match=message):
        validate_definition(definition)


def test_revision_settings_belong_to_human_steps_and_revise_only_ai_steps():
    definition = three_steps()
    definition["workflow"][1]["max_revisions"] = 2
    with pytest.raises(ValueError, match="revis"):
        validate_definition(definition)
    definition = three_steps()
    definition["agents"].append({"id": "second", "kind": "human", "instructions": "Check", "tools": []})
    definition["workflow"].insert(2, {"id": "check", "agent": "second", "task": "Check", "output": "decision"})
    definition["connections"] += [{"from": "draft", "to": "check"}, {"from": "check", "to": "review"}]
    definition["workflow"][-1]["revises"] = ["check"]
    with pytest.raises(ValueError, match="revises"):
        validate_definition(definition)


@pytest.mark.parametrize("settings", [{}, {"revises": ["research"]}, {"revises": ["research", "draft"]},
                                      {"max_revisions": 0}, {"max_revisions": None}, {"max_revisions": 100}])
def test_valid_revision_settings_are_accepted(settings):
    validate_definition(three_steps(**settings))


def test_revision_of_an_earlier_step_reruns_every_step_that_depends_on_it():
    definition = three_steps(revises=["research"])
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Text"})
    events = run(definition, provider, answers(decision("changes_requested"), decision("approved", "Fine")))
    assert events[-1]["status"] == "completed"
    started = [event["stage"] for event in events if event["type"] == "StageStarted"]
    assert started.count("research") == 2 and started.count("draft") == 2 and started.count("summary") == 2
    revision = next(event for event in events if event["type"] == "RevisionRequested")
    assert revision["revises"] == ["research"]
    assert set(revision["reset"]) == {"research", "draft", "summary", "review"}
    research = [prompt for name, prompt in provider.prompts if name == "researcher" and "STAGE TASK:\nResearch" in prompt]
    assert COMMENT in research[1]
    # Only the step the reviewer asked to revise is given the comment.
    assert all(COMMENT not in prompt for name, prompt in provider.prompts if name == "writer")


def test_revision_by_default_reruns_the_direct_predecessor_and_its_finished_dependents():
    definition = three_steps()
    provider = ScriptedProvider(default={"type": "final", "content": "Done", "text": "Text"})
    events = run(definition, provider, answers(decision("changes_requested"), decision("approved", "Fine")))
    started = [event["stage"] for event in events if event["type"] == "StageStarted"]
    assert started.count("research") == 1, "nothing before the reviewed step runs again"
    assert started.count("draft") == 2 and started.count("summary") == 2
    revision = next(event for event in events if event["type"] == "RevisionRequested")
    assert revision["revises"] == ["draft"] and set(revision["reset"]) == {"draft", "summary", "review"}
    assert events[-1]["status"] == "completed"


def test_revision_is_charged_to_the_run_budget():
    value = reviewed()
    value["definition"]["run_budget"]["max_per_agent"] = 1
    provider = drafts("One", "Two")
    events = run(value["definition"], provider, answers(decision("changes_requested")))
    assert events[-1]["status"] == "stopped"
    assert len(writer_prompts(provider)) == 1


def test_revision_outside_an_isolated_step_test_scope_stops():
    definition = reviewed()["definition"]
    events = run(definition, drafts("One"), None, execution_steps={"review"}, seed_patches={"draft": "Seeded"},
                 human_inputs={"review": decision("changes_requested")})
    assert events[-1]["status"] == "stopped"
    assert not any(event["type"] == "RevisionRequested" for event in events)


# --- Durable rounds --------------------------------------------------------------


def test_revision_loop_is_durable_across_review_submission_and_restart(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository = RunRepository(path)
    run_id = repository.create(reviewed())
    provider = drafts("Draft one", "Draft two")
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, provider, tools))
    first = repository.get(run_id)["reviews"]
    assert [(review["round"], review["status"]) for review in first] == [(1, "pending")]
    repository.submit_review(run_id, first[0]["id"], reviewer="priya", output=decision("changes_requested"))

    repository = RunRepository(path)
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "waiting_for_human"
    assert any(event["type"] == "RevisionRequested" for event in events)
    record = repository.get(run_id)
    assert [(review["round"], review["status"]) for review in record["reviews"]] == [(1, "revised"), (2, "pending")]
    second = record["reviews"][1]
    assert second["request"]["revision"]["history"][0]["comment"] == COMMENT
    assert second["request"]["state"]["brief"] == "Draft two"
    assert public_record(record)["reviews"][1]["round"] == 2

    repository = RunRepository(path)
    repository.submit_review(run_id, second["id"], reviewer="priya", output=decision("approved", "Good"))
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "completed"
    assert len(writer_prompts(provider)) == 2
    record = repository.get(run_id)
    assert [review["status"] for review in record["reviews"]] == ["revised", "consumed"]
    recorded = [event["event"] for event in record["events"] if event["event"]["type"] == "HumanInputRecorded"]
    assert [event["review_id"] for event in recorded] == [first[0]["id"], second["id"]]


def test_a_stale_round_cannot_be_answered_after_its_revision(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(reviewed())
    provider = drafts("One", "Two")
    collect(repository.execute(run_id, provider, ToolRegistry(20000)))
    old = repository.get(run_id)["reviews"][0]["id"]
    repository.submit_review(run_id, old, reviewer="priya", output=decision("changes_requested"))
    collect(repository.resume(run_id, provider, ToolRegistry(20000)))
    with pytest.raises(ValueError):
        repository.submit_review(run_id, old, reviewer="priya", output=decision("approved", "Late"))


class FailOnce:
    """The writer's backend times out on its second call, then answers."""

    def __init__(self):
        self.inner = drafts("Draft one", "Draft two")
        self.calls = 0

    @property
    def prompts(self):
        return self.inner.prompts

    async def complete(self, messages):
        self.calls += 1
        if self.calls == 2:
            raise ProviderError("timeout", "no answer")
        return await self.inner.complete(messages)


def test_revision_checkpoint_survives_a_transient_stop_during_the_rerun(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(reviewed())
    provider = FailOnce()
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, provider, tools))
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="priya", output=decision("changes_requested"))
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "stopped"
    assert events[-1]["checkpoint"]["revisions"]["review"]["round"] == 1

    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "waiting_for_human"
    prompts = [prompt for name, prompt in provider.prompts if name == "writer"]
    assert COMMENT in prompts[-1], "the comment is not lost when the rerun is interrupted"
    assert repository.get(run_id)["reviews"][-1]["request"]["revision"]["round"] == 2


def test_revision_checkpoint_rejects_tampered_revision_state():
    definition = reviewed()["definition"]
    events = run(definition, drafts("One", "Two"), answers(decision("changes_requested"), decision("approved", "Ok")))
    checkpoint = next(event for event in events if event["type"] == "RevisionRequested")["checkpoint"]
    from conclave import RunBudget, SharedStore, StoreSchema
    from conclave.application.definition import _request_fingerprint

    fingerprint = _request_fingerprint(definition, "Write the brief", {"sources": "Evidence"})

    def restore(value):
        async def go():
            budget = RunBudget(**definition["run_budget"])
            store = SharedStore(StoreSchema({item["key"]: (lambda current, incoming: incoming) for item in definition["state"]}),
                                {item["key"]: "" if item["type"] == "text" else [] for item in definition["state"]})
            return await _restore_checkpoint(value, fingerprint, definition, store, budget)
        return asyncio.run(go())

    restore(checkpoint)
    for tamper in (
        lambda c: c["revisions"].update(missing={"round": 1}),
        lambda c: c["revisions"]["review"].update(round=0),
        lambda c: c["revisions"]["review"].update(history="x"),
        lambda c: c.update(revisions=[]),
        lambda c: c["revisions"]["review"].update(pending={"draft": {"comment": 3}}),
        lambda c: c["revisions"]["review"].update(pending={"review": {"comment": "x", "previous": "y"}}),
    ):
        changed = copy.deepcopy(checkpoint)
        tamper(changed)
        with pytest.raises(ValueError, match="revision"):
            restore(changed)


def test_review_rounds_migration_keeps_existing_reviews(tmp_path):
    path = tmp_path / "runs.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE studio_runs (id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE studio_run_events (run_id TEXT NOT NULL REFERENCES studio_runs(id), sequence INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (run_id, sequence));
            CREATE TABLE studio_run_reviews (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES studio_runs(id),
                stage TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT, output TEXT,
                submitted_at TEXT, UNIQUE (run_id, stage));
        """)
        connection.execute("INSERT INTO studio_runs VALUES ('old', ?, 'completed', '2026-01-01T00:00:00+00:00')",
                           (json.dumps(reviewed()),))
        connection.execute("INSERT INTO studio_run_reviews VALUES ('r1', 'old', 'review', '{}', 'consumed', 'ana', NULL, NULL)")
    repository = RunRepository(path)
    RunRepository(path)  # opening again is a no-op
    assert [(review["id"], review["round"], review["status"]) for review in repository.get("old")["reviews"]] == [("r1", 1, "consumed")]
    with closing(sqlite3.connect(path)) as connection, connection:
        # Two rounds of one step now fit.
        connection.execute("INSERT INTO studio_run_reviews (id, run_id, stage, request, status, round) VALUES ('r2', 'old', 'review', '{}', 'pending', 2)")


def test_revision_request_reaches_rest_and_the_review_list(tmp_path):
    from test_api import make_api

    async def exercise():
        api = make_api(tmp_path, drafts("One", "Two"))
        value = reviewed()
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": value["definition"]})
        assert status == 201
        status, record = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                                   "task": value["task"], "inputs": value["inputs"]})
        run_id = record["id"]
        for _ in range(200):
            if api.host.repository.get(run_id)["status"] == "waiting_for_human":
                break
            await asyncio.sleep(0.01)
        review = api.host.repository.get(run_id)["reviews"][0]
        await api.dispatch("POST", f"/api/runs/{run_id}/reviews", {"review_id": review["id"], "output": decision("changes_requested")})
        await api.dispatch("POST", f"/api/runs/{run_id}/resume", {})
        for _ in range(200):
            reviews = (await api.dispatch("GET", f"/api/runs/{run_id}/reviews"))[1]["reviews"]
            if len(reviews) == 2 and reviews[1]["status"] == "pending":
                break
            await asyncio.sleep(0.01)
        assert [review["round"] for review in reviews] == [1, 2]
        assert reviews[1]["request"]["revision"]["history"][0]["comment"] == COMMENT
        await api.host.close()
    asyncio.run(exercise())


# --- Security audit (S3) -------------------------------------------------------------


def test_revision_with_a_very_long_reason_can_still_be_continued(tmp_path):
    """A reason longer than a checkpoint keeps is shortened, visibly, not a run lost."""
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(reviewed())
    provider = FailOnce()
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, provider, tools))
    review = repository.get(run_id)["reviews"][0]
    long = {"records": [{"id": f"d{index}", "title": "Review", "body": "x" * 20000, "status": "changes_requested", "source_refs": []}
                        for index in range(11)]}
    repository.submit_review(run_id, review["id"], reviewer="priya", output=long)
    assert collect(repository.resume(run_id, provider, tools))[-1]["status"] == "stopped"
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "waiting_for_human"
    prompt = [prompt for name, prompt in provider.prompts if name == "writer"][-1]
    assert "[shortened" in prompt
    history = repository.get(run_id)["reviews"][-1]["request"]["revision"]["history"]
    assert len(history[0]["comment"]) <= 200000 and history[0]["comment"].endswith("]")


def test_revision_checkpoint_that_cannot_be_restored_stops_rather_than_fails(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(reviewed())
    provider = FailOnce()
    tools = ToolRegistry(20000)
    collect(repository.execute(run_id, provider, tools))
    review = repository.get(run_id)["reviews"][0]
    repository.submit_review(run_id, review["id"], reviewer="priya", output=decision("changes_requested"))
    collect(repository.resume(run_id, provider, tools))
    with repository._connect() as connection:
        connection.execute("UPDATE studio_run_events SET payload = json_set(payload, '$.checkpoint.revisions.review.round', 0) "
                           "WHERE run_id = ? AND json_type(payload, '$.checkpoint') IS NOT NULL", (run_id,))
    events = collect(repository.resume(run_id, provider, tools))
    assert events[-1]["status"] == "stopped"
    assert "saved checkpoint could not be restored" in events[-1]["reason"]
    assert repository.get(run_id)["status"] == "stopped", "not failed: a person can still look, and cancel"
