"""T6 — end to end through the run store: a run that stops mid-write never sends
that write again without a check or a person, and completed writes are never
sent again on resume.

Each scenario runs a real application through `RunRepository` with a scripted
provider and a recording destructive tool.
"""

import asyncio
import json

import pytest

from actions_support import (LOOK, Recorder, approve, checker, drain, final, note, registry,
                             run_until_blocked, scripted, write_request)
from conclave import Completion
from conclave.application.runs import RunRepository
from conclave.application.service import public_record


@pytest.fixture
def repository(tmp_path):
    return RunRepository(tmp_path / "runs.sqlite")


def events(repository, run_id, kind=None):
    found = [item["event"] for item in repository.get(run_id)["events"]]
    return [event for event in found if kind is None or event["type"] == kind]


def interrupted_mid_write(repository, recorder, *, request=None, provider=None, tools=None):
    run_id = repository.create(request or write_request())
    stream = repository.execute(run_id, provider or scripted(writer=[note(), final()]), tools or registry(recorder),
                                approver=approve)
    asyncio.run(run_until_blocked(stream, recorder))
    recorder.gate = None
    return run_id


def resume(repository, run_id, provider, tools):
    async def exercise():
        async with asyncio.timeout(10):
            return await drain(repository.resume(run_id, provider, tools, approver=approve))

    return asyncio.run(exercise())


def only_action(repository, run_id):
    actions = repository.actions(run_id)
    assert len(actions) == 1
    return actions[0]


def test_a_run_that_stopped_mid_write_shows_the_write_as_uncertain(repository):
    recorder = Recorder(check=checker("found"))
    run_id = interrupted_mid_write(repository, recorder)
    record = public_record(repository.get(run_id))
    assert record["status"] == "interrupted"
    assert record["resumable"] is True, "a journaled run can be continued once its writes are settled"
    assert record["actions"] == {"to_check": 1, "needs_decision": 0, "unknown": 0}
    action = only_action(repository, run_id)
    assert (action["state"], action["stage"], action["agent"], action["tool"]) == ("uncertain", "announce", "writer", "send_note")
    uncertain = events(repository, run_id, "ActionUncertain")
    assert uncertain == [{**uncertain[0], "action_id": action["id"], "stage": "announce", "agent": "writer", "tool": "send_note"}]
    assert uncertain[0]["reason"]
    started = events(repository, run_id, "ApplicationStarted")[0]
    assert started["journal"] == 1


def test_a_crash_then_a_check_that_finds_it_never_sends_it_again(repository):
    recorder = Recorder(check=checker("found", summary="Found the note", detail="The note is in #eng-releases"))
    run_id = interrupted_mid_write(repository, recorder)
    tools = registry(recorder)
    checked = asyncio.run(repository.check_actions(run_id, tools))
    assert checked[0]["state"] == "done" and checked[0]["settled_by"] == "check"
    assert [event["type"] for event in events(repository, run_id)][-2:] == ["ActionChecked", "ActionSettled"]
    provider = scripted(writer=[final("Announced after the check")])
    resume(repository, run_id, provider, tools)
    assert repository.get(run_id)["status"] == "completed"
    assert len(recorder.calls) == 1, "the write was sent once, before the crash"
    assert "The note is in #eng-releases" in provider.prompts[0][1]


def test_a_crash_with_no_check_waits_for_a_person_then_continues_without_resending(repository):
    from conclave.application.runs import ActionsUnsettled

    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder)
    assert public_record(repository.get(run_id))["actions"] == {"to_check": 0, "needs_decision": 1, "unknown": 0}
    tools = registry(recorder)
    action_id = only_action(repository, run_id)["id"]
    with pytest.raises(ActionsUnsettled) as refused:
        resume(repository, run_id, scripted(writer=[final()]), tools)
    assert refused.value.actions == [action_id]
    assert "Settle 1 action before continuing" in str(refused.value)
    assert repository.get(run_id)["status"] == "interrupted", "a refused resume changes nothing"
    repository.settle(run_id, action_id, "done", by="Ana Ruiz", note="Posted at 10:41")
    provider = scripted(writer=[final()])
    resume(repository, run_id, provider, tools)
    assert repository.get(run_id)["status"] == "completed"
    assert len(recorder.calls) == 1
    assert "Ana Ruiz confirmed this action already happened" in provider.prompts[0][1]
    assert "Posted at 10:41" in provider.prompts[0][1]


def test_a_person_may_choose_to_send_it_again_exactly_once(repository):
    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder)
    tools = registry(recorder)
    action_id = only_action(repository, run_id)["id"]
    repository.settle(run_id, action_id, "resend", by="Ana Ruiz")
    resume(repository, run_id, scripted(writer=[final()]), tools)
    assert repository.get(run_id)["status"] == "completed"
    assert len(recorder.calls) == 2 and recorder.actions == [action_id, action_id]
    resent = [event for event in events(repository, run_id, "ToolCalled") if event.get("resent")]
    assert len(resent) == 1 and resent[0]["action_id"] == action_id


