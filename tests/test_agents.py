"""Tests for agent specs, JSON handling, private state and the runtime."""

import asyncio
import json

import pytest

from conftest import (
    DSML_TOOL_CALL,
    FINAL,
    CannedProvider,
    FailingProvider,
    ScriptedProvider,
)

from conclave import agents
from conclave.agents import (
    AGENTS,
    JSON_ONLY_RULE,
    AgentResult,
    ModelOutputError,
    PrivateState,
    as_list,
    complete_json,
    extract_json,
)
from conclave.budget import RunBudget
from conclave.registry import Tool, ToolRegistry


# =============================================================================
# JSON handling
# =============================================================================


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = '```json\n{"a": 1}\n```'

    assert extract_json(text) == {"a": 1}


def test_extract_json_fenced_without_closing_fence():
    text = '```\n{"a": 2}'

    assert extract_json(text) == {"a": 2}


def test_extract_json_wrapped_in_prose():
    text = 'here you go: {"a": 3} thanks'

    assert extract_json(text) == {"a": 3}


def test_extract_json_without_json_raises():
    with pytest.raises(
        RuntimeError,
        match="Expected JSON",
    ):
        extract_json("no json at all")


def test_extract_json_fenced_garbage_raises():
    with pytest.raises(RuntimeError):
        extract_json("```\nnope\n```")


def test_extract_json_broken_body_raises():
    with pytest.raises(ModelOutputError):
        extract_json("{not json}")


def test_extract_json_rejects_non_objects():
    for text in ['[1, 2]', '"text"', "5", "true"]:
        with pytest.raises(ModelOutputError):
            extract_json(text)


def test_extract_json_rejects_tool_call_markup():
    with pytest.raises(
        ModelOutputError,
        match="Expected JSON",
    ) as error:
        extract_json(DSML_TOOL_CALL)

    assert error.value.text == DSML_TOOL_CALL


def test_extract_json_finds_json_after_markup():
    text = (
        DSML_TOOL_CALL
        + '\n{"type": "final", "content": "ok"}'
    )

    assert extract_json(text) == {
        "type": "final",
        "content": "ok",
    }


def test_extract_json_reports_unparseable_braces():
    with pytest.raises(ModelOutputError):
        extract_json("before {broken json} after")


def test_complete_json_sends_system_and_user():
    provider = CannedProvider('{"ok": true}')

    assert asyncio.run(
        complete_json(
            provider,
            "system text",
            "user text",
        )
    ) == {"ok": True}

    messages = provider.calls[0]

    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(
        "system text"
    )
    assert JSON_ONLY_RULE in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "user text"


def test_complete_json_retries_unreadable_replies():
    provider = ScriptedProvider(
        {
            "coordinator": [
                DSML_TOOL_CALL,
                '{"ok": true}',
            ]
        }
    )

    assert asyncio.run(
        complete_json(
            provider,
            "system text",
            "user text",
        )
    ) == {"ok": True}

    assert len(provider.prompts) == 2

    correction = provider.prompts[1][1]

    assert "previous reply was rejected" in correction
    assert JSON_ONLY_RULE in correction


def test_complete_json_gives_up_after_retries():
    provider = ScriptedProvider(
        {
            "coordinator": [
                DSML_TOOL_CALL,
                DSML_TOOL_CALL,
            ]
        }
    )

    with pytest.raises(ModelOutputError):
        asyncio.run(
            complete_json(
                provider,
                "system text",
                "user text",
                retries=1,
            )
        )

    assert len(provider.prompts) == 2


def test_complete_json_can_skip_retries():
    provider = ScriptedProvider(
        {
            "coordinator": [DSML_TOOL_CALL]
        }
    )

    with pytest.raises(ModelOutputError):
        asyncio.run(
            complete_json(
                provider,
                "system text",
                "user text",
                retries=0,
            )
        )

    assert len(provider.prompts) == 1


def test_as_list_passes_lists_through():
    assert as_list([1, 2]) == [1, 2]


@pytest.mark.parametrize(
    "value",
    [None, "text", 5, {"a": 1}],
)
def test_as_list_rejects_everything_else(value):
    assert as_list(value) == []


# =============================================================================
# Agent-private state
# =============================================================================


