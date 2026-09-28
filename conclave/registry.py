"""Tool registry and the builder that wires up the local tools."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import MAX_TOOL_OUTPUT_CHARS
from .local_tools import LocalTools


# =============================================================================
# Tool registry
# =============================================================================


ToolFunc = Callable[
    ...,
    Awaitable[str],
]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: ToolFunc


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(
        self,
        tool: Tool,
    ) -> None:
        self.tools[tool.name] = tool

    def describe(
        self,
        allowed: set[str],
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for name, tool in self.tools.items()
            if name in allowed
        ]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        if name not in self.tools:
            raise RuntimeError(
                f"Unknown tool: {name}"
            )

        result = await self.tools[
            name
        ].func(**arguments)

        if len(result) > MAX_TOOL_OUTPUT_CHARS:
            result = (
                result[
                    :MAX_TOOL_OUTPUT_CHARS
                ]
                + "\n...[truncated]"
            )

        return result


def build_tool_registry(
    repo: Path,
) -> ToolRegistry:
    local = LocalTools(repo)

    registry = ToolRegistry()

    def add(
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: ToolFunc,
    ) -> None:
        registry.register(
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                func=func,
            )
        )

    add(
        "list_files",
        "List files under a repository path.",
        {
            "path": "string, optional",
            "max_entries": "integer, optional",
        },
        local.list_files,
    )

    add(
        "read_file",
        "Read a line range from a repository file.",
        {
            "path": "string",
            "start_line": "integer, optional",
            "end_line": "integer, optional",
        },
        local.read_file,
    )

    add(
        "search_files",
        "Regex search repository text files.",
        {
            "query": "string",
            "path": "string, optional",
            "max_results": "integer, optional",
        },
        local.search_files,
    )

    add(
        "git_status",
        "Show repository status.",
        {},
        local.git_status,
    )

    add(
        "git_log",
        "Show Git history for a path.",
        {
            "path": "string, optional",
            "max_count": "integer, optional",
        },
        local.git_log,
    )

    add(
        "git_diff",
        "Show a diff from a revision for a path.",
        {
            "revision": "string, optional",
            "path": "string, optional",
        },
        local.git_diff,
    )

    add(
        "git_show",
        "Show a commit/revision and optional path.",
        {
            "revision": "string",
            "path": "string, optional",
        },
        local.git_show,
    )

    add(
        "github_issue",
        "Read a GitHub issue in the current repository.",
        {
            "number": "integer",
        },
        local.github_issue,
    )

    add(
        "github_pr",
        "Read a GitHub pull request in the current repository.",
        {
            "number": "integer",
        },
        local.github_pr,
    )

    add(
        "github_search_issues",
        "Search GitHub issues.",
        {
            "query": "string",
            "limit": "integer, optional",
        },
        local.github_search_issues,
    )

    add(
        "github_search_prs",
        "Search GitHub pull requests.",
        {
            "query": "string",
            "limit": "integer, optional",
        },
        local.github_search_prs,
    )

    add(
        "web_search",
        "Search the public web.",
        {
            "query": "string",
            "max_results": "integer, optional",
        },
        local.web_search,
    )

    add(
        "web_fetch",
        "Fetch readable text from an HTTP/HTTPS URL.",
        {
            "url": "string",
            "max_chars": "integer, optional",
        },
        local.web_fetch,
    )

    return registry
