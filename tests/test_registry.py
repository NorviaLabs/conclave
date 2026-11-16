"""Tests for the tool bus: registry, describe, execute and truncation."""

import asyncio

import pytest

from conftest import (
    CONFIG,
    build_tools,
)

from conclave.registry import ToolRegistry
from conclave.spec import Tool
from conclave.tools import build_tool_registry


EXPECTED_TOOLS = {
    "list_files",
    "read_file",
    "search_files",
    "git_status",
    "git_log",
    "git_diff",
    "git_show",
    "github_issue",
    "github_pr",
    "github_search_issues",
    "github_search_prs",
    "web_search",
    "web_fetch",
}


def test_build_tool_registry_registers_every_tool(repo):
    registry = build_tools(repo)

    assert set(registry.tools) == EXPECTED_TOOLS

    for tool in registry.tools.values():
        assert tool.description
        assert callable(tool.func)


def test_describe_filters_by_permission(repo):
    registry = build_tools(repo)

    described = registry.describe(
        {"read_file", "git_status"}
    )

    assert {
        entry["name"] for entry in described
    } == {"read_file", "git_status"}

    for entry in described:
        assert set(entry) == {
            "name",
            "description",
            "parameters",
        }


def test_describe_is_empty_without_permission(repo):
    registry = build_tools(repo)

    assert registry.describe(set()) == []


def test_register_overwrites_by_name():
    registry = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    async def first() -> str:
        return "first"

    async def second() -> str:
        return "second"

    registry.register(
        Tool("dup", "d", {}, first)
    )

    registry.register(
        Tool("dup", "d", {}, second)
    )

    assert len(registry.tools) == 1

    assert (
        asyncio.run(
            registry.execute("dup", {})
        )
        == "second"
    )


def test_execute_unknown_tool_raises():
    registry = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    with pytest.raises(
        RuntimeError,
        match="Unknown tool: nope",
    ):
        asyncio.run(
            registry.execute("nope", {})
        )


def test_execute_passes_arguments():
    registry = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    seen = {}

    async def echo(path, max_entries=5) -> str:
        seen["path"] = path
        seen["max_entries"] = max_entries

        return "ok"

    registry.register(
        Tool("echo", "d", {}, echo)
    )

    assert (
        asyncio.run(
            registry.execute(
                "echo",
                {"path": "x"},
            )
        )
        == "ok"
    )

    assert seen == {
        "path": "x",
        "max_entries": 5,
    }


def test_execute_with_unlimited_output():
    registry = ToolRegistry(max_output_chars=None)
    text = "x" * 40_000

    async def big() -> str:
        return text

    registry.register(Tool("big", "d", {}, big))

    assert asyncio.run(registry.execute("big", {})) == text


def test_execute_truncates_long_output():
    registry = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    async def big() -> str:
        return "x" * (
            CONFIG.max_tool_output_chars + 50
        )

    registry.register(
        Tool("big", "d", {}, big)
    )

    result = asyncio.run(
        registry.execute("big", {})
    )

    assert result.endswith(
        "\n...[truncated]"
    )

    assert len(result) == (
        CONFIG.max_tool_output_chars
        + len("\n...[truncated]")
    )


def test_execute_keeps_short_output():
    registry = ToolRegistry(
        max_output_chars=CONFIG.max_tool_output_chars
    )

    async def small() -> str:
        return "short"

    registry.register(
        Tool("small", "d", {}, small)
    )

    assert (
        asyncio.run(
            registry.execute("small", {})
        )
        == "short"
    )


def test_bundled_tools_declare_their_capabilities(repo):
    """No bundled tool may be destructive: the app ships no approver."""

    registry = build_tools(repo)

    for name, registered in registry.tools.items():
        assert registered.capabilities, name
        assert not registered.destructive, name

        assert (
            registry.capabilities(name)
            == registered.capabilities
        )


def test_capabilities_of_an_unknown_tool_are_empty(repo):
    registry = build_tools(repo)

    assert registry.capabilities("nope") == (
        frozenset()
    )


def test_the_registry_can_name_a_tool(repo):
    registry = build_tools(repo)

    assert registry.tool("read_file").name == (
        "read_file"
    )

    assert registry.tool("nope") is None
    assert registry.tool(["read_file"]) is None



# =============================================================================
# A tool may name its own output limit for one call
# =============================================================================


def _echo_tool(size, output_limit=None):
    async def func(**arguments):
        return "x" * size

    return Tool(
        name="echo",
        description="Echo",
        parameters={"type": "object", "properties": {}},
        func=func,
        output_limit=output_limit,
    )


def test_a_tool_without_an_output_limit_uses_the_registry_cap():
    registry = ToolRegistry(10)
    registry.register(_echo_tool(40))

    assert _echo_tool(1).output_limit is None
    assert asyncio.run(registry.execute("echo", {})) == "x" * 10 + "\n...[truncated]"


def test_a_tools_output_limit_replaces_the_cap_for_that_call():
    seen = []

    def limit(arguments, cap):
        seen.append((arguments, cap))
        return 100 if arguments.get("big") else None

    registry = ToolRegistry(10)
    registry.register(_echo_tool(40, limit))

    # Raised for this call: the whole output is kept.
    assert asyncio.run(registry.execute("echo", {"big": True})) == "x" * 40
    # No opinion: the registry's cap still applies.
    assert asyncio.run(registry.execute("echo", {})) == "x" * 10 + "\n...[truncated]"
    # The tool is told the registry's own cap, so it can scale from it.
    assert seen == [({"big": True}, 10), ({}, 10)]


def test_a_tools_output_limit_still_truncates_above_it():
    registry = ToolRegistry(1000)
    registry.register(_echo_tool(40, lambda arguments, cap: 20))

    assert asyncio.run(registry.execute("echo", {})) == "x" * 20 + "\n...[truncated]"


def test_a_tools_output_limit_applies_to_an_executor_too():
    class Executor:
        async def execute(self, invocation):
            return "y" * 40

    registry = ToolRegistry(10, executor=Executor())
    registry.register(_echo_tool(1, lambda arguments, cap: 100))

    assert asyncio.run(registry.execute("echo", {})) == "y" * 40


def test_a_copied_registry_keeps_each_tools_output_limit():
    registry = ToolRegistry(10)
    registry.register(_echo_tool(40, lambda arguments, cap: 100))

    assert asyncio.run(registry.copy().execute("echo", {})) == "x" * 40


def test_a_running_tool_can_read_the_limit_its_call_runs_under():
    from conclave.registry import current_output_limit

    seen = []

    def tool(limit):
        async def func(**arguments):
            seen.append(current_output_limit())
            return "ok"

        return Tool(name="probe", description="Probe", parameters={"type": "object", "properties": {}},
                    func=func, output_limit=limit)

    registry = ToolRegistry(10)
    registry.register(tool(lambda arguments, cap: 100 if arguments.get("big") else None))
    asyncio.run(registry.execute("probe", {"big": True}))
    asyncio.run(registry.execute("probe", {}))
    unlimited = ToolRegistry(None)
    unlimited.register(tool(None))
    asyncio.run(unlimited.execute("probe", {}))

    assert seen == [100, 10, None]
    assert current_output_limit() is None, "outside a call there is no limit to report"
