"""Tests for structured investigation state and its model steps."""

import asyncio
import io
import json

import pytest

from conftest import CannedProvider

from conclave import RunBudget
from conclave.protocol import complete_json
from investigation.render import Renderer
from investigation.state import (
    EMPTY_STATE,
    STATE_SCHEMA,
    charge_usage,
    decide_next,
    force_final,
    initialize_state,
    make_store,
    normalize_state,
    state_json,
    update_state,
)


INITIAL = json.dumps(
    {
        "facts": [],
        "evidence": [],
        "hypotheses": [
            {
                "id": "H1",
                "claim": "retry double counts",
                "confidence": 0.2,
                "status": "open",
            }
        ],
        "open_questions": [
            {
                "id": "Q1",
                "question": "is the counter shared?",
                "status": "open",
            }
        ],
        "investigations": [],
    }
)


UNREADABLE = (
    '<\uff5cDSML\uff5cinvoke name="bash">'
    "</\uff5cDSML\uff5cinvoke>"
)


@pytest.fixture
def render():
    return Renderer(io.StringIO())


@pytest.fixture
def budget():
    return RunBudget()


def output(render) -> str:
    return render.stream.getvalue()


def test_state_json_is_indented():
    text = state_json(
        {"facts": [{"id": "F1"}]}
    )

    assert text.startswith("{\n")
    assert '"F1"' in text


def test_normalize_state_fills_missing_keys():
    assert normalize_state({}) == EMPTY_STATE


def test_normalize_state_rejects_non_lists():
    normalized = normalize_state(
        {
            "facts": "not a list",
            "evidence": None,
            "hypotheses": {"H1": 1},
            "open_questions": [],
            "investigations": 7,
        }
    )

    assert normalized == EMPTY_STATE


def test_normalize_state_keeps_lists():
    assert normalize_state(
        {"facts": [{"id": "F1"}]}
    )["facts"] == [{"id": "F1"}]


# =============================================================================
# The application's own store schema
# =============================================================================


def test_state_schema_declares_the_investigation_keys():
    assert STATE_SCHEMA.keys == frozenset(
        EMPTY_STATE
    )


def test_store_starts_empty_and_merges():
    store = make_store()

    assert store.snapshot() == EMPTY_STATE

    asyncio.run(
        store.merge(
            {"facts": [{"id": "F1"}]}
        )
    )

    assert store.snapshot()["facts"] == [
        {"id": "F1"}
    ]

    assert store.version == 1


def test_store_reports_what_changed():
    seen = []

    store = make_store(
        on_change=seen.append
    )

    asyncio.run(
        store.merge(
            {"facts": [{"id": "F1"}]},
            writer="coordinator",
        )
    )

    assert seen[0].keys == ("facts",)
    assert seen[0].writer == "coordinator"
    assert seen[0].version == 1


# =============================================================================
# Model steps
# =============================================================================


def test_initialize_state_normalizes(
    budget,
    render,
):
    provider = CannedProvider(INITIAL)

    state = asyncio.run(
        initialize_state(
            provider,
            "why?",
            budget,
            render,
        )
    )

    assert set(state) == set(EMPTY_STATE)
    assert state["hypotheses"][0]["id"] == "H1"

    prompt = provider.calls[0][-1]["content"]

    assert "why?" in prompt
    assert "hypotheses" in prompt


def test_decide_next_describes_budget_and_agents(
    budget,
    render,
):
    provider = CannedProvider(
        '{"status": "done", "answer": "yes"}'
    )

    decision = asyncio.run(
        decide_next(
            provider,
            "why?",
            EMPTY_STATE,
            2,
            budget,
            render,
        )
    )

    assert decision["status"] == "done"

    prompt = provider.calls[0][-1]["content"]

    assert "repo-researcher" in prompt
    assert "critic" in prompt
    assert "Use at most 3" in prompt
    assert "Round:" in prompt


def test_update_state_normalizes(
    budget,
    render,
):
    provider = CannedProvider(
        json.dumps(
            {
                "facts": [{"id": "F1"}],
                "evidence": "bad",
                "hypotheses": [],
                "open_questions": [],
                "investigations": [],
            }
        )
    )

    updated = asyncio.run(
        update_state(
            provider,
            "why?",
            EMPTY_STATE,
            1,
            [
                {
                    "agent": "repo-researcher",
                    "status": "completed",
                    "result": "summary",
                }
            ],
            budget,
            render,
        )
    )

    assert updated["facts"] == [{"id": "F1"}]
    assert updated["evidence"] == []

    prompt = provider.calls[0][-1]["content"]

    assert "New specialist results" in prompt
    assert "summary" in prompt


def test_force_final_returns_text():
    provider = CannedProvider("final answer")

    answer = asyncio.run(
        force_final(
            provider,
            "why?",
            EMPTY_STATE,
        )
    )

    assert answer == "final answer"

    messages = provider.calls[0]

    assert messages[0]["role"] == "system"
    assert "Do not request more research" in (
        messages[1]["content"]
    )


def test_complete_json_round_trip():
    provider = CannedProvider(
        '{"status": "delegate"}'
    )

    assert asyncio.run(
        complete_json(
            provider,
            "s",
            "u",
        )
    ) == {"status": "delegate"}


# =============================================================================
# Unreadable model replies
# =============================================================================