def test_private_state_records_notes():
    private = PrivateState()

    assert private.note("  checked   retry.py ") == (
        "Note 1/12 recorded."
    )

    assert private.notes == [
        "checked retry.py"
    ]

    assert private.render() == (
        "- note: checked retry.py"
    )


def test_private_state_ignores_empty_notes():
    private = PrivateState()

    assert private.note("   ") == (
        "Note ignored: it was empty."
    )

    assert private.notes == []


def test_private_state_refuses_when_full(monkeypatch):
    monkeypatch.setattr(agents, "MAX_NOTES", 1)

    private = PrivateState()

    assert private.note("first") == (
        "Note 1/1 recorded."
    )

    refusal = private.note("second")

    assert "scratchpad is full" in refusal
    assert private.notes == ["first"]


def test_private_state_truncates_long_notes(monkeypatch):
    monkeypatch.setattr(
        agents,
        "MAX_NOTE_CHARS",
        5,
    )

    private = PrivateState()
    private.note("0123456789")

    assert private.notes == ["01234"]


def test_private_state_keeps_recent_checks(monkeypatch):
    monkeypatch.setattr(agents, "MAX_NOTES", 2)

    private = PrivateState()

    for index in range(3):
        private.check(f"tool-{index}")

    assert private.checked == [
        "tool-1",
        "tool-2",
    ]


def test_private_state_render_is_empty_by_default():
    assert PrivateState().render() == "(empty)"


def test_private_state_render_includes_checked():
    private = PrivateState()
    private.note("a note")
    private.check("list_files({})")

    rendered = private.render()

    assert "- note: a note" in rendered
    assert (
        "- already checked: list_files({})"
        in rendered
    )


# =============================================================================
# Runtime: final, notes, tools
# =============================================================================


def test_run_returns_handoff(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "n"},
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert isinstance(result, AgentResult)
    assert result.content == "researcher summary"
    assert result.findings == FINAL["findings"]
    assert result.open_leads == FINAL["open_leads"]
    assert result.private.notes == ["n"]
    assert result.nested == []

    assert any(
        "NOTE RESULT" in prompt
        for _, prompt in provider.prompts
    )


def test_run_defaults_missing_handoff_fields(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "final",
                    "content": "legacy",
                }
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == "legacy"
    assert result.findings == []
    assert result.open_leads == []


def test_run_records_checked_ledger(runtime):
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

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert len(result.private.checked) == 1

    assert result.private.checked[0].startswith(
        "list_files("
    )

    assert any(
        "TOOL RESULT for list_files"
        in prompt
        for _, prompt in provider.prompts
    )


def test_run_denies_tools_outside_the_spec(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "web_fetch",
                    "arguments": {"url": "https://x.test"},
                },
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.private.checked == []

    assert any(
        "Permission denied: web_fetch"
        in prompt
        for _, prompt in provider.prompts
    )


def test_run_reports_tool_errors(runtime):
    async def broken(**kwargs) -> str:
        raise RuntimeError("tool exploded")

    tools = ToolRegistry()

    tools.register(
        Tool(
            "list_files",
            "d",
            {},
            broken,
        )
    )

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

    from conclave.agents import ToolAgentRuntime

    runner = ToolAgentRuntime(
        provider,
        tools,
        RunBudget(),
    )

    result = asyncio.run(
        runner.run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.private.checked == [
        "list_files({}) -> failed"
    ]

    assert any(
        "Tool error: tool exploded" in prompt
        for _, prompt in provider.prompts
    )


def test_run_rejects_unknown_action(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "sing"}
            ]
        }
    )

    with pytest.raises(
        RuntimeError,
        match="Invalid agent action",
    ):
        asyncio.run(
            runtime(provider).run(
                AGENTS["repo-researcher"],
                "task",
            )
        )


