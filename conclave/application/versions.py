"""What changed between two saved versions of an application, in words.

Differences are grouped by what a person edits (the team, the steps, the
results, limits, who may answer reviews, ...), and items are matched by their
identifiers, so a renamed step is a change to that step rather than one removed
and one added. Values are said as values ("was 1, now 3"); long prose such as
instructions is only said to have changed. Where the cards sit is noted on its
own: it does not change what an application does.
"""
from __future__ import annotations

import json
from typing import Any

MAX_CHANGES = 200
SHORT = 80

AREAS = ("Application", "Team", "Steps", "Connections", "Results", "Views", "Request form", "Limits and approvals",
         "Review policy", "Schedule", "Notifications", "Connectors", "Requirements", "Other settings")

STEP_FIELDS = {"name": "name", "agent": "member", "task": "task", "output": "result", "condition": "condition",
               "revises": "steps it revises", "max_revisions": "revision rounds", "answers_required": "people who must answer",
               "provider": "backend", "model": "model", "group": "group", "time_limit_seconds": "time limit"}
MEMBER_FIELDS = {"name": "name", "role": "role", "kind": "kind", "instructions": "instructions", "tools": "tools",
                 "provider": "backend", "model": "model"}
RESULT_FIELDS = {"label": "label", "type": "type", "merge": "merge", "kind": "kind", "statuses": "statuses", "fields": "fields"}
VIEW_FIELDS = {"title": "title", "collection": "result", "layout": "layout"}
INPUT_FIELDS = {"label": "label", "type": "type", "required": "required"}
PROSE = {"instructions", "task", "description"}
BUDGET = {"max_delegations": "Delegation limit", "max_per_agent": "Delegations per member", "max_depth": "Delegation depth",
          "tool_calls": "Tool-use limit", "wall_clock_seconds": "Time limit (seconds)", "tokens": "Token limit",
          "cost_usd": "Cost limit (USD)"}
CONFIG = {"max_tool_steps": "Action steps per step", "max_notes": "Notes per member", "max_note_chars": "Note length",
          "max_tool_output_chars": "Tool output length", "fan_out_limit": "Steps at once"}
# What an absent setting means, so leaving a default out is not a change.
DEFAULTS = {"answers_required": 1}
LAYOUT = {"canvas"}


def _said(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return f"“{value}”"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _short(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float)) or isinstance(value, str) and len(value) <= SHORT


class Said(str):
    """A change in words, and, when it has them, what it was and is now, apart."""
    parts: dict = {}


def _said_change(label, before, after):
    text = Said(f"{label} was {_said(before)}, now {_said(after)}")
    text.parts = {"subject": label, "was": _said(before), "now": _said(after)}
    return text


def duration_text(seconds: Any) -> str:
    """A step's time limit as Studio shows it: "5 m", "1 m 30 s", "2 h", or "none"."""
    if seconds is None:
        return "none"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [f"{hours} h" if hours else "", f"{minutes} m" if minutes else "", f"{seconds} s" if seconds else ""]
    return " ".join(part for part in parts if part) or "0 s"


def _value_change(label: str, before: Any, after: Any, *, key: str = "") -> str:
    """"task was “A”, now “B”", or "instructions changed" for prose and long values."""
    if key == "time_limit_seconds":
        text = Said(f"{label} was {duration_text(before)}, now {duration_text(after)}")
        text.parts = {"subject": label, "was": duration_text(before), "now": duration_text(after)}
        return text
    if key in PROSE and not (_short(before) and _short(after) and key != "instructions"):
        return Said(f"{label} changed")
    if isinstance(before, list) and isinstance(after, list) and all(isinstance(item, str) for item in before + after):
        added, removed = [item for item in after if item not in before], [item for item in before if item not in after]
        parts = ([f"added {', '.join(added)}"] if added else []) + ([f"removed {', '.join(removed)}"] if removed else [])
        return f"{label}: " + "; ".join(parts) if parts else f"{label}: order changed"
    if _short(before) and _short(after):
        return _said_change(label, before, after)
    return Said(f"{label} changed")


def _by(items, key):
    return {item[key]: item for item in items or [] if isinstance(item, dict) and key in item}


