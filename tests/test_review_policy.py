"""Who may answer a review, and how many people must.

An application's `review_policy` names who may answer its reviews (people, or
whole `@domain`s) and whether the person who started a run may sign off its own
review. A human step's `answers_required` asks for that many different people.
The rules are the host's, enforced where every answer is recorded, whoever
submits it and however: Studio, REST or the terminal.
"""

import asyncio
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from conclave import ToolRegistry
from conclave.application.definition import validate_definition
from conclave.application.runs import RunRepository
from conclave.application.service import LocalRunHost, public_record
from conftest import ScriptedProvider
from test_studio_runs import collect, request

STATUSES = ["proposed", "approved", "rejected", "changes_requested"]


def application(policy=None, required=None, final=True):
    """draft (AI) -> approve (human decision) [-> publish (AI)]."""
    value = request()
    definition = value["definition"]
    definition["state"].append({"key": "decision", "kind": "decision", "type": "records", "merge": "upsert", "statuses": list(STATUSES)})
    definition["views"].append({"id": "decision_view", "collection": "decision", "layout": "cards"})
    definition["workflow"][1].update(output="decision", name="Approve the release")
    if final:
        definition["workflow"].append({"id": "publish", "agent": "writer", "task": "Publish", "output": "brief"})
    if policy is not None:
        definition["review_policy"] = policy
    if required is not None:
        definition["workflow"][1]["answers_required"] = required
    return value


def answer(status, body="My reason", record_id="human-approve"):
    return {"records": [{"id": record_id, "title": "Approve the release", "body": body, "status": status, "source_refs": []}]}


def provider():
    return ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": text} for text in ("Draft", "Revised", "Published")]})


def waiting(tmp_path, value, *, actor="sam@acme.com"):
    repository = RunRepository(tmp_path / "runs.sqlite")
    run_id = repository.create(value, actor=actor)
    collect(repository.execute(run_id, provider(), ToolRegistry(20000)))
    assert repository.get(run_id)["status"] == "waiting_for_human"
    return repository, run_id


def pending(repository, run_id):
    return [review for review in repository.get(run_id)["reviews"] if review["status"] == "pending"][-1]


# --- Definitions ---------------------------------------------------------------------


@pytest.mark.parametrize("policy, message", [
    ("ana@acme.com", "review_policy"),
    ({"reviewers": ["ana@acme.com"], "quorum": 2}, "review_policy"),
    ({"reviewers": []}, "reviewers"),
    ({"reviewers": "ana@acme.com"}, "reviewers"),
    ({"reviewers": ["ana@acme.com", "ANA@acme.com"]}, "reviewers"),      # the same person twice
    ({"reviewers": [" "]}, "reviewers"),
    ({"reviewers": ["x" * 101]}, "reviewers"),
    ({"reviewers": ["@"]}, "reviewers"),
    ({"reviewers": ["@acme"]}, "reviewers"),
    ({"reviewers": [f"person{n}@acme.com" for n in range(51)]}, "reviewers"),
    ({"reviewers": ["ana@acme.com"], "allow_starter": "yes"}, "allow_starter"),
])
def test_review_policy_validation_rejects_bad_reviewer_lists(policy, message):
    with pytest.raises(ValueError, match=message):
        validate_definition(application(policy)["definition"])


@pytest.mark.parametrize("required, policy, message", [
    (0, None, "answers_required"),
    (51, None, "answers_required"),
    (True, None, "answers_required"),
    (1.5, None, "answers_required"),
    (3, {"reviewers": ["ana@acme.com", "ben@acme.com"]}, "answers_required"),   # only two people named
])
def test_review_sign_off_count_is_validated(required, policy, message):
    with pytest.raises(ValueError, match=message):
        validate_definition(application(policy, required)["definition"])


def test_review_sign_off_count_belongs_to_decision_reviews():
    value = application(required=2)
    value["definition"]["workflow"][1]["output"] = "brief"   # a text review
    with pytest.raises(ValueError, match="answers_required"):
        validate_definition(value["definition"])
    value = application()
    value["definition"]["workflow"][0]["answers_required"] = 2   # an AI step
    with pytest.raises(ValueError, match="answers_required"):
        validate_definition(value["definition"])


