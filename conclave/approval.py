"""The capability gate: who is allowed to let a tool change the world.

Destructive calls need an approver. There is no approver by default, so the
default answer for a destructive call is *no*, and the refusal is handed back
to the model as text — the same shape as a budget refusal — so an agent
finalises on the evidence it has instead of crashing.

Non-destructive calls are approved without asking. They still emit their
capabilities on the event stream, so an automatic approval is never silent.
"""

import asyncio
import inspect

from dataclasses import dataclass
from enum import StrEnum
from typing import (
    Any,
    Awaitable,
    Callable,
)

from .spec import (
    Capability,
    Tool,
)


class Approval(StrEnum):
    NOT_REQUIRED = "not-required"
    APPROVED = "approved"
    REFUSED = "refused"


@dataclass(frozen=True)
class ApprovalRequest:
    """What an approver is shown before a destructive call runs."""

    agent: str
    tool: str
    arguments: dict[str, Any]

    capabilities: frozenset[
        Capability
    ]


Approver = Callable[
    [ApprovalRequest],
    Awaitable[bool] | bool,
]


async def deny_all(
    request: ApprovalRequest,
) -> bool:
    """The explicit version of having no approver at all."""

    return False


async def approve_all(
    request: ApprovalRequest,
) -> bool:
    """Only for sandboxes: approves every destructive call."""

    return True


async def request_approval(
    tool: Tool,
    agent: str,
    arguments: dict[str, Any],
    approver: Approver | None,
    lock: asyncio.Lock | None = None,
) -> Approval:
    """Decide whether one call may run.

    Concurrent agents can ask at the same time; `lock` serialises the
    questions so an approver is never asked twice at once.
    """

    if not tool.destructive:
        return Approval.NOT_REQUIRED

    if approver is None:
        return Approval.REFUSED

    request = ApprovalRequest(
        agent=agent,
        tool=tool.name,
        arguments=arguments,
        capabilities=(
            tool.capabilities
        ),
    )

    if lock is None:
        approved = await _ask(
            approver,
            request,
        )

    else:
        async with lock:
            approved = await _ask(
                approver,
                request,
            )

    return (
        Approval.APPROVED
        if approved
        else Approval.REFUSED
    )


async def _ask(
    approver: Approver,
    request: ApprovalRequest,
) -> bool:
    answer = approver(request)

    if inspect.isawaitable(answer):
        answer = await answer

    return bool(answer)


def refusal_text(tool: str) -> str:
    """The text handed back to a model whose destructive call was denied."""

    return (
        f"Refused: {tool} changes the world and "
        "no approver allowed it. Do not repeat "
        "this call. Continue with the evidence "
        "you already have."
    )
