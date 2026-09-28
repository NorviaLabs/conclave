import argparse
import asyncio
import html
import json
import os
import re
import subprocess
import sys
import urllib.request

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse


MAX_ROUNDS = 8
MAX_DELEGATIONS = 15
MAX_DELEGATIONS_PER_ROUND = 3
MAX_TOOL_STEPS = 8
TIMEOUT_SECONDS = 300
MAX_TOOL_OUTPUT_CHARS = 20_000


# =============================================================================
# Model providers
# =============================================================================


class ModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        raise NotImplementedError


class OpenCodeProvider(ModelProvider):
    """
    Uses your existing OpenCode authentication.

    OpenCode is used only as a model backend.
    Its own tools are disabled.
    """

    def __init__(
        self,
        cwd: Path,
        model: str | None = None,
    ):
        self.cwd = cwd
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        prompt = "\n\n".join(
            f"{message['role'].upper()}:\n"
            f"{message['content']}"
            for message in messages
        )

        command = [
            "opencode",
            "run",
            "--agent",
            "model-only",
        ]

        if self.model:
            command.extend(
                [
                    "--model",
                    self.model,
                ]
            )

        command.append(prompt)

        env = os.environ.copy()
        env["NO_COLOR"] = "1"

        # Provider-specific implementation detail.
        # The rest of the agent system knows nothing about this.
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "agent": {
                    "model-only": {
                        "description": (
                            "Model-only backend for an "
                            "external agent runtime"
                        ),
                        "mode": "primary",
                        "permission": {
                            "*": "deny",
                        },
                    }
                }
            }
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=TIMEOUT_SECONDS,
            )

        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()

            raise RuntimeError(
                "OpenCode model call timed out"
            )

        if process.returncode != 0:
            raise RuntimeError(
                stderr.decode().strip()
                or "OpenCode failed"
            )

        return stdout.decode().strip()


