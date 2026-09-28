"""Agent specs, JSON extraction and the tool-using agent runtime."""

import json

from dataclasses import (
    dataclass,
    field,
)
from typing import Any

from .budget import RunBudget
from .config import (
    MAX_NOTES,
    MAX_NOTE_CHARS,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_TOOL_STEPS,
)
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


JSON_ONLY_RULE = (
    "Reply with exactly one JSON object and "
    "nothing else. Never emit tool-call "
    "markup, XML tags or function-call "
    "syntax such as DSML, antml, invoke or "
    "parameter elements. The Python runtime "
    "owns all tools; you cannot call tools."
)


class ModelOutputError(RuntimeError):
    """Raised when a model reply is not the JSON object we asked for.

    `text` keeps the raw reply so a caller can retry with it, or report
    what the model actually said instead of only the parse failure.
    """

    def __init__(
        self,
        text: str,
    ):
        self.text = text

        super().__init__(
            "Expected JSON, got:\n" + text
        )


def extract_json(
    text: str,
) -> dict[str, Any]:
    raw = text.strip()

    text = raw

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
        parsed = json.loads(text)

    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")

        if (
            start >= 0
            and end > start
        ):
            try:
                parsed = json.loads(
                    text[
                        start : end + 1
                    ]
                )

            except json.JSONDecodeError:
                raise ModelOutputError(
                    raw
                ) from exc

        else:
            raise ModelOutputError(
                raw
            ) from exc

    if not isinstance(parsed, dict):
        raise ModelOutputError(raw)

    return parsed


async def complete_json(
    provider: ModelProvider,
    system: str,
    user: str,
    retries: int = 1,
) -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": f"{system}\n\n{JSON_ONLY_RULE}",
        },
        {
            "role": "user",
            "content": user,
        },
    ]

    attempt = 0

    while True:
        text = await provider.complete(
            messages
        )

        try:
            return extract_json(text)

        except ModelOutputError as exc:
            if attempt >= retries:
                raise

            attempt += 1

            messages = messages[
                :2
            ] + [
                {
                    "role": "assistant",
                    "content": text,
                },
                {
                    "role": "user",
                    "content": (
                        f"{JSON_ONLY_RULE}\n\n"
                        "Your previous reply was "
                        "rejected:\n"
                        f"{exc.text[:1000]}\n\n"
                        "Reply again with only "
                        "the JSON object."
                    ),
                },
            ]


@dataclass
class PrivateState:
    """Working state owned by one agent run.

    Notes and the checked ledger stay with the
    agent that made them. Only the final handoff
    crosses back to the coordinator.
    """

    notes: list[str] = field(
        default_factory=list
    )

    checked: list[str] = field(
        default_factory=list
    )

    def note(
        self,
        text: str,
    ) -> str:
        text = " ".join(
            text.split()
        )

        if not text:
            return (
                "Note ignored: it was empty."
            )

        if (
            len(self.notes)
            >= MAX_NOTES
        ):
            return (
                "Note refused: the private "
                "scratchpad is full "
                f"({MAX_NOTES} notes). "
                "Continue without it."
            )

        self.notes.append(
            text[:MAX_NOTE_CHARS]
        )

        return (
            f"Note {len(self.notes)}/"
            f"{MAX_NOTES} recorded."
        )

    def check(
        self,
        summary: str,
    ) -> None:
        self.checked.append(
            summary[:MAX_NOTE_CHARS]
        )

        del self.checked[
            :-MAX_NOTES
        ]

    def render(
        self,
    ) -> str:
        if (
            not self.notes
            and not self.checked
        ):
            return "(empty)"

        lines = [
            f"- note: {note}"
            for note in self.notes
        ]

        lines.extend(
            f"- already checked: {item}"
            for item in self.checked
        )

        return "\n".join(lines)


@dataclass
class AgentResult:
    content: str

    findings: list[Any] = field(
        default_factory=list
    )

    open_leads: list[Any] = field(
        default_factory=list
    )

    private: PrivateState = field(
        default_factory=PrivateState
    )

    nested: list[
        dict[str, Any]
    ] = field(
        default_factory=list
    )


