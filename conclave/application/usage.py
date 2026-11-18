"""What a run used, step by step.

A step counts its own model calls (and those of any member it asked), the
tokens its backends measured, its tool calls and how long it worked. The run
record adds the steps up. Nothing here reads a prompt, a reply or a tool's
output: only counts, backend names and times.
"""

from __future__ import annotations

from typing import Any

from conclave.events import ModelCalled, ToolCalled, ToolDenied

WARNING_DIMENSIONS = ("tool_calls", "wall_clock_seconds", "tokens", "cost_usd")
# Without a threshold of its own, a limit warns when this much of it is used.
AUTOMATIC_WARNING = 0.8


def warning_thresholds(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """When each limit warns: the application's own threshold, else 80% of the limit.

    A threshold set to null turns that warning off; a dimension with neither a
    limit nor a threshold has no warning. Each says its limit and whether the
    threshold was chosen or automatic.
    """
    chosen, limits = definition.get("budget_warnings") or {}, definition.get("run_budget") or {}
    thresholds = {}
    for dimension in WARNING_DIMENSIONS:
        limit = limits.get(dimension)
        if dimension in chosen:
            if chosen[dimension] is not None:
                thresholds[dimension] = {"threshold": chosen[dimension], "limit": limit, "automatic": False}
        elif limit is not None:
            threshold = limit * AUTOMATIC_WARNING
            if type(limit) is int and threshold == int(threshold):
                threshold = int(threshold)
            thresholds[dimension] = {"threshold": threshold, "limit": limit, "automatic": True}
    return thresholds


COUNTS = ("model_calls", "measured_calls", "member_calls", "input_tokens", "output_tokens",
          "tool_calls", "tool_failures", "tool_denied")


class StepUsage:
    """One step's totals, as its events go by."""

    def __init__(self, agent: str):
        self.agent = agent
        self.counts = dict.fromkeys(COUNTS, 0)

    def observe(self, event: Any) -> None:
        if isinstance(event, ModelCalled):
            self.called(event.input_tokens, event.output_tokens, member=event.agent != self.agent)
        elif isinstance(event, ToolCalled):
            self.counts["tool_calls"] += 1
            if not event.ok:
                self.counts["tool_failures"] += 1
        elif isinstance(event, ToolDenied):
            # Asked for and refused: a tool call the step made, that did not run.
            self.counts["tool_calls"] += 1
            self.counts["tool_denied"] += 1

    def called(self, input_tokens: int | None, output_tokens: int | None, *, member: bool = False) -> None:
        self.counts["model_calls"] += 1
        if member:
            self.counts["member_calls"] += 1
        if input_tokens is not None or output_tokens is not None:
            self.counts["measured_calls"] += 1
            self.counts["input_tokens"] += input_tokens or 0
            self.counts["output_tokens"] += output_tokens or 0

    def summary(self, seconds: float) -> dict[str, Any]:
        return {**self.counts, "seconds": round(max(0.0, seconds), 3)}


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _seconds(value: Any) -> float:
    return float(value) if type(value) in {int, float} and value >= 0 and value != float("inf") else 0.0


def run_usage(events: list[dict[str, Any]], definition: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """What a run used, from its record: per step, in all, and against its limits.

    A step that ran more than once (a revision, a retry) adds up every time it
    ran, and says how many. A run recorded before steps carried their usage
    says so, with only the run-wide totals it kept.
    """
    steps: dict[str, dict[str, Any]] = {}
    unmeasured: dict[str, set[str]] = {}
    marked = False
    warnings = []
    limits_set = definition.get("run_budget") or {}
    for event in events:
        kind = event.get("type")
        if kind in {"ApplicationStarted", "ApplicationResumed"} and event.get("usage"):
            marked = True
        elif kind == "StageFinished":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                continue
            stage = str(event.get("stage", ""))
            totals = steps.setdefault(stage, {**dict.fromkeys(COUNTS, 0), "seconds": 0.0, "runs": 0})
            for key in COUNTS:
                totals[key] += _count(usage.get(key))
            totals["seconds"] = round(totals["seconds"] + _seconds(usage.get("seconds")), 3)
            totals["runs"] += 1
        elif kind == "ModelCalled" and event.get("input_tokens") is None and event.get("output_tokens") is None:
            unmeasured.setdefault(str(event.get("stage", "")), set()).add(str(event.get("provider") or "default"))
        elif kind == "BudgetWarning":
            dimension = event.get("dimension")
            warnings.append({"dimension": dimension, "used": event.get("used"), "threshold": event.get("threshold"),
                             "limit": event.get("limit", limits_set.get(dimension)), "automatic": bool(event.get("automatic", False)),
                             "stage": event.get("stage")})
    # Recorded once a start said so, or once a step carried its usage.
    recorded = marked or bool(steps)
    for stage, totals in steps.items():
        totals["unmeasured"] = sorted(unmeasured.get(stage, ()))
    if recorded:
        total: dict[str, Any] = {key: sum(item[key] for item in steps.values()) for key in COUNTS}
        total["tokens"] = total["input_tokens"] + total["output_tokens"]
        total["seconds"] = round(sum(item["seconds"] for item in steps.values()), 3)
    else:
        # Only what the run kept as a whole: nothing per step is made up.
        total = {**dict.fromkeys(COUNTS), "tokens": metrics.get("tokens"), "tool_calls": metrics.get("tool_calls"),
                 "seconds": metrics.get("wall_clock_seconds")}
    total["cost_usd"] = metrics.get("cost_usd")
    thresholds = warning_thresholds(definition)
    limits = {}
    for dimension in WARNING_DIMENSIONS:
        limit, warn_at = limits_set.get(dimension), (thresholds.get(dimension) or {}).get("threshold")
        if limit is not None or warn_at is not None:
            limits[dimension] = {"limit": limit, "used": metrics.get(dimension, 0), "warn_at": warn_at}
    return {"recorded": recorded, "steps": steps if recorded else {}, "total": total,
            "unmeasured_backends": sorted(set().union(*unmeasured.values())) if unmeasured else [],
            "limits": limits, "warnings": warnings}
