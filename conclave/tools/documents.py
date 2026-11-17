"""Reading PDF and Word documents as text, for an agent.

`extract(path)` returns the text of a `.pdf` (a `[Page N]` line before each
page) or a `.docx` (headings as `#`, `##`, …, list items as `- `, table rows as
`cell | cell`), or one clear sentence when there is no text to give: the file is
encrypted, damaged, scanned, too large, too slow or too hungry to read.

The reading itself happens in a separate, short-lived process (this file, run
as a script in isolated mode), with a time limit and a memory cap, and
it is killed when it runs over. A hostile or broken file can make one read
fail; it can never crash or hang the host. What was read is kept in memory until
the file changes, so searching a folder does not read every document each time.

`pypdf` is imported only in that process. `.docx` is read with the standard
library: it is a zip of XML, and no DTD or entity is ever expanded. The worker
imports nothing else of Conclave, so it starts in the time `pypdf` takes to load.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path

UNSUPPORTED = (".doc", ".pptx", ".xlsx", ".odt", ".rtf")
LABEL = {"pdf": "PDF", "docx": "Word document"}
OLE = bytes.fromhex("D0CF11E0A1B11AE1")
FAILURES = {"password", "damaged", "memory", "no_text", "legacy", "unpacked", "timeout"}
MB = 1024 * 1024
# The extracted text this process keeps, in characters, least recently used out first.
CACHE_CHARS = 20_000_000


def document_kind(path):
    """"pdf", "docx", ("unsupported", suffix) for other office formats, or None (read as text)."""

    suffix = Path(str(path)).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix in UNSUPPORTED:
        return ("unsupported", suffix)
    return None


@dataclass(frozen=True)
class Limits:
    max_bytes: int
    max_pages: int
    max_chars: int
    timeout: float
    max_memory_mb: int
    # Worker processes running at once in this process (security review DS1).
    workers: int = 2

    @classmethod
    def from_environment(cls) -> "Limits":
        def whole(name, default):
            value = os.environ.get(f"CONCLAVE_DOCUMENT_{name}", "").strip() or str(default)
            if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
                raise ValueError(f"CONCLAVE_DOCUMENT_{name} must be a whole number of at least 1")
            return int(value)

        timeout = os.environ.get("CONCLAVE_DOCUMENT_TIMEOUT", "").strip() or "30"
        try:
            seconds = float(timeout)
        except ValueError:
            seconds = 0.0
        if not 0 < seconds < float("inf"):
            raise ValueError("CONCLAVE_DOCUMENT_TIMEOUT must be a number of seconds above 0")
        return cls(whole("MAX_BYTES", 50 * MB), whole("MAX_PAGES", 1000), whole("MAX_CHARS", 2_000_000), seconds,
                   whole("MAX_MEMORY_MB", 1024), whole("WORKERS", 2))


def split_lines(text: str) -> tuple[str, ...]:
    """A document's lines, split once when it is extracted: reading a page of a
    cached document is then a slice (performance review DP3)."""
    return tuple(text.splitlines())


@dataclass(frozen=True)
class Extracted:
    kind: str
    lines: tuple[str, ...] | None  # None when there is no text, and `message` says why
    message: str | None
    pages: int = 0
    truncated: bool = False

    @property
    def text(self) -> str | None:
        return None if self.lines is None else "\n".join(self.lines)


# -- What a person is told --------------------------------------------------------------


def size_text(size: int) -> str:
    if size >= MB:
        return f"{size / MB:.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def unsupported_message(suffix: str) -> str:
    if suffix == ".doc":
        return "This is a Word 97–2003 (.doc) file; it cannot be read. Save it as .docx or PDF."
    return f"This {suffix} file cannot be read as text."


def message(kind: str, failure: str, *, limits: Limits, size: int | None = None) -> str:
    label = LABEL[kind]
    if failure == "size":
        return (f"This {label} is {size_text(size or 0)}; files over {size_text(limits.max_bytes)} are not read. "
                "Limit: CONCLAVE_DOCUMENT_MAX_BYTES.")
    if failure == "timeout":
        return (f"Reading this {label} took longer than {limits.timeout:g} seconds and was stopped. "
                "The file may be damaged or unusually complex. Limit: CONCLAVE_DOCUMENT_TIMEOUT.")
    if failure == "memory":
        return f"Reading this {label} needed more memory than allowed and was stopped. Limit: CONCLAVE_DOCUMENT_MAX_MEMORY_MB."
    if failure == "password":
        return f"This {label} is password-protected; it cannot be read."
    if failure == "no_text":
        return ("This PDF has no text layer (it may be scanned images); there is no text to read." if kind == "pdf"
                else "This Word document has no text.")
    if failure == "legacy":
        return unsupported_message(".doc")
    if failure == "unpacked":
        return "This Word document is too large to read once unpacked. Limit: CONCLAVE_DOCUMENT_MAX_CHARS."
    return ("This PDF could not be read: the file is damaged or not a PDF." if kind == "pdf"
            else "This Word document could not be read: the file is damaged or not a .docx file.")


# -- The worker process ----------------------------------------------------------------------


def worker_command(path, limits: Limits) -> list[str]:
    # Run as a script in isolated mode: no environment variables, no user site,
    # no script directory on the path, and none of Conclave's own imports.
    return [sys.executable, "-I", str(Path(__file__).resolve()), str(path), json.dumps(asdict(limits))]


def _child_limits(limits: Limits):
    """Set in the child before it runs: a memory cap and a CPU-time backstop.

    Some systems, macOS among them, refuse to lower the address-space limit.
    There the worker runs without the memory cap rather than not at all; the
    time limit, the CPU backstop and the output cap still apply.
    """

    def apply():
        import resource
        memory = limits.max_memory_mb * MB
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        except (ValueError, OSError):
            pass
        cpu = int(limits.timeout) + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    return apply


def _kill(process) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, 9)
        else:  # pragma: no cover - POSIX only here
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _answer(data: bytes) -> dict:
    """The worker's one JSON answer, or "damaged" for anything else."""
    try:
        answer = json.loads(data)
    except ValueError:
        return {"failure": "damaged"}
    if isinstance(answer, dict) and set(answer) == {"failure"} and answer["failure"] in FAILURES:
        return answer
    if (isinstance(answer, dict) and set(answer) == {"text", "pages", "truncated"} and isinstance(answer["text"], str)
            and type(answer["pages"]) is int and type(answer["truncated"]) is bool):
        return answer
    return {"failure": "damaged"}


