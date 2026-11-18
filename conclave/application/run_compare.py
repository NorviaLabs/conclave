"""Two runs of one application, compared step by step.

For every step: what happened in each run (completed, skipped by routing,
failed, not reached…), how its own output changed, how its review went and
what it used. Around the steps: the version each run used and how the
requests differed. Built from the records a person may already read, so it
shows nothing they could not open themselves; it never reads a checkpoint.
"""

from __future__ import annotations

import difflib
from typing import Any
from urllib.parse import quote

from .versions import compare_definitions

# A text diff stops here, and a record list here; the rest is counted.
MAX_DIFF_LINES = 400
MAX_RECORD_CHANGES = 200
# The most line pairs a text diff weighs (lines left in A × lines left in B),
# about 30 ms; a bigger difference is shown as one changed block.
MAX_DIFF_WORK = 250_000
# The most a whole comparison weighs, across its steps: about 100 ms.
MAX_COMPARISON_WORK = 1_000_000
# A line longer than this is shortened, so a response stays small.
MAX_LINE_CHARS = 1000
# Unchanged lines kept around each change.
CONTEXT_LINES = 2
# How much of a reviewer's comment is shown, and the longest value shown as was/now.
MAX_COMMENT = 280
MAX_SHOWN_VALUE = 80
# A change in tokens this large is worth a tag on the step.
USAGE_TAG_PERCENT = 10

IN_PROGRESS = {"pending", "running", "waiting_for_human", "paused"}
RECORD_LABELS = {"title": "Title", "body": "Body", "status": "Status", "source_refs": "Sources"}
# Said by the record itself about how it was made, not what it says.
PROVENANCE = {"id", "contributions", "source_checks"}


def _definition(record: dict) -> dict:
    return (record.get("request") or {}).get("definition") or {}


def _name(step: dict | None) -> str | None:
    return None if step is None else step.get("name") or step["id"]


def _shorten(value: Any, limit: int = MAX_COMMENT) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _states(record: dict) -> tuple[dict[str, dict], dict[str, Any]]:
    """What happened to each step in one run, and each step's own final output."""
    states: dict[str, dict] = {}
    outputs: dict[str, Any] = {}
    for item in record.get("events") or []:
        event = item.get("event", item)
        kind, stage = event.get("type"), event.get("stage")
        if kind == "StageStarted":
            states[stage] = {"status": "running", "reason": ""}
        elif kind == "StageFinished":
            if event.get("status") == "completed":
                states[stage] = {"status": "completed", "reason": ""}
                if event.get("held"):
                    outputs[stage] = event.get("patch")
            else:
                states[stage] = {"status": event.get("status") or "stopped", "reason": event.get("reason") or ""}
        elif kind == "ApplicationStateChanged" and stage:
            states[stage] = {"status": "completed", "reason": ""}
            outputs[stage] = event.get("patch")
        elif kind == "StageSkipped":
            states[stage] = {"status": "skipped", "reason": event.get("reason") or ""}
            outputs.pop(stage, None)
        elif kind == "HumanInputRequested":
            states[stage] = {"status": "waiting", "reason": ""}
        elif kind == "HumanInputRecorded":
            states[stage] = {"status": "completed", "reason": ""}
        elif kind == "RevisionRequested":
            # Sent back: those steps are to be done again.
            for reset in event.get("reset") or []:
                states.pop(reset, None)
                outputs.pop(reset, None)
        elif kind == "ApplicationFinished" and stage and (states.get(stage) or {}).get("status") in {"running", "waiting", None}:
            if event.get("status") in {"failed", "stopped", "interrupted", "cancelled"}:
                states[stage] = {"status": event["status"], "reason": event.get("reason") or ""}
    status = record.get("status")
    for stage, state in states.items():
        if state["status"] == "running" and status not in IN_PROGRESS:
            # The run ended while this step worked: it stopped with the run.
            state.update(status="stopped" if status not in {"failed", "interrupted", "cancelled"} else status)
        if state["status"] == "waiting" and status != "waiting_for_human":
            state.update(status="stopped")
    return states, outputs


