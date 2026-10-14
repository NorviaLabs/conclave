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