async def _run_worker(path: Path, limits: Limits) -> dict:
    import asyncio
    command = worker_command(path, limits)
    # The worker parses hostile files, so it is given nothing of the host's
    # environment beyond what a process needs: no keys, no tokens (DS4). The
    # real worker runs isolated (-I) and ignores PYTHONPATH; a test's stand-in
    # that imports this package finds it there.
    env = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT") if name in os.environ}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    options = {"preexec_fn": _child_limits(limits), "start_new_session": True} if os.name == "posix" else {}
    try:
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.DEVNULL, env=env, **options)
    except OSError:
        return {"failure": "damaged"}
    cap = limits.max_chars * 4 + 65536

    async def collect():
        data = bytearray()
        while chunk := await process.stdout.read(65536):
            data += chunk
            if len(data) > cap:
                _kill(process)
                await process.wait()
                return None
        await process.wait()
        return bytes(data)

    try:
        data = await asyncio.wait_for(collect(), limits.timeout)
    except (TimeoutError, asyncio.TimeoutError):
        _kill(process)
        await process.wait()
        return {"failure": "timeout"}
    except BaseException:
        _kill(process)
        await process.wait()
        raise
    if data is None or process.returncode != 0:
        return {"failure": "damaged"}
    return _answer(data)


def guarded(work) -> dict:
    """Run the worker's work; any failure becomes an answer, never a traceback."""
    try:
        return work()
    except MemoryError:
        return {"failure": "memory"}
    except Exception:
        return {"failure": "damaged"}


