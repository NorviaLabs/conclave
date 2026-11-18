"""Checks of the sources a record cites against what its step actually read.

A record's `source_refs` are strings an agent wrote. This module says, for each
one, whether the step that wrote it read or fetched what it names:

- `found`: the step read that file, fetched that page, opened that issue, pull
  request or commit, or was given that record;
- `not_found`: it names something a check can look for, and the step did not
  read it (seeing a link in search results, or a file in a listing, is not
  reading it);
- `not_checkable`: it names nothing a check can look for ("interview notes"),
  or the step also read through a tool whose reads cannot be interpreted, so
  its absence from what could be interpreted proves nothing.

What a step read is gathered from the step's successful tool calls while it runs,
including those of members it delegated to. Tool output is read here and kept
nowhere: only the locators it names are held, for the life of the step.
"""
from __future__ import annotations

import json
import posixpath
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

FOUND, NOT_FOUND, NOT_CHECKABLE = "found", "not_found", "not_checkable"
CHECKS = (FOUND, NOT_FOUND, NOT_CHECKABLE)

# Tools whose successful calls say what was read, and how to read them.
READERS = {"read_file", "search_files", "web_fetch", "github_issue", "github_pr", "git_show"}
# Tools that read nothing a record could cite as read: listings, search results,
# status and history summaries.
SILENT = {"list_files", "git_status", "git_log", "git_diff", "web_search", "github_search_issues", "github_search_prs"}

# How much of one tool result is looked through, and how many locators of each
# kind one step may collect: enough for any real step, bounded for any other.
MAX_SCANNED_CHARS = 1_000_000
MAX_LOCATORS = 5000

URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
UNSAFE_SCHEME = re.compile(r"^(?:javascript|data|file|mailto|vbscript|about|blob):", re.IGNORECASE)
ISSUE = re.compile(r"^(?:(?:issue|pr|pull request|pull)\s*)?#(\d{1,9})$", re.IGNORECASE)
REPOSITORY_ISSUE = re.compile(r"^[\w.-]{1,100}/[\w.-]{1,100}#(\d{1,9})$")
COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
ANCHOR = re.compile(r"#.*$")
LINES = re.compile(r":\d+(?:-\d+)?$")
EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
SEARCH_HIT = re.compile(r"^(.+?):\d+: ", re.MULTILINE)
SHOWN_COMMIT = re.compile(r"^commit ([0-9a-f]{7,40})\b", re.MULTILINE)
DEFAULT_PORTS = {"http": 80, "https": 443}


