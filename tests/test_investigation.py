"""Tests for the round loop that ties coordinator and agents together."""

import asyncio

import pytest

from conftest import (
    DSML_TOOL_CALL,
    build_tools,
    FINAL,
    ScriptedProvider,
    make_budget,
)

from investigation import loop as investigation
from investigation import run_investigation


INITIAL_STATE = {
    "facts": [],
    "evidence": [],
    "hypotheses": [],
    "open_questions": [],
    "investigations": [],
}


def decide(
    delegations,
    reason="because",
):
    return {
        "status": "delegate",
        "reason": reason,
        "delegations": delegations,
    }


def delegate_to(
    agent,
    task,
    **extra,
):
    payload = {
        "agent": agent,
        "task": task,
        "purpose": "remove uncertainty",
    }

    payload.update(extra)

    return payload


def coordinator(
    *extra,
):
    return [INITIAL_STATE, *extra]


def run(
    provider,
    repo,
    monkeypatch=None,
    rounds=None,
):
    if rounds is not None:
        monkeypatch.setattr(
            investigation,
            "MAX_ROUNDS",
            rounds,
        )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why does retry double count?",
        )
    )


def test_full_round_with_nested_delegation(
    repo,
    capsys,
):
    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "investigate retries",
                        )
                    ]
                ),
                INITIAL_STATE,
                {
                    "status": "done",
                    "answer": "END TO END OK",
                },
            ),
            "repo-researcher": [
                {
                    "type": "note",
                    "content": "private note marker",
                },
                {
                    "type": "tool",
                    "name": "git_status",
                    "arguments": {},
                },
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "look up retry semantics",
                },
                FINAL,
            ],
            "web-researcher": [FINAL],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert "ROUND 1" in output
    assert "Reason: because" in output
    assert "-> repo-researcher: investigate retries" in output
    assert "note: private note marker" in output

    assert (
        "nested: repo-researcher -> web-researcher "
        "[completed]"
        in output
    )

    assert "researcher summary" in output
    assert "END TO END OK" in output

    updates = [
        prompt
        for _, prompt in provider.prompts
        if "New specialist results" in prompt
    ]

    assert len(updates) == 1

    assert "nested_delegations" in updates[0]
    assert "findings" in updates[0]
    assert "git_status(" in updates[0]

    assert "private note marker" not in updates[0]


def test_nested_refusals_are_printed(
    repo,
    capsys,
):
    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "deep dive",
                        )
                    ]
                ),
                INITIAL_STATE,
                {
                    "status": "done",
                    "answer": "done",
                },
            ),
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "level two",
                },
                FINAL,
            ],
            "web-researcher": [
                {
                    "type": "delegate",
                    "agent": "architect",
                    "task": "level three",
                },
                FINAL,
            ],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
            budget=make_budget(),
        )
    )

    output = capsys.readouterr().out

    assert "[refused]" in output
    assert "maximum depth" in output


def test_invalid_decision_raises(
    repo,
):
    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                {"status": "meditate"}
            )
        }
    )

    with pytest.raises(
        RuntimeError,
        match="Invalid coordinator decision",
    ):
        asyncio.run(
            run_investigation(
                provider,
                build_tools(repo),
                "why?",
            )
        )


def test_no_delegations_forces_a_final_answer(
    repo,
    capsys,
):
    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide([])
            ),
            "default": {
                "type": "final",
                "content": "forced answer",
            },
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert (
        "[investigation budget exhausted -> final]"
        in output
    )


def test_per_run_limits_reach_coordinator_and_bound_delegations(repo, capsys):
    provider = ScriptedProvider({
        "coordinator": coordinator(
            decide([
                delegate_to("critic", "first"),
                delegate_to("critic", "second"),
                delegate_to("critic", "third"),
            ]),
            INITIAL_STATE,
        ),
        "critic": [FINAL],
    })
    budget = make_budget(max_delegations=50, max_per_agent=10)
    asyncio.run(run_investigation(
        provider, build_tools(repo), "why?",
        budget=budget, max_rounds=1, max_delegations_per_round=1,
    ))
    assert budget.used == 1
    decisions = [prompt for _, prompt in provider.prompts if "Round:" in prompt]
    assert "1/1" in decisions[0]
    assert "Use at most 1" in decisions[0]
    output = capsys.readouterr().out
    assert "-> critic: first" in output
    assert "-> critic: second" not in output
    assert "ROUND 2" not in output


def test_invalid_delegations_are_filtered(
    repo,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        investigation,
        "MAX_ROUNDS",
        1,
    )

    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to("astrologer", "x"),
                        delegate_to("critic", "   "),
                        delegate_to("critic", "real work"),
                        delegate_to("critic", "REAL WORK"),
                        delegate_to("critic", "more work"),
                    ]
                )
            ),
            "critic": [FINAL],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    critic_calls = [
        prompt
        for name, prompt in provider.prompts
        if name == "critic"
    ]

    assert len(critic_calls) == 2

    output = capsys.readouterr().out

    assert "-> critic: real work" in output
    assert "-> critic: more work" in output
    assert "-> astrologer:" not in output

    assert (
        "[investigation budget exhausted -> final]"
        in output
    )