@pytest.mark.parametrize("policy, required", [
    ({"reviewers": ["ana@acme.com"]}, None),
    ({"reviewers": ["ana@acme.com", "@ops.acme.com"], "allow_starter": True}, 5),   # a domain has room for more
    ({"allow_starter": False}, 3),
    ({"reviewers": ["ana@acme.com", "ben@acme.com"]}, 2),
    (None, 50),
])
def test_review_policy_valid_reviewer_settings_are_accepted(policy, required):
    validate_definition(application(policy, required)["definition"])


# --- Who may answer ----------------------------------------------------------------------


def test_review_by_the_actor_who_started_the_run_is_refused(tmp_path):
    repository, run_id = waiting(tmp_path, application({"allow_starter": False}))
    review = pending(repository, run_id)
    with pytest.raises(ValueError, match="You started this run, so someone else must answer its review"):
        repository.submit_review(run_id, review["id"], reviewer="SAM@acme.com", output=answer("approved"))
    assert pending(repository, run_id)["answers"] == []
    repository.submit_review(run_id, review["id"], reviewer="ana@acme.com", output=answer("approved"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted"


def test_review_by_the_actor_is_allowed_when_the_policy_says_so(tmp_path):
    repository, run_id = waiting(tmp_path, application({"allow_starter": True}))
    repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer="sam@acme.com", output=answer("approved"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted"


def test_review_by_the_actor_is_allowed_without_a_policy(tmp_path):
    """An application that names no policy behaves as it always has."""
    repository, run_id = waiting(tmp_path, application())
    repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer="sam@acme.com", output=answer("approved"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted"


@pytest.mark.parametrize("reviewer", ["eve@acme.com", "eve@evilacme.com", "ops.acme.com", "ana@acme.com.evil.com"])
def test_review_by_an_unlisted_reviewer_is_refused(tmp_path, reviewer):
    repository, run_id = waiting(tmp_path, application({"reviewers": ["ana@acme.com", "@ops.acme.com"]}))
    with pytest.raises(ValueError, match="Only ana@acme.com or anyone at @ops.acme.com may answer this review"):
        repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer=reviewer, output=answer("approved"))


@pytest.mark.parametrize("reviewer", ["ana@acme.com", "Ana@ACME.com", "raj@ops.acme.com", "RAJ@OPS.ACME.COM"])
def test_review_by_a_listed_reviewer_or_domain_is_accepted(tmp_path, reviewer):
    repository, run_id = waiting(tmp_path, application({"reviewers": ["ana@acme.com", "@ops.acme.com"]}))
    repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer=reviewer, output=answer("approved"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted"


def test_review_reviewer_cannot_answer_twice(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=2))
    review = pending(repository, run_id)
    repository.submit_review(run_id, review["id"], reviewer="ana@acme.com", output=answer("approved"))
    with pytest.raises(ValueError, match="You already answered this review; it needs a different person"):
        repository.submit_review(run_id, review["id"], reviewer="ANA@acme.com", output=answer("approved", "Again"))
    assert len(pending(repository, run_id)["answers"]) == 1


# --- How many answers --------------------------------------------------------------------


def test_review_waits_for_every_required_sign_off(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=3))
    review = pending(repository, run_id)
    for index, reviewer in enumerate(["ana@acme.com", "ben@acme.com"], start=1):
        repository.submit_review(run_id, review["id"], reviewer=reviewer, output=answer("approved", f"Fine {index}"))
        current = repository.get(run_id)
        assert current["status"] == "waiting_for_human" and current["reviews"][0]["status"] == "pending"
        assert [item["reviewer"] for item in current["reviews"][0]["answers"]] == ["ana@acme.com", "ben@acme.com"][:index]
        assert current["reviews"][0]["required"] == 3
    with pytest.raises(ValueError, match="Submit the pending review"):
        collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    repository.submit_review(run_id, review["id"], reviewer="cai@acme.com", output=answer("approved", "Fine 3"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted"
    submitted = [item["event"] for item in repository.get(run_id)["events"] if item["event"]["type"] == "HumanReviewSubmitted"]
    assert [(event["answers"], event["required"]) for event in submitted] == [(1, 3), (2, 3), (3, 3)]
    events = collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    assert events[-1]["status"] == "completed"
    recorded = next(event for event in events if event["type"] == "HumanInputRecorded")
    assert recorded["reviewers"] == ["ana@acme.com", "ben@acme.com", "cai@acme.com"]
    decisions = events[-1]["state"]["decision"]
    assert sorted(record["body"] for record in decisions) == ["Fine 1", "Fine 2", "Fine 3"]
    assert len({record["id"] for record in decisions}) == 3, "each reviewer's record keeps its own id"


def test_review_first_rejection_ends_the_sign_off_at_once(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=3))
    review = pending(repository, run_id)
    repository.submit_review(run_id, review["id"], reviewer="ana@acme.com", output=answer("approved"))
    repository.submit_review(run_id, review["id"], reviewer="ben@acme.com", output=answer("rejected", "Not this week"))
    assert repository.get(run_id)["reviews"][0]["status"] == "submitted", "waiting for a third answer cannot change a rejection"
    events = collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    assert events[-1]["status"] == "stopped"


def test_review_changes_requested_waits_for_every_reviewer_then_revises_with_all_comments(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=2))
    review = pending(repository, run_id)
    repository.submit_review(run_id, review["id"], reviewer="ana@acme.com", output=answer("changes_requested", "Name the migration"))
    assert pending(repository, run_id)["id"] == review["id"], "a request for changes waits for everyone"
    repository.submit_review(run_id, review["id"], reviewer="ben@acme.com", output=answer("changes_requested", "Add a rollback note"))
    events = collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    revision = next(event for event in events if event["type"] == "RevisionRequested")
    assert "Name the migration" in revision["comment"] and "Add a rollback note" in revision["comment"]
    assert events[-1]["status"] == "waiting_for_human"


def test_review_each_revision_round_needs_every_sign_off_again(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=2))
    first = pending(repository, run_id)
    repository.submit_review(run_id, first["id"], reviewer="ana@acme.com", output=answer("changes_requested", "More"))
    repository.submit_review(run_id, first["id"], reviewer="ben@acme.com", output=answer("approved", "Fine"))
    collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    second = pending(repository, run_id)
    assert second["id"] != first["id"] and second["round"] == 2 and second["answers"] == [] and second["required"] == 2
    repository.submit_review(run_id, second["id"], reviewer="ana@acme.com", output=answer("approved", "Now fine"))
    assert pending(repository, run_id)["id"] == second["id"], "one answer is not enough in round 2 either"


def test_review_stale_round_cannot_be_signed(tmp_path):
    repository, run_id = waiting(tmp_path, application(required=1))
    first = pending(repository, run_id)
    repository.submit_review(run_id, first["id"], reviewer="ana@acme.com", output=answer("changes_requested", "More"))
    collect(repository.resume(run_id, provider(), ToolRegistry(20000)))
    with pytest.raises(ValueError, match="not waiting"):
        repository.submit_review(run_id, first["id"], reviewer="ben@acme.com", output=answer("approved"))


# --- The host ----------------------------------------------------------------------------


def host_for(tmp_path, principal="sam@acme.com"):
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer=principal)
    return host


