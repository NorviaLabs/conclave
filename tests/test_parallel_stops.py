"""P4: pause, review, failure and stops, with several steps running.

One rule for each: nothing new starts, running steps finish and keep their
work, then the run ends (or asks for the review). A failure no longer cancels
the steps beside it.
"""

import asyncio

from actions_support import Recorder, approve, checker, drain, note, registry, scripted, write_request
from conclave import ProviderError, ToolRegistry
from conclave.application.runs import RunRepository
from parallel_support import Driven, Gated, collect, commits, drive, plan, start, store_run, until, without_run_ids

EMPTY = {"type": "final", "content": ""}  # no text: fails output validation, and each repair does too


def test_a_pause_lets_running_steps_finish_and_starts_nothing_new(tmp_path):
    definition = plan(["a", "b", "c"], [("a", "c")])
    repository, run_id = store_run(tmp_path, definition)
    provider = Gated(definition, gated=["a", "b"])
    asked = {"pause": False}

    async def go():
        run = Driven(repository.execute(run_id, provider, ToolRegistry(20000), pause_requested=lambda: asked["pause"]))
        await until(lambda: {"a", "b"} <= set(provider.started), what="a and b to start")
        asked["pause"] = True
        await until(lambda: run.of("PauseRequested"), what="the run to see the pause while a and b work")
        assert run.of("PauseRequested")[0]["running"] == ["a", "b"]
        provider.release("a")
        await until(lambda: run.of("StageFinished", "a"), what="a to finish")
        await asyncio.sleep(0.05)
        assert "c" not in provider.started, "c became ready, but nothing starts after a pause"
        provider.release("b")
        return await run.done()

    events = asyncio.run(go())
    finished = events[-1]
    assert finished["status"] == "paused" and finished["next_stage"] == "c"
    assert finished["reason"] == "Paused: no new step started; running steps finished"
    assert set(finished["checkpoint"]["patches"]) == {"a", "b"}

    again = Gated(definition)
    resumed = collect(repository.resume(run_id, again, ToolRegistry(20000)))
    assert resumed[-1]["status"] == "completed"
    assert again.calls == ["c"], "resume repeats none of the steps that finished"


def test_a_pause_keeps_a_held_step_and_resume_commits_it_in_its_turn():
    # Plan order x, a, b; a needs x. b finishes first and is held behind x and a.
    definition = plan(["x", "a", "b"], [("x", "a")])
    provider = Gated(definition, gated=["x"])
    asked = {"pause": False}

    async def go():
        run = start(definition, provider, pause_requested=lambda: asked["pause"])
        await until(lambda: run.of("StageFinished", "b"), what="b to finish first")
        asked["pause"] = True
        await until(lambda: run.of("PauseRequested"), what="the pause to be seen")
        provider.release("x")
        return await run.done()

    events = asyncio.run(go())
    finished = events[-1]
    assert finished["status"] == "paused" and finished["next_stage"] == "a"
    assert set(finished["checkpoint"]["patches"]) == {"x", "b"}, "the held step is kept"
    assert "a" not in provider.started

    again = Gated(definition)
    resumed = drive(definition, again, checkpoint=finished["checkpoint"])
    assert again.calls == ["a"]
    assert [event["stage"] for event in resumed if event["type"] == "ApplicationStateChanged"] == ["a", "b"]
    straight = drive(plan(["x", "a", "b"], [("x", "a")], limit=1), Gated(definition))
    assert without_run_ids(resumed[-1]["state"]) == without_run_ids(straight[-1]["state"])


def review_plan(limit=3):
    return plan(["a", "h", "b"], human=["h"], limit=limit)


def test_a_review_that_becomes_ready_waits_for_running_steps_and_nothing_after_it_starts():
    definition = review_plan()
    provider = Gated(definition, gated=["a"])
    asked = []

    def handler(request):
        asked.append(list(provider.started))
        return {"text": "Looks right"}

    async def go():
        run = start(definition, provider, human_handler=handler)
        await until(lambda: "a" in provider.started, what="a to start")
        await asyncio.sleep(0.05)
        assert not run.of("HumanInputRequested"), "the review waits for the step running beside it"
        assert "b" not in provider.started, "and nothing after the review starts"
        provider.release("a")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "completed"
    assert asked == [["a"]], "asked once, after a finished and before b started"
    assert provider.started.index("b") > 0
    straight = drive(review_plan(limit=1), Gated(definition), human_handler=lambda request: {"text": "Looks right"})
    assert commits(events) == commits(straight)


def test_a_review_without_a_handler_waits_with_the_running_steps_work_saved():
    definition = review_plan()
    events = drive(definition, Gated(definition, delays={"a": 0.05}))
    assert events[-1]["status"] == "waiting_for_human" and events[-1]["stage"] == "h"
    assert set(events[-1]["checkpoint"]["patches"]) == {"a"}
    assert not any(event.get("stage") == "b" for event in events)