def test_undo_is_possible_until_continue(repository):
    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder)
    action_id = only_action(repository, run_id)["id"]
    repository.settle(run_id, action_id, "resend", by="Ana Ruiz")
    repository.settle(run_id, action_id, "undo", by="Ana Ruiz")
    assert only_action(repository, run_id)["state"] == "uncertain"
    assert events(repository, run_id)[-1] == {**events(repository, run_id)[-1], "type": "ActionDecisionUndone",
                                              "action_id": action_id, "undone_by": "Ana Ruiz"}
    repository.settle(run_id, action_id, "done", by="Ana Ruiz")
    resume(repository, run_id, scripted(writer=[final()]), registry(recorder))
    with pytest.raises(ValueError, match="already used when the run continued"):
        repository.settle(run_id, action_id, "undo", by="Ana Ruiz")


def test_a_completed_write_is_never_sent_again_when_a_later_step_crashes(repository):
    recorder = Recorder()
    blocked = asyncio.Event()

    class StallsAfterTheWrite:
        def __init__(self):
            self.replies = [note()]

        async def complete(self, messages):
            if self.replies:
                return Completion(text=json.dumps(self.replies.pop(0)))
            blocked.set()
            await asyncio.Event().wait()

    run_id = repository.create(write_request())

    async def crash():
        task = asyncio.create_task(drain(repository.execute(run_id, StallsAfterTheWrite(), registry(recorder), approver=approve)))
        await blocked.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(crash())
    record = public_record(repository.get(run_id))
    assert record["status"] == "interrupted" and record["resumable"]
    assert record["actions"] == {"to_check": 0, "needs_decision": 0, "unknown": 0}
    provider = scripted(writer=[final()])
    resume(repository, run_id, provider, registry(recorder))
    assert len(recorder.calls) == 1
    assert "TOOL RESULT for send_note" in provider.prompts[0][1], "the agent continues from the write's result"


def test_a_step_stopped_by_its_action_limit_continues_from_its_last_write(repository):
    recorder = Recorder()
    run_id = repository.create(write_request(max_tool_steps=2))
    # The write, a look, then a write-up that is not an answer: the step runs out.
    asyncio.run(drain(repository.execute(run_id, scripted(writer=[note(), LOOK, LOOK]), registry(recorder), approver=approve)))
    assert repository.get(run_id)["status"] == "stopped"
    provider = scripted(writer=[final()])
    resume(repository, run_id, provider, registry(recorder))
    assert repository.get(run_id)["status"] == "completed"
    assert len(recorder.calls) == 1, "the step continued; it did not start over"
    assert "TOOL RESULT for send_note" in provider.prompts[0][1]


def test_a_step_that_never_wrote_still_starts_over(repository):
    recorder = Recorder()
    run_id = repository.create(write_request(max_tool_steps=1))
    asyncio.run(drain(repository.execute(run_id, scripted(writer=[LOOK, LOOK]), registry(recorder), approver=approve)))
    assert repository.get(run_id)["status"] == "stopped"
    assert repository.journal.progress(run_id) == {}, "reads leave no progress behind"
    provider = scripted(writer=[final()])
    resume(repository, run_id, provider, registry(recorder))
    assert "STAGE TASK" in provider.prompts[0][1], "the step is asked from its start, as before"


def test_an_unclear_failure_in_a_live_run_stops_it_for_a_decision(repository):
    recorder = Recorder(check=checker("inconclusive", summary="The service did not answer the check"))
    recorder.fail = [TimeoutError()]
    run_id = repository.create(write_request())
    asyncio.run(drain(repository.execute(run_id, scripted(writer=[note(), final()]), registry(recorder), approver=approve)))
    finished = events(repository, run_id, "ApplicationFinished")[-1]
    assert finished["status"] == "stopped"
    assert finished["reason"].startswith("The outcome of") and "uncertain" in finished["reason"]
    record = public_record(repository.get(run_id))
    assert record["resumable"] and record["actions"] == {"to_check": 0, "needs_decision": 1, "unknown": 0}
    action = only_action(repository, run_id)
    assert action["check_result"] == "inconclusive"
    repository.settle(run_id, action["id"], "done", by="Ana Ruiz")
    resume(repository, run_id, scripted(writer=[final()]), registry(recorder))
    assert repository.get(run_id)["status"] == "completed" and len(recorder.calls) == 1