def person(name):
    from conclave.application.principal import Principal
    return Principal(name)


async def started(host, value):
    _, record = await host.handle("start", value)
    await host.until_idle(record["id"])
    return record["id"]


def test_review_policy_continues_the_run_by_itself_after_the_last_sign_off(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["@acme.com"]}, required=2))
        review = host.repository.get(run_id)["reviews"][0]
        _, shown = await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")},
                                     principal=person("ana@acme.com"))
        assert shown["status"] == "waiting_for_human" and shown["reviews"][0]["answers"][0]["reviewer"] == "ana@acme.com"
        _, shown = await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")},
                                     principal=person("ben@acme.com"))
        await host.until_idle(run_id)
        assert host.repository.get(run_id)["status"] == "completed", "nobody had to press Continue"
        await host.close()
    asyncio.run(exercise())


def test_review_without_a_policy_still_waits_for_resume_after_sign_off(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application())
        review = host.repository.get(run_id)["reviews"][0]
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")})
        await host.until_idle(run_id)
        assert host.repository.get(run_id)["status"] == "waiting_for_human", "as before: the caller continues it"
        await host.close()
    asyncio.run(exercise())


def test_review_public_record_says_whether_the_viewer_can_sign(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com", "@ops.acme.com"]}, required=2))
        _, mine = await host.handle("get", {"run_id": run_id})
        assert mine["started_by"] == "sam@acme.com"
        assert mine["reviews"][0]["required"] == 2 and mine["reviews"][0]["answers"] == []
        assert mine["can_answer"] == {"allowed": False, "reason": "You started this run, so someone else must answer its review"}
        _, other = await host.handle("get", {"run_id": run_id}, principal=person("eve@acme.com"))
        assert other["can_answer"] == {"allowed": False, "reason": "Only ana@acme.com or anyone at @ops.acme.com may answer this review"}
        _, ana = await host.handle("get", {"run_id": run_id}, principal=person("ana@acme.com"))
        assert ana["can_answer"] == {"allowed": True, "reason": "", "completes": False}
        review = ana["reviews"][0]
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved", "Good")},
                          principal=person("ana@acme.com"))
        _, ana = await host.handle("get", {"run_id": run_id}, principal=person("ana@acme.com"))
        assert ana["can_answer"] == {"allowed": False, "reason": "You already answered this review; it needs a different person"}
        assert ana["reviews"][0]["answers"][0]["reviewer"] == "ana@acme.com"
        assert ana["reviews"][0]["answers"][0]["output"]["records"][0]["status"] == "approved"
        _, raj = await host.handle("get", {"run_id": run_id}, principal=person("raj@ops.acme.com"))
        assert raj["can_answer"] == {"allowed": True, "reason": "", "completes": True}, "the last answer needed"
        await host.close()
    asyncio.run(exercise())


