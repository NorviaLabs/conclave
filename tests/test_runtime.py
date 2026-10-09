"""Tests for the JSON action protocol and the agent runtime."""

import asyncio
import json

import pytest

from conftest import (
    AGENTS,
    CONFIG,
    DSML_TOOL_CALL,
    FINAL,
    CannedProvider,
    FailingProvider,
    ScriptedProvider,
    make_budget,
)

from dataclasses import replace

from conclave import ToolAgentRuntime
from conclave.protocol import (
    JSON_ONLY_RULE,
    AgentResult,
    ModelOutputError,
    PrivateState,
    as_list,
    complete_json,
    extract_json,
)
from conclave.spec import (
    Capability,
    Tool,
)
from conclave.registry import ToolRegistry


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
    private = PrivateState(CONFIG)

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
    private = PrivateState(CONFIG)

    assert private.note("   ") == (
        "Note ignored: it was empty."
    )

    assert private.notes == []


def test_private_state_refuses_when_full():
    private = PrivateState(
        replace(CONFIG, max_notes=1)
    )

    assert private.note("first") == (
        "Note 1/1 recorded."
    )

    refusal = private.note("second")

    assert "scratchpad is full" in refusal
    assert private.notes == ["first"]


def test_private_state_truncates_long_notes():
    private = PrivateState(
        replace(CONFIG, max_note_chars=5)
    )
    private.note("0123456789")

    assert private.notes == ["01234"]


def test_private_state_keeps_recent_checks():
    private = PrivateState(
        replace(CONFIG, max_notes=2)
    )

    for index in range(3):
        private.check(f"tool-{index}")

    assert private.checked == [
        "tool-1",
        "tool-2",
    ]


def test_private_state_render_is_empty_by_default():
    assert PrivateState(CONFIG).render() == "(empty)"


def test_private_state_render_includes_checked():
    private = PrivateState(CONFIG)
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

    # The handoff is passed through, not understood.
    assert (
        result.data["findings"]
        == FINAL["findings"]
    )

    assert (
        result.data["open_leads"]
        == FINAL["open_leads"]
    )

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
    assert result.data == {}


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

    tools = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    tools.register(
        Tool(
            "list_files",
            "d",
            {},
            broken,
            frozenset({Capability.READ}),
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

    runner = ToolAgentRuntime(
        provider,
        tools,
        make_budget(),
        AGENTS,
        config=CONFIG,
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


def test_run_corrects_an_unknown_action(runtime):
    """A misspelled action must not throw away the agent's work."""

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "read_file", "path": "a.py"},
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

    assert any(
        "No such action type: 'read_file'" in prompt
        and (
            "Valid types are tool, note, "
            "delegate and final"
        )
        in prompt
        for _, prompt in provider.prompts
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

    budget = make_budget()

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
    assert record["content"] == (
        FINAL["content"]
    )

    assert (
        record["data"]["findings"]
        == FINAL["findings"]
    )
    assert "nested" not in record

    assert any(
        "DELEGATION RESULT for web-researcher"
        in prompt
        for _, prompt in provider.prompts
    )


def test_run_refuses_unknown_agent(runtime):
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

    budget = make_budget()

    result = asyncio.run(
        runtime(provider, budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.nested[0]["status"] == "refused"

    assert "Unknown agent" in (
        result.nested[0]["reason"]
    )

    assert budget.used == 0


def test_run_refuses_non_string_agent(runtime):
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

    budget = make_budget()

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
                    "agent": "critic",
                    "task": "level three",
                },
                FINAL,
            ],
        }
    )

    budget = make_budget()

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
            make_budget(max_delegations=0),
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
        PrivateState(CONFIG),
    )

    deep = runner.system_prompt(
        AGENTS["critic"],
        2,
        PrivateState(CONFIG),
    )

    assert '"type": "delegate"' in shallow
    assert '"type": "delegate"' not in deep
    assert "You are at depth 2 of" in deep
    assert "Available agents" in shallow


