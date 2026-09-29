"""Tests for the event stream a run produces, and the guards behind it."""

import asyncio

from dataclasses import replace

from conftest import (
    AGENTS,
    CONFIG,
    DSML_TOOL_CALL,
    FINAL,
    ScriptedProvider,
    make_budget,
)

from conclave import (
    TRUNCATION_MARKER,
    AgentSpec,
    AgentStep,
    Approval,
    BudgetExceeded,
    Delegation,
    NoteRecorded,
    RunBudget,
    RunConfig,
    ToolAgentRuntime,
    ToolCalled,
    ToolDenied,
    ToolRegistry,
    Usage,
)
from conclave.spec import (
    Capability,
    Tool,
)


def collect(events) -> list:
    async def main():
        return [event async for event in events]

    return asyncio.run(main())


def with_tool(
    name: str,
    func,
    *capabilities: Capability,
    max_output_chars=CONFIG.max_tool_output_chars,
) -> ToolRegistry:
    registry = ToolRegistry(
        max_output_chars=max_output_chars
    )

    registry.register(
        Tool(
            name,
            "d",
            {},
            func,
            frozenset(capabilities),
        )
    )

    return registry


def spec_for(name: str) -> AgentSpec:
    return AgentSpec(
        name=name,
        instructions="i",
        tools={"list_files", "write_file"},
    )


# =============================================================================
# The stream
# =============================================================================


def test_the_stream_wraps_the_run(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [FINAL]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert [
        type(event).__name__
        for event in events
    ] == [
        "RunStarted",
        "AgentStarted",
        "AgentStep",
        "AgentFinished",
        "RunFinished",
    ]

    assert events[0].task == "task"
    assert events[0].config == replace(CONFIG, )
    assert events[-1].result.content == (
        "researcher summary"
    )


def test_steps_report_the_action(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "n"},
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    steps = [
        event
        for event in events
        if isinstance(event, AgentStep)
    ]

    assert [
        (step.step, step.action)
        for step in steps
    ] == [
        (0, "note"),
        (1, "final"),
    ]


def test_unreadable_replies_are_steps_without_an_action(
    runtime,
):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                DSML_TOOL_CALL,
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    steps = [
        event
        for event in events
        if isinstance(event, AgentStep)
    ]

    assert steps[0].action is None
    assert steps[0].reply == DSML_TOOL_CALL


def test_notes_are_reported(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": " checked  it "},
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    notes = [
        event
        for event in events
        if isinstance(event, NoteRecorded)
    ]

    assert len(notes) == 1
    assert notes[0].note == "checked it"
    assert notes[0].total == 1
    assert notes[0].result == "Note 1/12 recorded."


def test_a_refused_note_is_reported(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "checked"},
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(
            provider,
            config=replace(CONFIG, max_notes=0),
        ).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    notes = [
        event
        for event in events
        if isinstance(event, NoteRecorded)
    ]

    assert notes[0].total == 0
    assert notes[0].result.startswith("Note refused")


def test_delegation_is_reported_at_both_ends(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "critic",
                    "task": "review it",
                    "purpose": "sanity",
                },
                FINAL,
            ],
            "critic": [FINAL],
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    delegations = [
        event
        for event in events
        if isinstance(event, Delegation)
    ]

    assert [
        event.status
        for event in delegations
    ] == ["started", "completed"]

    assert delegations[0].agent == "critic"
    assert delegations[0].delegated_by == (
        "repo-researcher"
    )

    assert delegations[0].depth == 2
    assert delegations[0].task == "review it"
    assert delegations[1].reason == ""


def test_a_refused_delegation_is_reported(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "astrologer",
                    "task": "read the stars",
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    delegations = [
        event
        for event in events
        if isinstance(event, Delegation)
    ]

    assert len(delegations) == 1
    assert delegations[0].status == "refused"
    assert delegations[0].reason.startswith(
        "Unknown agent"
    )


def test_child_events_are_propagated(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "critic",
                    "task": "review it",
                },
                FINAL,
            ],
            "critic": [FINAL],
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    agents = [
        (event.agent, event.depth)
        for event in events
        if type(event).__name__
        == "AgentStarted"
    ]

    assert agents == [
        ("repo-researcher", 1),
        ("critic", 2),
    ]


def test_a_failing_child_does_not_kill_the_parent(runtime):
    class ExplodingChild(ScriptedProvider):
        async def complete(self, messages):
            if self.agent_name(messages) == "critic":
                raise RuntimeError("child exploded")

            return await super().complete(messages)

    provider = ExplodingChild(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "critic",
                    "task": "review it",
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    delegations = [
        event
        for event in events
        if isinstance(event, Delegation)
    ]

    assert delegations[1].status == "failed"
    assert delegations[1].reason == "child exploded"

    assert events[-1].result.content == (
        "researcher summary"
    )


# =============================================================================
# Tools
# =============================================================================


def test_tool_events_report_approval_and_size(runtime):
    async def small(**kwargs) -> str:
        return "ok"

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "list_files",
                    "arguments": {"path": "."},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        ToolAgentRuntime(
            provider,
            with_tool(
                "list_files",
                small,
                Capability.READ,
            ),
            make_budget(),
            AGENTS,
            config=CONFIG,
        ).stream(
            spec_for("repo-researcher"),
            "task",
        )
    )

    called = [
        event
        for event in events
        if isinstance(event, ToolCalled)
    ]

    assert called[0].approval is Approval.NOT_REQUIRED
    assert called[0].arguments == {"path": "."}
    assert called[0].output_chars == 2
    assert called[0].ok
    assert called[0].truncated is False


def test_a_truncated_result_is_flagged(runtime):
    async def big(**kwargs) -> str:
        return "x" * 50

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "list_files",
                    "arguments": {},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        ToolAgentRuntime(
            provider,
            with_tool(
                "list_files",
                big,
                Capability.READ,
                max_output_chars=10,
            ),
            make_budget(),
            AGENTS,
            config=CONFIG,
        ).stream(
            spec_for("repo-researcher"),
            "task",
        )
    )

    called = [
        event
        for event in events
        if isinstance(event, ToolCalled)
    ]

    assert called[0].truncated

    assert called[0].observation.endswith(
        TRUNCATION_MARKER
    )

    assert called[0].output_chars == (
        10 + len(TRUNCATION_MARKER)
    )