def _title(item, key):
    return item.get("name") or item.get("title") or item.get("label") or item[key]


class _Changes:
    def __init__(self):
        self.found: dict[str, list[dict]] = {area: [] for area in AREAS}

    def add(self, area, kind, text, item="", prefix=""):
        change = {"kind": kind, "text": prefix + str(text), "item": item}
        parts = getattr(text, "parts", {})
        if parts:
            change.update(subject=prefix + parts["subject"], was=parts["was"], now=parts["now"])
        self.found[area].append(change)

    def items(self, area, noun, before, after, key, fields, *, describe=None):
        """Items matched by identifier: added, removed, and each changed field."""
        old, new = _by(before, key), _by(after, key)
        for identifier, item in new.items():
            if identifier not in old:
                self.add(area, "added", f"{noun} “{_title(item, key)}”" + (f" ({describe(item)})" if describe else ""), identifier)
        for identifier, item in old.items():
            if identifier not in new:
                self.add(area, "removed", f"{noun} “{_title(item, key)}”", identifier)
        for identifier in [item for item in new if item in old]:
            was, now = old[identifier], new[identifier]
            for field, label in fields.items():
                a, b = was.get(field, DEFAULTS.get(field)), now.get(field, DEFAULTS.get(field))
                if a != b:
                    self.add(area, "changed", _value_change(label, a, b, key=field), identifier, prefix=f"{noun} “{_title(was, key)}”: ")


def _who(policy) -> str:
    """"ana@acme.com or anyone at @ops.acme.com", or "anyone"."""
    entries = [f"anyone at {entry}" if entry.startswith("@") else entry for entry in policy.get("reviewers") or []]
    return " or ".join(entries) if entries else "anyone"


def _policy(policy) -> str:
    who = f"only {_who(policy)}" if policy.get("reviewers") else "anyone"
    starter = "may" if policy.get("allow_starter") else "may not"
    return f"{who} may answer; the person who started a run {starter}"


def _parts(subject, was, now):
    text = Said(f"{subject}: was {was}, now {now}")
    text.parts = {"subject": subject, "was": was, "now": now}
    return text


