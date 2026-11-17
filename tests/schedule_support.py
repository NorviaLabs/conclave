"""Shared builders for the scheduled-applications tests.

Like actions_support, nothing here imports the new interfaces at module level,
so before the feature exists each test fails on its own.
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import CONFIG

CASES = json.loads((Path(__file__).parent / "data" / "schedule_cases.json").read_text())


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def schedule(**change):
    block = copy.deepcopy(CASES["base"])
    for key, value in change.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    return block


def hourly(**change):
    return schedule(**{"kind": "interval", "times": None, "days": None, "every_minutes": 60, **change})


def definition(block=None, *, tools=(), autonomous=False, start_mode="schedule"):
    """The support digest: one required input field, one AI step."""
    value = {
        "version": 1, "name": "Support digest", "description": "Summarise support tickets.",
        "inputs": [{"id": "team", "label": "Team", "type": "text", "required": True}],
        "agents": [{"id": "writer", "name": "Writer", "instructions": "Write the digest", "tools": list(tools)}],
        "state": [{"key": "digest", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": "write", "agent": "writer", "task": "Write", "output": "digest"}],
        "views": [{"id": "digest_view", "collection": "digest", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
        "start_mode": start_mode,
    }
    if block is not None:
        value["schedule"] = block
    if autonomous:
        value["autonomous_execution"] = True
    return value