def test_a_broken_tool_is_reported_not_raised(runtime):
    async def broken(**kwargs) -> str:
        raise RuntimeError("tool exploded")

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "list_files",
                    "arguments": {},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        ToolAgentRuntime(
            provider,
            with_tool(
                "list_files",
                broken,
                Capability.READ,
            ),
            make_budget(),
            AGENTS,
            config=CONFIG,
        ).stream(
            spec_for("repo-researcher"),
            "task",
        )
    )

    called = [
        event
        for event in events
        if isinstance(event, ToolCalled)
    ]

    assert called[0].ok is False
    assert called[0].observation == (
        "Tool error: tool exploded"
    )


def test_a_tool_outside_the_spec_is_denied(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "web_fetch",
                    "arguments": {},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    denied = [
        event
        for event in events
        if isinstance(event, ToolDenied)
    ]

    assert denied[0].reason == "not-permitted"
    assert denied[0].tool == "web_fetch"
    assert "Permission denied" in (
        denied[0].observation
    )

    assert denied[0].capabilities == frozenset(
        {Capability.NETWORK}
    )


def test_a_destructive_tool_needs_an_approver(runtime):
    async def write(**kwargs) -> str:
        return "written"

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "write_file",
                    "arguments": {"path": "a.py"},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        ToolAgentRuntime(
            provider,
            with_tool("write_file", write),
            make_budget(),
            AGENTS,
            config=CONFIG,
        ).stream(
            spec_for("repo-researcher"),
            "task",
        )
    )

    denied = [
        event
        for event in events
        if isinstance(event, ToolDenied)
    ]

    assert denied[0].reason == "approval-refused"

    assert any(
        "no approver allowed it" in prompt
        for _, prompt in provider.prompts
    )