class OllamaProvider(ModelProvider):
    """
    Optional second provider.

    This exists mainly to demonstrate that the multi-agent
    runtime itself is not coupled to OpenCode.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
            }
        ).encode()

        def request() -> str:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(
                req,
                timeout=TIMEOUT_SECONDS,
            ) as response:
                data = json.loads(
                    response.read().decode()
                )

                return data["message"]["content"]

        return await asyncio.to_thread(
            request
        )


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


# =============================================================================
# Local tools
# =============================================================================


class LocalTools:
    def __init__(
        self,
        repo: Path,
    ):
        self.repo = repo.resolve()

    def _safe_path(
        self,
        relative: str,
    ) -> Path:
        path = (
            self.repo / relative
        ).resolve()

        try:
            path.relative_to(
                self.repo
            )

        except ValueError as exc:
            raise RuntimeError(
                "Path escapes repository"
            ) from exc

        return path

    async def list_files(
        self,
        path: str = ".",
        max_entries: int = 200,
    ) -> str:
        root = self._safe_path(path)

        if not root.exists():
            return (
                f"Path does not exist: {path}"
            )

        ignored = {
            ".git",
            "node_modules",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        }

        entries: list[str] = []

        for item in root.rglob("*"):
            if any(
                part in ignored
                for part in item.parts
            ):
                continue

            try:
                rel = item.relative_to(
                    self.repo
                )

            except ValueError:
                continue

            entries.append(
                str(rel)
                + (
                    "/"
                    if item.is_dir()
                    else ""
                )
            )

            if (
                len(entries)
                >= max_entries
            ):
                break

        return "\n".join(entries)

    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 250,
    ) -> str:
        file_path = self._safe_path(
            path
        )

        if not file_path.is_file():
            return f"Not a file: {path}"

        lines = file_path.read_text(
            errors="replace"
        ).splitlines()

        start = max(
            1,
            start_line,
        )

        end = min(
            len(lines),
            max(
                start,
                end_line,
            ),
        )

        return "\n".join(
            f"{i}: {lines[i - 1]}"
            for i in range(
                start,
                end + 1,
            )
        )

    async def search_files(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        root = self._safe_path(path)

        pattern = re.compile(
            query,
            re.IGNORECASE,
        )

        ignored = {
            ".git",
            "node_modules",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        }

        results: list[str] = []

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue

            if any(
                part in ignored
                for part in file_path.parts
            ):
                continue

            try:
                text = file_path.read_text(
                    errors="replace"
                )

            except OSError:
                continue

            for (
                line_number,
                line,
            ) in enumerate(
                text.splitlines(),
                start=1,
            ):
                if not pattern.search(
                    line
                ):
                    continue

                rel = (
                    file_path
                    .relative_to(
                        self.repo
                    )
                )

                results.append(
                    f"{rel}:"
                    f"{line_number}: "
                    f"{line.strip()}"
                )

                if (
                    len(results)
                    >= max_results
                ):
                    return "\n".join(
                        results
                    )

        return (
            "\n".join(results)
            or "No matches"
        )

    async def _run(
        self,
        command: list[str],
    ) -> str:
        def run() -> str:
            completed = subprocess.run(
                command,
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=60,
            )

            output = (
                completed.stdout.strip()
            )

            error = (
                completed.stderr.strip()
            )

            if (
                completed.returncode
                != 0
            ):
                raise RuntimeError(
                    error
                    or (
                        "Command failed: "
                        + " ".join(
                            command
                        )
                    )
                )

            return (
                output
                or "(no output)"
            )

        return await asyncio.to_thread(
            run
        )

    async def git_status(
        self,
    ) -> str:
        return await self._run(
            [
                "git",
                "status",
                "--short",
                "--branch",
            ]
        )

    async def git_log(
        self,
        path: str = ".",
        max_count: int = 20,
    ) -> str:
        safe = self._safe_path(
            path
        )

        rel = (
            str(
                safe.relative_to(
                    self.repo
                )
            )
            or "."
        )

        return await self._run(
            [
                "git",
                "log",
                (
                    "--max-count="
                    f"{max(1, min(max_count, 100))}"
                ),
                "--date=short",
                (
                    "--pretty=format:"
                    "%h %ad %an %s"
                ),
                "--",
                rel,
            ]
        )

    async def git_diff(
        self,
        revision: str = "HEAD",
        path: str = ".",
    ) -> str:
        safe = self._safe_path(
            path
        )

        rel = (
            str(
                safe.relative_to(
                    self.repo
                )
            )
            or "."
        )

        return await self._run(
            [
                "git",
                "diff",
                revision,
                "--",
                rel,
            ]
        )

    async def git_show(
        self,
        revision: str,
        path: str | None = None,
    ) -> str:
        command = [
            "git",
            "show",
            "--stat",
            "--patch",
            revision,
        ]

        if path:
            safe = self._safe_path(
                path
            )

            command.extend(
                [
                    "--",
                    str(
                        safe.relative_to(
                            self.repo
                        )
                    ),
                ]
            )

        return await self._run(
            command
        )

    async def github_issue(
        self,
        number: int,
    ) -> str:
        return await self._run(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--comments",
                "--json",
                (
                    "number,title,state,"
                    "body,author,comments,"
                    "labels,url"
                ),
            ]
        )

    async def github_pr(
        self,
        number: int,
    ) -> str:
        return await self._run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--comments",
                "--json",
                (
                    "number,title,state,"
                    "body,author,comments,"
                    "reviews,commits,"
                    "files,url"
                ),
            ]
        )

    async def github_search_issues(
        self,
        query: str,
        limit: int = 20,
    ) -> str:
        return await self._run(
            [
                "gh",
                "search",
                "issues",
                query,
                "--limit",
                str(
                    max(
                        1,
                        min(
                            limit,
                            100,
                        ),
                    )
                ),
                "--json",
                (
                    "number,title,state,"
                    "url,repository"
                ),
            ]
        )

    async def github_search_prs(
        self,
        query: str,
        limit: int = 20,
    ) -> str:
        return await self._run(
            [
                "gh",
                "search",
                "prs",
                query,
                "--limit",
                str(
                    max(
                        1,
                        min(
                            limit,
                            100,
                        ),
                    )
                ),
                "--json",
                (
                    "number,title,state,"
                    "url,repository"
                ),
            ]
        )

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
    ) -> str:
        try:
            from ddgs import DDGS

        except ImportError as exc:
            raise RuntimeError(
                "web_search requires: "
                "pip install ddgs"
            ) from exc

        def search() -> str:
            results = DDGS(
                timeout=10
            ).text(
                query,
                max_results=max(
                    1,
                    min(
                        max_results,
                        10,
                    ),
                ),
            )

            return json.dumps(
                results,
                indent=2,
                ensure_ascii=False,
            )

        return await asyncio.to_thread(
            search
        )

    async def web_fetch(
        self,
        url: str,
        max_chars: int = 15_000,
    ) -> str:
        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise RuntimeError(
                "Only http/https URLs "
                "are allowed"
            )

        def fetch() -> str:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "toy-multi-agent-poc"
                    )
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                raw = response.read(
                    1_000_000
                ).decode(
                    errors="replace"
                )

            raw = re.sub(
                (
                    r"(?is)<script.*?>"
                    r".*?</script>"
                ),
                " ",
                raw,
            )

            raw = re.sub(
                (
                    r"(?is)<style.*?>"
                    r".*?</style>"
                ),
                " ",
                raw,
            )

            text = re.sub(
                r"(?s)<[^>]+>",
                " ",
                raw,
            )

            text = html.unescape(
                text
            )

            text = re.sub(
                r"\s+",
                " ",
                text,
            ).strip()

            return text[
                :max(
                    1000,
                    min(
                        max_chars,
                        50_000,
                    ),
                )
            ]

        return await asyncio.to_thread(
            fetch
        )


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


# =============================================================================
# Provider-independent agent runtime
# =============================================================================


@dataclass
class AgentSpec:
    name: str
    instructions: str
    tools: set[str]


AGENTS = {
    "repo-researcher": AgentSpec(
        name="repo-researcher",
        instructions="""
