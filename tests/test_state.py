"""Tests for structured investigation state and its model steps."""

import asyncio
import json

from conftest import CannedProvider

from conclave import state as state_module
from conclave.agents import complete_json
from conclave.state import (
    EMPTY_STATE,
    decide_next,
    force_final,
    initialize_state,
    normalize_state,
    print_state_summary,
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


def test_initialize_state_normalizes():
    provider = CannedProvider(INITIAL)

    state = asyncio.run(
        initialize_state(
            provider,
            "why?",
        )
    )

    assert set(state) == set(EMPTY_STATE)
    assert state["hypotheses"][0]["id"] == "H1"

    prompt = provider.calls[0][-1]["content"]

    assert "why?" in prompt
    assert "hypotheses" in prompt


def test_decide_next_describes_budget_and_agents():
    provider = CannedProvider(
        '{"status": "done", "answer": "yes"}'
    )

    decision = asyncio.run(
        decide_next(
            provider,
            "why?",
            EMPTY_STATE,
            2,
        )
    )

    assert decision["status"] == "done"

    prompt = provider.calls[0][-1]["content"]

    assert "repo-researcher" in prompt
    assert "critic" in prompt
    assert "Use at most 3" in prompt
    assert "Round:" in prompt


def test_update_state_normalizes(monkeypatch):
    monkeypatch.setattr(
        state_module,
        "MAX_ROUNDS",
        8,
    )

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


def test_print_state_summary(capsys):
    print_state_summary(
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

    output = capsys.readouterr().out

    assert "H1 [supported, 0.8]: retry double counts" in output
    assert "Open questions:" in output
    assert "Q1: is it shared?" in output
    assert "Q2" not in output
    assert "Facts=1 Evidence=1 Investigations=1" in output


def test_print_state_summary_without_questions(capsys):
    print_state_summary(
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

    output = capsys.readouterr().out

    assert "[shared state]" in output
    assert "Open questions:" not in output
    assert "Facts=0 Evidence=0 Investigations=0" in output


def test_print_state_summary_tolerates_garbage(capsys):
    print_state_summary(
        {
            "hypotheses": [{}],
            "open_questions": [{"status": "open"}],
        }
    )

    output = capsys.readouterr().out

    assert "? [?, ?]" in output


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
