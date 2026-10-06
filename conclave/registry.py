"""The tool bus: registration, lookup, permission-filtered description and
execution with output truncation.

The registry knows nothing about agents, models or budgets. It stores tools,
tells a caller what a given set of tools looks like, and runs one — here, or
through an executor when a deployment runs tool calls somewhere else.

The output cap is required: how much text a tool may put into a prompt is a
decision about the system, not something a library should pick.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
)

from .spec import (
    Capability,
    Tool,
)


# =============================================================================
# Tool registry
# =============================================================================


TRUNCATION_MARKER = "\n...[truncated]"


@dataclass(frozen=True)
class ToolInvocation:
    """One permitted call, as it crosses to an executor.

    It carries the call and the run it belongs to, and nothing else: no
    approver, no budget, no store. Those decided whether the call may run, and
    that decision must not travel to wherever the call now runs.
    """

    name: str
    arguments: dict[str, Any]
    run_id: str = ""
    agent: str = ""


class ToolExecutor(Protocol):
    """Runs a call the runtime already permitted.

    A deployment that runs tool calls in a sandbox supplies one of these; the
    default runs the call in this process.
    """

    async def execute(
        self,
        invocation: ToolInvocation,
    ) -> str: ...


class ToolRegistry:
    def __init__(
        self,
        max_output_chars: int | None,
        *,
        executor: ToolExecutor | None = None,
    ):
        self.tools: dict[str, Tool] = {}

        self.max_output_chars = (
            max_output_chars
        )

        # None means "run here". An executor runs the call elsewhere, and the
        # output cap below still applies to whatever it returns.
        self.executor = executor

    def register(
        self,
        tool: Tool,
    ) -> None:
        self.tools[tool.name] = tool

    def copy(self) -> "ToolRegistry":
        """The same tools and output cap, as a registry a caller may change.

        A run's registry carries that run's executor, so it must not be the
        host's shared one.
        """

        copied = ToolRegistry(
            self.max_output_chars,
            executor=self.executor,
        )

        copied.tools = dict(self.tools)

        return copied

    def tool(
        self,
        name: Any,
    ) -> Tool | None:
        if not isinstance(name, str):
            return None

        return self.tools.get(name)

    def capabilities(
        self,
        name: str,
    ) -> frozenset[Capability]:
        tool = self.tools.get(name)

        return (
            tool.capabilities
            if tool is not None
            else frozenset()
        )

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
        *,
        run_id: str = "",
        agent: str = "",
    ) -> str:
        if name not in self.tools:
            raise RuntimeError(
                f"Unknown tool: {name}"
            )

        if self.executor is None:
            result = await self.tools[
                name
            ].func(**arguments)

        else:
            result = await self.executor.execute(
                ToolInvocation(
                    name=name,
                    arguments=arguments,
                    run_id=run_id,
                    agent=agent,
                )
            )

        if (
            self.max_output_chars is not None
            and len(result) > self.max_output_chars
        ):
            result = (
                result[
                    :self.max_output_chars
                ]
                + "\n...[truncated]"
            )

        return result
