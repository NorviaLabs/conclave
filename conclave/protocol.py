"""The JSON action protocol agents speak, and the state that bounds it.

Every agent reply is exactly one JSON object describing the next action.
The Python runtime owns all tools and executes the action, which is why
this module also owns the retry-with-correction path and the private
scratchpad each agent run keeps.
"""

import json

from dataclasses import (
    dataclass,
    field,
)
from typing import Any

from .config import RunConfig
from .providers import (
    ModelProvider,
    Usage,
)


# =============================================================================
# The JSON-only rule
# =============================================================================


# Native tool-call syntax is what this forbids, not tools: an agent told it
# "cannot call tools" concluded that its listed tools would not run, and gave
# up on work it had been given the tools to do.
JSON_ONLY_RULE = (
    "Reply with exactly one JSON object and "
    "nothing else. Never emit tool-call "
    "markup, XML tags or function-call "
    "syntax such as DSML, antml, invoke or "
    "parameter elements: native tool calls "
    "do not run here. The Python runtime runs "
    "a tool only when you reply with the JSON "
    "tool action your instructions describe, "
    "and returns its result to you."
)


# Said once, when a bounded agent has one action left. A budget that ends without
# an answer produces nothing, so the agent is told to spend its last action on the
# answer rather than on work it cannot finish.
# Said when the run's clock is nearly out. Time is not an action budget: the call
# that carries this may not be answered before the limit, so it asks rather than
# promises.
TIME_LEFT_RULE = (
    "The run's time limit is nearly reached. Return the final answer now, from "
    "what you have already gathered: there may not be time for another action."
)


LAST_ACTION_RULE = (
    "One action remains, and it must be the final answer: the stage ends after "
    "your next reply. Build it from what you have already gathered, and do not "
    "begin work you cannot finish."
)


# Said once, when the action budget is spent without an answer. The call that
# carries it has no tools left to offer, so the work already gathered is what the
# answer is made of.
WRITE_UP_RULE = (
    "You have used all of your actions and no more are available: no tools, no "
    "delegations, no further notes. Return the final answer now, from what you "
    "have already gathered. An answer that reports less than everything you hoped "
    "to find is worth more than no answer at all."
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

        # Several actions in one reply: the first is the one to take, and the
        # rest are answered in turn, rather than refusing the whole reply.
        if start >= 0:
            try:
                first, _ = json.JSONDecoder().raw_decode(text, start)
            except json.JSONDecodeError:
                first = None
            if isinstance(first, dict):
                return first

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
    on_usage: Any = None,
) -> dict[str, Any]:
    """Ask a provider for one JSON object, retrying one bad reply.

    `on_usage` is called with the `Usage` of every completion, including
    the retries, so a caller can charge them to a budget.
    """

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
        completion = await provider.complete(
            messages
        )

        report_usage(
            on_usage,
            completion.usage,
        )

        text = completion.text

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


def report_usage(
    on_usage: Any,
    usage: Usage | None,
) -> None:
    if on_usage is not None and usage is not None:
        on_usage(usage)


@dataclass
class PrivateState:
    """Working state owned by one agent run.

    Notes and the checked ledger stay with the
    agent that made them. Only the final handoff
    crosses back to the caller.

    The scratchpad bounds come from the run's config: how much an agent
    may jot down is a limit somebody chose.
    """

    config: RunConfig

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

        if self.config.max_notes is not None and len(self.notes) >= self.config.max_notes:
            return (
                "Note refused: the private "
                "scratchpad is full "
                f"({self.config.max_notes} "
                "notes). "
                "Continue without it."
            )

        self.notes.append(text if self.config.max_note_chars is None else text[:self.config.max_note_chars])

        return (
            f"Note {len(self.notes)}/"
            f"{self.config.max_notes} "
            "recorded."
        )

    def check(
        self,
        summary: str,
    ) -> None:
        self.checked.append(
            summary if self.config.max_note_chars is None else summary[:self.config.max_note_chars]
        )

        if self.config.max_notes is not None:
            del self.checked[:-self.config.max_notes]

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
    """What an agent hands back.

    `content` is prose. `data` is everything else the agent put in its
    final action, passed through untouched: the shape of a handoff is the
    caller's business, so the runtime neither names those fields nor
    inspects them.
    """

    content: str
    private: PrivateState

    data: dict[str, Any] = field(
        default_factory=dict
    )

    # Which budget stopped an agent that never answered, named as the setting
    # that carries it. Empty when the agent finished on its own terms.
    exhausted_by: str = ""


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
