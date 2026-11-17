"""P3: a checkpoint holds every finished step, held ones included.

The format is unchanged (version 1, `patches` by step). What changes is the
rule on restore: the steps in it must be closed under their prerequisites,
rather than a prefix of the plan order. The prefix is merged into the results
at once; the rest stay held and join in their turn. Old prefix checkpoints
are still valid.
"""

import asyncio

import pytest

from conclave import ToolRegistry
from conclave.application.definition import _request_fingerprint
from conclave.application.service import public_record
from parallel_support import Driven, Gated, collect, commits, drive, plan, store_run, until, without_run_ids


def checkpoint(definition, patches, *, used=None):
    per_agent = {step: 1 for step in patches if step in {item["id"] for item in definition["agents"] if item.get("kind", "ai") == "ai"}}
    return {"version": 1, "request_fingerprint": _request_fingerprint(definition, "Do the work", {}),
            "patches": patches, "budget": {"used": sum(per_agent.values()) if used is None else used, "per_agent": per_agent, "spent": {}}}


def test_an_old_prefix_checkpoint_still_resumes():
    definition = plan(["a", "b", "c"], [("a", "b")])
    provider = Gated(definition)
    events = drive(definition, provider, checkpoint=checkpoint(definition, {"a": "a says hi"}))
    assert events[0]["type"] == "ApplicationResumed"
    assert events[-1]["status"] == "completed"
    assert "a" not in provider.calls and sorted(provider.calls) == ["b", "c"]
    assert events[-1]["state"] == {"a": "a says hi", "b": "b says hi", "c": "c says hi"}


def test_a_checkpoint_with_a_held_step_resumes_without_asking_it_again():
    # Plan order a, b, c: b finished while a was still working, so it was held.
    definition = plan(["a", "b", "c"], [("b", "c")])
    provider = Gated(definition)
    events = drive(definition, provider, checkpoint=checkpoint(definition, {"b": "b says hi"}))
    assert events[-1]["status"] == "completed"
    assert "b" not in provider.calls, "a finished step is never asked again"
    assert [event["stage"] for event in events if event["type"] == "ApplicationStateChanged"] == ["a", "b", "c"]
    straight = drive(plan(["a", "b", "c"], [("b", "c")], limit=1), Gated(definition))
    assert commits(events) == commits(straight), "the results go through the same changes as one at a time"


def test_a_checkpoint_that_skips_a_prerequisite_is_refused():
    definition = plan(["a", "b", "c"], [("b", "c")])
    with pytest.raises(ValueError, match="prerequisite"):
        drive(definition, Gated(definition), checkpoint=checkpoint(definition, {"c": "c says hi"}))


def test_a_held_step_after_a_terminal_human_decision_is_refused():
    definition = plan(["h", "a"], human=["h"], records={"h": "append"})
    definition["state"][0]["kind"] = "decision"
    definition["state"][0]["statuses"] = ["approved", "rejected", "changes_requested"]
    rejected = [{"id": "d", "title": "No", "body": "Rejected", "status": "rejected", "source_refs": [],
                 "contributions": [{"agent": "h", "stage": "h", "run_id": "human-1"}]}]
    with pytest.raises(ValueError, match="terminal human decision"):
        drive(definition, Gated(definition), checkpoint=checkpoint(definition, {"h": rejected, "a": "a says hi"}))


def test_each_finished_step_is_saved_so_an_interrupted_run_keeps_held_work(tmp_path):
    """The host stops while a works and b is held: b is not asked again."""
    definition = plan(["a", "b", "c"], [("b", "c")])
    repository, run_id = store_run(tmp_path, definition)
    first = Gated(definition, gated=["a"])

    async def interrupted():
        run = Driven(repository.execute(run_id, first, ToolRegistry(20000)))
        await until(lambda: run.of("StageFinished", "b") and run.of("StageFinished", "c"), what="b and c to finish while a works")
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)

    asyncio.run(interrupted())
    assert repository.get(run_id)["status"] == "interrupted"
    saved = [item["event"] for item in repository.get(run_id)["events"] if "checkpoint" in item["event"]]
    assert saved and set(saved[-1]["checkpoint"]["patches"]) == {"b", "c"}, "both finished steps were saved as they finished"

    second = Gated(definition)
    events = collect(repository.resume(run_id, second, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed"
    assert second.calls == ["a"], "only the step that was still working runs again"
    assert without_run_ids(events[-1]["state"]) == {"a": "a says hi", "b": "b says hi", "c": "c says hi"}


def test_the_published_record_keeps_held_results_and_no_checkpoint(tmp_path):
    definition = plan(["a", "b"])
    repository, run_id = store_run(tmp_path, definition)
    collect(repository.execute(run_id, Gated(definition, delays={"a": 0.05}), ToolRegistry(20000)))
    record = public_record(repository.get(run_id))
    finished = [item["event"] for item in record["events"] if item["event"]["type"] == "StageFinished"]
    # Only the held step repeats its output there (performance review PP2).
    assert {event["stage"]: event.get("patch") for event in finished} == {"a": None, "b": "b says hi"}
    assert next(event for event in finished if event["stage"] == "b")["held"] is True
    assert not any("checkpoint" in item["event"] for item in record["events"])
    changed = [item["event"] for item in record["events"] if item["event"]["type"] == "ApplicationStateChanged"]
    assert [event["patch"] for event in changed] == ["a says hi", "b says hi"]
