"""External actions: what a run is sending, and what is known about it.

An action is one call to a tool that needs approval — a write, or a tool whose
capabilities are unknown. The runtime records each one before it is sent, so a
run that stops mid-call can say exactly what was in flight; a check asks the
service whether it happened; and nothing that may already have happened is sent
again unless a check or a person says so.

This module is the shared vocabulary. It holds no storage and no policy about
where a record lives: the runtime asks an `ActionLog`, and the application layer
supplies one backed by the run database.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


# The action whose tool is running right now, so a tool can attach an
# idempotency key or a marker without its signature changing.
current_action: ContextVar[str | None] = ContextVar("conclave_current_action", default=None)


class ActionNotPerformed(Exception):
    """Raised by a tool when its effect certainly did not happen.

    A refusal before anything left the host — invalid arguments, a missing
    grant, a path outside the folder — is the usual case. Anything else that
    goes wrong leaves the outcome unknown.
    """


class ActionStopped(Exception):
    """A run stops because an action's outcome is uncertain and nobody settled it."""

    def __init__(self, action_id: str, reason: str):
        super().__init__(reason)
        self.action_id, self.reason = action_id, reason


def classify(error: BaseException) -> str:
    """`failed` when the error proves nothing happened; `uncertain` otherwise."""

    if isinstance(error, ActionNotPerformed):
        return "failed"

    if getattr(error, "sent", None) is False or getattr(error, "rejected", False) is True:
        return "failed"

    return "uncertain"


CHECK_RESULTS = ("found", "absent", "inconclusive", "unavailable")
SUMMARY_LIMIT, DETAIL_LIMIT = 300, 2000


@dataclass(frozen=True)
class CheckResult:
    """What a service said when asked whether an action happened.

    `exact` marks an `absent` answer as reliable — a strongly consistent read
    with a unique match — which is the only kind that may send an action again
    without a person. `summary` is shown to people; `detail` is what the agent
    is told when the action turns out to have happened.
    """

    result: str
    summary: str
    exact: bool = False
    detail: str = ""

    def __post_init__(self):
        if self.result not in CHECK_RESULTS:
            raise ValueError(f"A check answers one of {', '.join(CHECK_RESULTS)}")
        object.__setattr__(self, "summary", str(self.summary)[:SUMMARY_LIMIT])
        object.__setattr__(self, "detail", str(self.detail)[:DETAIL_LIMIT])


@dataclass(frozen=True)
class ActionContext:
    """What a check is told about the action it checks."""

    action_id: str
    started_at: str
    arguments: dict[str, Any]


def _describes_nothing(arguments: dict[str, Any]) -> str:
    return ""


@dataclass(frozen=True)
class Check:
    """How to ask a service whether one call already happened.

    `run` must only read. `describe` says, in a person's terms, what it will
    look for with these arguments.
    """

    run: Callable[[dict[str, Any], ActionContext], Awaitable[CheckResult]]
    describe: Callable[[dict[str, Any]], str] = _describes_nothing


@dataclass(frozen=True)
class ActionStart:
    """One action, as it is recorded before it is sent."""

    action_id: str
    scope: str
    agent: str
    tool: str
    arguments: dict[str, Any]
    approval: str
    frames: list[dict[str, Any]] = field(default_factory=list)
    checkable: bool = False


@dataclass(frozen=True)
class Resolution:
    """What to do with an action whose outcome was uncertain.

    `done`: tell the agent `observation` instead of sending it; `resend`: send
    it once more under the same action; `stop`: stop the run so a person can
    decide, for `reason`.
    """

    kind: str
    observation: str = ""
    reason: str = ""
    # For a resend: the recorded call, which is what is sent — exactly what
    # the person (or the check) settled, whatever saved progress says.
    tool: str = ""
    arguments: dict[str, Any] | None = None


class ActionLog(Protocol):
    """Where a run records its actions. The application layer provides it."""

    def begin(self, start: ActionStart) -> None: ...

    def finish(self, action_id: str, state: str, *, frames: list[dict[str, Any]], reason: str = "") -> None: ...

    def checked(self, action_id: str, result: CheckResult) -> None: ...

    def settle(self, action_id: str, decision: str, *, by: str, note: str | None = None, kind: str = "person") -> None: ...

    def resend(self, action_id: str) -> int: ...

    def resolution(self, action_id: str) -> dict[str, Any] | None: ...


def settled_observation(resolution: dict[str, Any]) -> str:
    """What an agent is told about an action that already happened.

    What a check found is reported as what the service showed; a person's
    confirmation as theirs, with their note as their words — never as a reply
    from the service.
    """

    # Who settled it is a kind the code recorded, not a name: a person whose
    # name is "check" is reported as a person.
    if resolution.get("settled_kind", "check" if resolution.get("settled_by") == "check" else "person") == "check":
        found = resolution.get("check_detail") or resolution.get("check_summary") or "it was found"
        return (f"Conclave checked and found that this action already happened: {found}. "
                "Do not repeat it. Continue with what you have.")

    text = (f"{resolution.get('settled_by') or 'A person'} confirmed this action already happened. "
            "Do not repeat it. Its result was not recorded.")
    if resolution.get("note"):
        text += f" Their note (their words, not the service's reply): {resolution['note']}"
    return text
