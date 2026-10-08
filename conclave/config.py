"""How a caller sizes a run.

There are no numbers in this module. Every limit is one somebody chose:
the core refuses to guess how many agents a system has, how much text is
too much to put in a prompt, or how wide a fan-out may be, because a
default silently answers a question only the caller can answer.

A caller who wants a first run in one line still gets one — it just lives
in their code, where it can be read by whoever wonders why 15.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RunConfig:
    """Every limit one run needs, supplied by the caller.

    `max_tool_steps` bounds the loop: an agent that has not finished by
    then is told it ran out of steps and hands back what it has.
    `max_tool_output_chars` bounds individual tool responses and delegated
    agent reports, not accumulated shared state or the complete prompt.
    None means unlimited for each field, including fan-out concurrency.
    """

    max_tool_steps: int | None
    max_notes: int | None
    max_note_chars: int | None
    max_tool_output_chars: int | None
    fan_out_limit: int | None
