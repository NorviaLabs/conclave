"""Readable text for a run, for whoever is at a terminal.

Formatting only: it takes one published run record and returns a string. Nothing
here prints, and nothing here knows a domain — a run is steps, members, state,
outputs, reviews and events whatever the application is about.

The record is the durable one, and the status fold below is the same one the run
canvas uses, so the terminal and Studio cannot end up describing one run two
different ways. What differs is the medium, not the account.
"""

import json

from .definition import plan_connections


# A run that is over but did not complete: its unfinished steps were not reached.
STOPPED = ("failed", "stopped", "cancelled", "interrupted")

MODE_LABELS = {"completed": "completed", "running": "running", "waiting_for_human": "waiting for you",
               "paused": "paused", "failed": "failed", "stopped": "stopped", "cancelled": "cancelled",
               "interrupted": "interrupted"}

# A terminal is not a pager: long values are shown up to these bounds and the
# rest is left to `--format json`, which prints the record whole.
MAX_VALUE_LINES = 24

MAX_VALUE_CHARS = 2000

# Budget dimensions, in the words a person reads rather than the config's.
BUDGET_NAMES = {"max_delegations": "delegations", "max_per_agent": "per member", "max_depth": "depth",
                "tool_calls": "tool calls", "wall_clock_seconds": "wall clock (s)",
                "tokens": "tokens", "cost_usd": "cost (usd)"}


def run_mode(record: dict) -> str:
    """The run's status, defaulted rather than trusted to be present."""

    return record.get("status") or "unknown"


def pending_review(record: dict):
    """The review a person still has to answer, if the run is waiting on one."""

    for item in record.get("reviews") or []:
        if item.get("status") == "pending":
            return item
    return None


def fold(record: dict) -> dict:
    """What each step is doing, folded out of the event stream.

    The statuses and their precedence match `studio/web/run-canvas.js`: an active
    step first, then a completed one, then a run that stopped.
    """

    definition = (record.get("request") or {}).get("definition") or {}
    workflow = definition.get("workflow") or []
    position = {step["id"]: index for index, step in enumerate(workflow)}
    completed, working, finished_stage, state, views = [], set(), None, {}, []
    for item in record.get("events") or []:
        event = item.get("event", item)
        index = position.get(event.get("stage"))
        if event.get("state"):
            state = event["state"]
        if event.get("views"):
            views = event["views"]
        if event["type"] == "StageStarted" and index is not None:
            working.add(index)
        elif event["type"] == "ApplicationStateChanged" and index is not None:
            working.discard(index)
            if workflow[index]["id"] not in completed:
                completed.append(workflow[index]["id"])
        elif event["type"] == "ApplicationFinished":
            finished_stage = event.get("stage")

    mode, review = run_mode(record), pending_review(record)
    if review and review.get("stage") in position:
        working.add(position[review["stage"]])
    if mode not in ("running", "waiting_for_human"):
        working.clear()
    return {"definition": definition, "workflow": workflow, "completed": completed, "working": working,
            "review": review, "state": state, "views": views, "finished_stage": finished_stage, "mode": mode}


def step_status(step: dict, index: int, folded: dict) -> str:
    """One step's state, in the words a person would use."""

    if index in folded["working"]:
        return "needs your input" if (folded["review"] or {}).get("stage") == step["id"] else "working"
    if step["id"] in folded["completed"]:
        return "done"
    if folded["mode"] == "failed" and folded["finished_stage"] == step["id"]:
        return "failed"
    if folded["mode"] in STOPPED:
        return "not reached"
    incoming = [edge["from"] for edge in plan_connections(folded["definition"]) if edge["to"] == step["id"]]
    return "ready" if all(source in folded["completed"] for source in incoming) else "waiting"


def value_lines(value, prefix: str = "    ") -> list[str]:
    """A state or output value as bounded lines a terminal can show."""

    if value is None or value == "" or value == [] or value == {}:
        return [f"{prefix}(empty)"]
    if isinstance(value, str):
        lines = value.rstrip().splitlines()
    elif isinstance(value, list) and all(isinstance(entry, (str, int, float, bool)) for entry in value):
        lines = [f"- {entry}" for entry in value]
    else:
        lines = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False).splitlines()
    shown, text = lines[:MAX_VALUE_LINES], "\n".join(lines[:MAX_VALUE_LINES])
    rendered = [f"{prefix}{line}" if line else "" for line in text[:MAX_VALUE_CHARS].splitlines()]
    if len(lines) > MAX_VALUE_LINES or len(text) > MAX_VALUE_CHARS:
        rendered.append(f"{prefix}… (--format json shows the whole record)")
    return rendered or [f"{prefix}(empty)"]