class Owners:
    """The part of a connection store that says who owns a run."""

    def __init__(self, owner):
        self.owner = owner

    def run_owner(self, run_id, tenant):
        return self.owner

    def bind(self, tools, *args, **kwargs):
        return tools


def test_review_access_for_an_eligible_reviewer_with_connections(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        value = application({"reviewers": ["ana@acme.com"]})
        run_id = await started(host, value)
        host.connections = Owners("sam@acme.com")
        ana, eve = person("ana@acme.com"), person("eve@acme.com")
        # The reviewer can find and open the run waiting for her, and answer it.
        _, listed = await host.handle("list", {"application_id": value["application_id"]}, principal=ana)
        assert [run["id"] for run in listed["runs"]] == [run_id]
        _, shown = await host.handle("get", {"run_id": run_id}, principal=ana)
        assert shown["can_answer"]["allowed"] is True
        # Nothing else: continuing, cancelling and approving tools stay the owner's.
        for command in ("resume", "cancel", "pause", "cancel_retry"):
            with pytest.raises(ValueError, match="another principal"):
                await host.handle(command, {"run_id": run_id}, principal=ana)
        # Someone who may not answer sees nothing.
        _, listed = await host.handle("list", {"application_id": value["application_id"]}, principal=eve)
        assert listed["runs"] == []
        with pytest.raises(ValueError, match="another principal"):
            await host.handle("get", {"run_id": run_id}, principal=eve)
        review = shown["reviews"][0]
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")}, principal=ana)
        await host.until_idle(run_id)
        assert host.repository.get(run_id)["status"] == "completed"
        # Once nothing waits for her, the run is the owner's alone again.
        with pytest.raises(ValueError, match="another principal"):
            await host.handle("get", {"run_id": run_id}, principal=ana)
        host.connections = None
        await host.close()
    asyncio.run(exercise())


def test_review_rest_refusal_says_why_the_reviewer_cannot_answer(tmp_path):
    from conclave.api import ApplicationAPI
    from conclave.application.workspace import Workspace

    async def exercise():
        host = host_for(tmp_path)
        api = ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))
        status, saved = await api.dispatch("POST", "/api/applications", {"definition": application({"allow_starter": False})["definition"]})
        assert status == 201, saved
        value = application()
        status, record = await api.dispatch("POST", "/api/runs", {"version": 1, "application_id": saved["id"], "revision": saved["revision"],
                                                                   "task": value["task"], "inputs": value["inputs"]})
        await host.until_idle(record["id"])
        review = host.repository.get(record["id"])["reviews"][0]
        status, body = await api.dispatch("POST", f"/api/runs/{record['id']}/reviews", {"review_id": review["id"], "output": answer("approved")})
        assert status == 400 and body["error"] == "You started this run, so someone else must answer its review"
        await host.close()
    asyncio.run(exercise())


