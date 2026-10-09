"""The terminal's account of a run.

Records come from real executions through the repository, so the fold is checked
against the event stream the runtime actually emits rather than one written by
hand for the test.
"""

import json

import pytest

from conclave import ToolRegistry
from conclave.application.presentation import (budget_lines, fold, pending_review, render_run, run_mode,
                                               step_status, value_lines)
from conclave.application.runs import RunRepository
from conftest import ScriptedProvider
from test_studio_runs import collect, request, with_final_stage


def scripted(**options):
    return ScriptedProvider(default={"type": "final", "content": "Done", "text": "Draft text"}, **options)


def steps_block(text):
    """Just the step lines, so a footer word cannot decide what a step says."""

    assert "\nSteps\n" in text, text
    return text.split("\nSteps\n")[1].split("\n\n")[0]


class Broken:
    """A backend that fails, which is how a run reaches `failed`."""

    async def complete(self, messages):
        raise RuntimeError("the backend refused the request")


def record_of(tmp_path, value=None, *, provider=None, budget=None, execute=True, **options):
    repository = RunRepository(tmp_path / "runs.sqlite")
    value = value or request()
    if budget is not None:
        value["definition"]["run_budget"] = {**(value["definition"]["run_budget"] or {}), **budget}
    run_id = repository.create(value)
    if execute:
        collect(repository.execute(run_id, provider or scripted(), ToolRegistry(20000), **options))
    return repository.get(run_id)


# --- Reading a run ----------------------------------------------------------

def test_a_waiting_run_reads_as_steps_and_one_question(tmp_path):
    record = record_of(tmp_path, with_final_stage())
    text = render_run(record)
    assert record["status"] == "waiting_for_human"
    assert f"Run        {record['id']}" in text
    assert "Status     waiting for you" in text
    assert "Application  Research brief · revision 3" in text
    assert "Task       Review the supplied evidence" in text
    # Every step is on one line, in plan order, with what it is doing.
    assert "  1  draft   writer    done" in text
    assert "  2  review  reviewer  needs your input" in text
    assert "  3  final   writer    waiting" in text
    # The question says what to answer, and how, with the run's own identifiers.
    review = pending_review(record)
    assert "Waiting for you" in text
    assert f"conclave review {record['id']} {review['id']} --output <file>" in text
    assert 'Write     "brief" (text)' in text
    assert "Custom result" not in text and "Draft text" in text
    # A waiting run has its own section: it is not also reported as stopped.
    assert "Stopped" not in text


def test_a_completed_run_shows_its_outputs_and_asks_nothing(tmp_path):
    record = record_of(tmp_path, with_final_stage(), human_handler=lambda review: {"text": "Approved"})
    text = render_run(record)
    assert record["status"] == "completed"
    assert "Status     completed" in text
    assert "needs your input" not in text and "Waiting for you" not in text
    assert "  2  review  reviewer  done" in text
    assert "  3  final   writer    done" in text
    assert "Outputs" in text and "brief_view (prose)" in text
    assert "steps done 3 of 3" in text


def test_a_failed_run_says_what_failed_and_what_was_never_reached(tmp_path):
    record = record_of(tmp_path, with_final_stage(), provider=Broken())
    text = render_run(record)
    assert record["status"] == "failed"
    assert "Status     failed" in text
    assert "  1  draft   writer    failed" in steps_block(text)
    assert "not reached" in steps_block(text)
    assert "Stopped    " in text


def test_a_stopped_run_is_not_described_as_complete(tmp_path):
    record = record_of(tmp_path, with_final_stage(), provider=ScriptedProvider(default={"type": "nonsense"}))
    text = render_run(record)
    assert record["status"] == "stopped"
    assert "Status     stopped" in text
    assert "not reached" in steps_block(text) and "done" not in steps_block(text)
    # A stopped run names the member, the limit and the stage, not just that
    # something did not finish.
    assert "Stopped    writer: used all " in text and "action steps without completing draft" in text


def test_a_paused_run_shows_no_question_and_no_reason(tmp_path):
    record = record_of(tmp_path, with_final_stage(), pause_requested=lambda: True)
    text = render_run(record)
    assert record["status"] == "paused"
    assert "Status     paused" in text
    assert "Waiting for you" not in text and "Stopped" not in text
    # Nothing started, so every step is waiting its turn.
    assert "needs your input" not in text and "done" not in steps_block(text)


def test_a_plan_that_has_not_run_yet_is_still_readable(tmp_path):
    record = record_of(tmp_path, with_final_stage(), execute=False)
    text = render_run(record)
    assert record["status"] == "pending" and not record["events"]
    assert "Status     pending" in text
    assert steps_block(text).splitlines()[0].strip().endswith("ready")
    assert steps_block(text).count("waiting") == 2


