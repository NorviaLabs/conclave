"""Run-scoped work budgets and the guards that bound recursion.

Every delegation, top-level or nested, is reserved here before it runs.
Refusals are returned as text so a caller can finalize on partial evidence
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


@dataclass
class RunBudget:
    max_delegations: int
    max_per_agent: int
    max_depth: int

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

    @property
    def remaining(
        self,
    ) -> int:
        return max(
            0,
            self.max_delegations
            - self.used,
        )

    def may_delegate(
        self,
        depth: int,
    ) -> bool:
        return depth < self.max_depth

    def reserve(
        self,
        agent: str,
        depth: int,
        ancestry: tuple[str, ...],
    ) -> str | None:
        """Charge one delegation, or explain why it is refused."""

        if depth > self.max_depth:
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

        if (
            self.used
            >= self.max_delegations
        ):
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

        if (
            used_by_agent
            >= self.max_per_agent
        ):
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

        if self.exceeded(dimension):
            return self.refusal(
                dimension
            )

        return None

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
