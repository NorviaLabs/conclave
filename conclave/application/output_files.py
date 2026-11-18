"""A step's output as a real file: Markdown, CSV or JSON.

Made by the host from the step's validated output, never by the model: the
model chooses no name, path or link. A document is stored as Markdown, a
dataset as CSV (or JSON), an artifact as JSON; an output's `file` setting may
choose another allowed format, or none.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# A stored file is at most this large; a bigger one is not stored, and the run says why.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_NAME = 60
PREVIEW_LINES = 20
PREVIEW_CHARS = 2000

# What each kind of output becomes, and what else it may become.
DEFAULT_FORMAT = {"document": "markdown", "dataset": "csv", "artifact": "json"}
ALLOWED_FORMATS = {"document": {"markdown"}, "dataset": {"csv", "json"}, "artifact": {"json"}}
EXTENSIONS = {"markdown": ".md", "csv": ".csv", "json": ".json"}
MEDIA_TYPES = {"markdown": "text/markdown; charset=utf-8", "csv": "text/csv; charset=utf-8", "json": "application/json"}
FORMAT_LABELS = {"markdown": "Markdown", "csv": "CSV", "json": "JSON"}
# Who wrote a record and which run it came from: the host's bookkeeping, not the result.
PROVENANCE = {"contributions"}
# A spreadsheet runs a cell that starts with one of these as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


class FileChanged(Exception):
    """A stored file is gone, or is no longer what was stored."""


@dataclass(frozen=True)
class FileDownload:
    """A file to send as an attachment, checked against what was stored."""

    body: bytes
    media_type: str
    name: str


def validate_file_setting(item: dict[str, Any]) -> None:
    """An output's `file`: absent, false, or {"format": one its kind allows}."""
    if "file" not in item:
        return
    setting, kind = item["file"], item.get("kind")
    if setting is False:
        if kind not in DEFAULT_FORMAT:
            raise ValueError("Only a document, dataset or artifact has a stored file to turn off")
        return
    if not isinstance(setting, dict) or set(setting) != {"format"}:
        raise ValueError("A stored file setting is false or {\"format\": …}")
    if setting["format"] not in ALLOWED_FORMATS.get(kind, set()):
        allowed = ", ".join(sorted(ALLOWED_FORMATS.get(kind, ()))) or "none"
        raise ValueError(f"A {kind or 'result'} output's stored file may be: {allowed}")


def file_format(collection: dict[str, Any]) -> str | None:
    """The format an output is stored in, or None when it has no file."""
    kind = collection.get("kind")
    if kind not in DEFAULT_FORMAT or collection.get("file") is False:
        return None
    setting = collection.get("file")
    if isinstance(setting, dict) and setting.get("format") in ALLOWED_FORMATS[kind]:
        return setting["format"]
    return DEFAULT_FORMAT[kind]


def file_name(collection: dict[str, Any]) -> str:
    """A name Conclave makes from the output's label: letters, digits and hyphens."""
    text = unicodedata.normalize("NFKD", str(collection.get("label") or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    stem = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_NAME].strip("-")
    if not stem:
        stem = re.sub(r"[^a-z0-9]+", "-", str(collection.get("key") or "output").lower()).strip("-")[:MAX_NAME] or "output"
    return stem + EXTENSIONS[file_format(collection) or "json"]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    text = str(value)
    # Text that would run as a formula is kept as text.
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def _csv(collection: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    fields = collection.get("fields") or []
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    # A label is written by whoever edits the application: guarded like any cell.
    writer.writerow(["id", "title", "body", "status", "sources", *(_cell(field.get("label") or field["key"]) for field in fields)])
    for row in rows:
        writer.writerow([_cell(row.get("id")), _cell(row.get("title")), _cell(row.get("body")), _cell(row.get("status")),
                         _cell(row.get("source_refs") or []), *(_cell(row.get(field["key"])) for field in fields)])
    return buffer.getvalue().encode()


def render_file(collection: dict[str, Any], payload: Any) -> tuple[str, str, bytes]:
    """(name, media type, bytes) for one output; refused when it would be too large."""
    chosen = file_format(collection)
    if chosen is None:
        raise ValueError("This output is not stored as a file")
    if chosen == "markdown":
        body = str(payload).encode()
    elif chosen == "csv":
        body = _csv(collection, payload)
    else:
        records = [{key: value for key, value in item.items() if key not in PROVENANCE} for item in payload] \
            if isinstance(payload, list) else payload
        body = json.dumps(records, ensure_ascii=False, indent=2).encode()
    if len(body) > MAX_FILE_BYTES:
        raise ValueError(f"The file would be too large to store ({len(body):,} bytes; at most {MAX_FILE_BYTES:,})")
    return file_name(collection), MEDIA_TYPES[chosen], body


def preview(body: bytes) -> tuple[str, int]:
    """The first lines of a file, bounded, and how many lines it has."""
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[:PREVIEW_LINES])[:PREVIEW_CHARS], len(lines)
