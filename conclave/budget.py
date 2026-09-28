"""Run-scoped delegation budget and the guards that bound recursion.

Every delegation, top-level or nested, is reserved here before it runs.
Refusals are returned as text so a specialist can finalize on partial
evidence instead of crashing, and one shared counter is the only thing
that decides whether nested work is still affordable.
"""

from dataclasses import (
    dataclass,
    field,
)

from .config import (
    MAX_DELEGATIONS,
    MAX_DELEGATIONS_PER_AGENT,
    MAX_DEPTH,
)


@dataclass
class RunBudget:
    max_delegations: int = (
        MAX_DELEGATIONS
    )

    max_per_agent: int = (
        MAX_DELEGATIONS_PER_AGENT
    )

    max_depth: int = MAX_DEPTH

    used: int = 0

    per_agent: dict[
        str,
        int,
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
                "Refused: this investigation "
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
                "investigation."
            )

        self.used += 1

        self.per_agent[
            agent
        ] = used_by_agent + 1

        return None