def _url(value: str) -> str | None:
    """An http(s) URL as compared: lower-case scheme and host, no default
    port, no fragment, no trailing slash, no credentials."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname or parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    if port is not None and port != DEFAULT_PORTS[scheme]:
        host += f":{port}"
    path = parts.path.rstrip("/")
    return f"{scheme}://{host}{path}" + (f"?{parts.query}" if parts.query else "")


def _path(value: str) -> str | None:
    """A path inside the source folder, as compared; None for one that leaves it."""
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    if not value:
        return None
    normalized = posixpath.normpath(value)
    if normalized in {".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def source_kind(ref: Any) -> tuple[str, str] | None:
    """What a cited source names, normalized for comparison, or None for nothing checkable.

    Kinds: `url`, `path`, `issue` (a number), `commit` (a hex prefix) and
    `name` (a single token that may be a record's ID).
    """
    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if not text or len(text) > 2000:
        return None
    if URL_SCHEME.match(text):
        url = _url(text)
        return ("url", url) if url else None
    if UNSAFE_SCHEME.match(text):
        return None
    for pattern in (ISSUE, REPOSITORY_ISSUE):
        match = pattern.match(text)
        if match:
            return "issue", str(int(match.group(1)))
    if COMMIT.match(text):
        return "commit", text.lower()
    if not re.search(r"\s", text):
        bare = LINES.sub("", ANCHOR.sub("", text))
        if "/" in bare or EXTENSION.search(bare):
            path = _path(bare)
            return ("path", path) if path else None
        if NAME.match(text):
            return "name", text
    return None


class StepReads:
    """What one step read, gathered from its tool calls as they happen."""

    def __init__(self, records: Iterable[str] = ()):
        self.records = set(records)
        self.paths: set[str] = set()
        self.urls: set[str] = set()
        self.issues: set[str] = set()
        # Commits by their first seven characters: a cited commit and one read
        # match only if one starts with the other, and both have at least seven.
        self.commits: dict[str, set[str]] = {}
        self.commit_count = 0
        # The step read through a tool whose reads cannot be interpreted.
        self.opaque = False

    def _add(self, found: set[str], value: str | None) -> None:
        if value and len(found) < MAX_LOCATORS:
            found.add(value)

    def _add_commit(self, value: str) -> None:
        if self.commit_count < MAX_LOCATORS and value not in self.commits.get(value[:7], ()):
            self.commits.setdefault(value[:7], set()).add(value)
            self.commit_count += 1

    def observe(self, event: Any) -> None:
        if type(event).__name__ != "ToolCalled" or not getattr(event, "ok", False):
            return
        tool = getattr(event, "tool", "")
        arguments = getattr(event, "arguments", None)
        observation = getattr(event, "observation", "")
        observation = observation[:MAX_SCANNED_CHARS] if isinstance(observation, str) else ""
        if tool in SILENT or "write" in {str(item) for item in getattr(event, "capabilities", ())}:
            return
        if tool not in READERS or not isinstance(arguments, dict):
            # Read something, but nothing here can say what.
            self.opaque = True
            return
        if tool == "read_file":
            self._add(self.paths, _path(str(arguments.get("path", ""))))
        elif tool == "search_files":
            for match in SEARCH_HIT.finditer(observation):
                self._add(self.paths, _path(match.group(1)))
        elif tool == "web_fetch":
            self._add(self.urls, _url(str(arguments.get("url", ""))))
        elif tool in {"github_issue", "github_pr"}:
            number = arguments.get("number")
            if type(number) is int or isinstance(number, str) and number.isdigit():
                self._add(self.issues, str(int(number)))
            try:
                read = json.loads(observation)
            except ValueError:
                read = None
            if isinstance(read, dict):
                if isinstance(read.get("url"), str):
                    self._add(self.urls, _url(read["url"]))
                if type(read.get("number")) is int:
                    self._add(self.issues, str(read["number"]))
        elif tool == "git_show":
            revision = str(arguments.get("revision", ""))
            if COMMIT.match(revision):
                self._add_commit(revision.lower())
            for match in SHOWN_COMMIT.finditer(observation):
                self._add_commit(match.group(1))

    def check(self, ref: str) -> str:
        if isinstance(ref, str) and ref.strip() in self.records:
            return FOUND
        kind = source_kind(ref)
        if kind is None or kind[0] == "name":
            return NOT_CHECKABLE
        category, value = kind
        if category == "commit":
            found = any(commit.startswith(value) or value.startswith(commit) for commit in self.commits.get(value[:7], ()))
        else:
            found = value in {"url": self.urls, "path": self.paths, "issue": self.issues}[category]
        if found:
            return FOUND
        return NOT_CHECKABLE if self.opaque else NOT_FOUND


def record_ids(state: dict[str, Any]) -> set[str]:
    """The IDs of every record in a step's input, which it may cite."""
    return {record["id"] for value in state.values() if isinstance(value, list)
            for record in value if isinstance(record, dict) and isinstance(record.get("id"), str)}


def with_source_checks(records: list[dict], reads: StepReads | None) -> list[dict]:
    """Each record with one check per cited source; None means a person cited them."""
    for record in records:
        refs = record.get("source_refs") or []
        record["source_checks"] = [reads.check(ref) if reads is not None else NOT_CHECKABLE for ref in refs]
    return records


def valid_source_checks(record: dict) -> bool:
    """A recorded check list matches its sources one for one, in the known words."""
    checks = record.get("source_checks")
    if checks is None:
        return True
    return (isinstance(checks, list) and len(checks) == len(record.get("source_refs") or [])
            and all(check in CHECKS for check in checks))