def test_initialize_state_falls_back_to_empty(
    budget,
    render,
):
    provider = CannedProvider(UNREADABLE)

    state = asyncio.run(
        initialize_state(
            provider,
            "why?",
            budget,
            render,
        )
    )

    assert state == EMPTY_STATE
    assert state is not EMPTY_STATE

    assert "continuing with an empty state" in (
        output(render)
    )


def test_decide_next_stops_when_unreadable(
    budget,
    render,
):
    provider = CannedProvider(UNREADABLE)

    decision = asyncio.run(
        decide_next(
            provider,
            "why?",
            EMPTY_STATE,
            1,
            budget,
            render,
        )
    )

    assert decision["status"] == "delegate"
    assert decision["delegations"] == []

    assert "stopping the investigation" in (
        output(render)
    )


def test_update_state_keeps_previous_state(
    budget,
    render,
):
    provider = CannedProvider(UNREADABLE)

    previous = {
        "facts": [{"id": "F1"}],
        "evidence": [],
        "hypotheses": [],
        "open_questions": [],
        "investigations": [],
    }

    updated = asyncio.run(
        update_state(
            provider,
            "why?",
            previous,
            1,
            [],
            budget,
            render,
        )
    )

    assert updated == previous

    assert "keeping the previous state" in (
        output(render)
    )


def test_initialize_state_retries_before_falling_back(
    budget,
    render,
):
    provider = CannedProvider(UNREADABLE)

    asyncio.run(
        initialize_state(
            provider,
            "why?",
            budget,
            render,
        )
    )

    assert len(provider.calls) == 2


# =============================================================================
# Budget accounting for coordinator calls
# =============================================================================


def test_charge_usage_settles_tokens(
    budget,
    render,
):
    from conclave import Usage

    asyncio.run(
        initialize_state(
            CannedProvider(INITIAL),
            "why?",
            budget,
            render,
        )
    )

    charger = charge_usage(budget)

    charger(
        Usage(
            input_tokens=3,
            output_tokens=4,
        )
    )

    assert budget.spent_on("tokens") == 7


# =============================================================================
# The progress log
# =============================================================================


def test_state_summary(render):
    render.state_summary(
        {
            "facts": [{"id": "F1"}],
            "evidence": [{"id": "E1"}],
            "investigations": [{"id": "I1"}],
            "hypotheses": [
                {
                    "id": "H1",
                    "status": "supported",
                    "confidence": 0.8,
                    "claim": "retry double counts",
                }
            ],
            "open_questions": [
                {
                    "id": "Q1",
                    "question": "is it shared?",
                    "status": "open",
                },
                {
                    "id": "Q2",
                    "question": "answered already",
                    "status": "resolved",
                },
            ],
        }
    )

    printed = output(render)

    assert (
        "H1 [supported, 0.8]: retry double counts"
        in printed
    )

    assert "Open questions:" in printed
    assert "Q1: is it shared?" in printed
    assert "Q2" not in printed

    assert (
        "Facts=1 Evidence=1 Investigations=1"
        in printed
    )


def test_state_summary_without_questions(render):
    render.state_summary(
        {
            "hypotheses": [],
            "open_questions": [
                {
                    "id": "Q1",
                    "question": "done",
                    "status": "resolved",
                }
            ],
        }
    )

    printed = output(render)

    assert "[shared state]" in printed
    assert "Open questions:" not in printed

    assert (
        "Facts=0 Evidence=0 Investigations=0"
        in printed
    )


def test_state_summary_tolerates_garbage(render):
    render.state_summary(
        {
            "hypotheses": [{}],
            "open_questions": [{"status": "open"}],
        }
    )

    assert "? [?, ?]" in output(render)


# =============================================================================
# Telling the coordinator what it can still afford
# =============================================================================


def test_delegation_budget_reports_each_agent(budget):
    from investigation.state import (
        delegation_budget,
    )

    budget.used = 1
    budget.per_agent = {"repo-researcher": 1}

    text = delegation_budget(budget)

    assert "1/15 delegations used" in text
    assert "Per-agent limit 4" in text
    assert "repo-researcher: 1/4 (3 left)" in text
    assert "web-researcher: 0/4 (4 left)" in text


def test_delegation_budget_names_an_exhausted_agent(
    budget,
):
    from investigation.state import (
        delegation_budget,
    )

    budget.per_agent = {"repo-researcher": 4}

    assert (
        "repo-researcher: 4/4 (exhausted)"
        in delegation_budget(budget)
    )


def test_the_coordinator_is_told_the_agent_budget(
    budget,
    render,
):
    from investigation.state import (
        delegation_budget,
    )

    budget.per_agent = {"repo-researcher": 4}

    provider = CannedProvider(
        '{"status": "done", "answer": "yes"}'
    )

    asyncio.run(
        decide_next(
            provider,
            "why?",
            EMPTY_STATE,
            2,
            budget,
            render,
        )
    )

    prompt = provider.calls[0][-1]["content"]

    assert "Delegation budget left:" in prompt

    assert (
        delegation_budget(budget) in prompt
    )


def test_the_final_answer_forbids_tool_markup():
    provider = CannedProvider("answer")

    asyncio.run(
        force_final(
            provider,
            "why?",
            EMPTY_STATE,
        )
    )

    system = provider.calls[0][0]["content"]

    assert "Write the answer as prose" in system
    assert "never emit tool-call markup" in system