def test_run_prompt_lists_only_allowed_tools(runtime):
    runner = runtime(
        ScriptedProvider({})
    )

    prompt = runner.system_prompt(
        AGENTS["repo-researcher"],
        1,
        PrivateState(CONFIG),
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


def test_complete_json_can_report_usage():
    provider = CannedProvider('{"ok": true}')

    seen = []

    assert asyncio.run(
        complete_json(
            provider,
            "s",
            "u",
            on_usage=seen.append,
        )
    ) == {"ok": True}

    # CannedProvider reports no usage, so nothing is charged.
    assert seen == []


def test_complete_json_charges_every_attempt():
    from conclave import Usage

    provider = ScriptedProvider(
        {
            "coordinator": [
                DSML_TOOL_CALL,
                '{"ok": true}',
            ]
        },
        usage=Usage(
            input_tokens=1,
            output_tokens=2,
        ),
    )

    seen = []

    asyncio.run(
        complete_json(
            provider,
            "s",
            "u",
            on_usage=seen.append,
        )
    )

    assert [
        usage.total_tokens
        for usage in seen
    ] == [3, 3]


def test_an_agent_on_a_clock_is_told_to_answer_before_time_runs_out(runtime):
    """Time is not an action budget, so the agent is asked rather than cut off.

    The clock is read from the run's own accounting, which is what makes this
    testable without waiting: the budget says nine and a half seconds of a
    ten-second run are already spent.
    """

    from conclave.protocol import TIME_LEFT_RULE
    from conclave.runtime import TIME_LEFT_WINDOW

    budget = make_budget(wall_clock_seconds=10)
    budget.spent["wall_clock_seconds"] = 10 - (TIME_LEFT_WINDOW / 2)
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "One finding"},
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider, run_budget=budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == FINAL["content"]
    prompts = [prompt for _, prompt in provider.prompts]
    assert TIME_LEFT_RULE in prompts[-1], "the answer is asked for while there is time to give it"
    assert TIME_LEFT_RULE not in prompts[0], "and not before the clock is nearly out"


def test_an_agent_with_no_clock_is_never_told_time_is_short(runtime):
    """A run with no time limit has no clock to warn about."""

    from conclave.protocol import TIME_LEFT_RULE

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "One finding"},
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider, run_budget=make_budget()).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == FINAL["content"]
    assert all(TIME_LEFT_RULE not in prompt for _, prompt in provider.prompts)


def test_the_agent_is_told_where_it_stands_after_every_action(runtime):
    """An agent that cannot see its budget cannot plan with it.

    The private scratchpad already reports its own count ("Note 1/12 recorded.");
    the action budget is reported the same way, so the last-action rule is not the
    first time an agent hears about it.
    """

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "One finding"},
                {"type": "note", "content": "Another"},
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

    assert result.content == FINAL["content"]
    prompts = [prompt for _, prompt in provider.prompts]
    limit = CONFIG.max_tool_steps
    assert f"Action 1 of {limit}." in prompts[1], "the count follows the action it counts"
    assert f"Action 2 of {limit}." in prompts[2]
    assert "Action" not in prompts[0], "nothing has been spent before the first action"


def test_an_agent_with_no_action_limit_is_not_counted(runtime):
    """A run with no limit has no count to report, and says nothing about one."""

    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "One finding"},
                FINAL,
            ]
        }
    )

    asyncio.run(
        runtime(provider, config=replace(CONFIG, max_tool_steps=None)).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert all("Action " not in prompt for _, prompt in provider.prompts)


def test_the_instructions_name_the_action_budget_before_any_is_spent(runtime):
    """The limit is in the instructions, so the first action is already planned."""

    from conclave.protocol import PrivateState

    runner = runtime(ScriptedProvider({"repo-researcher": []}))
    spec = AGENTS["repo-researcher"]
    text = runner.system_prompt(spec, 1, PrivateState(runner.config))
    assert f"You have at most {CONFIG.max_tool_steps} actions for this stage" in text
    assert "Every tool call, note or delegation is one action." in text

    unlimited = runtime(ScriptedProvider({"repo-researcher": []}), config=replace(CONFIG, max_tool_steps=None))
    assert "actions for this stage" not in unlimited.system_prompt(spec, 1, PrivateState(unlimited.config))


def test_a_stage_that_runs_out_of_time_writes_up_what_it_has(runtime):
    """Time, too: a clock that ends a search does not end the stage's answer.

    The write-up is one call beyond the limit, which is the trade this makes
    deliberately: a spent budget should cost time, not the work.
    """

    from conclave.protocol import WRITE_UP_RULE

    budget = make_budget(wall_clock_seconds=1)
    budget.spent["wall_clock_seconds"] = 2
    provider = ScriptedProvider(
        {
            "repo-researcher": [
                {"type": "note", "content": "One finding"},
                FINAL,
            ]
        }
    )

    result = asyncio.run(
        runtime(provider, run_budget=budget).run(
            AGENTS["repo-researcher"],
            "task",
        )
    )

    assert result.content == FINAL["content"], "the stage still answers"
    assert result.exhausted_by == "wall_clock_seconds", "and the record says why it had to"
    assert WRITE_UP_RULE in provider.prompts[-1][1], "asked for with nothing left to offer"
