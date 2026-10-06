"""The tools that ship alongside the core.

They are importable, useful and *outside* the stability promise: a bundled
tool may change or leave without a major version. Importing `conclave` does not
import this package or launch tools. The application host registers these tools
automatically; external Git and GitHub executables remain host-owned.

Every tool here declares its capabilities. None of them declares WRITE, so
none of them needs an approver to run.
"""

from pathlib import Path
from typing import Any

from ..registry import ToolRegistry
from ..spec import (
    Capability,
    Tool,
)
from .local import LocalTools


# =============================================================================
# What each bundled tool promises
# =============================================================================

READ_ONLY = frozenset(
    {
        Capability.READ,
    }
)

SHELLS_OUT = frozenset(
    {
        Capability.READ,
        Capability.SHELL,
    }
)

NETWORK_AND_SHELL = frozenset(
    {
        Capability.READ,
        Capability.NETWORK,
        Capability.SHELL,
    }
)

NETWORK_ONLY = frozenset(
    {
        Capability.NETWORK,
    }
)


def build_tool_registry(
    repo: Path,
    max_output_chars: int,
) -> ToolRegistry:
    local = LocalTools(repo)

    registry = ToolRegistry(
        max_output_chars
    )

    def add(
        name: str,
        description: str,
        parameters: dict[str, Any],
        capabilities: frozenset[
            Capability
        ],
        func: Any,
    ) -> None:
        registry.register(
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                func=func,
                capabilities=capabilities,
            )
        )

    add(
        "list_files",
        "List files under a repository path.",
        {
            "path": "string, optional",
            "max_entries": "integer, optional",
        },
        READ_ONLY,
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
        READ_ONLY,
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
        READ_ONLY,
        local.search_files,
    )

    add(
        "git_status",
        "Show repository status.",
        {},
        SHELLS_OUT,
        local.git_status,
    )

    add(
        "git_log",
        "Show Git history for a path.",
        {
            "path": "string, optional",
            "max_count": "integer, optional",
        },
        SHELLS_OUT,
        local.git_log,
    )

    add(
        "git_diff",
        "Show a diff from a revision for a path.",
        {
            "revision": "string, optional",
            "path": "string, optional",
        },
        SHELLS_OUT,
        local.git_diff,
    )

    add(
        "git_show",
        "Show a commit/revision and optional path.",
        {
            "revision": "string",
            "path": "string, optional",
        },
        SHELLS_OUT,
        local.git_show,
    )

    add(
        "github_issue",
        "Read a GitHub issue in the current repository.",
        {
            "number": "integer",
        },
        NETWORK_AND_SHELL,
        local.github_issue,
    )

    add(
        "github_pr",
        "Read a GitHub pull request in the current repository.",
        {
            "number": "integer",
        },
        NETWORK_AND_SHELL,
        local.github_pr,
    )

    add(
        "github_search_issues",
        "Search GitHub issues.",
        {
            "query": "string",
            "limit": "integer, optional",
        },
        NETWORK_AND_SHELL,
        local.github_search_issues,
    )

    add(
        "github_search_prs",
        "Search GitHub pull requests.",
        {
            "query": "string",
            "limit": "integer, optional",
        },
        NETWORK_AND_SHELL,
        local.github_search_prs,
    )

    add(
        "web_search",
        "Search the public web.",
        {
            "query": "string",
            "max_results": "integer, optional",
        },
        NETWORK_ONLY,
        local.web_search,
    )

    add(
        "web_fetch",
        "Fetch readable text from an HTTP/HTTPS URL.",
        {
            "url": "string",
            "max_chars": "integer, optional",
        },
        NETWORK_ONLY,
        local.web_fetch,
    )

    return registry