def test_one_failing_step_does_not_cancel_the_steps_beside_it():
    definition = plan(["a", "b", "c"])
    provider = Gated(definition, gated=["a", "c"], scripts={"b": [EMPTY, EMPTY, EMPTY]})

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "b"), what="b to fail")
        failed = run.of("StageFinished", "b")[0]
        assert failed["status"] == "failed" and failed["reason"]
        assert "patch" not in failed
        assert not run.of("ApplicationFinished"), "the run waits for a and c"
        provider.release("a", "c")
        return await run.done()

    events = asyncio.run(go())
    finished = events[-1]
    assert finished["status"] == "failed" and finished["stage"] == "b"
    assert "checkpoint" not in finished
    assert {"a", "c"} <= set(provider.finished), "a and c were not cancelled"
    outcomes = {event["stage"]: event["status"] for event in events if event["type"] == "StageFinished"}
    assert outcomes == {"a": "completed", "b": "failed", "c": "completed"}
    # c comes after the failed b in plan order: it joins the saved results anyway.
    assert [event["stage"] for event in events if event["type"] == "ApplicationStateChanged"] == ["a", "c"]
    assert finished["state"] == {"a": "a says hi", "b": "", "c": "c says hi"}


def test_with_a_failure_and_a_stop_at_once_the_run_reports_the_failure():
    definition = plan(["a", "b", "c"])
    provider = Gated(definition, gated=["c"], scripts={"a": [ProviderError("timeout", "slow")], "b": [EMPTY, EMPTY, EMPTY]})

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "a") and run.of("StageFinished", "b"), what="a and b to end")
        provider.release("c")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "failed" and events[-1]["stage"] == "b"
    outcomes = {event["stage"]: event["status"] for event in events if event["type"] == "StageFinished"}
    assert outcomes == {"a": "stopped", "b": "failed", "c": "completed"}
    stopped = next(event for event in events if event["type"] == "StageFinished" and event["stage"] == "a")
    assert stopped["reason"], "each step says why it ended"


def test_a_transient_stop_lets_the_others_finish_and_keeps_them_in_the_checkpoint():
    definition = plan(["a", "b", "c"])
    provider = Gated(definition, gated=["b", "c"], scripts={"a": [ProviderError("unreachable", "down")]})

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "a"), what="a to stop")
        provider.release("b", "c")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "stopped" and events[-1]["stage"] == "a"
    assert set(events[-1]["checkpoint"]["patches"]) == {"b", "c"}, "their work is saved for the resume"


def test_an_uncertain_action_lets_the_step_beside_it_finish(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    announce = Recorder(check=checker("inconclusive", summary="No answer"))
    announce.fail = [TimeoutError()]
    build = Recorder("open_change")
    build.gate = asyncio.Event()
    tools = registry(announce, build)
    run_id = repository.create(write_request(parallel=True))
    provider = scripted(writer=[note(), {"type": "final", "content": "Done", "text": "Announced"}],
                        builder=[{"type": "tool", "name": "open_change", "arguments": {"text": "change"}},
                                 {"type": "final", "content": "Done", "text": "Built"}])

    async def go():
        run = Driven(repository.execute(run_id, provider, tools, approver=approve))
        await until(lambda: run.of("StageFinished", "announce") and build.started.is_set(), what="announce to stop mid-build")
        assert run.of("StageFinished", "announce")[0]["status"] == "stopped"
        assert not run.of("ApplicationFinished")
        build.gate.set()
        return await run.done()

    events = asyncio.run(go())
    finished = events[-1]
    assert finished["status"] == "stopped" and "uncertain" in finished["reason"]
    assert set(finished["checkpoint"]["patches"]) == {"build"}
    assert len(build.calls) == 1 and repository.actions(run_id)[0]["stage"] == "announce"


def test_a_blocked_step_keeps_its_output_and_the_step_beside_it_finishes():
    definition = plan(["a", "b"])
    blocked = {"type": "final", "content": "Done", "text": "What I found", "blocker": "No access to the archive"}
    provider = Gated(definition, gated=["b"], scripts={"a": [blocked]})

    async def go():
        run = start(definition, provider)
        await until(lambda: run.of("StageFinished", "a"), what="a to end blocked")
        provider.release("b")
        return await run.done()

    events = asyncio.run(go())
    assert events[-1]["status"] == "failed" and events[-1]["stage"] == "a"
    assert "No access to the archive" in events[-1]["reason"]
    assert events[-1]["state"] == {"a": "What I found", "b": "b says hi"}
    assert "b" in provider.finished


def test_nothing_is_left_running_when_the_run_itself_is_cancelled():
    definition = plan(["a", "b"])
    provider = Gated(definition, gated=["a", "b"])

    async def go():
        run = start(definition, provider)
        await until(lambda: provider.running == 2, what="both to start")
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        await asyncio.sleep(0.02)
        return provider.running

    assert asyncio.run(go()) == 0