def kept(repository, run_id):
    saved = [event["checkpoint"]["patches"] for event in events(repository, run_id) if "checkpoint" in event]
    return "announce" in (saved[-1] if saved else {}) or (repository.journal.progress(run_id).get("announce") or {}).get("output") is not None


def test_a_step_that_finished_beside_a_crashed_one_is_not_run_again(repository):
    announce = Recorder()
    build = Recorder("open_change")
    tools = registry(announce, build)
    run_id = repository.create(write_request(parallel=True))
    provider = scripted(writer=[note(), final("Announced")],
                        builder=[{"type": "tool", "name": "open_change", "arguments": {"text": "change"}}, final("Built")])

    async def crash():
        build.gate = asyncio.Event()
        task = asyncio.create_task(drain(repository.execute(run_id, provider, tools, approver=approve)))
        async with asyncio.timeout(5):
            # The finished step is kept either way: in the checkpoint its
            # finish records (steps run side by side), or as saved output.
            while not (kept(repository, run_id) and build.started.is_set()):
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(crash())
    build.gate = None
    uncertain = [action for action in repository.actions(run_id) if action["state"] == "uncertain"]
    assert [action["stage"] for action in uncertain] == ["build"]
    repository.settle(run_id, uncertain[0]["id"], "done", by="Ana Ruiz")
    again = scripted(writer=[final("Written twice")], builder=[final("Built")])
    resume(repository, run_id, again, tools)
    record = repository.get(run_id)
    assert record["status"] == "completed"
    assert "writer" not in [name for name, _ in again.prompts], "the finished step is not asked again"
    assert events(repository, run_id, "ApplicationFinished")[-1]["state"]["notice"] == "Announced"
    assert len(announce.calls) == 1 and len(build.calls) == 1


def test_a_cancelled_run_keeps_the_uncertain_write_for_the_record_only(repository):
    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder)
    action_id = only_action(repository, run_id)["id"]
    repository.cancel_waiting(run_id)
    record = public_record(repository.get(run_id))
    assert record["status"] == "cancelled" and record["resumable"] is False
    assert record["actions"] == {"to_check": 0, "needs_decision": 0, "unknown": 1}
    assert repository.journal.arguments(action_id) is None, "details are purged once nothing can be decided"
    with pytest.raises(ValueError):
        repository.settle(run_id, action_id, "done", by="Ana Ruiz")


def test_a_run_from_before_the_journal_still_cannot_be_continued(repository):
    run_id = repository.create(write_request())
    repository._claim(run_id)
    repository._append(run_id, {"type": "ApplicationStarted", "name": "Announce", "state": {}, "views": []})
    repository._append(run_id, {"type": "ApplicationFinished", "status": "interrupted", "reason": "The host stopped"})
    assert public_record(repository.get(run_id))["resumable"] is False
    with pytest.raises(ValueError, match="Only a waiting, paused or stopped run"):
        asyncio.run(drain(repository.resume(run_id, scripted(writer=[final()]), registry(Recorder()))))


def test_an_interrupted_journaled_run_with_nothing_in_flight_resumes_from_its_last_step(repository):
    run_id = repository.create(write_request())

    async def interrupt():
        stream = repository.execute(run_id, scripted(writer=[final()]), registry(Recorder()), approver=approve)
        await anext(stream)
        await stream.aclose()

    asyncio.run(interrupt())
    assert repository.get(run_id)["status"] == "interrupted"
    resume(repository, run_id, scripted(writer=[final("Resumed")]), registry(Recorder()))
    assert repository.get(run_id)["status"] == "completed"


def test_a_host_restart_sweeps_writes_that_were_in_flight(tmp_path):
    repository = RunRepository(tmp_path / "runs.sqlite")
    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder)
    action_id = only_action(repository, run_id)["id"]
    # What a process that died outright leaves: still running, still sending.
    with repository._connect() as connection:
        connection.execute("UPDATE studio_runs SET status = 'running' WHERE id = ?", (run_id,))
        connection.execute("UPDATE studio_run_actions SET state = 'sending' WHERE id = ?", (action_id,))
    successor = RunRepository(tmp_path / "runs.sqlite")
    assert successor.sweep_abandoned() == [run_id]
    assert only_action(successor, run_id)["state"] == "uncertain"
    assert public_record(successor.get(run_id))["resumable"] is True


def test_the_public_record_never_carries_progress_arguments_or_notes(repository):
    recorder = Recorder()
    run_id = interrupted_mid_write(repository, recorder, provider=scripted(writer=[note("private words"), final()]))
    action_id = only_action(repository, run_id)["id"]
    repository.settle(run_id, action_id, "done", by="Ana Ruiz", note="private note")
    text = json.dumps(public_record(repository.get(run_id)))
    assert "private words" not in text and "private note" not in text
    assert '"checkpoint"' not in text and '"frames"' not in text