Investigate the local repository and Git history.

Gather concrete evidence.
Do not modify anything.
""".strip(),
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

    "github-researcher": AgentSpec(
        name="github-researcher",
        instructions="""
Investigate GitHub issues, pull requests,
reviews, comments, and history.

Gather evidence.
Never mutate GitHub.
""".strip(),
        tools={
            "github_issue",
            "github_pr",
            "github_search_issues",
            "github_search_prs",
        },
    ),

    "web-researcher": AgentSpec(
        name="web-researcher",
        instructions="""
Research current external information.

Prefer primary and official sources.
Preserve URLs and provenance.
""".strip(),
        tools={
            "web_search",
            "web_fetch",
        },
    ),

    "architect": AgentSpec(
        name="architect",
        instructions="""
Reason about architecture and tradeoffs
using only supplied evidence.

Call out missing evidence rather than guessing.
""".strip(),
        tools=set(),
    ),

    "critic": AgentSpec(
        name="critic",
        instructions="""
Challenge hypotheses and conclusions.

Look for contradictions,
unsupported assumptions,
and missing evidence.
""".strip(),
        tools=set(),
    ),
}


def extract_json(
    text: str,
) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()[1:]

        if (
            lines
            and lines[-1].strip()
            == "```"
        ):
            lines = lines[:-1]

        text = "\n".join(
            lines
        ).strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if (
            start >= 0
            and end > start
        ):
            return json.loads(
                text[
                    start : end + 1
                ]
            )

        raise RuntimeError(
            "Expected JSON, got:\n"
            + text
        )


async def complete_json(
    provider: ModelProvider,
    system: str,
    user: str,
) -> dict[str, Any]:
    text = await provider.complete(
        [
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": user,
            },
        ]
    )

    return extract_json(text)


class ToolAgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
    ):
        self.provider = provider
        self.tools = tools

    async def run(
        self,
        spec: AgentSpec,
        task: str,
    ) -> str:
        available = (
            self.tools.describe(
                spec.tools
            )
        )

        system = f"""
You are {spec.name} inside a larger
multi-agent investigation.

{spec.instructions}

The Python runtime owns all tools.
You cannot invoke tools directly.

At every step, return exactly one JSON object.

To use a tool:

{{
  "type": "tool",
  "name": "tool_name",
  "arguments": {{}}
}}

When finished:

{{
  "type": "final",
  "content": "your evidence-driven findings"
}}

Available tools:

{json.dumps(available, indent=2)}

Rules:

- Use only listed tools.
- Gather evidence rather than guessing.
- Include provenance in the final result:
  file paths, commits, issues, PRs, URLs, etc.