def _text_diff(before: str, after: str, budget: dict | None = None) -> dict | None:
    """A line diff: changes with two lines of context, the unchanged rest counted.

    `budget` is what is left of the comparison's line-pair work, shared by its
    steps; a diff that would weigh more than it, or than one step may, is
    shown as one changed block (`coarse`).
    """
    old, new = before.splitlines(), after.splitlines()
    # What both share at the start and the end is the same, whatever lies between.
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    if head == len(old) == len(new):
        return None
    tail = 0
    while tail < min(len(old), len(new)) - head and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]:
        tail += 1
    middle_old, middle_new = old[head:len(old) - tail], new[head:len(new) - tail]
    # A line diff can take time that grows with the product of the lines left;
    # past a bound, what is between is shown as one changed block.
    work = len(middle_old) * len(middle_new)
    coarse = work > MAX_DIFF_WORK or (budget is not None and work > budget["left"])
    if budget is not None and not coarse:
        budget["left"] -= work

    lines: list[dict] = []
    more = 0

    def add(kind: str, text: str = "", count: int = 0) -> None:
        nonlocal more
        if len(lines) >= MAX_DIFF_LINES:
            more += 1
            return
        if kind == "gap":
            lines.append({"kind": "gap", "text": "", "count": count})
        elif len(text) > MAX_LINE_CHARS:
            lines.append({"kind": kind, "text": text[:MAX_LINE_CHARS - 1] + "…", "shortened": True})
        else:
            lines.append({"kind": kind, "text": text})

    def unchanged(run: list[str]) -> None:
        # Between two changes: context after the one before and before the next.
        if len(run) <= 2 * CONTEXT_LINES:
            for line in run:
                add("same", line)
            return
        for line in run[:CONTEXT_LINES]:
            add("same", line)
        add("gap", count=len(run) - 2 * CONTEXT_LINES)
        for line in run[len(run) - CONTEXT_LINES:]:
            add("same", line)

    # Only the context lines of the shared start and end are ever read.
    if head > CONTEXT_LINES:
        add("gap", count=head - CONTEXT_LINES)
    for line in old[max(0, head - CONTEXT_LINES):head]:
        add("same", line)
    if coarse:
        for line in middle_old:
            add("del", line)
        for line in middle_new:
            add("add", line)
    else:
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, middle_old, middle_new, autojunk=False).get_opcodes():
            if tag == "equal":
                unchanged(middle_old[i1:i2])
                continue
            for line in middle_old[i1:i2]:
                add("del", line)
            for line in middle_new[j1:j2]:
                add("add", line)
    for line in old[len(old) - tail:len(old) - tail + CONTEXT_LINES]:
        add("same", line)
    if tail > CONTEXT_LINES:
        add("gap", count=tail - CONTEXT_LINES)
    return {"kind": "text", "lines": lines, "more": more, "coarse": coarse}


