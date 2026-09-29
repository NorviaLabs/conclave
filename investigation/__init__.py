"""The investigation app: conclave's first consumer.

Everything domain-specific lives in this package — the five specialists,
the shape of investigation state, the round loop and the progress log. The
`conclave` package next to it knows none of it.
"""

from .agents import AGENTS
from .loop import (
    run_delegation,
    run_investigation,
)


__all__ = [
    "AGENTS",
    "run_delegation",
    "run_investigation",
]