def test_an_approver_can_allow_a_destructive_tool(runtime):
    calls = []

    async def write(**kwargs) -> str:
        calls.append(kwargs)

        return "written"

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "write_file",
                    "arguments": {"path": "a.py"},
                },
                FINAL,
            ]
        }
    )

    asked = []

    def approver(request):
        asked.append(request)

        return True

    events = collect(
        ToolAgentRuntime(
            provider,
            with_tool(
                "write_file",
                write,
                Capability.WRITE,
            ),
            make_budget(),
            AGENTS,
            config=CONFIG,
            approver=approver,
        ).stream(
            spec_for("repo-researcher"),
            "task",
        )
    )

    assert calls == [{"path": "a.py"}]
    assert asked[0].tool == "write_file"
    assert asked[0].agent == "repo-researcher"

    called = [
        event
        for event in events
        if isinstance(event, ToolCalled)
    ]

    assert called[0].approval is Approval.APPROVED


def test_a_non_string_tool_name_is_denied(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": ["list_files"],
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(provider).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    denied = [
        event
        for event in events
        if isinstance(event, ToolDenied)
    ]

    assert denied[0].reason == "not-permitted"
    assert denied[0].capabilities == frozenset()


# =============================================================================
# Budgets
# =============================================================================


def test_a_spent_tool_budget_denies_the_call(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "list_files",
                    "arguments": {},
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(
            provider,
            make_budget(tool_calls=0),
        ).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    denied = [
        event
        for event in events
        if isinstance(event, ToolDenied)
    ]

    assert denied[0].reason == "budget"
    assert "tool-call budget is spent" in (
        denied[0].observation
    )


def test_a_spent_wall_clock_stops_the_run(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "list_files",
                    "arguments": {},
                }
            ]
        }
    )

    events = collect(
        runtime(
            provider,
            make_budget(wall_clock_seconds=0.0),
        ).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert any(
        isinstance(event, BudgetExceeded)
        and event.dimension
        == "wall_clock_seconds"
        and event.limit == 0.0
        for event in events
    )

    assert events[-1].status == "exhausted"

    assert (
        "Wall-clock budget exhausted"
        in events[-1].result.content
    )


def test_reported_usage_is_settled(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [FINAL]
        },
        usage=Usage(
            input_tokens=6,
            output_tokens=4,
        ),
    )

    budget = make_budget(tokens=100)

    result = asyncio.run(
        runtime(
            provider,
            budget,
        ).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == "researcher summary"
    assert budget.spent_on("tokens") == 10


def test_a_spent_token_budget_is_reported(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [FINAL]
        },
        usage=Usage(
            input_tokens=50,
            output_tokens=50,
        ),
    )

    events = collect(
        runtime(
            provider,
            make_budget(tokens=10),
        ).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert any(
        isinstance(event, BudgetExceeded)
        and event.dimension == "tokens"
        and event.used == 100.0
        for event in events
    )


def test_unmeasurable_usage_is_not_charged(runtime):
    budget = make_budget(tokens=100)

    asyncio.run(
        runtime(
            ScriptedProvider(
                {
                    "repo-researcher": [FINAL]
                }
            ),
            budget,
        ).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert budget.spent_on("tokens") == 0


def test_a_run_without_a_catalog_cannot_delegate(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "critic",
                    "task": "review it",
                },
                FINAL,
            ]
        }
    )

    events = collect(
        runtime(
            provider,
            agents={},
        ).stream(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    delegations = [
        event
        for event in events
        if isinstance(event, Delegation)
    ]

    assert delegations[0].status == "refused"
    assert "Unknown agent" in (
        delegations[0].reason
    )
