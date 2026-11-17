"""P2: independent steps run side by side, and the results keep the plan's order.

A step starts as soon as every step it depends on has finished and a slot is
free. Outputs join the shared results strictly in plan order: a step that
finishes before an earlier one is held (`StageFinished` with `held: true`)
and committed when its turn comes. So, for the same answers, the results go
through exactly the changes they go through one step at a time.
"""

import asyncio

import pytest

from parallel_support import Gated, commits, drive, plan, shuffled_delays, start, until, without_run_ids


def test_a_step_starts_when_its_own_prerequisites_finish_not_when_a_wave_does():
    # a is slow and unrelated; c needs only b.
    definition = plan(["a", "b", "c"], [("b", "c")])
    provider = Gated(definition, gated=["a"])

    async def go():
        run = start(definition, provider)
        await until(lambda: "c" in provider.finished, what="c to run while a is still working")
        assert "a" not in provider.finished
        provider.release("a")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "completed"


def test_a_step_that_finishes_early_is_held_then_committed_in_plan_order():
    definition = plan(["a", "b"])
    provider = Gated(definition, gated=["a"])

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "b"), what="b to finish")
        finished = run.of("StageFinished", "b")[0]
        assert finished["status"] == "completed" and finished["held"] is True
        assert finished["patch"] == "b says hi", "the held result is on the event, for Studio to show"
        assert finished["agent"] == "b"
        assert not run.of("ApplicationStateChanged"), "b does not join the results before a"
        provider.release("a")
        return await run.done()

    events = asyncio.run(go())
    assert [event["stage"] for event in events if event["type"] == "ApplicationStateChanged"] == ["a", "b"]
    a_finished = next(event for event in events if event["type"] == "StageFinished" and event["stage"] == "a")
    assert a_finished.get("held") in (None, False), "a joined at once"
    # a's finish commits a and the held b in one turn; the turn's last event carries the checkpoint.
    a_index = events.index(a_finished)
    turn = events[a_index:a_index + 3]
    assert [event["type"] for event in turn] == ["StageFinished", "ApplicationStateChanged", "ApplicationStateChanged"]
    assert "checkpoint" in turn[-1] and set(turn[-1]["checkpoint"]["patches"]) == {"a", "b"}


def test_a_dependent_starts_from_a_held_result_without_waiting_for_the_commit():
    # Plan order a, b, c; c needs b only. b is held behind a, and c runs anyway.
    definition = plan(["a", "b", "c"], [("b", "c")])
    provider = Gated(definition, gated=["a", "c"])

    async def go():
        run = start(definition, provider)
        await until(lambda: "c" in provider.started, what="c to start while a works and b is held")
        assert run.of("StageFinished", "b")[0]["held"] is True
        assert not run.of("ApplicationStateChanged")
        provider.release("a", "c")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "completed"
    assert '"b": "b says hi"' in provider.prompts["c"][0], "c's input is b's result"
    assert '"a": "a says hi"' not in provider.prompts["c"][0], "and only what c depends on"


def test_every_event_from_a_step_names_that_step():
    definition = plan(["a", "b", "c"])
    events = drive(definition, Gated(definition, delays={"a": 0.02, "b": 0.01, "c": 0}))
    per_step = [event for event in events if event["type"] not in {"ApplicationStarted", "ApplicationFinished"}]
    assert per_step and all(event.get("stage") in {"a", "b", "c"} for event in per_step)
    for step in "abc":
        kinds = [event["type"] for event in events if event.get("stage") == step]
        assert kinds[0] == "StageStarted" and "StageFinished" in kinds and kinds[-1] == "ApplicationStateChanged"


SHAPES = {
    "independent": (["a", "b", "c", "d"], [], {}, {}),
    "chain": (["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "d")], {}, {}),
    "diamond": (["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")], {}, {}),
    "fan-in": (["a", "b", "c", "d", "e", "f"], [(step, "f") for step in "abcde"], {}, {}),
    "two chains": (["a", "b", "c", "d", "e", "f"], [("a", "b"), ("b", "c"), ("d", "e"), ("e", "f")], {}, {}),
    # Several steps write one records collection, and one text collection, so
    # the merge order decides the result.
    "shared collections": (["a", "b", "c", "d", "e", "f"], [("a", "d"), ("b", "e")],
                           {"a": "log", "b": "log", "c": "log", "d": "table", "e": "table", "f": "note"},
                           {"log": "append", "table": "upsert"}),
}


def one_at_a_time(shape):
    steps, edges, outputs, records = SHAPES[shape]
    definition = plan(steps, edges, limit=1, outputs=outputs, records=records)
    events = drive(definition, Gated(definition))
    assert events[-1]["status"] == "completed"
    return commits(events), without_run_ids(events[-1]["state"])


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("limit", [2, 3, None])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_running_side_by_side_changes_the_results_exactly_as_one_at_a_time_does(shape, limit, seed):
    steps, edges, outputs, records = SHAPES[shape]
    definition = plan(steps, edges, limit=limit, outputs=outputs, records=records)
    events = drive(definition, Gated(definition, delays=shuffled_delays(definition, seed)))
    assert events[-1]["status"] == "completed"
    expected_commits, expected_state = one_at_a_time(shape)
    assert commits(events) == expected_commits
    assert without_run_ids(events[-1]["state"]) == expected_state


def test_the_shared_collections_shape_really_depends_on_merge_order():
    """Guard for the test above: a different order would give another state."""
    steps, edges, outputs, records = SHAPES["shared collections"]
    forward = plan(steps, edges, limit=1, outputs=outputs, records=records)
    backward = plan(list(reversed(steps)), [], limit=1, outputs=outputs, records=records)
    state = without_run_ids(drive(forward, Gated(forward))[-1]["state"])
    other = without_run_ids(drive(backward, Gated(backward))[-1]["state"])
    assert state["log"] != other["log"] and state["table"] != other["table"]


def test_a_step_whose_answer_was_saved_is_not_asked_again_in_a_parallel_run():
    definition = plan(["a", "b", "c"])
    provider = Gated(definition)
    events = drive(definition, provider, progress={"b": {"output": {"payload": "b was saved", "blocker": None}}})
    assert events[-1]["status"] == "completed"
    assert "b" not in provider.calls
    assert events[-1]["state"]["b"] == "b was saved"
    finished = [event for event in events if event["type"] == "StageFinished" and event["stage"] == "b"]
    assert len(finished) == 1 and finished[0]["status"] == "completed"
