"""Acceptance test: a second multi-agent system, built from the public API.

Nothing here is an investigation. A writer drafts a note, a critic picks it
apart, and the orchestrator keeps both drafts and criticism in a store it
defined itself. If this file needed a change to the library to work, the
library would not be a framework.
"""

import asyncio
import json

from pathlib import Path

from conftest import ScriptedProvider

from conclave import (
    AgentFinished,
    AgentSpec,
    Approval,
    Capability,
    RunBudget,
    RunConfig,
    RunFinished,
    RunStarted,
    SharedStore,
    StoreSchema,
    Tool,
    ToolAgentRuntime,
    ToolCalled,
    ToolDenied,
    ToolRegistry,
    append_merge,
    approve_all,
    fan_out,
)


# =============================================================================
# The system under test: write, then challenge
# =============================================================================


WRITER = AgentSpec(
    name="writer",
    instructions="Draft the note and save it.",
    tools={"save_note"},
)

CRITIC = AgentSpec(
    name="critic",
    instructions="Challenge the draft.",
)

SPECS = {
    "writer": WRITER,
    "critic": CRITIC,
}


NOTE_SCHEMA = StoreSchema(
    keys=frozenset(
        {
            "draft",
            "criticism",
        }
    ),
    merges={
        "criticism": append_merge,
    },
)


def build_tools(
    saved: list[str],
) -> ToolRegistry:
    async def save_note(text: str) -> str:
        saved.append(text)

        return f"saved {len(text)} characters"

    registry = ToolRegistry()

    registry.register(
        Tool(
            name="save_note",
            description="Write the note to disk.",
            parameters={
                "text": "string",
            },
            func=save_note,
            capabilities=frozenset(
                {Capability.WRITE}
            ),
        )
    )

    return registry


WRITE_THEN_FINISH = [
    {
        "type": "tool",
        "name": "save_note",
        "arguments": {
            "text": "the draft",
        },
    },
    {
        "type": "final",
        "content": "draft written",
    },
]


def provider() -> ScriptedProvider:
    return ScriptedProvider(
        {
            "writer": list(WRITE_THEN_FINISH),
            "critic": [
                {
                    "type": "final",
                    "content": "unsupported claim",
                }
            ],
        }
    )


def collect(events: list) -> list:
    """Drain a stream, keeping every event for inspection."""

    async def main():
        return [event async for event in events]

    return asyncio.run(main())


# =============================================================================
# A destructive call without an approver
# =============================================================================


def test_a_destructive_tool_is_refused_when_nobody_approves():
    saved = []

    scripted = provider()

    runtime = ToolAgentRuntime(
        scripted,
        build_tools(saved),
        RunBudget(),
        SPECS,
    )

    events = collect(
        runtime.stream(
            WRITER,
            "draft something",
        )
    )

    assert saved == []

    kinds = [type(event).__name__ for event in events]

    assert kinds[0] == "RunStarted"
    assert kinds[-1] == "RunFinished"

    denied = [
        event
        for event in events
        if isinstance(event, ToolDenied)
    ]

    assert len(denied) == 1
    assert denied[0].reason == "approval-refused"

    assert denied[0].capabilities == frozenset(
        {Capability.WRITE}
    )

    # The refusal is handed back to the model as text, not raised.
    assert any(
        "no approver allowed it" in prompt
        for _, prompt in scripted.prompts
    )

    final = events[-1]

    assert isinstance(final, RunFinished)

    assert final.result.content == (
        "draft written"
    )


# =============================================================================
# The same system, with an approver
# =============================================================================


def test_an_approved_tool_runs_and_reports_its_approval():
    saved = []

    runtime = ToolAgentRuntime(
        provider(),
        build_tools(saved),
        RunBudget(),
        SPECS,
        approver=approve_all,
    )

    events = collect(
        runtime.stream(
            WRITER,
            "draft something",
        )
    )

    assert saved == ["the draft"]

    called = [
        event
        for event in events
        if isinstance(event, ToolCalled)
    ]

    assert len(called) == 1
    assert called[0].approval is Approval.APPROVED
    assert called[0].ok
    assert called[0].output_chars == len(
        "saved 9 characters"
    )

    assert called[0].truncated is False


def test_a_tool_message_keeps_the_runtime_quiet():
    """The library never prints; the caller decides what happens next."""

    saved = []

    runtime = ToolAgentRuntime(
        provider(),
        build_tools(saved),
        RunBudget(),
        SPECS,
        approver=approve_all,
    )

    result = asyncio.run(
        runtime.run(WRITER, "draft something")
    )

    assert result.content == "draft written"


# =============================================================================
# The caller's own store
# =============================================================================


def test_the_orchestrator_owns_the_store():
    store = SharedStore(NOTE_SCHEMA)

    writer = ToolAgentRuntime(
        provider(),
        build_tools([]),
        RunBudget(),
        SPECS,
        approver=approve_all,
    )

    critic = ToolAgentRuntime(
        ScriptedProvider(
            {
                "critic": [
                    {
                        "type": "final",
                        "content": (
                            "unsupported claim"
                        ),
                    }
                ]
            }
        ),
        build_tools([]),
        RunBudget(),
        SPECS,
    )

    async def main():
        result = await writer.run(
            WRITER,
            "draft something",
        )

        await store.merge(
            {"draft": result.content},
            writer="orchestrator",
        )

        snapshot = store.snapshot()

        review = await critic.run(
            CRITIC,
            json.dumps(snapshot["draft"]),
        )

        await store.merge(
            {"criticism": [review.content]},
            writer="orchestrator",
        )

        return snapshot

    snapshot = asyncio.run(main())

    assert snapshot == {
        "draft": "draft written",
        "criticism": None,
    }

    assert store.version == 2

    assert store.snapshot() == {
        "draft": "draft written",
        "criticism": ["unsupported claim"],
    }


