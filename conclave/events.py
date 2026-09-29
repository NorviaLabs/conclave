"""What a caller watches while work happens.

Events are the public contract: a caller reacts to a stream of them instead
of reading the library's stdout, and every automatic decision — an approval,
a refusal, a budget stop — is visible as one of them.

The vocabulary is deliberately small. `RunStarted` and `RunFinished` wrap a
top-level run; `AgentStarted`/`AgentFinished` wrap each agent inside it,
including nested ones, so a caller can reconstruct the whole tree from the
stream alone.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Union,
)

from .approval import Approval
from .config import RunConfig
from .protocol import AgentResult
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
