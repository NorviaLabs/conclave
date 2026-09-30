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