def test_run_recovers_from_tool_call_markup(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                DSML_TOOL_CALL,
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == "researcher summary"
    assert result.private.checked == []

    assert any(
        "FORMAT ERROR" in prompt
        and JSON_ONLY_RULE in prompt
        for _, prompt in provider.prompts
    )


def test_run_gives_up_after_format_errors(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [DSML_TOOL_CALL]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert (
        "Tool-step budget exhausted"
        in result.content
    )


def test_run_reports_step_budget_exhaustion(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "tool",
                    "name": "git_status",
                    "arguments": {},
                }
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert (
        "Tool-step budget exhausted"
        in result.content
    )

    assert len(result.private.checked) == 8


# =============================================================================
# Runtime: nested delegation
# =============================================================================


def test_run_nests_and_propagates_handoff(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "look up retry semantics",
                    "purpose": "remove uncertainty",
                },
                FINAL,
            ],
            "web-researcher": [FINAL],
        }
    )

    budget = RunBudget()

    result = asyncio.run(
        runtime(provider, budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert budget.used == 1

    assert len(result.nested) == 1

    record = result.nested[0]

    assert record["agent"] == "web-researcher"
    assert record["delegated_by"] == "repo-researcher"
    assert record["status"] == "completed"
    assert record["depth"] == 2
    assert record["findings"] == FINAL["findings"]
    assert record["open_leads"] == FINAL["open_leads"]
    assert "nested" not in record

    assert any(
        "DELEGATION RESULT for web-researcher"
        in prompt
        for _, prompt in provider.prompts
    )


def test_run_refuses_unknown_specialist(runtime):
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

    budget = RunBudget()

    result = asyncio.run(
        runtime(provider, budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.nested[0]["status"] == "refused"

    assert "Unknown specialist" in (
        result.nested[0]["reason"]
    )

    assert budget.used == 0


def test_run_refuses_non_string_specialist(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": ["web-researcher"],
                    "task": "look up",
                },
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.nested[0]["status"] == "refused"


def test_run_refuses_empty_task(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "   ",
                },
                FINAL,
            ]
        }
    )

    budget = RunBudget()

    result = asyncio.run(
        runtime(provider, budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert "task was empty" in (
        result.nested[0]["reason"]
    )

    assert budget.used == 0


def test_run_refuses_deep_delegation(runtime):
    provider = ScriptedProvider(
        {
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

    budget = RunBudget()

    result = asyncio.run(
        runtime(provider, budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert len(result.nested) == 2
    assert result.nested[0]["status"] == "completed"

    refusal = result.nested[1]

    assert refusal["status"] == "refused"
    assert "maximum depth" in refusal["reason"]
    assert budget.used == 1

    assert any(
        "Do not repeat this delegation"
        in prompt
        for _, prompt in provider.prompts
    )


def test_run_refuses_cycles(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "repo-researcher",
                    "task": "again",
                },
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.nested[0]["status"] == "refused"

    assert "cycle detected" in (
        result.nested[0]["reason"]
    )


def test_run_refuses_when_budget_is_spent(runtime):
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "look up",
                },
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(
            provider,
            RunBudget(max_delegations=0),
        ).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.nested[0]["status"] == "refused"

    assert "already used 0/0" in (
        result.nested[0]["reason"]
    )


def test_run_survives_failing_child(runtime):
    class ExplodingChild(ScriptedProvider):
        async def complete(self, messages):
            if self.agent_name(messages) == (
                "web-researcher"
            ):
                raise RuntimeError("child exploded")

            return await super().complete(
                messages
            )

    provider = ExplodingChild(
        {
            "repo-researcher": [
                {
                    "type": "delegate",
                    "agent": "web-researcher",
                    "task": "look up",
                },
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == "researcher summary"

    record = result.nested[0]

    assert record["status"] == "failed"
    assert record["reason"] == "child exploded"

    assert "Continue without it" in (
        record["observation"]
    )


def test_run_advertises_delegation_only_below_max_depth(runtime):
    runner = runtime(
        ScriptedProvider({})
    )

    shallow = runner.system_prompt(
        AGENTS["critic"],
        1,
        PrivateState(),
    )

    deep = runner.system_prompt(
        AGENTS["critic"],
        2,
        PrivateState(),
    )

    assert '"type": "delegate"' in shallow
    assert '"type": "delegate"' not in deep
    assert "You are at depth 2 of" in deep
    assert "Available specialists" in shallow


def test_run_prompt_lists_only_allowed_tools(runtime):
    runner = runtime(
        ScriptedProvider({})
    )

    prompt = runner.system_prompt(
        AGENTS["repo-researcher"],
        1,
        PrivateState(),
    )

    assert "list_files" in prompt
    assert "web_fetch" not in prompt


def test_run_reports_provider_failure(runtime):
    with pytest.raises(
        RuntimeError,
        match="boom",
    ):
        asyncio.run(
            runtime(FailingProvider()).run(
                AGENTS["critic"],
                "task",
            )
        )
