"""Tests for the progress log, driven by events instead of a real run."""

import io

from conftest import (
    CONFIG,
    make_budget,
)

from conclave import (
    AgentFinished,
    Approval,
    BudgetExceeded,
    RunBudget,
    ToolCalled,
    ToolDenied,
)
from conclave.protocol import (
    AgentResult,
    PrivateState,
)
from investigation.render import Renderer


def make() -> Renderer:
    return Renderer(io.StringIO())


def out(render: Renderer) -> str:
    return render.stream.getvalue()


def finished(
    agent: str = "writer",
    parent_run_id=None,
    content: str = "summary",
    notes=None,
    nested=None,
) -> AgentFinished:
    result = AgentResult(
        content=content,
        private=PrivateState(CONFIG),
        nested=nested or [],
    )

    if notes:
        result.private.notes.extend(notes)

    return AgentFinished(
        run_id="r1",
        parent_run_id=parent_run_id,
        agent=agent,
        status="completed",
        result=result,
    )


def test_round_header():
    render = make()
    render.round_header(2)

    printed = out(render)

    assert "=" * 70 in printed
    assert "ROUND 2" in printed


def test_reason_and_planned_and_skipped():
    render = make()

    render.reason("because")
    render.planned("critic", "review it")
    render.skipped("delegation budget exhausted")

    printed = out(render)

    assert "Reason: because" in printed
    assert "-> critic: review it" in printed
    assert "  x  delegation budget exhausted" in printed


def test_notice_is_bracketed():
    render = make()
    render.notice("something odd happened")

    assert (
        "[something odd happened]"
        in out(render)
    )


def test_final_answer():
    render = make()
    render.final("the answer")

    printed = out(render)

    assert "[coordinator -> done]" in printed
    assert "the answer" in printed


def test_header():
    render = make()

    render.header(
        "why?",
        "/repo",
        "ollama",
    )

    printed = out(render)

    assert "QUESTION\nwhy?" in printed
    assert "WORKSPACE\n/repo" in printed
    assert "MODEL PROVIDER\nollama" in printed
    assert "MODEL\nprovider default" in printed


def test_store_changed():
    from conclave import StoreChanged

    render = make()

    render.store_changed(
        StoreChanged(
            keys=("draft", "notes"),
            writer="coordinator",
            version=2,
        )
    )

    assert (
        "[state v2 by coordinator: "
        "draft, notes]"
        in out(render)
    )


# =============================================================================
# Events
# =============================================================================


def test_tool_called_reports_size():
    render = make()

    render.handle(
        ToolCalled(
            run_id="r1",
            agent="writer",
            tool="read_file",
            arguments={},
            capabilities=frozenset(),
            approval=Approval.NOT_REQUIRED,
            observation="some text",
            output_chars=9,
            truncated=False,
            ok=True,
        )
    )

    assert (
        "· writer read_file -> 9 chars"
        in out(render)
    )


def test_tool_called_reports_truncation():
    render = make()

    render.handle(
        ToolCalled(
            run_id="r1",
            agent="writer",
            tool="read_file",
            arguments={},
            capabilities=frozenset(),
            approval=Approval.NOT_REQUIRED,
            observation="text",
            output_chars=4,
            truncated=True,
            ok=True,
        )
    )

    assert "(truncated)" in out(render)


def test_tool_called_reports_the_reason_it_failed():
    """A bare "failed" is not a diagnosis; the tool's message is."""

    render = make()

    render.handle(
        ToolCalled(
            run_id="r1",
            agent="writer",
            tool="read_file",
            arguments={},
            capabilities=frozenset(),
            approval=Approval.NOT_REQUIRED,
            observation=(
                "Tool error: gh is not authenticated\n"
                "more detail"
            ),
            output_chars=45,
            truncated=False,
            ok=False,
        )
    )

    printed = out(render)

    assert (
        "! writer read_file: Tool error: "
        "gh is not authenticated" in printed
    )

    assert "more detail" not in printed


def test_tool_denied_is_reported():
    render = make()

    render.handle(
        ToolDenied(
            run_id="r1",
            agent="writer",
            tool="save_draft",
            reason="approval-refused",
            observation="Refused.",
            capabilities=frozenset(),
        )
    )

    assert (
        "! writer save_draft: "
        "approval-refused" in out(render)
    )


def test_budget_exceeded_is_reported():
    render = make()

    render.handle(
        BudgetExceeded(
            run_id="r1",
            dimension="tokens",
            limit=100.0,
            used=120.0,
        )
    )

    assert (
        "! budget: tokens exhausted (120/100)"
        in out(render)
    )


def test_top_level_agents_report_their_work():
    render = make()

    render.handle(
        finished(
            notes=["checked a.py"],
            nested=[
                {
                    "agent": "web-researcher",
                    "delegated_by": "writer",
                    "task": "look it up",
                    "status": "refused",
                    "reason": "maximum depth is 1",
                }
            ],
        )
    )

    printed = out(render)

    assert "[writer]\nsummary" in printed
    assert "note: checked a.py" in printed

    assert (
        "nested: writer -> web-researcher "
        "[refused] look it up" in printed
    )

    assert "maximum depth is 1" in printed


def test_nested_agents_do_not_print_their_own_report():
    render = make()

    render.handle(
        finished(parent_run_id="parent")
    )

    assert out(render) == ""


def test_unrelated_events_are_ignored():
    from conclave import (
        AgentStarted,
        RunConfig,
        RunStarted,
    )

    render = make()

    render.handle(
        AgentStarted(
            run_id="r1",
            parent_run_id=None,
            agent="writer",
            depth=1,
            task="t",
        )
    )

    render.handle(
        RunStarted(
            run_id="r1",
            task="t",
            config=CONFIG,
        )
    )

    assert out(render) == ""


# =============================================================================
# Budgets
# =============================================================================


def test_budget_plan_is_silent_when_unbounded():
    render = make()

    render.budget_plan(make_budget())

    assert out(render) == ""


def test_budget_plan_lists_the_active_dimensions():
    render = make()

    render.budget_plan(
        make_budget(
            tool_calls=5,
            tokens=1000,
        )
    )

    printed = out(render)

    assert "BUDGET" in printed
    assert "tool_calls <= 5" in printed
    assert "tokens <= 1000" in printed
    assert "cost_usd" not in printed
