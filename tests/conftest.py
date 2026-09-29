"""Shared fixtures and stub model backends for the offline test suite.

No test needs a network, `opencode`, `gh` or a real model: the providers are
replaced by scripts of JSON actions, and external processes and HTTP calls are
faked at the boundary.

The agent catalog here belongs to the tests, not to the library: the core
must work with any catalog a caller hands it.
"""

import json
import re
import subprocess

from pathlib import Path

import pytest

from conclave import (
    AgentSpec,
    Completion,
    ModelProvider,
    RunBudget,
    ToolAgentRuntime,
    Usage,
)
from conclave.tools import build_tool_registry
from conclave.tools.local import LocalTools


FINAL = {
    "type": "final",
    "content": "researcher summary",
    "findings": [
        {
            "claim": "counter is global",
            "provenance": "a.py:1",
            "confidence": 0.6,
        }
    ],
    "open_leads": ["error handling unclear"],
}


DSML_TOOL_CALL = (
    "<\uff5cDSML\uff5ccalls>\n"
    '<\uff5cDSML\uff5cinvoke name="bash">\n'
    '<\uff5cDSML\uff5cparameter name="command">'
    "ls -la\n"
    "</\uff5cDSML\uff5cparameter>\n"
    "</\uff5cDSML\uff5cinvoke>\n"
    "</\uff5cDSML\uff5ccalls>"
)


AGENTS = {
    "repo-researcher": AgentSpec(
        name="repo-researcher",
        instructions=(
            "Investigate the local repository."
        ),
        tools={
            "list_files",
            "read_file",
            "search_files",
            "git_status",
            "git_log",
            "git_diff",
            "git_show",
        },
    ),
    "web-researcher": AgentSpec(
        name="web-researcher",
        instructions=(
            "Research current external information."
        ),
        tools={
            "web_search",
            "web_fetch",
        },
    ),
    "critic": AgentSpec(
        name="critic",
        instructions=(
            "Challenge hypotheses and conclusions."
        ),
    ),
}


class ScriptedProvider(ModelProvider):
    """Replays queued JSON actions, one queue per agent name.

    The agent name is read from the system prompt, so nested agents get
    their own script without the test having to know the call order.
    """

    def __init__(
        self,
        scripts=None,
        default=None,
        usage: Usage | None = None,
    ):
        self.scripts = {
            name: list(actions)
            for name, actions in (scripts or {}).items()
        }

        self.prompts: list[tuple[str, str]] = []

        self.usage = usage

        self._default = default or {
            "type": "final",
            "content": "stub",
        }

    def agent_name(
        self,
        messages,
    ) -> str:
        match = re.search(
            r"You are (\S+) inside",
            messages[0]["content"],
        )

        return match.group(1) if match else "coordinator"

    async def complete(
        self,
        messages,
    ) -> Completion:
        name = self.agent_name(messages)

        self.prompts.append(
            (name, messages[-1]["content"])
        )

        queue = self.scripts.get(name)

        if not queue:
            return Completion(
                text=json.dumps(self._default),
                usage=self.usage,
            )

        if len(queue) > 1:
            return Completion(
                text=self.render(queue.pop(0)),
                usage=self.usage,
            )

        return Completion(
            text=self.render(queue[0]),
            usage=self.usage,
        )

    def render(
        self,
        item,
    ) -> str:
        """Dicts become JSON actions; strings pass through verbatim."""

        if isinstance(item, str):
            return item

        return json.dumps(item)


class CannedProvider(ModelProvider):
    """Returns one fixed response and records every message list."""

    def __init__(
        self,
        response: str = "{}",
    ):
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages,
    ) -> Completion:
        self.calls.append(list(messages))

        return Completion(text=self.response)


class FailingProvider(ModelProvider):
    """Always raises, to exercise the failure paths."""

    def __init__(
        self,
        message: str = "boom",
    ):
        self.message = message

    async def complete(
        self,
        messages,
    ) -> Completion:
        raise RuntimeError(self.message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed Git repository containing a couple of files."""

    subprocess.run(
        ["git", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )

    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=tmp_path,
        check=True,
    )

    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=tmp_path,
        check=True,
    )

    (tmp_path / "a.py").write_text(
        "def retry():\n    return 'retry counter'\n"
    )

    (tmp_path / "notes.md").write_text(
        "nothing to see here\n"
    )

    subprocess.run(
        ["git", "add", "."],
        cwd=tmp_path,
        check=True,
    )

    subprocess.run(
        ["git", "commit", "-qm", "first"],
        cwd=tmp_path,
        check=True,
    )

    return tmp_path


@pytest.fixture
def local(repo: Path) -> LocalTools:
    return LocalTools(repo)


@pytest.fixture
def tools(repo: Path):
    return build_tool_registry(repo)


@pytest.fixture
def runtime(tools):
    def build(
        provider,
        budget=None,
        agents=None,
        **kwargs,
    ) -> ToolAgentRuntime:
        return ToolAgentRuntime(
            provider,
            tools,
            budget or RunBudget(),
            AGENTS if agents is None else agents,
            **kwargs,
        )

    return build