# -- The cache ---------------------------------------------------------------------------------

_cache: "OrderedDict[tuple, Extracted]" = OrderedDict()
_pending: dict = {}
_cached_chars = 0


def _weight(result: Extracted) -> int:
    return sum(len(line) + 1 for line in result.lines) if result.lines is not None else len(result.message or "")


def clear_cache() -> None:
    global _cached_chars
    _cache.clear()
    _pending.clear()
    _cached_chars = 0


def cache_size() -> int:
    return _cached_chars


def _remember(key, result: Extracted) -> None:
    global _cached_chars
    weight = _weight(result)
    if weight > CACHE_CHARS:
        return
    _cache[key] = result
    _cached_chars += weight
    while _cached_chars > CACHE_CHARS and _cache:
        _, old = _cache.popitem(last=False)
        _cached_chars -= _weight(old)


async def extract(path, limits: Limits | None = None) -> Extracted:
    """The text of a PDF or Word document, or the sentence that says why there is none."""
    import asyncio

    limits = limits or Limits.from_environment()
    resolved = Path(path).resolve()
    kind = document_kind(resolved)
    if kind not in LABEL:
        raise ValueError("Only PDF and Word (.docx) documents are extracted")
    stat = resolved.stat()
    if stat.st_size > limits.max_bytes:
        return Extracted(kind, None, message(kind, "size", limits=limits, size=stat.st_size))
    key = (str(resolved), stat.st_size, stat.st_mtime_ns, limits)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    running = _pending.get(key)
    if running is None or running.get_loop() is not asyncio.get_running_loop():
        running = _pending[key] = asyncio.ensure_future(_extract(resolved, kind, limits, key))
    return await asyncio.shield(running)


_slots: dict = {}


def _slot(count: int):
    """At most `count` workers at once in this process, per event loop (DS1).
    A read waits here for its turn; its time limit starts once it has one."""
    import asyncio
    loop = asyncio.get_running_loop()
    held = _slots.get(loop)
    if held is None or held[0] != count:
        held = _slots[loop] = (count, asyncio.Semaphore(count))
        for other in [item for item in _slots if item is not loop and item.is_closed()]:
            _slots.pop(other, None)
    return held[1]


async def _extract(path: Path, kind: str, limits: Limits, key) -> Extracted:
    try:
        async with _slot(limits.workers):
            answer = await _run_worker(path, limits)
        if "failure" in answer:
            result = Extracted(kind, None, message(kind, answer["failure"], limits=limits))
        else:
            result = Extracted(kind, split_lines(answer["text"]), None, answer["pages"], answer["truncated"])
        if answer.get("failure") != "timeout":
            # A busy machine may manage next time; everything else is the file's own answer.
            _remember(key, result)
        return result
    finally:
        _pending.pop(key, None)


# -- Reading, inside the worker ------------------------------------------------------------------


def read_pdf(path: Path, limits: Limits) -> dict:
    import logging
    # pypdf reports damage through its own loggers; the answer says it instead.
    # Only pypdf is silenced: the worker is also run in-process by the tests.
    quiet = logging.getLogger("pypdf")
    if not quiet.handlers:
        quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    from pypdf import PdfReader

    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")  # many PDFs are only locked against printing or copying
        except Exception:
            opened = 0
        if not opened:
            return {"failure": "password"}
    total = len(reader.pages)
    lines, chars, any_text, stopped = [], 0, False, None
    for number in range(1, total + 1):
        if number > limits.max_pages:
            stopped = number - 1
            break
        text = [line.rstrip() for line in (reader.pages[number - 1].extract_text() or "").splitlines() if line.strip()]
        block = [f"[Page {number}]", *text] if text else [f"[Page {number}: no text layer]"]
        size = sum(len(line) + 1 for line in block)
        if chars + size > limits.max_chars:
            if not lines:  # one huge first page: as much of it as fits
                block = _fit(block, limits.max_chars)
                lines.extend(block)
                any_text = any_text or bool(text)
                stopped = number
            else:
                stopped = number - 1
            break
        lines.extend(block)
        chars += size
        any_text = any_text or bool(text)
    if not any_text:
        return {"failure": "no_text"}
    if stopped is not None:
        lines.append(f"[Stopped after page {stopped} of {total}: the rest is not read. "
                     "Limits: CONCLAVE_DOCUMENT_MAX_PAGES, CONCLAVE_DOCUMENT_MAX_CHARS]")
    return {"text": "\n".join(lines), "pages": total, "truncated": stopped is not None}