- Do not invent tool results.
""".strip()

        messages = [
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": task,
            },
        ]

        for _ in range(
            MAX_TOOL_STEPS
        ):
            response = (
                await self.provider.complete(
                    messages
                )
            )

            action = extract_json(
                response
            )

            if (
                action.get("type")
                == "final"
            ):
                return str(
                    action.get(
                        "content",
                        "",
                    )
                ).strip()

            if (
                action.get("type")
                != "tool"
            ):
                raise RuntimeError(
                    "Invalid agent action: "
                    f"{action}"
                )

            name = action.get(
                "name"
            )

            arguments = (
                action.get(
                    "arguments"
                )
                or {}
            )

            if name not in spec.tools:
                observation = (
                    "Permission denied: "
                    f"{name} is not available "
                    f"to {spec.name}."
                )

            else:
                try:
                    observation = (
                        await self.tools.execute(
                            name,
                            arguments,
                        )
                    )

                except Exception as exc:
                    observation = (
                        f"Tool error: {exc}"
                    )

            messages.append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"TOOL RESULT for "
                        f"{name}:\n"
                        f"{observation}\n\n"
                        "Continue. Return only "
                        "the next JSON action."
                    ),
                }
            )

        return (
            "Tool-step budget exhausted "
            "before the specialist produced "
            "a final answer."
        )


# =============================================================================
# Structured investigation state
# =============================================================================


EMPTY_STATE = {
    "facts": [],
    "evidence": [],
    "hypotheses": [],
    "open_questions": [],
    "investigations": [],
}


def state_json(
    state: dict[str, Any],
) -> str:
    return json.dumps(
        state,
        indent=2,
        ensure_ascii=False,
    )


def normalize_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: (
            state.get(
                key,
                [],
            )
            if isinstance(
                state.get(
                    key,
                    [],
                ),
                list,
            )
            else []
        )
        for key in EMPTY_STATE
    }


async def initialize_state(
    provider: ModelProvider,
    question: str,
) -> dict[str, Any]:
    state = await complete_json(
        provider,
        """
You initialize structured investigation state.

Return JSON only.
""".strip(),
        f"""
Question:

{question}

Create 2-4 plausible, testable hypotheses
and the important open questions.

There is no evidence yet.

Therefore:
- facts must be empty
- evidence must be empty
- investigations must be empty

Confidence is a heuristic from 0.0 to 1.0
and should initially be low.

Return:

{{
  "facts": [],
  "evidence": [],
  "hypotheses": [
    {{
      "id": "H1",
      "claim": "...",
      "confidence": 0.2,
      "status": "open",
      "evidence_for": [],
      "evidence_against": []
    }}
  ],
  "open_questions": [
    {{
      "id": "Q1",
      "question": "...",
      "priority": "high",
      "status": "open",
      "related_hypotheses": ["H1"],
      "answer": null,
      "evidence": []
    }}
  ],
  "investigations": []
}}
""".strip(),
    )

    return normalize_state(
        state
    )


async def decide_next(
    provider: ModelProvider,
    question: str,
    state: dict[str, Any],
    round_number: int,
) -> dict[str, Any]:
    return await complete_json(
        provider,
        """
You coordinate a multi-agent investigation.

Return JSON only.
""".strip(),
        f"""
Original question:

{question}


Round:

{round_number}/{MAX_ROUNDS}


Current state:

{state_json(state)}


Available specialists:

repo-researcher
    local files and Git history

github-researcher
    GitHub issues, PRs and history

web-researcher
    current external research

architect
    architectural reasoning over supplied evidence

critic
    challenges conclusions and assumptions


Choose investigations that reduce the most
important remaining uncertainty.

Do not repeat completed work.

Use at most {MAX_DELEGATIONS_PER_ROUND}
independent delegations in this round.

Finish when remaining uncertainty is not
important enough to justify more investigation.


Delegate:

{{
  "status": "delegate",
  "reason": "...",
  "delegations": [
    {{
      "agent": "repo-researcher",
      "task": "specific investigation",
      "purpose": "what uncertainty this reduces",
      "targets_hypotheses": ["H1"],
      "targets_questions": ["Q1"],
      "expected_evidence": "what evidence would discriminate"
    }}
  ]
}}


Finish:

{{
  "status": "done",
  "answer": "evidence-grounded final answer"
}}
""".strip(),
    )


async def update_state(
    provider: ModelProvider,
    question: str,
    previous_state: dict[str, Any],
    round_number: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    updated = await complete_json(
        provider,
        """
