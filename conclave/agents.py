"""Agent specs, JSON extraction and the tool-using agent runtime."""

import json

from dataclasses import dataclass
from typing import Any

from .config import MAX_TOOL_STEPS
from .providers import ModelProvider
from .registry import ToolRegistry


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