def test_review_cli_refuses_the_actor_with_a_reason(tmp_path, monkeypatch, capsys):
    from applications import cli

    async def prepare():
        host = host_for(tmp_path, "operator")
        run_id = await started(host, application({"allow_starter": False}))
        review_id = host.repository.get(run_id)["reviews"][0]["id"]
        await host.close()
        return run_id, review_id
    run_id, review_id = asyncio.run(prepare())
    output = tmp_path / "answer.json"
    output.write_text(json.dumps(answer("approved")))
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: host_for(tmp_path, "operator"))
    with pytest.raises(SystemExit) as stopped:
        cli.run(["review", run_id, review_id, "--output", str(output)])
    assert stopped.value.code == 2
    assert "You started this run, so someone else must answer its review" in capsys.readouterr().err


def test_review_answers_are_added_to_an_existing_store_and_reviewers_can_sign(tmp_path):
    path = tmp_path / "runs.sqlite"
    repository, run_id = waiting(tmp_path, application())
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE studio_run_review_answers")
    repository = RunRepository(path)   # an older store: the table is added
    repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer="ana@acme.com", output=answer("approved"))
    assert repository.get(run_id)["reviews"][0]["answers"][0]["reviewer"] == "ana@acme.com"


def test_review_cli_text_shows_answers_and_why_the_actor_cannot_sign(tmp_path):
    from conclave.application.presentation import render_run

    repository, run_id = waiting(tmp_path, application({"reviewers": ["@acme.com"]}, required=3))
    repository.submit_review(run_id, pending(repository, run_id)["id"], reviewer="ana@acme.com", output=answer("approved", "Fine"))
    text = render_run(public_record(repository.get(run_id), "sam@acme.com"))
    assert "  Answers   1 of 3 · ana@acme.com approved" in text
    assert "  You       You started this run, so someone else must answer its review" in text
    assert "  You       " not in render_run(public_record(repository.get(run_id), "ben@acme.com"))


# --- Security audit (V1–V6) ---------------------------------------------------------------


def test_review_stream_stops_once_the_reviewer_may_no_longer_read_the_run(tmp_path):
    from conclave.api import ApplicationAPI
    from conclave.application.workspace import Workspace
    from conclave.asgi import stream_run

    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com"]}))
        host.connections = Owners("sam@acme.com")
        api = ApplicationAPI(host, Workspace(tmp_path / "applications.sqlite"))
        received, ended = [], []

        async def send(message):
            if message.get("body"):
                received.append(json.loads(message["body"][len(b"data: "):]))
            if message["type"] == "http.response.body" and not message.get("more_body"):
                ended.append(True)
        watcher = asyncio.create_task(stream_run(send, api, run_id, [], principal=person("ana@acme.com")))
        await asyncio.sleep(0.05)
        review = host.repository.get(run_id)["reviews"][0]
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")}, principal=person("ana@acme.com"))
        await host.until_idle(run_id)
        await asyncio.wait_for(watcher, 2)
        assert ended, "the stream ended"
        assert all(record["status"] == "waiting_for_human" for record in received), "nothing after her review reached her"
        assert "Published" not in json.dumps(received)
        host.connections = None
        await host.close()
    asyncio.run(exercise())


def test_review_continue_after_sign_off_does_not_act_for_an_owner_no_longer_admitted(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com"]}))
        host.schedules = SimpleNamespace(admits=lambda owner: owner != "sam@acme.com")
        review = host.repository.get(run_id)["reviews"][0]
        _, shown = await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")},
                                     principal=person("ana@acme.com"))
        await host.until_idle(run_id)
        record = host.repository.get(run_id)
        assert record["status"] == "waiting_for_human" and record["reviews"][0]["status"] == "submitted", "her answer stands"
        withheld = [item["event"] for item in record["events"] if item["event"]["type"] == "ContinueWithheld"]
        assert withheld == [{**withheld[0], "reason": "owner_not_admitted"}]
        host.schedules = None
        await host.close()
    asyncio.run(exercise())


