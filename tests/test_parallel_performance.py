"""Performance review of parallel steps (docs/architecture/parallel-steps-performance-review.md).

PP1: a run's record keeps only its newest checkpoint, so it grows with the
     steps, not with their square; every resume still works.
PP2: `StageFinished` repeats a step's output only while the step is held.
"""

import json
import sqlite3

from conclave import ToolRegistry
from parallel_support import Gated, collect, plan, store_run


def stored(repository, run_id):
    with sqlite3.connect(repository.path) as connection:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM studio_run_events WHERE run_id = ? ORDER BY sequence", (run_id,))]


class Large(Gated):
    """Each step answers with about `size` characters."""

    def __init__(self, definition, size, **kwargs):
        super().__init__(definition, **kwargs)
        self.size = size

    async def complete(self, messages, **kwargs):
        completion = await super().complete(messages, **kwargs)
        action = json.loads(completion.text)
        if action.get("type") == "final" and "text" in action:
            action["text"] = (action["text"] + " ") * (self.size // 10)
        return type(completion)(text=json.dumps(action), usage=completion.usage)


def test_a_run_keeps_only_its_newest_checkpoint(tmp_path):
    steps = [f"s{index}" for index in range(8)]
    definition = plan(steps, limit=3)
    repository, run_id = store_run(tmp_path, definition)
    collect(repository.execute(run_id, Large(definition, 20_000, delays={step: 0.001 * (index % 3) for index, step in enumerate(steps)}),
                               ToolRegistry(20000)))
    events = stored(repository, run_id)
    kept = [event for event in events if "checkpoint" in event]
    assert len(kept) == 1, "one checkpoint, the newest"
    assert set(kept[0]["checkpoint"]["patches"]) == set(steps)
    checkpoint_bytes = len(json.dumps(kept[0]["checkpoint"]))
    assert checkpoint_bytes < 8 * 20_000 * 1.5, "the checkpoints grow with the steps, not their square"
    assert all(event.get("patch") for event in events if event["type"] == "ApplicationStateChanged"), "each output is still on its own event"


def test_a_paused_run_resumes_from_its_newest_checkpoint_and_keeps_one(tmp_path):
    definition = plan(["a", "b", "c"], [("a", "c"), ("b", "c")])
    repository, run_id = store_run(tmp_path, definition)
    asked = {"pause": False}

    class PauseAfterB(Gated):
        async def complete(self, messages, **kwargs):
            if self.member(messages) == "b":
                asked["pause"] = True
            return await super().complete(messages, **kwargs)

    collect(repository.execute(run_id, PauseAfterB(definition), ToolRegistry(20000), pause_requested=lambda: asked["pause"]))
    assert repository.get(run_id)["status"] == "paused"
    again = Gated(definition)
    events = collect(repository.resume(run_id, again, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed" and again.calls == ["c"]
    assert len([event for event in stored(repository, run_id) if "checkpoint" in event]) == 1


def test_a_review_resumes_after_the_older_checkpoints_are_gone(tmp_path):
    definition = plan(["a", "b", "h", "c"], [("a", "h"), ("b", "h"), ("h", "c")], human=["h"])
    repository, run_id = store_run(tmp_path, definition)
    collect(repository.execute(run_id, Gated(definition), ToolRegistry(20000)))
    record = repository.get(run_id)
    assert record["status"] == "waiting_for_human"
    assert len([event for event in stored(repository, run_id) if "checkpoint" in event]) == 1
    repository.submit_review(run_id, record["reviews"][0]["id"], reviewer="Ana", output={"text": "Looks right"})
    again = Gated(definition)
    events = collect(repository.resume(run_id, again, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed" and again.calls == ["c"]


def test_a_record_written_with_many_checkpoints_still_resumes_from_its_newest(tmp_path):
    """Runs recorded before this change keep every checkpoint; the newest is the one read."""
    definition = plan(["a", "b", "c"], [("a", "b"), ("b", "c")], limit=1)
    repository, run_id = store_run(tmp_path, definition)
    asked = {"pause": False}

    class PauseAfterB(Gated):
        async def complete(self, messages, **kwargs):
            if self.member(messages) == "b":
                asked["pause"] = True
            return await super().complete(messages, **kwargs)

    collect(repository.execute(run_id, PauseAfterB(definition), ToolRegistry(20000), pause_requested=lambda: asked["pause"]))
    # Put back an older checkpoint, as an older version would have left it.
    with sqlite3.connect(repository.path) as connection:
        sequence, payload = connection.execute(
            "SELECT sequence, payload FROM studio_run_events WHERE run_id = ? AND payload LIKE '%\"ApplicationStateChanged\"%' ORDER BY sequence LIMIT 1",
            (run_id,)).fetchone()
        newest = next(event for event in reversed(stored(repository, run_id)) if "checkpoint" in event)["checkpoint"]
        older = {**newest, "patches": {"a": newest["patches"]["a"]}}
        connection.execute("UPDATE studio_run_events SET payload = ? WHERE run_id = ? AND sequence = ?",
                           (json.dumps({**json.loads(payload), "checkpoint": older}), run_id, sequence))
    again = Gated(definition)
    events = collect(repository.resume(run_id, again, ToolRegistry(20000)))
    assert events[-1]["status"] == "completed" and again.calls == ["c"]


def test_stage_finished_repeats_the_output_only_while_the_step_is_held(tmp_path):
    definition = plan(["a", "b"])
    repository, run_id = store_run(tmp_path, definition)
    collect(repository.execute(run_id, Gated(definition, delays={"a": 0.05}), ToolRegistry(20000)))
    finished = {event["stage"]: event for event in stored(repository, run_id) if event["type"] == "StageFinished"}
    assert "patch" not in finished["a"], "a joined the results at once: its output is on ApplicationStateChanged"
    assert finished["b"]["held"] is True and finished["b"]["patch"] == "b says hi", "b waited: Studio shows it from here"