You maintain canonical investigation state.

Return JSON only.
""".strip(),
        f"""
Question:

{question}


Round:

{round_number}


Previous state:

{state_json(previous_state)}


New specialist results:

{json.dumps(
    results,
    indent=2,
    ensure_ascii=False,
)}


Update the COMPLETE state.

Rules:

- Preserve existing IDs.
- Add F/E/H/Q IDs using the next unused number.
- Facts must be directly supported by evidence.
- Evidence must preserve concrete provenance.
- Evidence may support or contradict hypotheses.
- Update evidence_for and evidence_against.
- Change confidence only when evidence justifies it.
- Hypothesis status:
  open | supported | weakened | rejected
- Question status:
  open | resolved
- Never delete resolved questions.
- Record completed and failed investigations.
- Do not invent paths, commits, URLs,
  issues, PRs or claims.
- Keep state compact.

Return exactly these top-level keys:

facts
evidence
hypotheses
open_questions
investigations
""".strip(),
    )

    return normalize_state(
        updated
    )


async def force_final(
    provider: ModelProvider,
    question: str,
    state: dict[str, Any],
) -> str:
    return await provider.complete(
        [
            {
                "role": "system",
                "content": """
Answer from the supplied investigation state.

Distinguish evidence, inference,
and unresolved uncertainty.
""".strip(),
            },
            {
                "role": "user",
                "content": f"""
Question:

{question}


State:

{state_json(state)}


Give the best concise final answer.