def test_review_continue_after_sign_off_of_a_scheduled_run_still_refuses_writes(tmp_path):
    async def exercise():
        host = host_for(tmp_path)
        value = application({"reviewers": ["ana@acme.com"]})
        value["trigger"] = {"kind": "schedule", "scheduled_for": "2026-10-20T09:00:00+00:00"}
        run_id = host.repository.create(value, actor="sam@acme.com")
        await host._schedule(run_id, approvals="refuse")
        await host.until_idle(run_id)
        calls, schedule = [], host._schedule

        async def recording(run, **options):
            calls.append(options)
            return await schedule(run, **options)
        host._schedule = recording
        review = host.repository.get(run_id)["reviews"][0]
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")}, principal=person("ana@acme.com"))
        await host.until_idle(run_id)
        assert calls == [{"resume": True, "approvals": "refuse"}], "nobody watches a scheduled run's continue either"
        assert host.repository.get(run_id)["status"] == "completed"
        await host.close()
    asyncio.run(exercise())


def test_review_sign_off_stands_when_the_continue_cannot_start(tmp_path, caplog):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com"]}))

        async def cannot_start(run, **options):
            raise ValueError("This request is already running")
        host._schedule = cannot_start
        review = host.repository.get(run_id)["reviews"][0]
        status, shown = await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")},
                                          principal=person("ana@acme.com"))
        assert status == 200, "the reviewer's answer was recorded, and they are told so"
        assert shown["reviews"][0]["status"] == "submitted" and shown["status"] == "waiting_for_human"
        assert "could not continue" in caplog.text
        await host.close()
    asyncio.run(exercise())


# --- Performance audit (Q1–Q4) --------------------------------------------------------------


def count_whole_reads(monkeypatch, repository):
    counted = {"reads": 0}
    real = repository.get

    def counting(run_id):
        counted["reads"] += 1
        return real(run_id)
    monkeypatch.setattr(repository, "get", counting)
    return counted


def test_review_list_for_a_reviewer_reads_no_whole_run_record(tmp_path, monkeypatch):
    async def exercise():
        host = host_for(tmp_path)
        value = application({"reviewers": ["ana@acme.com"]})
        runs = [await started(host, value) for _ in range(3)]
        host.connections = Owners("sam@acme.com")
        counted = count_whole_reads(monkeypatch, host.repository)
        _, listed = await host.handle("list", {"application_id": value["application_id"]}, principal=person("ana@acme.com"))
        assert sorted(run["id"] for run in listed["runs"]) == sorted(runs)
        assert counted["reads"] == 0, counted
        host.connections = None
        await host.close()
    asyncio.run(exercise())


def test_review_get_by_a_reviewer_reads_the_record_no_more_than_the_owner(tmp_path, monkeypatch):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com"]}))
        host.connections = Owners("sam@acme.com")
        counted = count_whole_reads(monkeypatch, host.repository)
        await host.handle("get", {"run_id": run_id}, principal=person("sam@acme.com"))
        owner = counted["reads"]
        counted["reads"] = 0
        await host.handle("get", {"run_id": run_id}, principal=person("ana@acme.com"))
        assert counted["reads"] == owner == 2, (counted, owner)
        host.connections = None
        await host.close()
    asyncio.run(exercise())


def test_review_stream_access_check_for_a_reviewer_reads_no_whole_record(tmp_path, monkeypatch):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["ana@acme.com"]}))
        host.connections = Owners("sam@acme.com")
        counted = count_whole_reads(monkeypatch, host.repository)
        host._require_run_owner(run_id, person("ana@acme.com"), reviewing=True)
        assert counted["reads"] == 0
        host.connections = None
        await host.close()
    asyncio.run(exercise())


def test_review_sign_off_reads_the_whole_record_once_for_the_reply(tmp_path, monkeypatch):
    async def exercise():
        host = host_for(tmp_path)
        run_id = await started(host, application({"reviewers": ["@acme.com"]}, required=2))
        review = host.repository.get(run_id)["reviews"][0]
        counted = count_whole_reads(monkeypatch, host.repository)
        await host.handle("review", {"run_id": run_id, "review_id": review["id"], "output": answer("approved")}, principal=person("ana@acme.com"))
        assert counted["reads"] == 1, counted
        await host.close()
    asyncio.run(exercise())