def _shown(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and len(value) <= MAX_SHOWN_VALUE:
        return value
    return None


def _record_diff(before: list, after: list, labels: dict[str, str], order: list[str]) -> dict | None:
    old = {item.get("id"): item for item in before if isinstance(item, dict)}
    new = {item.get("id"): item for item in after if isinstance(item, dict)}
    changes = []
    for identifier, item in new.items():
        if identifier not in old:
            changes.append({"id": identifier, "title": item.get("title") or identifier, "kind": "added", "fields": []})
            continue
        previous, fields = old[identifier], []
        keys = [key for key in order if key in previous or key in item]
        keys += sorted((set(previous) | set(item)) - set(keys) - PROVENANCE)
        for key in keys:
            if key in PROVENANCE or previous.get(key) == item.get(key):
                continue
            was, now = previous.get(key), item.get(key)
            shown = key != "body" and _shown(was) == was and _shown(now) == now
            fields.append({"field": key, "label": labels.get(key, key), "was": was if shown else None, "now": now if shown else None})
        if fields:
            changes.append({"id": identifier, "title": item.get("title") or identifier, "kind": "changed", "fields": fields})
    for identifier, item in old.items():
        if identifier not in new:
            changes.append({"id": identifier, "title": item.get("title") or identifier, "kind": "removed", "fields": []})
    if not changes:
        return None
    return {"kind": "records", "count": len(changes), "changes": changes[:MAX_RECORD_CHANGES],
            "more": max(0, len(changes) - MAX_RECORD_CHANGES)}


def _output_diff(before: Any, after: Any, labels: dict[str, str], order: list[str], budget: dict | None = None) -> dict | None:
    if before is None or after is None:
        return None
    if isinstance(before, str) and isinstance(after, str):
        return _text_diff(before, after, budget)
    if isinstance(before, list) and isinstance(after, list):
        return _record_diff(before, after, labels, order)
    return None if before == after else {"kind": "value", "changed": True}


def _answer_text(output: Any) -> tuple[str | None, str]:
    """A review answer's decision and its words."""
    if isinstance(output, dict) and isinstance(output.get("records"), list) and output["records"]:
        first = output["records"][0] if isinstance(output["records"][0], dict) else {}
        return first.get("status"), str(first.get("body") or "")
    if isinstance(output, dict) and "text" in output:
        return "answered", str(output.get("text") or "")
    return None, ""


def _review(record: dict, stage: str) -> dict | None:
    rounds = [item for item in record.get("reviews") or [] if item.get("stage") == stage]
    if not rounds:
        return None
    answered = [item for item in rounds if item.get("output") is not None or item.get("answers")]
    last = max(rounds, key=lambda item: item.get("round") or 1)
    summary: dict[str, Any] = {"decision": None, "reviewers": [], "rounds": max(item.get("round") or 1 for item in rounds),
                               "changes_requested": False, "comment": ""}
    final = max(answered, key=lambda item: item.get("round") or 1) if answered else None
    if final is not None:
        answers = final.get("answers") or [{"reviewer": final.get("reviewer"), "output": final.get("output")}]
        decision, words = _answer_text(answers[-1].get("output") if answers[-1].get("output") is not None else final.get("output"))
        summary["decision"], summary["comment"] = decision, _shorten(words)
        summary["reviewers"] = list(dict.fromkeys(str(answer["reviewer"]) for answer in answers if answer.get("reviewer")))
    if last.get("status") == "pending" and final is not last:
        # Its latest round still waits: no decision yet.
        summary.update(decision=None, comment="", reviewers=[])
    sent_back = [item for item in rounds if item.get("status") == "revised"]
    if sent_back:
        first = min(sent_back, key=lambda item: item.get("round") or 1)
        summary["changes_requested"] = True
        summary["first_comment"] = _shorten(_answer_text(first.get("output"))[1])
    return summary


def _usage(record: dict, stage: str) -> dict | None:
    usage = record.get("usage") or {}
    step = (usage.get("steps") or {}).get(stage) if usage.get("recorded") else None
    if not step:
        return None
    return {"model_calls": step.get("model_calls", 0), "tokens": step.get("input_tokens", 0) + step.get("output_tokens", 0),
            "tool_calls": step.get("tool_calls", 0), "seconds": step.get("seconds", 0.0),
            "measured": bool(step.get("measured_calls"))}


def _percent(before: float, after: float) -> int | None:
    return round((after - before) / before * 100) if before else None


def _usage_delta(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return None
    return {"model_calls": b["model_calls"] - a["model_calls"], "tokens": b["tokens"] - a["tokens"],
            "tokens_percent": _percent(a["tokens"], b["tokens"]) if a["measured"] and b["measured"] else None,
            "tool_calls": b["tool_calls"] - a["tool_calls"], "seconds": round(b["seconds"] - a["seconds"], 3)}


def _signed_percent(value: int) -> str:
    return f"+{value}%" if value >= 0 else f"−{abs(value)}%"


def _tags(presence: str, status: dict, output: dict | None, review: dict | None, usage: dict | None) -> list[dict]:
    tags = []
    if presence != "both":
        side = "A" if presence == "only_a" else "B"
        return [{"kind": f"only_in_{side.lower()}", "text": f"Only in {side}"}]
    a, b = status["a"]["status"], status["b"]["status"]
    if a != b:
        for side, mine, theirs in (("b", b, a), ("a", a, b)):
            if mine == "skipped" and theirs != "skipped":
                tags.append({"kind": f"skipped_only_in_{side}", "text": f"Skipped only in {side.upper()}"})
                break
            if mine == "not_reached" and theirs != "not_reached":
                tags.append({"kind": f"not_reached_in_{side}", "text": f"Not reached in {side.upper()}"})
                break
        else:
            tags.append({"kind": "status_changed", "text": "Ended differently"})
    if output is not None:
        tags.append({"kind": "output_changed", "text": "Output changed"})
    if review is not None and review["a"] is not None and review["b"] is not None:
        keys = ("decision", "rounds", "changes_requested", "reviewers")
        if any(review["a"].get(key) != review["b"].get(key) for key in keys):
            tags.append({"kind": "review_differs", "text": "Review differs"})
    if usage is not None and usage["delta"] is not None and usage["delta"]["tokens_percent"] is not None \
            and abs(usage["delta"]["tokens_percent"]) >= USAGE_TAG_PERCENT:
        tags.append({"kind": "usage", "text": "Usage " + _signed_percent(usage["delta"]["tokens_percent"])})
    return tags


def _labels(*definitions: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Record field labels, and each result's field order, from both versions."""
    labels, orders = dict(RECORD_LABELS), {}
    for definition in reversed(definitions):
        for collection in definition.get("state") or []:
            fields = collection.get("fields") or []
            orders[collection["key"]] = list(RECORD_LABELS) + [field["key"] for field in fields]
            labels.update({field["key"]: field.get("label") or field["key"] for field in fields})
    return labels, orders


def _run_card(record: dict) -> dict:
    request = record.get("request") or {}
    return {"id": record.get("id"), "created_at": record.get("created_at"), "status": record.get("status"),
            "version": request.get("revision"), "started_by": record.get("started_by"),
            "trigger": request.get("trigger") or {"kind": "manual"}, "task": request.get("task", ""),
            "in_progress": record.get("status") in IN_PROGRESS}


def _request(a: dict, b: dict) -> dict:
    first, second = a.get("request") or {}, b.get("request") or {}
    task = None if first.get("task") == second.get("task") else {"a": first.get("task"), "b": second.get("task")}
    fields = {}
    for definition in (_definition(a), _definition(b)):
        fields.update({field["id"]: field.get("label") or field["id"] for field in definition.get("inputs") or []})
    values_a, values_b = first.get("inputs") or {}, second.get("inputs") or {}
    keys = [key for key in fields if key in values_a or key in values_b]
    keys += sorted((set(values_a) | set(values_b)) - set(keys))
    inputs = [{"key": key, "label": fields.get(key, key), "a": values_a.get(key), "b": values_b.get(key)}
              for key in keys if values_a.get(key) != values_b.get(key)]
    return {"task": task, "inputs": inputs}


def _version(a: dict, b: dict, application: str) -> dict:
    first, second = (a.get("request") or {}).get("revision"), (b.get("request") or {}).get("revision")
    if first == second:
        return {"a": first, "b": second, "same": True, "changes": None, "link": None}
    changes = compare_definitions(_definition(a), _definition(b))
    return {"a": first, "b": second, "same": False, "changes": changes,
            "link": f"/api/applications/{quote(application, safe='')}/versions/compare?from={first}&to={second}"}


def _names(items: list[dict], kind: str) -> list[str]:
    return [item["name"] for item in items if any(tag["kind"] == kind for tag in item["tags"])]


def _phrase(names: list[str], what: str, plural: str) -> str:
    if not names:
        return ""
    if len(names) <= 2:
        quoted = " and ".join(f"“{name}”" for name in names)
        return f"{quoted} {what}"
    return f"{len(names)} steps {plural}"


def _summary(steps: list[dict], a: dict, b: dict) -> dict:
    differing = [item for item in steps if item["differs"]]
    counts = {kind: sum(1 for item in steps if any(tag["kind"] == kind for tag in item["tags"]))
              for kind in ("output_changed", "status_changed", "only_in_a", "only_in_b", "review_differs")}
    parts = []
    if counts["output_changed"]:
        parts.append(f"{counts['output_changed']} output{'s' if counts['output_changed'] != 1 else ''} changed")
    for kind, what, plural in (("only_in_b", "ran only in B", "ran only in B"), ("only_in_a", "ran only in A", "ran only in A"),
                               ("skipped_only_in_b", "was skipped in B", "were skipped in B"),
                               ("skipped_only_in_a", "was skipped in A", "were skipped in A"),
                               ("not_reached_in_b", "was not reached in B", "were not reached in B"),
                               ("not_reached_in_a", "was not reached in A", "were not reached in A"),
                               ("status_changed", "ended differently", "ended differently")):
        phrase = _phrase(_names(steps, kind), what, plural)
        if phrase:
            parts.append(phrase)
    reviews = [item for item in steps if any(tag["kind"] == "review_differs" for tag in item["tags"])]
    if reviews:
        decided = any(item["review"]["a"]["decision"] != item["review"]["b"]["decision"] for item in reviews)
        parts.append("a review decision differs" if decided else "a review went differently")
    if not differing:
        text = "No step differs between the two runs."
    else:
        listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
        text = f"{len(differing)} of {len(steps)} steps differ" + (f": {listed}." if parts else ".")
    token_change = None
    usage_a, usage_b = a.get("usage") or {}, b.get("usage") or {}
    if usage_a.get("recorded") and usage_b.get("recorded"):
        totals_a, totals_b = usage_a.get("total") or {}, usage_b.get("total") or {}
        if totals_a.get("measured_calls") and totals_b.get("measured_calls"):
            token_change = _percent(totals_a.get("tokens") or 0, totals_b.get("tokens") or 0)
    if token_change:
        text += f" B used {abs(token_change)}% {'more' if token_change > 0 else 'fewer'} tokens."
    return {"steps": len(steps), "differing": len(differing), **counts, "token_change": token_change, "text": text}


def compare_runs(a: dict, b: dict, *, order: str = "time") -> dict:
    """Compare two published run records of one application, step by step.

    The earlier run is A, unless `order` is "given".
    """
    if order not in {"time", "given"}:
        raise ValueError("Order runs by time or as given")
    if a.get("id") == b.get("id"):
        raise ValueError("Choose two different runs; a run cannot be compared with itself")
    application = (a.get("request") or {}).get("application_id")
    if application is None or application != (b.get("request") or {}).get("application_id"):
        raise ValueError("Runs of different applications cannot be compared")
    if order == "time" and (b.get("created_at") or "") < (a.get("created_at") or ""):
        a, b = b, a
    definition_a, definition_b = _definition(a), _definition(b)
    steps_a = {step["id"]: step for step in definition_a.get("workflow") or []}
    steps_b = {step["id"]: step for step in definition_b.get("workflow") or []}
    members = {member["id"]: member for definition in (definition_a, definition_b) for member in definition.get("agents") or []}
    states_a, outputs_a = _states(a)
    states_b, outputs_b = _states(b)
    labels, orders = _labels(definition_a, definition_b)
    # Line-pair work the whole comparison may spend on its text diffs.
    budget = {"left": MAX_COMPARISON_WORK}
    steps = []
    for identifier in [*steps_b, *(key for key in steps_a if key not in steps_b)]:
        first, second = steps_a.get(identifier), steps_b.get(identifier)
        presence = "both" if first and second else "only_a" if first else "only_b"
        chosen = second or first
        member = members.get(chosen["agent"]) or {}
        human = member.get("kind") == "human"

        def state(step, states):
            if step is None:
                return {"status": "not_in_version", "reason": ""}
            return dict(states.get(identifier) or {"status": "not_reached", "reason": ""})
        status = {"a": state(first, states_a), "b": state(second, states_b)}
        output = _output_diff(outputs_a.get(identifier), outputs_b.get(identifier), labels, orders.get(chosen.get("output"), list(RECORD_LABELS)),
                              budget) if presence == "both" else None
        review = {"a": _review(a, identifier), "b": _review(b, identifier)} if human else None
        usage = None
        if not human:
            usage_a, usage_b = _usage(a, identifier), _usage(b, identifier)
            if usage_a is not None or usage_b is not None:
                usage = {"a": usage_a, "b": usage_b, "delta": _usage_delta(usage_a, usage_b)}
        tags = _tags(presence, status, output, review, usage)
        steps.append({"id": identifier, "name": _name(chosen), "names": {"a": _name(first), "b": _name(second)},
                      "agent": member.get("name") or chosen["agent"], "kind": "human" if human else "ai",
                      "presence": presence, "status": status, "output": output, "review": review, "usage": usage,
                      "tags": tags, "differs": any(tag["kind"] != "usage" for tag in tags)})
    return {"application_id": application, "name": definition_b.get("name") or definition_a.get("name") or application,
            "runs": {"a": _run_card(a), "b": _run_card(b)}, "version": _version(a, b, application),
            "request": _request(a, b), "steps": steps, "summary": _summary(steps, a, b)}
