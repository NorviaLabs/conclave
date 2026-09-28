"""Tests for the tool bus: registry, describe, execute and truncation."""

import asyncio

import pytest

from conclave.config import MAX_TOOL_OUTPUT_CHARS
from conclave.registry import (
    Tool,
    ToolRegistry,
    build_tool_registry,
)


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
    registry = build_tool_registry(repo)

    assert set(registry.tools) == EXPECTED_TOOLS

    for tool in registry.tools.values():
        assert tool.description
        assert callable(tool.func)


def test_describe_filters_by_permission(repo):
    registry = build_tool_registry(repo)

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
    registry = build_tool_registry(repo)

    assert registry.describe(set()) == []


def test_register_overwrites_by_name():
    registry = ToolRegistry()

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
    registry = ToolRegistry()

    with pytest.raises(
        RuntimeError,
        match="Unknown tool: nope",
    ):
        asyncio.run(
            registry.execute("nope", {})
        )


def test_execute_passes_arguments():
    registry = ToolRegistry()

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


def test_execute_truncates_long_output():
    registry = ToolRegistry()

    async def big() -> str:
        return "x" * (
            MAX_TOOL_OUTPUT_CHARS + 50
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
        MAX_TOOL_OUTPUT_CHARS
        + len("\n...[truncated]")
    )


def test_execute_keeps_short_output():
    registry = ToolRegistry()

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
