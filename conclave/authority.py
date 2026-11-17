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

from datetime import datetime, timezone

from .actions import (
    ActionContext,
    ActionLog,
    ActionStart,
    CheckResult,
    Resolution,
    settled_observation,
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

    `provider` and `model` are the backend the agent declared, empty when it
    declared none. They are a request, not a promise: the authority serves it
    or refuses it, and never substitutes another backend silently.
    """

    run_id: str
    agent: str
    messages: list[dict[str, Any]]
    provider: str = ""
    model: str = ""


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
        preview: dict[str, Any] | None = None,
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
        log: ActionLog | None = None,
    ):
        self.provider = provider
        self.budget = budget
        self.approver = approver
        # Concurrent agents may ask at the same time; one lock keeps an
        # approver from being asked twice at once.
        self.lock = lock
        # Where external actions are recorded before they are sent. Without
        # one, a write runs exactly as it always has.
        self.log = log
        self.started: dict[str, str] = {}

    async def complete(
        self,
        call: ModelCall,
    ) -> Completion:
        provider = self.provider

        # A declared backend is served or refused. Asking the provider object
        # rather than a registry keeps one thing threaded through every path: a
        # host that can only run one backend has no `for_call`, and a call that
        # names another one fails here instead of running somewhere else.
        if call.provider or call.model:
            choose = getattr(provider, "for_call", None)
            chosen = choose(call.provider, call.model) if callable(choose) else None

            if chosen is None:
                raise ValueError(
                    "This host cannot run the model backend "
                    + (call.provider or "requested")
                    + (f" · {call.model}" if call.model else "")
                )

            provider = chosen

        # Past a run-wide limit only write-ups are answered, so the limit holds
        # for a loop that does not stop at it, wherever that loop runs.
        self.budget.admit_call()

        # Under way until it returns or raises, so the next call can be
        # weighed against the ones already spending.
        self.budget.calls_in_flight += 1

        try:
            return await provider.complete(
                call.messages
            )

        finally:
            self.budget.calls_in_flight -= 1

    async def approve(
        self,
        tool: Tool,
        agent: str,
        arguments: dict[str, Any],
        run_id: str,
        preview: dict[str, Any] | None = None,
    ) -> Approval:
        return await request_approval(
            tool,
            agent,
            arguments,
            self.approver,
            self.lock,
            preview,
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

    async def call_fits(
        self,
        dimension: str,
    ) -> bool:
        return self.budget.call_fits(
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

    # -- External actions -------------------------------------------------

    async def records_actions(
        self,
    ) -> bool:
        return self.log is not None

    async def begin_action(
        self,
        start: ActionStart,
    ) -> bool:
        """Record an action before it is sent. False: this run keeps no log."""

        if self.log is None:
            return False

        self.log.begin(start)
        self.started[start.action_id] = datetime.now(timezone.utc).isoformat()

        return True

    async def finish_action(
        self,
        action_id: str,
        state: str,
        frames: list[dict[str, Any]],
        reason: str = "",
    ) -> None:
        if self.log is not None:
            self.log.finish(action_id, state, frames=frames, reason=reason)

    async def check_action(
        self,
        action_id: str,
        tool: Tool,
        arguments: dict[str, Any],
        approval: Approval | str,
    ) -> Resolution:
        """Ask the service whether an uncertain action happened, and settle what it can.

        Found: it happened, and is not sent again. Absent, by a read that can
        be trusted, of a call that was approved: it is sent once more. Anything
        else waits for a person.
        """

        stop = Resolution("stop", reason=f"The outcome of {tool.name} is uncertain")

        if self.log is None:
            return stop

        result = await run_check(tool, action_id, arguments, self.started.get(action_id))
        self.log.checked(action_id, result)

        return decide(self.log, action_id, result, approval, stop)

    async def begin_resend(
        self,
        action_id: str,
    ) -> None:
        if self.log is not None:
            self.log.resend(action_id)

    async def resolve_action(
        self,
        action_id: str,
    ) -> Resolution:
        """What an agent resuming after this action should do with it."""

        resolution = self.log.resolution(action_id) if self.log is not None else None

        return resolution_for(resolution)


async def run_check(
    tool: Tool | None,
    action_id: str,
    arguments: dict[str, Any],
    started_at: str | None,
) -> CheckResult:
    """Run a tool's check, never raising: a check that fails could not tell."""

    if tool is None:
        return CheckResult("unavailable", "This host no longer offers the tool, so it cannot be checked")

    if tool.check is None:
        return CheckResult("unavailable", tool.check_unavailable or f"{tool.name} cannot be checked automatically")

    context = ActionContext(action_id=action_id, arguments=dict(arguments),
                            started_at=started_at or datetime.now(timezone.utc).isoformat())

    try:
        result = await tool.check.run(dict(arguments), context)
    except Exception:
        return CheckResult("inconclusive", "The check could not complete")

    if not isinstance(result, CheckResult):
        return CheckResult("inconclusive", "The check gave no usable answer")

    return result


def decide(
    log: ActionLog,
    action_id: str,
    result: CheckResult,
    approval: Approval | str,
    stop: Resolution,
) -> Resolution:
    """The settlement rules: what a check's answer decides on its own."""

    if result.result == "found":
        log.settle(action_id, "done", by="check", kind="check")

        return Resolution("done", observation=settled_observation(log.resolution(action_id) or {}))

    if result.result == "absent" and result.exact and str(approval) == str(Approval.APPROVED):
        log.settle(action_id, "resend", by="check", kind="check")

        return Resolution("resend")

    return stop


def resolution_for(
    resolution: dict[str, Any] | None,
) -> Resolution:
    if resolution is None or resolution.get("state") in (None, "sending", "uncertain"):
        return Resolution("stop", reason="Settle this action before continuing")

    if resolution["state"] == "resend":
        return Resolution("resend", tool=str(resolution.get("tool") or ""), arguments=resolution.get("arguments"))

    if resolution["state"] == "done":
        return Resolution("done", observation=settled_observation(resolution))

    # Completed or failed: already recorded, and never sent again.
    return Resolution("done", observation="This action already finished earlier in this run. Do not repeat it.")