def test_a_branch_plan_marks_only_reachable_steps_ready(tmp_path):
    value = request()
    value["definition"]["workflow"] = [
        {"id": "first", "agent": "writer", "task": "First", "output": "brief"},
        {"id": "second", "agent": "writer", "task": "Second", "output": "brief"},
        {"id": "third", "agent": "writer", "task": "Third", "output": "brief"},
    ]
    value["definition"]["connections"] = [{"from": "first", "to": "third"}]
    record = record_of(tmp_path, value, execute=False)
    folded = fold(record)
    statuses = {step["id"]: step_status(step, index, folded) for index, step in enumerate(folded["workflow"])}
    assert statuses == {"first": "ready", "second": "ready", "third": "waiting"}


# --- What a value may cost a terminal ---------------------------------------

def test_a_long_value_is_bounded_and_points_at_the_record(tmp_path):
    value = request()
    value["definition"]["workflow"] = [{"id": "draft", "agent": "writer", "task": "Draft", "output": "brief"}]
    record = record_of(tmp_path, value, provider=ScriptedProvider(
        default={"type": "final", "content": "Done", "text": "\n".join(f"line {number}" for number in range(200))}))
    text = render_run(record)
    assert "… (--format json shows the whole record)" in text
    assert "line 23" in text and "line 199" not in text
    assert len(text) < 4000


def test_values_are_rendered_by_their_shape():
    assert value_lines("one\ntwo") == ["    one", "    two"]
    assert value_lines(["a", "b"]) == ["    - a", "    - b"]
    assert value_lines(None) == ["    (empty)"] and value_lines([]) == ["    (empty)"]
    assert value_lines({"key": "value"}) == ["    {", '      "key": "value"', "    }"]
    assert value_lines("") == ["    (empty)"]


# --- Spend ------------------------------------------------------------------

def test_budget_shows_spend_against_declared_limits_only(tmp_path):
    record = record_of(tmp_path, with_final_stage(), budget={"tool_calls": 8, "max_depth": 2}, execute=False)
    (line,) = budget_lines({**record, "metrics": {"tool_calls": 3, "tokens": 1200, "wall_clock_seconds": 0.0009}},
                           fold(record))
    assert line == "Budget     depth 0/2 · tool calls 3/8 · wall clock (s) <0.01 · tokens 1200"
    # A dimension nobody bounded and nobody spent is left out entirely.
    assert budget_lines({**record, "metrics": {}}, fold(record)) == ["Budget     depth 0/2 · tool calls 0/8"]


# --- Refusals ---------------------------------------------------------------

def test_a_record_without_an_id_or_a_definition_is_refused_or_survives():
    with pytest.raises(ValueError, match="without an id"):
        render_run({"status": "completed"})
    text = render_run({"id": "run-1", "status": "running", "events": [], "request": {}})
    assert "Run        run-1" in text and "Application  (unnamed)" in text
    assert run_mode({}) == "unknown"


def test_events_for_steps_this_plan_does_not_have_are_ignored():
    record = {"id": "run-2", "status": "completed", "request": {"definition": {"workflow": [{"id": "a", "agent": "x"}]}},
              "events": [{"sequence": 1, "event": {"type": "StageStarted", "stage": "ghost"}},
                         {"sequence": 2, "event": {"type": "ApplicationStateChanged", "stage": "ghost"}},
                         {"sequence": 3, "event": {"type": "ApplicationFinished", "status": "completed"}}]}
    text = render_run(record)
    assert "  1  a  x  ready" in text and "Status     completed" in text


# --- The command that shows it ----------------------------------------------

def test_the_cli_shows_a_run_as_text_on_request_and_json_by_default(tmp_path, monkeypatch, capsys):
    from conclave import cli

    path = tmp_path / "application.json"
    cli.run(["create", str(path), "--name", "Release writer", "--instructions", "Write release notes."])
    provider = scripted()
    monkeypatch.setattr(cli, "configured_host", lambda **kwargs: __import__(
        "conclave.application.service", fromlist=["LocalRunHost"]).LocalRunHost(
        RunRepository(tmp_path / "runs.sqlite"), provider, ToolRegistry(20000), reviewer="operator"))
    cli.run(["run", str(path), "--task", "Version one landed"])
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "completed"

    cli.run(["status", record["id"], "--format", "text"])
    text = capsys.readouterr().out
    assert "Status     completed" in text and not text.lstrip().startswith("{")
    assert "Run        " + record["id"] in text

    cli.run(["status", record["id"], "--format", "json"])
    assert json.loads(capsys.readouterr().out)["id"] == record["id"]

    with pytest.raises(SystemExit) as invalid:
        cli.run(["status", record["id"], "--format", "yaml"])
    assert invalid.value.code == 2