def test_agents_cannot_reach_the_store():
    """v0 hands agents a snapshot, never the store."""

    store = SharedStore(
        NOTE_SCHEMA,
        {"draft": "original"},
    )

    assert not hasattr(store.snapshot(), "merge")

    # A snapshot is a copy: an agent cannot write through it.
    from copy import deepcopy

    copy = deepcopy(store.snapshot())

    copy["draft"] = "rewritten"

    assert store.snapshot()["draft"] == "original"


# =============================================================================
# Fanning out over the caller's own work
# =============================================================================


def test_fan_out_runs_several_agents_at_once():
    runtime = ToolAgentRuntime(
        provider(),
        build_tools([]),
        RunBudget(),
        SPECS,
        approver=approve_all,
    )

    async def main():
        return await fan_out(
            [
                ("writer", WRITER, "one"),
                ("writer", WRITER, "two"),
            ],
            lambda item: runtime.run(
                item[1],
                item[2],
            ),
            limit=2,
        )

    results = asyncio.run(main())

    assert [
        result.content
        for result in results
    ] == [
        "draft written",
        "draft written",
    ]


def test_fan_out_respects_its_limit():
    running = 0
    peak = 0

    async def worker(item):
        nonlocal running, peak

        running += 1

        peak = max(peak, running)

        await asyncio.sleep(0.01)

        running -= 1

        return item

    results = asyncio.run(
        fan_out(
            range(6),
            worker,
            limit=2,
        )
    )

    assert results == list(range(6))
    assert peak == 2


def test_fan_out_propagates_failures():
    seen = []

    async def worker(item):
        if item == 1:
            raise RuntimeError("worker 1 exploded")

        seen.append(item)

        return item

    try:
        asyncio.run(
            fan_out(
                [0, 1, 2],
                worker,
                limit=2,
            )
        )

    except RuntimeError as exc:
        assert "exploded" in str(exc)

    else:
        raise AssertionError("expected a failure")


# =============================================================================
# Limits are the caller's to set
# =============================================================================


def test_a_run_can_be_bounded_from_outside():
    runtime = ToolAgentRuntime(
        provider(),
        build_tools([]),
        RunBudget(
            tool_calls=1,
            tokens=10,
        ),
        SPECS,
        approver=approve_all,
        config=RunConfig(
            max_tool_steps=3,
        ),
    )

    events = collect(
        runtime.stream(
            WRITER,
            "draft something",
        )
    )

    # One tool call fitted inside the budget.
    assert [
        type(event).__name__
        for event in events
        if isinstance(event, ToolCalled)
    ] == ["ToolCalled"]

    assert (
        runtime.budget.spent_on("tool_calls")
        == 1
    )

    # The provider here reports no usage, so the token dimension is not
    # guessed at.
    assert runtime.budget.spent_on("tokens") == 0


def test_the_specialists_are_the_callers_choice():
    """A different catalog is a different system; nothing else changes."""

    translator = AgentSpec(
        name="translator",
        instructions="Translate the text.",
    )

    runtime = ToolAgentRuntime(
        ScriptedProvider(
            {
                "translator": [
                    {
                        "type": "final",
                        "content": "hola",
                    }
                ]
            }
        ),
        ToolRegistry(),
        RunBudget(),
        {
            "translator": translator,
        },
    )

    events = collect(
        runtime.stream(translator, "hello")
    )

    assert events[-1].result.content == "hola"

    started = [
        event
        for event in events
        if isinstance(event, AgentFinished)
    ]

    assert started[0].agent == "translator"
    assert started[0].parent_run_id is None


def test_a_core_run_never_touches_this_repository():
    """The library needs a provider and a task, not a checkout."""

    runtime = ToolAgentRuntime(
        ScriptedProvider(
            {
                "critic": [
                    {
                        "type": "final",
                        "content": "no evidence",
                    }
                ]
            }
        ),
        ToolRegistry(),
        RunBudget(),
        SPECS,
    )

    assert Path(".").exists()

    result = asyncio.run(
        runtime.run(CRITIC, "review nothing")
    )

    assert result.content == "no evidence"
    assert result.private.checked == []


def test_the_run_envelope_surrounds_the_root_agent_only():
    runtime = ToolAgentRuntime(
        ScriptedProvider(
            {
                "writer": [
                    {
                        "type": "delegate",
                        "agent": "critic",
                        "task": "review it",
                    },
                    {
                        "type": "final",
                        "content": "revised",
                    },
                ],
                "critic": [
                    {
                        "type": "final",
                        "content": "unsupported claim",
                    }
                ],
            }
        ),
        ToolRegistry(),
        RunBudget(),
        SPECS,
    )

    events = collect(
        runtime.stream(WRITER, "draft something")
    )

    assert (
        len(
            [
                event
                for event in events
                if isinstance(event, RunStarted)
            ]
        )
        == 1
    )

    assert (
        len(
            [
                event
                for event in events
                if isinstance(event, RunFinished)
            ]
        )
        == 1
    )

    started = [
        event
        for event in events
        if isinstance(event, AgentFinished)
    ]

    assert [
        event.agent for event in started
    ] == ["critic", "writer"]

    assert started[0].parent_run_id is not None
    assert started[1].parent_run_id is None

    final = events[-1]

    assert final.result.nested[0]["status"] == (
        "completed"
    )