Do not request more research.
""".strip(),
            },
        ]
    )


def print_state_summary(
    state: dict[str, Any],
) -> None:
    print(
        "\n[shared state]"
    )

    for hypothesis in state.get(
        "hypotheses",
        [],
    ):
        print(
            f"  {hypothesis.get('id', '?')} "
            f"[{hypothesis.get('status', '?')}, "
            f"{hypothesis.get('confidence', '?')}]: "
            f"{hypothesis.get('claim', '')}"
        )

    open_questions = [
        question
        for question in state.get(
            "open_questions",
            [],
        )
        if question.get(
            "status"
        )
        == "open"
    ]

    if open_questions:
        print(
            "  Open questions:"
        )

        for question in open_questions:
            print(
                f"    "
                f"{question.get('id', '?')}: "
                f"{question.get('question', '')}"
            )

    print(
        f"  Facts="
        f"{len(state.get('facts', []))} "
        f"Evidence="
        f"{len(state.get('evidence', []))} "
        f"Investigations="
        f"{len(state.get('investigations', []))}"
    )


# =============================================================================
# Main orchestration
#
# Nothing below this line knows or cares whether the provider is
# OpenCode, Ollama, Anthropic, OpenAI, etc.
# =============================================================================


async def run_investigation(
    provider: ModelProvider,
    tools: ToolRegistry,
    question: str,
) -> None:
    runtime = ToolAgentRuntime(
        provider,
        tools,
    )

    state = await initialize_state(
        provider,
        question,
    )

    print_state_summary(
        state
    )

    seen_tasks: set[
        tuple[str, str]
    ] = set()

    total_delegations = 0

    for round_number in range(
        1,
        MAX_ROUNDS + 1,
    ):
        print(
            f"\n{'=' * 70}"
        )

        print(
            f"ROUND {round_number}"
        )

        print(
            "=" * 70
        )

        decision = await decide_next(
            provider,
            question,
            state,
            round_number,
        )

        if (
            decision.get("status")
            == "done"
        ):
            print(
                "\n[coordinator -> done]\n"
            )

            print(
                decision.get(
                    "answer",
                    "",
                )
            )

            return

        if (
            decision.get("status")
            != "delegate"
        ):
            raise RuntimeError(
                "Invalid coordinator decision: "
                f"{decision}"
            )

        print(
            "\nReason: "
            f"{decision.get('reason', '')}"
        )

        valid: list[
            dict[str, Any]
        ] = []

        for delegation in decision.get(
            "delegations",
            [],
        ):
            if (
                total_delegations
                >= MAX_DELEGATIONS
            ):
                break

            agent_name = (
                delegation.get(
                    "agent"
                )
            )

            task = str(
                delegation.get(
                    "task",
                    "",
                )
            ).strip()

            if (
                agent_name
                not in AGENTS
            ):
                continue

            if not task:
                continue

            key = (
                agent_name,
                " ".join(
                    task
                    .lower()
                    .split()
                ),
            )

            if key in seen_tasks:
                continue

            seen_tasks.add(
                key
            )

            total_delegations += 1

            valid.append(
                delegation
            )

        if not valid:
            break

        for delegation in valid:
            print(
                f"  -> "
                f"{delegation['agent']}: "
                f"{delegation['task']}"
            )

        async def execute_delegation(
            delegation: dict[str, Any],
        ) -> dict[str, Any]:
            name = delegation[
                "agent"
            ]

            spec = AGENTS[
                name
            ]

            context = {
                "original_question": (
                    question
                ),
                "purpose": (
                    delegation.get(
                        "purpose",
                        "",
                    )
                ),
                "targets_hypotheses": (
                    delegation.get(
                        "targets_hypotheses",
                        [],
                    )
                ),
                "targets_questions": (
                    delegation.get(
                        "targets_questions",
                        [],
                    )
                ),
                "expected_evidence": (
                    delegation.get(
                        "expected_evidence",
                        "",
                    )
                ),
                "current_state": (
                    state
                ),
            }

            task = (
                delegation["task"]
                + "\n\n"
                + "Investigation context:\n"
                + json.dumps(
                    context,
                    indent=2,
                    ensure_ascii=False,
                )
            )

            try:
                result = (
                    await runtime.run(
                        spec,
                        task,
                    )
                )

                status = (
                    "completed"
                )

            except Exception as exc:
                result = (
                    f"Agent failed: {exc}"
                )

                status = "failed"

            print(
                f"\n[{name}]\n"
                f"{result}"
            )

            return {
                "agent": name,
                "task": delegation[
                    "task"
                ],
                "purpose": (
                    delegation.get(
                        "purpose",
                        "",
                    )
                ),
                "targets_hypotheses": (
                    delegation.get(
                        "targets_hypotheses",
                        [],
                    )
                ),
                "targets_questions": (
                    delegation.get(
                        "targets_questions",
                        [],
                    )
                ),
                "status": status,
                "result": result,
            }

        results = await asyncio.gather(
            *(
                execute_delegation(
                    delegation
                )
                for delegation in valid
            )
        )

        state = await update_state(
            provider,
            question,
            state,
            round_number,
            results,
        )

        print_state_summary(
            state
        )

    print(
        "\n[investigation budget exhausted -> final]\n"
    )

    print(
        await force_final(
            provider,
            question,
            state,
        )
    )


# =============================================================================
# CLI / provider selection
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "question",
        nargs="*",
    )

    parser.add_argument(
        "--repo",
        default=".",
    )

    parser.add_argument(
        "--provider",
        choices=[
            "opencode",
            "ollama",
        ],
        default="opencode",
    )

    parser.add_argument(
        "--model",
        default=None,
    )

    parser.add_argument(
        "--ollama-url",
        default=(
            "http://127.0.0.1:11434"
        ),
    )

    return parser.parse_args()


def make_provider(
    args: argparse.Namespace,
    repo: Path,
) -> ModelProvider:
    if (
        args.provider
        == "opencode"
    ):
        return OpenCodeProvider(
            repo,
            model=args.model,
        )

    model = (
        args.model
        or "qwen3.6"
    )

    return OllamaProvider(
        model=model,
        base_url=args.ollama_url,
    )


async def main() -> None:
    args = parse_args()

    repo = (
        Path(args.repo)
        .expanduser()
        .resolve()
    )

    if not repo.exists():
        raise RuntimeError(
            "Repository does not exist: "
            f"{repo}"
        )

    question = " ".join(
        args.question
    ).strip()

    if not question:
        question = """
Investigate this repository and identify one important
architectural weakness, using repository, GitHub and web
evidence where useful.
""".strip()

    provider = make_provider(
        args,
        repo,
    )

    tools = build_tool_registry(
        repo
    )

    print(
        f"QUESTION\n{question}\n"
    )

    print(
        f"WORKSPACE\n{repo}\n"
    )

    print(
        f"MODEL PROVIDER\n"
        f"{args.provider}\n"
    )

    await run_investigation(
        provider,
        tools,
        question,
    )


if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print(
            "\nCancelled."
        )

        sys.exit(130)

    except Exception as exc:
        print(
            f"\nERROR: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)