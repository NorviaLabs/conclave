"""Run-scoped work budgets and the guards that bound recursion.

Every delegation, top-level or nested, is reserved here before it runs.
Refusals are returned as text so a caller can finalize on partial results
instead of crashing, and one shared accounting object is the only thing
that decides whether more work is still affordable.

Two kinds of accounting live side by side:

- work that can be counted before it starts (delegations, tool calls) is
  `reserve`d, and a refusal means the work never happens;
- work that can only be measured afterwards (tokens, cost, wall clock) is
  `settle`d with the actual amount, which reports the overspend instead of
  pretending it could be avoided.

The structural caps have no defaults: how many agents a system has, how
deep it nests and how often one agent may repeat are properties of the
system, not of the framework. The measurement dimensions default to None,
which means unmetered rather than a chosen number.
"""

from dataclasses import (
    dataclass,
    field,
)


DIMENSION_LABELS = {
    "tool_calls": "tool-call",
    "wall_clock_seconds": "wall-clock",
    "tokens": "token",
    "cost_usd": "cost",
}


# The limits that bound a whole run, measured as it goes.
RUN_WIDE_LIMITS = (
    "tokens",
    "wall_clock_seconds",
    "cost_usd",
)


class LimitReached(RuntimeError):
    """A model call refused because a run-wide limit has been reached.

    A loop that stops at the limit never sees this: it writes up instead, and
    its write-ups fit the allowance. It is what a loop that ignores the limit
    gets, wherever it runs.
    """

    def __init__(self, dimension: str):
        super().__init__(f"The run reached its {dimension} limit")
        self.dimension = dimension


@dataclass
class RunBudget:
    max_delegations: int | None
    max_per_agent: int | None
    max_depth: int | None

    tool_calls: int | None = None
    wall_clock_seconds: float | None = None
    tokens: int | None = None
    cost_usd: float | None = None

    used: int = 0

    per_agent: dict[
        str,
        int,
    ] = field(
        default_factory=dict
    )

    spent: dict[
        str,
        float,
    ] = field(
        default_factory=dict
    )

    # The most one call has spent, per measured dimension, and how many model
    # calls are under way. Kept for this process only: they say whether the
    # next call is likely to pass a limit, not what was spent.
    largest_call: dict[
        str,
        float,
    ] = field(
        default_factory=dict
    )

    calls_in_flight: int = 0

    # How many model calls the whole run may still make once a run-wide limit
    # has been reached: its write-ups. None leaves calls unrefused, as a core
    # caller that brings its own loop has always had it; the application
    # runner sets it, so a loop that ignores the limit is stopped anyway.
    post_limit_calls: int | None = None

    post_limit_used: int = 0

    @property
    def remaining(
        self,
    ) -> int:
        return (max(0, self.max_delegations - self.used)
                if self.max_delegations is not None else 0)

    def may_delegate(
        self,
        depth: int,
    ) -> bool:
        return self.max_depth is None or depth < self.max_depth

    def reserve(
        self,
        agent: str,
        depth: int,
        ancestry: tuple[str, ...],
    ) -> str | None:
        """Charge one delegation, or explain why it is refused."""

        if self.max_depth is not None and depth > self.max_depth:
            return (
                "Refused: "
                f"{agent} would run at depth "
                f"{depth}, and the maximum "
                f"depth is {self.max_depth}."
            )

        if agent in ancestry:
            return (
                "Refused: cycle detected, "
                f"{agent} is already an "
                "ancestor: "
                + " -> ".join(
                    ancestry
                )
                + "."
            )

        if self.max_delegations is not None and self.used >= self.max_delegations:
            return (
                "Refused: this run "
                f"already used {self.used}/"
                f"{self.max_delegations} "
                "delegations."
            )

        used_by_agent = (
            self.per_agent.get(
                agent,
                0,
            )
        )

        if self.max_per_agent is not None and used_by_agent >= self.max_per_agent:
            return (
                f"Refused: {agent} already "
                f"ran {used_by_agent}/"
                f"{self.max_per_agent} "
                "delegations in this "
                "run."
            )

        self.used += 1

        self.per_agent[
            agent
        ] = used_by_agent + 1

        return None

    # =====================================================================
    # Dimensions other than delegations
    # =====================================================================

    def limit(
        self,
        dimension: str,
    ) -> float | None:
        self.check_dimension(dimension)

        return getattr(
            self,
            dimension,
        )

    def check_dimension(
        self,
        dimension: str,
    ) -> None:
        if dimension not in DIMENSION_LABELS:
            raise ValueError(
                "Unknown budget dimension: "
                f"{dimension}"
            )

    def spent_on(
        self,
        dimension: str,
    ) -> float:
        return self.spent.get(
            dimension,
            0.0,
        )

    def remaining_on(
        self,
        dimension: str,
    ) -> float | None:
        limit = self.limit(
            dimension
        )

        if limit is None:
            return None

        return max(
            0.0,
            limit
            - self.spent_on(
                dimension
            ),
        )

    def reserve_dimension(
        self,
        dimension: str,
        amount: float = 1,
    ) -> str | None:
        """Charge countable work, or refuse before it runs."""

        limit = self.limit(
            dimension
        )

        if (
            limit is not None
            and self.spent_on(dimension)
            + amount
            > limit
        ):
            return self.refusal(
                dimension
            )

        self.spent[dimension] = (
            self.spent_on(dimension)
            + amount
        )

        return None

    def settle(
        self,
        dimension: str,
        amount: float | None,
    ) -> str | None:
        """Charge work that could only be measured after it ran."""

        if not amount:
            return None

        self.spent[dimension] = (
            self.spent_on(dimension)
            + amount
        )

        self.largest_call[dimension] = max(
            self.largest_call.get(dimension, 0),
            amount,
        )

        if self.exceeded(dimension):
            return self.refusal(
                dimension
            )

        return None

    def admit_call(
        self,
    ) -> None:
        """Let a model call through, or refuse it past the limit and its allowance."""

        reached = next(
            (
                dimension
                for dimension in RUN_WIDE_LIMITS
                if self.exceeded(dimension)
            ),
            None,
        )

        if reached is None or self.post_limit_calls is None:
            return

        if self.post_limit_used >= self.post_limit_calls:
            raise LimitReached(reached)

        self.post_limit_used += 1

    def call_fits(
        self,
        dimension: str,
    ) -> bool:
        """Whether one more call is likely to stay within the limit.

        Each call under way, and this one, is taken to spend as much as the
        largest call so far: with several steps of a run working at once, a
        limit that only counted what was spent would let every one of them
        start a call just below it.
        """

        limit = self.limit(
            dimension
        )

        largest = self.largest_call.get(
            dimension
        )

        return (
            limit is None
            or not largest
            or self.spent_on(dimension)
            + (self.calls_in_flight + 1) * largest
            <= limit
        )

    def exceeded(
        self,
        dimension: str,
    ) -> bool:
        limit = self.limit(
            dimension
        )

        return (
            limit is not None
            and self.spent_on(dimension)
            >= limit
        )

    def refusal(
        self,
        dimension: str,
    ) -> str:
        self.check_dimension(dimension)

        limit = self.limit(
            dimension
        )

        label = DIMENSION_LABELS[
            dimension
        ]

        return (
            f"Refused: the {label} budget "
            f"is spent "
            f"({self.spent_on(dimension):g}/"
            f"{limit:g})."
        )
