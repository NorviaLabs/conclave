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
    """One agent: what it is told, and what it is allowed to touch."""

    name: str
    instructions: str
    tools: set[str] = field(
        default_factory=set
    )
