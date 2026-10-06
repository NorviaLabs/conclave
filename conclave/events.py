"""What a caller watches while work happens.

Events are the public contract: a caller reacts to a stream of them instead
of reading the library's stdout, and every automatic decision — an approval,
a refusal, a budget stop — is visible as one of them.

The vocabulary is deliberately small. `RunStarted` and `RunFinished` wrap a
top-level run; `AgentStarted`/`AgentFinished` wrap each agent inside it,
including nested ones, so a caller can reconstruct the whole tree from the
stream alone.
"""

from dataclasses import (
    dataclass,
    fields,
)
from typing import (
    Any,
    Union,
    get_origin,
)

from .approval import Approval
from .config import RunConfig
from .protocol import (
    AgentResult,
    PrivateState,
)
from .spec import Capability


# =============================================================================
# Run envelope
# =============================================================================


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    task: str
    config: RunConfig


@dataclass(frozen=True)
class RunFinished:
    run_id: str
    agent: str
    status: str
    result: AgentResult


# =============================================================================
# One agent
# =============================================================================


@dataclass(frozen=True)
class AgentStarted:
    run_id: str
    parent_run_id: str | None
    agent: str
    depth: int
    task: str


@dataclass(frozen=True)
class AgentStep:
    run_id: str
    agent: str
    step: int
    reply: str
    action: str | None


@dataclass(frozen=True)
class AgentFinished:
    run_id: str
    parent_run_id: str | None
    agent: str
    status: str
    result: AgentResult


# =============================================================================
# Work the agent asked for
# =============================================================================


@dataclass(frozen=True)
class ToolCalled:
    run_id: str
    agent: str
    tool: str
    arguments: dict[str, Any]

    capabilities: frozenset[
        Capability
    ]

    approval: Approval
    observation: str
    output_chars: int
    truncated: bool
    ok: bool


@dataclass(frozen=True)
class ToolDenied:
    run_id: str
    agent: str
    tool: str
    reason: str
    observation: str

    capabilities: frozenset[
        Capability
    ]


@dataclass(frozen=True)
class NoteRecorded:
    run_id: str
    agent: str
    note: str
    total: int
    result: str


@dataclass(frozen=True)
class Delegation:
    run_id: str
    agent: str
    delegated_by: str
    depth: int
    task: str
    status: str
    reason: str


# =============================================================================
# Shared state and budgets
# =============================================================================


@dataclass(frozen=True)
class StoreChanged:
    keys: tuple[str, ...]
    writer: str
    version: int


@dataclass(frozen=True)
class BudgetExceeded:
    run_id: str
    dimension: str
    limit: float
    used: float


Event = Union[
    RunStarted,
    RunFinished,
    AgentStarted,
    AgentStep,
    AgentFinished,
    ToolCalled,
    ToolDenied,
    NoteRecorded,
    Delegation,
    StoreChanged,
    BudgetExceeded,
]


# =============================================================================
# The same events, as data
# =============================================================================


EVENT_TYPES: dict[str, Any] = {
    kind.__name__: kind
    for kind in (
        RunStarted,
        RunFinished,
        AgentStarted,
        AgentStep,
        AgentFinished,
        ToolCalled,
        ToolDenied,
        NoteRecorded,
        Delegation,
        StoreChanged,
        BudgetExceeded,
    )
}


def event_payload(event: Event) -> dict[str, Any]:
    """One runtime event as data, for a loop that runs somewhere else.

    Every field the vocabulary declares crosses, except `AgentResult.private`:
    an agent's scratchpad is its own, and only the handoff comes back.
    """

    payload = {"type": type(event).__name__}

    for field in fields(event):
        payload[field.name] = _as_data(
            getattr(event, field.name)
        )

    return payload


def rebuild_event(
    payload: dict[str, Any],
    config: RunConfig,
) -> Event:
    """The event a payload describes.

    `config` is what a rebuilt `AgentResult` needs for the private state it no
    longer carries: the run's own limits, supplied by whoever started the run.
    A payload that does not describe an event exactly is refused rather than
    half-rebuilt.
    """

    if not isinstance(payload, dict):
        raise ValueError("An event payload is required")

    kind = EVENT_TYPES.get(payload.get("type"))

    if kind is None:
        raise ValueError(
            "Unknown event type: "
            f"{payload.get('type')}"
        )

    declared = {
        field.name
        for field in fields(kind)
    }
    given = set(payload) - {"type"}

    if given != declared:
        raise ValueError(
            f"{payload.get('type')} needs exactly "
            + ", ".join(sorted(declared))
        )

    return kind(
        **{
            field.name: _from_data(
                field,
                payload[field.name],
                config,
            )
            for field in fields(kind)
        }
    )


def _as_data(
    value: Any,
) -> Any:
    if isinstance(value, AgentResult):
        return {
            "content": value.content,
            "data": value.data,
            "nested": value.nested,
        }

    if isinstance(value, RunConfig):
        return dict(vars(value))

    if isinstance(value, (frozenset, set)):
        return sorted(
            str(item)
            for item in value
        )

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, Approval):
        return str(value)

    return value


def _from_data(
    field: Any,
    value: Any,
    config: RunConfig,
) -> Any:
    # Dispatch on the declared type, not the field's name: two events may use
    # the same name for different things (`NoteRecorded.result` is text).
    kind = field.type

    if get_origin(kind) is frozenset:
        return frozenset(
            Capability(item)
            for item in value
        )

    if kind is Approval:
        return Approval(value)

    if kind is RunConfig:
        return RunConfig(**value)

    if kind is AgentResult:
        return AgentResult(
            content=value["content"],
            private=PrivateState(
                config=config
            ),
            data=value["data"],
            nested=value["nested"],
        )

    if get_origin(kind) is tuple:
        return tuple(value)

    return value
