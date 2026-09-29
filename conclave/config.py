"""Tunable limits for a run, and the defaults they fall back to.

The module constants are the defaults; `RunConfig` is how a caller changes
them for one run without editing the library.
"""

from dataclasses import dataclass

MAX_DELEGATIONS = 15
MAX_DELEGATIONS_PER_AGENT = 4
MAX_DEPTH = 2
MAX_TOOL_STEPS = 8
TIMEOUT_SECONDS = 300
MAX_TOOL_OUTPUT_CHARS = 20_000
MAX_NOTES = 12
MAX_NOTE_CHARS = 300
FAN_OUT_LIMIT = 4


@dataclass(frozen=True)
class RunConfig:
    max_tool_steps: int = MAX_TOOL_STEPS
    max_notes: int = MAX_NOTES
    max_note_chars: int = MAX_NOTE_CHARS

    # How much of a delegated agent's report is handed back to the agent
    # that asked for it. Truncation of *tool* output belongs to the tool
    # registry, which owns `MAX_TOOL_OUTPUT_CHARS`.
    max_delegation_report_chars: int = (
        MAX_TOOL_OUTPUT_CHARS
    )

    timeout_seconds: float = TIMEOUT_SECONDS
    fan_out_limit: int = FAN_OUT_LIMIT


DEFAULT_CONFIG = RunConfig()
