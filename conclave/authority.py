"""What a run may not decide for itself.

A loop that runs behind a boundary — in a sandbox, in another process, on
another machine — still needs the model call, the money and the permission. It
asks an authority for them and holds nothing it could answer with: no provider
credentials, no budget counters, no approver, and no durable store.

`InProcessAuthority` is today's behavior with all of that in one process. A
deployment that runs the loop elsewhere supplies an authority that forwards each
question to its control plane, where the real budget and the real approval live.

The questions are deliberately the ones the loop already asks. Mirroring them
keeps the loop identical wherever it runs; inventing a coarser protocol would
have meant restructuring the loop to save round trips it does not make.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
)

from .approval import (
    Approval,
    ApprovalRequest,
    Approver,
    request_approval,
)
from .budget import RunBudget
from .providers import (
    Completion,
    ModelProvider,
)
from .spec import Tool


@dataclass(frozen=True)
class ModelCall:
    """One model call, as it crosses to an authority.

    The messages are the prompt, so a deployment decides where a prompt may be
    assembled and sent; the run and agent are what an audit trail needs to
    attribute the call and its cost.
    """

    run_id: str
    agent: str
    messages: list[dict[str, Any]]


class RunAuthority(Protocol):
    """The model call, the budget, the permission and the approval.

    Everything that reads or moves a counter is asynchronous, because the
    counters live where the authority does and a loop elsewhere has to ask.
    Depth is the exception: it is *declared* policy, not accounting, so it
    travels with the run and a prompt can be built without a round trip. The
    counter that enforces it is still `reserve_delegation`.
    """

    async def complete(
        self,
        call: ModelCall,
    ) -> Completion: ...

    async def approve(
        self,
        tool: Tool,
        agent: str,
        arguments: dict[str, Any],
        run_id: str,
    ) -> Approval: ...

    async def reserve(
        self,
        dimension: str,
        amount: float = 1,
    ) -> str | None: ...

    async def settle(
        self,
        dimension: str,
        amount: float | None,
    ) -> str | None: ...

    async def reserve_delegation(
        self,
        agent: str,
        depth: int,
        ancestry: tuple[str, ...],
    ) -> str | None: ...

    def may_delegate(
        self,
        depth: int,
    ) -> bool: ...

    async def exceeded(
        self,
        dimension: str,
    ) -> bool: ...

    async def numbers(
        self,
        dimension: str,
    ) -> tuple[float | None, float]: ...

    @property
    def max_depth(
        self,
    ) -> int | None: ...


class InProcessAuthority:
    """The provider, the budget and the approver, all in this process."""

    def __init__(
        self,
        provider: ModelProvider,
        budget: RunBudget,
        approver: Approver | None = None,
        *,
        lock: Any = None,
    ):
        self.provider = provider
        self.budget = budget
        self.approver = approver
        # Concurrent agents may ask at the same time; one lock keeps an
        # approver from being asked twice at once.
        self.lock = lock

    async def complete(
        self,
        call: ModelCall,
    ) -> Completion:
        return await self.provider.complete(
            call.messages
        )

    async def approve(
        self,
        tool: Tool,
        agent: str,
        arguments: dict[str, Any],
        run_id: str,
    ) -> Approval:
        return await request_approval(
            tool,
            agent,
            arguments,
            self.approver,
            self.lock,
        )

    async def reserve(
        self,
        dimension: str,
        amount: float = 1,
    ) -> str | None:
        return self.budget.reserve_dimension(
            dimension,
            amount,
        )

    async def settle(
        self,
        dimension: str,
        amount: float | None,
    ) -> str | None:
        return self.budget.settle(
            dimension,
            amount,
        )

    async def reserve_delegation(
        self,
        agent: str,
        depth: int,
        ancestry: tuple[str, ...],
    ) -> str | None:
        return self.budget.reserve(
            agent,
            depth,
            ancestry,
        )

    def may_delegate(
        self,
        depth: int,
    ) -> bool:
        return self.budget.may_delegate(
            depth
        )

    async def exceeded(
        self,
        dimension: str,
    ) -> bool:
        return self.budget.exceeded(
            dimension
        )

    async def numbers(
        self,
        dimension: str,
    ) -> tuple[float | None, float]:
        return (
            self.budget.limit(
                dimension
            ),
            self.budget.spent_on(
                dimension
            ),
        )

    @property
    def max_depth(
        self,
    ) -> int | None:
        return self.budget.max_depth
