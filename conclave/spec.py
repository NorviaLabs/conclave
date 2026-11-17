"""The types a caller describes their agents and tools with."""

from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum
from typing import (
    Any,
    Awaitable,
    Callable,
)


class Capability(StrEnum):
    """What a tool does to the world, declared by whoever wrote it."""

    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    SHELL = "shell"


ToolFunc = Callable[
    ...,
    Awaitable[str],
]


class ToolRefused(Exception):
    """Raised by a tool's precheck to refuse a call before anyone is asked to
    approve it. The message is shown to the agent and recorded, so it must be
    safe to show: no secrets, no provider response bodies."""


@dataclass
class Tool:
    """A callable the runtime owns, described for the model.

    `capabilities` is a promise made by the tool's author, not a guess:
    a tool that declares nothing is treated as destructive, and any tool
    declaring WRITE always is.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    func: ToolFunc

    capabilities: frozenset[
        Capability
    ] = field(
        default_factory=frozenset
    )

    # How much text one call may return, decided per call from its arguments
    # and the registry's own cap (None when unlimited). None here, or None from
    # the callable, leaves the registry's cap in force; a tool uses it when a
    # call legitimately returns more, such as a read that follows pages.
    output_limit: Callable[
        [dict[str, Any], int | None],
        int | None,
    ] | None = None

    # A side-effect-free check of one destructive call, run before anyone is
    # asked to approve it. It raises ToolRefused to refuse the call, or returns
    # what the approver should see of the action (None for nothing extra).
    precheck: Callable[
        [dict[str, Any]],
        Awaitable[dict[str, Any] | None],
    ] | None = None
    # How to ask the service whether one call already happened (a
    # `conclave.actions.Check`), or, when there is none, why not. Only a
    # destructive tool is ever checked, and only after its outcome is unknown.
    check: Any = None
    check_unavailable: str = ""

    @property
    def destructive(self) -> bool:
        return (
            not self.capabilities
            or Capability.WRITE
            in self.capabilities
        )


@dataclass
class AgentSpec:
    """One agent: what it is told, what it is allowed to touch, and what runs it.

    `provider` and `model` are a *declared* choice rather than a connection:
    the host resolves them, and a host that cannot serve the choice refuses the
    call instead of quietly running the agent on another backend. Empty means
    "whatever this host runs by default".
    """

    name: str
    instructions: str
    tools: set[str] = field(
        default_factory=set
    )
    provider: str = ""
    model: str = ""
    # Read once per run with the agent's first final action. Text it returns
    # goes back to the agent as an observation in place of ending the run, so
    # a caller can ask for a second look at an answer it doubts.
    review_final: Callable[[dict[str, Any]], str | None] | None = None