def test_exhausted_budget_stops_the_round(
    repo,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        investigation,
        "make_budget",
        lambda: make_budget(max_delegations=0),
    )

    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "work",
                        )
                    ]
                ),
                {
                    "status": "done",
                    "answer": "stopped",
                },
            )
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert (
        "x  delegation budget exhausted"
        in output
    )

    assert (
        "[investigation budget exhausted -> final]"
        in output
    )


def test_per_agent_refusal_is_printed(
    repo,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        investigation,
        "make_budget",
        lambda: make_budget(max_per_agent=0),
    )

    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "work",
                        )
                    ]
                )
            )
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert "already ran 0/0" in output


def test_failing_agent_is_recorded_and_reported(
    repo,
    capsys,
):
    class ExplodingAgent(ScriptedProvider):
        async def complete(self, messages):
            if self.agent_name(messages) == (
                "repo-researcher"
            ):
                raise RuntimeError("agent exploded")

            return await super().complete(
                messages
            )

    provider = ExplodingAgent(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "work",
                        )
                    ]
                ),
                INITIAL_STATE,
                {
                    "status": "done",
                    "answer": "recovered",
                },
            )
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert "Agent failed: agent exploded" in output
    assert "recovered" in output

    updates = [
        prompt
        for _, prompt in provider.prompts
        if "New specialist results" in prompt
    ]

    assert '"status": "failed"' in updates[0]


def test_rounds_are_bounded(
    repo,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        investigation,
        "MAX_ROUNDS",
        2,
    )

    tasks = [f"task {i}" for i in range(4)]

    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            tasks[0],
                        )
                    ]
                ),
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            tasks[1],
                        )
                    ]
                ),
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            tasks[2],
                        )
                    ]
                ),
            ),
            "repo-researcher": [FINAL],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert "ROUND 1" in output
    assert "ROUND 2" in output
    assert "ROUND 3" not in output

    assert (
        "[investigation budget exhausted -> final]"
        in output
    )


def test_unreadable_backend_does_not_abort_the_run(
    repo,
    capsys,
):
    """Regression: a backend that only emits tool-call markup.

    Reproduces the real failure where opencode returned DSML tool-call
    markup instead of JSON and the run died with "Expected JSON".
    """

    provider = ScriptedProvider(
        default=DSML_TOOL_CALL
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why?",
        )
    )

    output = capsys.readouterr().out

    assert "continuing with an empty state" in output

    assert "coordinator reply was unreadable" in output

    assert (
        "[investigation budget exhausted -> final]"
        in output
    )


def test_a_binary_file_in_the_repo_does_not_break_a_round(
    repo,
    capsys,
):
    """Regression: a NUL byte used to kill the agent run.

    The tool bus now refuses binary files, so a repository containing one
    costs a tool call at most — and no NUL ever reaches a prompt.
    """

    (repo / "blob.bin").write_bytes(
        b"retry\x00retry\x00"
    )

    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "search for retries",
                        )
                    ]
                ),
                INITIAL_STATE,
                {
                    "status": "done",
                    "answer": "SURVIVED",
                },
            ),
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "search_files",
                    "arguments": {"query": "retry"},
                },
                {
                    "type": "tool",
                    "name": "read_file",
                    "arguments": {"path": "blob.bin"},
                },
                FINAL,
            ],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why does retry double count?",
        )
    )

    assert "SURVIVED" in capsys.readouterr().out

    # The invariant that was violated: no prompt ever carried a NUL.
    for _, prompt in provider.prompts:
        assert "\x00" not in prompt

    assert any(
        "Binary file, not readable as text"
        in prompt
        for _, prompt in provider.prompts
    )


def test_an_invented_action_costs_a_step_not_the_run(
    repo,
    capsys,
):
    provider = ScriptedProvider(
        {
            "coordinator": coordinator(
                decide(
                    [
                        delegate_to(
                            "repo-researcher",
                            "investigate retries",
                        )
                    ]
                ),
                INITIAL_STATE,
                {
                    "status": "done",
                    "answer": "RECOVERED",
                },
            ),
            "repo-researcher": [
                {
                    "type": "read_file",
                    "path": "a.py",
                },
                FINAL,
            ],
        }
    )

    asyncio.run(
        run_investigation(
            provider,
            build_tools(repo),
            "why does retry double count?",
        )
    )

    output = capsys.readouterr().out

    assert "RECOVERED" in output
    assert "Agent failed" not in output
