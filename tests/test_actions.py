"""T1 — the core vocabulary of an external action.

An action is one call to a tool that needs approval. These are the pieces the
runtime, the journal and the checks share: whether a failure proves nothing
happened, what a check can answer, and which action a running tool belongs to.
"""

import asyncio

import pytest

from conclave import Capability, Tool


async def noop(**arguments):
    return "ok"


def test_only_a_proven_non_event_is_a_clear_failure():
    from conclave.actions import ActionNotPerformed, classify

    class Refused(ActionNotPerformed, ValueError):
        pass

    class NotSent(RuntimeError):
        sent = False

    class Rejected(RuntimeError):
        sent, rejected = True, True

    assert classify(ActionNotPerformed("bad path")) == "failed"
    assert classify(Refused("bad path")) == "failed", "a subclass keeps its ValueError behaviour and its meaning"
    assert classify(NotSent("DNS failed")) == "failed"
    assert classify(Rejected("HTTP 422")) == "failed"


@pytest.mark.parametrize("error", [RuntimeError("boom"), TimeoutError(), OSError("reset"),
                                   asyncio.CancelledError(), KeyError("shape")])
def test_anything_else_leaves_the_outcome_unknown(error):
    from conclave.actions import classify

    assert classify(error) == "uncertain"


def test_a_sent_request_without_a_clear_answer_is_unknown():
    from conclave.actions import classify

    class Sent(RuntimeError):
        sent, rejected = True, False

    assert classify(Sent("HTTP 502")) == "uncertain"


def test_a_check_answers_in_a_closed_vocabulary_and_stays_short():
    from conclave.actions import CHECK_RESULTS, CheckResult

    assert CHECK_RESULTS == ("found", "absent", "inconclusive", "unavailable")
    found = CheckResult("found", "Found pull request #866", detail="#866 open")
    assert (found.result, found.exact, found.detail) == ("found", False, "#866 open")
    assert len(CheckResult("absent", "x" * 1000, exact=True).summary) == 300
    assert len(CheckResult("found", "ok", detail="d" * 5000).detail) == 2000
    with pytest.raises(ValueError):
        CheckResult("maybe", "unknown word")


def test_a_tool_declares_its_check_or_why_it_has_none():
    from conclave.actions import Check, CheckResult

    plain = Tool("write", "Write", {}, noop, frozenset({Capability.WRITE}))
    assert plain.check is None and plain.check_unavailable == ""

    async def run(arguments, context):
        return CheckResult("found", "there")

    checked = Tool("write", "Write", {}, noop, frozenset({Capability.WRITE}),
                   check=Check(run=run, describe=lambda arguments: f"Will look for {arguments['path']}"))
    assert checked.check.describe({"path": "a.txt"}) == "Will look for a.txt"
    assert Check(run=run).describe({"path": "a.txt"}) == "", "a check may describe nothing"
    uncheckable = Tool("send", "Send", {}, noop, frozenset({Capability.WRITE}), check_unavailable="Webhooks cannot be checked")
    assert uncheckable.check is None and uncheckable.check_unavailable == "Webhooks cannot be checked"


def test_each_running_tool_sees_its_own_action():
    from conclave.actions import current_action

    seen = {}

    async def tool(name):
        token = current_action.set(name)
        try:
            await asyncio.sleep(0)
            seen[name] = current_action.get()
        finally:
            current_action.reset(token)

    async def exercise():
        await asyncio.gather(tool("a1"), tool("b2"))

    asyncio.run(exercise())
    assert seen == {"a1": "a1", "b2": "b2"}
    assert current_action.get() is None


def test_a_stop_names_the_action_and_why():
    from conclave.actions import ActionStopped

    stop = ActionStopped("a1", "The outcome of Slack · Send message is uncertain")
    assert stop.action_id == "a1"
    assert str(stop) == stop.reason == "The outcome of Slack · Send message is uncertain"


def test_what_the_agent_is_told_about_a_settled_action():
    from conclave.actions import settled_observation

    checked = settled_observation({"state": "done", "settled_by": "check", "check_summary": "Found pull request #866",
                                   "check_detail": "#866 Fix duplicate notifications (open)", "note": None})
    assert "#866 Fix duplicate notifications (open)" in checked
    assert "already happened" in checked and "Do not repeat" in checked

    person = settled_observation({"state": "done", "settled_by": "Ana Ruiz", "note": "Posted at 10:41",
                                  "check_summary": None, "check_detail": None})
    assert "Ana Ruiz confirmed this action already happened" in person
    assert "Their note (their words, not the service's reply): Posted at 10:41" in person

    silent = settled_observation({"state": "done", "settled_by": "Ana Ruiz", "note": None,
                                  "check_summary": None, "check_detail": None})
    assert "note" not in silent.lower()


def test_the_vocabulary_is_part_of_the_public_package():
    import conclave

    for name in ("ActionNotPerformed", "ActionStopped", "ActionContext", "ActionStart", "Check",
                 "CheckResult", "Resolution", "classify", "current_action"):
        assert hasattr(conclave, name), name