def _fit(lines: list[str], limit: int) -> list[str]:
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    return kept


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _refuse_dtd(data: bytes) -> None:
    """No DTD, whatever the XML's encoding, so no entity is ever declared or
    expanded (DS3). A byte search for "<!DOCTYPE" misses UTF-16; the parser
    does not."""
    import xml.parsers.expat

    def refuse(*_):
        raise ValueError("DTD refused")

    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.Parse(data, True)


def _part(archive, name: str, cap: int) -> bytes | None:
    """A part of the package, refused when it would unpack past `cap`."""
    import zipfile
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > cap:
        raise _Unpacked()
    with archive.open(info) as stream:
        data = stream.read(cap + 1)
    if len(data) > cap:
        raise _Unpacked()
    if b"<!DOCTYPE" in data.upper():
        # The cheap first look; `_refuse_dtd` is the one that holds.
        raise zipfile.BadZipFile("DTD refused")
    _refuse_dtd(data)
    return data


class _Unpacked(Exception):
    pass


def read_docx(path: Path, limits: Limits) -> dict:
    import xml.etree.ElementTree as ET
    import zipfile

    with open(path, "rb") as handle:
        head = handle.read(8)
    if head == OLE:
        # Word writes a password-protected .docx as an OLE container around an
        # EncryptedPackage; an OLE file without one is a Word 97-2003 document.
        content = Path(path).read_bytes()
        return {"failure": "password" if "EncryptedPackage".encode("utf-16-le") in content else "legacy"}
    # XML is much larger than its text (often 10-50 times); past this it is a zip bomb, not a document.
    cap = min(max(limits.max_chars * 50, MB), 256 * MB)
    try:
        with zipfile.ZipFile(path) as archive:
            document = _part(archive, "word/document.xml", cap)
            styles = _part(archive, "word/styles.xml", cap)
    except _Unpacked:
        return {"failure": "unpacked"}
    if document is None:
        return {"failure": "damaged"}
    root = ET.fromstring(document)
    body = root.find(f"{W}body")
    if body is None:
        return {"failure": "damaged"}
    style = _Styles(ET.fromstring(styles) if styles else None)

    lines, chars, stopped = [], 0, False
    for line in _blocks(body, style):
        if chars + len(line) + 1 > limits.max_chars:
            stopped = True
            break
        lines.append(line)
        chars += len(line) + 1
    if not lines:
        return {"failure": "no_text"}
    if stopped:
        lines.append(f"[Stopped after {chars} characters: the rest is not read. Limit: CONCLAVE_DOCUMENT_MAX_CHARS]")
    return {"text": "\n".join(lines), "pages": 0, "truncated": stopped}


def _vanish(properties) -> bool | None:
    """Whether run properties hide the text: True, False, or None when they do not say."""
    if properties is None:
        return None
    mark = properties.find(f"{W}vanish")
    if mark is None:
        return None
    return mark.get(f"{W}val", "true").lower() not in ("false", "0", "off")


