"""The tool bus: registration, lookup, permission-filtered description and
execution with output truncation.

The registry knows nothing about agents, models or budgets. It stores tools,
tells a caller what a given set of tools looks like, and runs one.
"""

from typing import Any

from .config import (
    MAX_TOOL_OUTPUT_CHARS,
)
from .spec import (
    Capability,
    Tool,
)


# =============================================================================
# Tool registry
# =============================================================================


TRUNCATION_MARKER = "\n...[truncated]"


class ToolRegistry:
    def __init__(
        self,
        max_output_chars: int = (
            MAX_TOOL_OUTPUT_CHARS
        ),
    ):
        self.tools: dict[str, Tool] = {}

        self.max_output_chars = (
            max_output_chars
        )

    def register(
        self,
        tool: Tool,
    ) -> None:
        self.tools[tool.name] = tool

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
    ) -> str:
        if name not in self.tools:
            raise RuntimeError(
                f"Unknown tool: {name}"
            )

        result = await self.tools[
            name
        ].func(**arguments)

        if (
            len(result)
            > self.max_output_chars
        ):
            result = (
                result[
                    :self.max_output_chars
                ]
                + "\n...[truncated]"
            )

        return result