def as_list(
    value: Any,
) -> list[Any]:
    if isinstance(value, list):
        return value

    return []


class ToolAgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        budget: RunBudget,
    ):
        self.provider = provider
        self.tools = tools
        self.budget = budget

    async def run(
        self,
        spec: AgentSpec,
        task: str,
        *,
        depth: int = 1,
        ancestry: tuple[str, ...] = (),
    ) -> AgentResult:
        private = PrivateState()

        nested: list[
            dict[str, Any]
        ] = []

        messages: list[
            dict[str, str]
        ] = [
            {
                "role": "system",
                "content": "",
            },
            {
                "role": "user",
                "content": task,
            },
        ]

        for _ in range(
            MAX_TOOL_STEPS
        ):
            messages[0][
                "content"
            ] = self.system_prompt(
                spec,
                depth,
                private,
            )

            response = (
                await self.provider.complete(
                    messages
                )
            )

            try:
                action = extract_json(
                    response
                )

            except ModelOutputError:
                action = None

            kind = (
                action.get("type")
                if action is not None
                else None
            )

            if kind == "final":
                return AgentResult(
                    content=str(
                        action.get(
                            "content",
                            "",
                        )
                    ).strip(),
                    findings=as_list(
                        action.get(
                            "findings"
                        )
                    ),
                    open_leads=as_list(
                        action.get(
                            "open_leads"
                        )
                    ),
                    private=private,
                    nested=nested,
                )

            if kind == "note":
                label = "NOTE RESULT"

                observation = private.note(
                    str(
                        action.get(
                            "content",
                            "",
                        )
                    )
                )

            elif kind == "delegate":
                record = await self.delegate(
                    spec,
                    action,
                    depth,
                    ancestry,
                )

                label = (
                    "DELEGATION RESULT for "
                    f"{record['agent']}"
                )

                observation = record[
                    "observation"
                ]

                descendants = record.pop(
                    "nested"
                )

                nested.append(record)

                nested.extend(
                    descendants
                )

            elif kind == "tool":
                name = action.get(
                    "name"
                )

                label = (
                    f"TOOL RESULT for {name}"
                )

                observation = (
                    await self.use_tool(
                        spec,
                        name,
                        action.get(
                            "arguments"
                        )
                        or {},
                        private,
                    )
                )

            elif kind is None:
                label = "FORMAT ERROR"

                observation = JSON_ONLY_RULE

            else:
                raise RuntimeError(
                    "Invalid agent action: "
                    f"{action}"
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
                        f"{label}:\n"
                        f"{observation}\n\n"
                        "Continue. Return only "
                        "the next JSON action."
                    ),
                }
            )

        return AgentResult(
            content=(
                "Tool-step budget exhausted "
                "before the specialist produced "
                "a final answer."
            ),
            private=private,
            nested=nested,
        )

    async def use_tool(
        self,
        spec: AgentSpec,
        name: Any,
        arguments: dict[str, Any],
        private: PrivateState,
    ) -> str:
        if name not in spec.tools:
            return (
                "Permission denied: "
                f"{name} is not available "
                f"to {spec.name}."
            )

        rendered = json.dumps(
            arguments,
            ensure_ascii=False,
        )

        summary = f"{name}({rendered})"

        try:
            observation = (
                await self.tools.execute(
                    name,
                    arguments,
                )
            )

        except Exception as exc:
            private.check(
                summary + " -> failed"
            )

            return f"Tool error: {exc}"

        private.check(summary)

        return observation

    async def delegate(
        self,
        spec: AgentSpec,
        action: dict[str, Any],
        depth: int,
        ancestry: tuple[str, ...],
    ) -> dict[str, Any]:
        target = action.get(
            "agent"
        )

        task = str(
            action.get(
                "task",
                "",
            )
        ).strip()

        purpose = str(
            action.get(
                "purpose",
                "",
            )
        ).strip()

        record: dict[str, Any] = {
            "agent": target,
            "delegated_by": spec.name,
            "task": task,
            "purpose": purpose,
            "depth": depth + 1,
            "status": "refused",
            "reason": "",
            "observation": "",
            "nested": [],
        }

        if (
            not isinstance(target, str)
            or target not in AGENTS
        ):
            return self.refuse(
                record,
                f"Unknown specialist: {target}",
            )

        if not task:
            return self.refuse(
                record,
                "Refused: the delegated "
                "task was empty.",
            )

        refusal = self.budget.reserve(
            str(target),
            depth + 1,
            ancestry + (spec.name,),
        )

        if refusal:
            return self.refuse(
                record,
                refusal,
            )

        nested_task = (
            task
            + "\n\n"
            + "Nested delegation:\n"
            + json.dumps(
                {
                    "delegated_by": (
                        spec.name
                    ),
                    "purpose": purpose,
                    "depth": depth + 1,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        try:
            child = await self.run(
                AGENTS[target],
                nested_task,
                depth=depth + 1,
                ancestry=(
                    ancestry + (spec.name,)
                ),
            )

        except Exception as exc:
            record["status"] = "failed"
            record["reason"] = str(exc)

            record["observation"] = (
                f"{target} failed: {exc}\n\n"
                "Continue without it. "
                "Return only the next "
                "JSON action."
            )

            return record

        record["status"] = "completed"
        record["findings"] = child.findings
        record["open_leads"] = child.open_leads
        record["nested"] = child.nested

        record["observation"] = (
            f"{target} reported:\n"
            f"{child.content[:MAX_TOOL_OUTPUT_CHARS]}"
            "\n\nFindings:\n"
            f"{json.dumps(child.findings, ensure_ascii=False)}"
            "\n\nOpen leads:\n"
            f"{json.dumps(child.open_leads, ensure_ascii=False)}"
        )

        return record

    def refuse(
        self,
        record: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        record["reason"] = reason

        record["observation"] = (
            f"{reason}\n\n"
            "Do not repeat this delegation. "
            "Continue with the evidence you "
            "already have. Return only the "
            "next JSON action."
        )

        return record

    def system_prompt(
        self,
        spec: AgentSpec,
        depth: int,
        private: PrivateState,
    ) -> str:
        available = (
            self.tools.describe(
                spec.tools
            )
        )

        delegation = ""

        if self.budget.may_delegate(
            depth
        ):
            delegation = f"""
To delegate follow-up work:

{{
  "type": "delegate",
  "agent": "agent-name",
  "task": "self-contained investigation",
  "purpose": "what uncertainty this removes"
}}

Delegation rules:

- Delegations are limited and counted
  against the whole investigation.
- The task must be self-contained: the other
  specialist sees only your task text, not
  your notes, your transcript or the shared
  investigation state.
- Never delegate work you already did.
- You stay responsible for the final answer.
- Available specialists:
  {", ".join(sorted(AGENTS))}.
"""

        return f"""
You are {spec.name} inside a larger
multi-agent investigation.

{spec.instructions}

You are at depth {depth} of
{self.budget.max_depth}.

The Python runtime owns all tools.
You cannot invoke tools directly.

At every step, return exactly one JSON object.

To use a tool:

{{
  "type": "tool",
  "name": "tool_name",
  "arguments": {{}}
}}

To record a private note:

{{
  "type": "note",
  "content": "what you checked and what it showed"
}}

Notes are private to you. They are never shared
with the coordinator or with other specialists.
{delegation}
When finished:

{{
  "type": "final",
  "content": "your evidence-driven findings",
  "findings": [
    {{
      "claim": "...",
      "provenance": "path, commit, issue, PR or URL",
      "confidence": 0.7
    }}
  ],
  "open_leads": ["what you could not resolve"]
}}

Your private scratchpad:

{private.render()}

Available tools:

{json.dumps(available, indent=2)}

Rules:

- Use only listed tools.
- Gather evidence rather than guessing.
- Include provenance in the final result:
  file paths, commits, issues, PRs, URLs, etc.
- Return findings and open_leads as lists,
  empty when there are none.
- Do not invent tool results.
- {JSON_ONLY_RULE}
""".strip()