class _Styles:
    """Heading levels, list styles and hidden character styles, resolved through `basedOn`."""

    def __init__(self, root):
        self.styles = {}
        self.characters = {}
        for item in (root.iter(f"{W}style") if root is not None else ()):
            if item.get(f"{W}type") == "character":
                based = item.find(f"{W}basedOn")
                self.characters[item.get(f"{W}styleId")] = (_vanish(item.find(f"{W}rPr")),
                                                            based.get(f"{W}val") if based is not None else None)
                continue
            name = item.find(f"{W}name")
            based = item.find(f"{W}basedOn")
            numbered = item.find(f"{W}pPr/{W}numPr") is not None
            self.styles[item.get(f"{W}styleId")] = ((name.get(f"{W}val") if name is not None else "").lower(),
                                                    based.get(f"{W}val") if based is not None else None, numbered)

    def chain(self, identifier):
        seen = set()
        while identifier and identifier not in seen and len(seen) < 32:
            seen.add(identifier)
            name, based, numbered = self.styles.get(identifier, (identifier.lower(), None, False))
            yield identifier.lower(), name, numbered
            identifier = based

    def heading(self, identifier) -> int:
        for key, name, _ in self.chain(identifier):
            if name == "title" or key == "title":
                return 1
            match = re.fullmatch(r"heading ?([1-9])", name) or re.fullmatch(r"heading([1-9])", key)
            if match:
                return min(int(match.group(1)) + 1, 6)
        return 0

    def listed(self, identifier) -> bool:
        return any(numbered for _, _, numbered in self.chain(identifier))

    def hidden_style(self, identifier) -> bool:
        seen = set()
        while identifier and identifier not in seen and len(seen) < 32:
            seen.add(identifier)
            hidden, based = self.characters.get(identifier, (None, None))
            if hidden is not None:
                return hidden
            identifier = based
        return False

    def hidden(self, run) -> bool:
        """Text Word does not show or print (`w:vanish`): a person reading the
        document never sees it, so neither does the agent (DS2)."""
        properties = run.find(f"{W}rPr")
        direct = _vanish(properties)
        if direct is not None:
            return direct
        chosen = properties.find(f"{W}rStyle") if properties is not None else None
        return self.hidden_style(chosen.get(f"{W}val")) if chosen is not None else False


def _text(node, style: "_Styles | None" = None) -> str:
    """A paragraph's text in reading order: deleted revisions, field codes and
    hidden text left out."""
    out = []

    def walk(element):
        for child in element:
            tag = child.tag
            if tag in (f"{W}del", f"{W}delText", f"{W}instrText", f"{W}pPr", f"{W}rPr"):
                continue
            if tag == f"{W}r" and style is not None and style.hidden(child):
                continue
            if tag == f"{W}t":
                out.append(child.text or "")
            elif tag == f"{W}tab":
                out.append("\t")
            elif tag in (f"{W}br", f"{W}cr"):
                out.append("\n")
            else:
                walk(child)
    walk(node)
    return "".join(out)


def _paragraph(paragraph, style: _Styles) -> str:
    text = _text(paragraph, style).rstrip()
    if not text.strip():
        return ""
    properties = paragraph.find(f"{W}pPr")
    identifier = None
    numbered = False
    if properties is not None:
        chosen = properties.find(f"{W}pStyle")
        identifier = chosen.get(f"{W}val") if chosen is not None else None
        numbered = properties.find(f"{W}numPr") is not None
    level = style.heading(identifier) if identifier else 0
    if level:
        return "#" * level + " " + text.strip()
    if numbered or (identifier and style.listed(identifier)):
        return "- " + text.strip()
    return text


def _blocks(body, style: _Styles):
    for child in body:
        if child.tag == f"{W}p":
            text = _paragraph(child, style)
            if text:
                yield from text.split("\n")
        elif child.tag == f"{W}tbl":
            for row in child.findall(f"{W}tr"):
                cells = [" ".join(filter(None, (_text(part, style).strip() for part in cell.findall(f"{W}p"))))
                         for cell in row.findall(f"{W}tc")]
                if any(cells):
                    yield " | ".join(cells)
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                yield from _blocks(content, style)


def main(argv: list[str]) -> None:
    import warnings
    try:
        path, limits = Path(argv[0]), Limits(**json.loads(argv[1]))
    except Exception:
        sys.stdout.write(json.dumps({"failure": "damaged"}))
        return
    kind = document_kind(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # one JSON answer, and nothing on stderr
        answer = guarded(lambda: read_pdf(path, limits) if kind == "pdf" else read_docx(path, limits))
    sys.stdout.write(json.dumps(answer))
    sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