def compare_definitions(before: dict, after: dict, *, limit: int = MAX_CHANGES) -> dict:
    """The differences between two definitions, grouped by area, in words."""
    changes = _Changes()
    names = {step["id"]: step.get("name") or step["id"] for step in (before.get("workflow") or []) + (after.get("workflow") or [])}
    members = {member["id"]: member.get("name") or member["id"] for member in (before.get("agents") or []) + (after.get("agents") or [])}

    changes.items("Team", "Member", before.get("agents"), after.get("agents"), "id", MEMBER_FIELDS,
                  describe=lambda item: "person" if item.get("kind") == "human" else "AI")
    changes.items("Steps", "Step", before.get("workflow"), after.get("workflow"), "id", STEP_FIELDS,
                  describe=lambda item: members.get(item.get("agent"), item.get("agent", "")))
    edges = lambda definition: [(edge["from"], edge["to"]) for edge in definition.get("connections") or []]
    if "connections" in before or "connections" in after:
        old, new = edges(before), edges(after)
        for edge in new:
            if edge not in old:
                changes.add("Connections", "added", f"Connection “{names.get(edge[0], edge[0])}” → “{names.get(edge[1], edge[1])}”")
        for edge in old:
            if edge not in new:
                changes.add("Connections", "removed", f"Connection “{names.get(edge[0], edge[0])}” → “{names.get(edge[1], edge[1])}”")
    changes.items("Results", "Result", before.get("state"), after.get("state"), "key", RESULT_FIELDS)
    changes.items("Views", "View", before.get("views"), after.get("views"), "id", VIEW_FIELDS)
    changes.items("Request form", "Field", before.get("inputs"), after.get("inputs"), "id", INPUT_FIELDS)

    for group, labels in (("run_budget", BUDGET), ("run_config", CONFIG)):
        for key, label in labels.items():
            a, b = (before.get(group) or {}).get(key), (after.get(group) or {}).get(key)
            if a != b:
                changes.add("Limits and approvals", "changed", _value_change(label, a, b))
    for key in sorted(set(before.get("budget_warnings") or {}) | set(after.get("budget_warnings") or {})):
        a, b = (before.get("budget_warnings") or {}).get(key), (after.get("budget_warnings") or {}).get(key)
        if a != b:
            changes.add("Limits and approvals", "changed", _value_change(f"Warning at {BUDGET.get(key, key).lower()}", a, b))
    if bool(before.get("autonomous_execution")) != bool(after.get("autonomous_execution")):
        changes.add("Limits and approvals", "changed",
                    _value_change("Autonomous execution", bool(before.get("autonomous_execution")), bool(after.get("autonomous_execution"))))

    a, b = before.get("review_policy"), after.get("review_policy")
    if a != b:
        if a is None:
            changes.add("Review policy", "added", f"Review policy added: {_policy(b)}")
        elif b is None:
            changes.add("Review policy", "removed", "Review policy removed: anyone may answer, including the person who started a run")
        else:
            if a.get("reviewers") != b.get("reviewers"):
                changes.add("Review policy", "changed", _parts("Who may answer", _who(a), _who(b)))
            if bool(a.get("allow_starter")) != bool(b.get("allow_starter")):
                changes.add("Review policy", "changed", _parts("The person who started a run may answer its reviews",
                                                               "yes" if a.get("allow_starter") else "no", "yes" if b.get("allow_starter") else "no"))

    for key, label in (("start_mode", "Start method"),):
        if before.get(key, "manual") != after.get(key, "manual"):
            changes.add("Schedule", "changed", _value_change(label, before.get(key, "manual"), after.get(key, "manual")))
    for area, key, noun in (("Schedule", "schedule", "Schedule"), ("Notifications", "notifications", "Notifications")):
        a, b = before.get(key), after.get(key)
        if a != b:
            kind = "added" if a is None else "removed" if b is None else "changed"
            changes.add(area, kind, f"{noun} {kind}")
    changes.items("Connectors", "Connector", before.get("integrations"), after.get("integrations"), "connector",
                  {"mode": "mode", "purpose": "purpose", "scope": "scope"})
    old = {item["question"]: item["answer"] for item in before.get("requirements") or []}
    new = {item["question"]: item["answer"] for item in after.get("requirements") or []}
    for question, answer in new.items():
        if question not in old:
            changes.add("Requirements", "added", f"Question “{question}”")
        elif old[question] != answer:
            changes.add("Requirements", "changed", _value_change("answer", old[question], answer), prefix=f"Question “{question}”: ")
    for question in old:
        if question not in new:
            changes.add("Requirements", "removed", f"Question “{question}”")

    for key, label in (("name", "Name"), ("description", "Goal"), ("resources", "Source")):
        if before.get(key) != after.get(key):
            changes.add("Application", "changed", _value_change(label, before.get(key), after.get(key), key=key))
    known = {"agents", "workflow", "connections", "state", "views", "inputs", "run_budget", "run_config", "budget_warnings",
             "autonomous_execution", "review_policy", "start_mode", "schedule", "notifications", "integrations",
             "requirements", "name", "description", "resources", "version"} | LAYOUT
    for key in sorted((set(before) | set(after)) - known):
        if before.get(key) != after.get(key):
            changes.add("Other settings", "changed", f"{key.replace('_', ' ').capitalize()} changed")

    count = sum(len(items) for items in changes.found.values())
    areas, left = [], limit
    for area in AREAS:
        if changes.found[area] and left > 0:
            shown = changes.found[area][:left]
            areas.append({"area": area, "changes": shown})
            left -= len(shown)
    return {"areas": areas, "count": count, "layout_changed": before.get("canvas") != after.get("canvas"),
            "truncated": count - (limit - left)}


def summarize_changes(before: dict | None, after: dict) -> str:
    """One line for a version in a list: its first change, and how many more."""
    if before is None:
        return "First saved version"
    comparison = compare_definitions(before, after, limit=1)
    if not comparison["count"]:
        return "No change to what it does"
    first = comparison["areas"][0]["changes"][0]["text"]
    more = comparison["count"] - 1
    return first + (f" · {more} more change{'s' if more != 1 else ''}" if more else "")