def review_lines(review: dict, run_id: str) -> list[str]:
    """What a waiting step wants, and how a person answers it."""

    request = review.get("request") or {}
    output = request.get("output") or {}
    merge = ", replacing what is there" if output.get("merge") == "replace" and output.get("statuses") else ""
    lines = [f"  Step      {request.get('stage', '?')} · {request.get('agent', '?')}",
             f"  Task      {request.get('task') or request.get('application_task') or ''}",
             f"  Write     \"{output.get('key', '?')}\" ({output.get('type', 'text')}){merge}",
             f"  Answer    conclave review {run_id} {review.get('id', '?')} --output <file>"]
    if request.get("state"):
        lines.append("  Currently")
        lines.extend(value_lines(request["state"], "      "))
    return lines


def _number(value) -> str:
    """A number a person can read: no scientific notation, no false precision."""

    if isinstance(value, int) or value == int(value):
        return f"{value:g}"
    return f"{value:.2f}" if abs(value) >= 0.01 else "<0.01"


def budget_lines(record: dict, folded: dict) -> list[str]:
    """Spend against the limits this application actually set."""

    spent, limits = record.get("metrics") or {}, folded["definition"].get("run_budget") or {}
    parts = []
    for key, name in BUDGET_NAMES.items():
        used, limit = spent.get(key), limits.get(key)
        if used is None and limit is None:
            continue
        parts.append(f"{name} {_number(used or 0)}" + ("" if limit is None else f"/{_number(limit)}"))
    return ["Budget     " + " · ".join(parts)] if parts else []


def render_run(record: dict) -> str:
    """One run, as a terminal reads it."""

    folded = fold(record)
    request = record.get("request") or {}
    definition = folded["definition"]
    if not record.get("id"):
        raise ValueError("A run record without an id cannot be shown")
    lines = [f"Run        {record['id']}",
             f"Status     {MODE_LABELS.get(folded['mode'], folded['mode'])}",
             f"Application  {definition.get('name', '(unnamed)')} · revision {request.get('revision', 1)}",
             f"Task       {request.get('task', '')}"]
    if record.get("created_at"):
        lines.append(f"Started    {record['created_at']}")
    if (request.get("resources") or {}).get("source"):
        lines.append(f"Source     {request['resources']['source']}")

    workflow = folded["workflow"]
    if workflow:
        id_width = max(len(step["id"]) for step in workflow)
        agent_width = max(len(step.get("agent", "")) for step in workflow)
        lines += ["", "Steps"]
        for index, step in enumerate(workflow):
            lines.append(f"  {index + 1}  {step['id']:<{id_width}}  {step.get('agent', ''):<{agent_width}}"
                         f"  {step_status(step, index, folded)}")

    if folded["review"]:
        lines += ["", "Waiting for you", *review_lines(folded["review"], record["id"])]

    if folded["views"]:
        lines += ["", "Outputs"]
        for view in folded["views"]:
            lines.append(f"  {view.get('id', '?')} ({view.get('layout', 'text')})")
            lines.extend(value_lines(view.get("value")))
    elif folded["state"]:
        lines += ["", "State"]
        for key, value in folded["state"].items():
            lines.append(f"  {key}")
            lines.extend(value_lines(value))

    finished = [item.get("event", item) for item in record.get("events") or []
                if item.get("event", item).get("type") == "ApplicationFinished"]
    last = finished[-1] if finished else {}
    # A waiting or paused run has its own section, and a completed one has nothing
    # to explain: a reason is worth printing when the run actually stopped.
    if last.get("reason") and last.get("status") not in {"completed", "waiting_for_human", "paused"}:
        lines += ["", f"Stopped    {last['reason']}"]

    calls = sum(1 for item in record.get("events") or []
                if item.get("event", item).get("type") in {"ToolCalled", "ToolDenied"})
    lines += ["", f"Members    {len(definition.get('agents') or [])} · steps done {len(folded['completed'])}"
                  f" of {len(workflow)} · tool calls {calls}", *budget_lines(record, folded)]
    return "\n".join(lines) + "\n"
