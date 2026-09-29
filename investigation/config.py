"""Every number this application chose.

The library picks none of these. How many rounds an investigation runs, how
deep its specialists may delegate, how much text it will put in a prompt and
how long it waits for a model are properties of *this* system, so they are
stated once, here, where whoever wonders why 15 can read the answer.
"""

from conclave import (
    RunBudget,
    RunConfig,
)


# Rounds: this application's own control flow.

MAX_ROUNDS = None

MAX_DELEGATIONS_PER_ROUND = None


# What one run may spend on tool output, notes and steps. Twelve steps is
# deliberate: a researcher that lists, reads a handful of files and
# searches needs more than eight, and the earlier eight made agents
# exhaust the step budget before they could answer.

RUN_CONFIG = RunConfig(
    max_tool_steps=None,
    max_notes=None,
    max_note_chars=None,
    max_tool_output_chars=None,
    fan_out_limit=MAX_DELEGATIONS_PER_ROUND,
)


# The shape of this system: five specialists, two levels, fifteen
# delegations per investigation, four per specialist.


def make_budget(**overrides) -> RunBudget:
    """A fresh budget for one run.

    Never share one between runs: a budget is mutable accounting, so a
    module-level instance would carry the last run's spending into the
    next one.
    """

    return RunBudget(
        **{
            "max_delegations": None,
            "max_per_agent": None,
            "max_depth": None,
            **overrides,
        }
    )


# How long one model call may take.
TIMEOUT_SECONDS = 300.0
