"""D2 — limits, the worker process and the cache: a document can make one read
fail, never crash or hang the host."""

import asyncio
import json
import os
import sys

import pytest

from document_support import REPORT, pdf_bytes


def limits(**change):
    from conclave.tools.documents import Limits
    values = {"max_bytes": 50 * 1024 * 1024, "max_pages": 1000, "max_chars": 2_000_000, "timeout": 30, "max_memory_mb": 1024}
    return Limits(**{**values, **change})


def extract(path, **change):
    from conclave.tools.documents import extract as run
    return asyncio.run(run(path, limits(**change)))


@pytest.fixture(autouse=True)
def fresh_cache():
    from conclave.tools.documents import clear_cache
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def starts(monkeypatch):
    """Counts the worker processes started."""
    from conclave.tools import documents

    started = []
    original = documents.worker_command

    def counting(path, chosen):
        started.append(str(path))
        return original(path, chosen)

    monkeypatch.setattr(documents, "worker_command", counting)
    return started


def stand_in(monkeypatch, code):
    """Replace the worker with a Python one-liner."""
    from conclave.tools import documents
    monkeypatch.setattr(documents, "worker_command", lambda path, chosen: [sys.executable, "-c", code])


def report(tmp_path, name="report.pdf"):
    path = tmp_path / name
    path.write_bytes(pdf_bytes(REPORT))
    return path


def test_the_defaults_and_the_settings(monkeypatch):
    from conclave.tools.documents import Limits

    for name in ("MAX_BYTES", "MAX_PAGES", "MAX_CHARS", "TIMEOUT", "MAX_MEMORY_MB"):
        monkeypatch.delenv(f"CONCLAVE_DOCUMENT_{name}", raising=False)
    assert Limits.from_environment() == limits()
    monkeypatch.setenv("CONCLAVE_DOCUMENT_MAX_PAGES", "20")
    monkeypatch.setenv("CONCLAVE_DOCUMENT_TIMEOUT", "2.5")
    assert (Limits.from_environment().max_pages, Limits.from_environment().timeout) == (20, 2.5)


@pytest.mark.parametrize("name, value", [("MAX_BYTES", "0"), ("MAX_PAGES", "-1"), ("MAX_CHARS", "lots"), ("TIMEOUT", "0"),
                                         ("MAX_MEMORY_MB", "1.5")])
def test_a_bad_setting_is_named(monkeypatch, name, value):
    from conclave.tools.documents import Limits

    monkeypatch.setenv(f"CONCLAVE_DOCUMENT_{name}", value)
    with pytest.raises(ValueError, match=f"CONCLAVE_DOCUMENT_{name}"):
        Limits.from_environment()


@pytest.mark.parametrize("name, kind", [("a.pdf", "pdf"), ("A.PDF", "pdf"), ("b.docx", "docx"), ("c.doc", ("unsupported", ".doc")),
                                        ("d.pptx", ("unsupported", ".pptx")), ("e.xlsx", ("unsupported", ".xlsx")),
                                        ("f.odt", ("unsupported", ".odt")), ("g.rtf", ("unsupported", ".rtf")),
                                        ("h.txt", None), ("i.md", None), ("j", None)])
def test_what_kind_of_document_a_file_is(name, kind):
    from conclave.tools.documents import document_kind
    assert document_kind(name) == kind


def test_a_file_over_the_size_limit_is_not_opened(tmp_path, starts):
    path = report(tmp_path)
    result = extract(path, max_bytes=100)
    assert result.text is None and result.message.startswith("This PDF is ")
    assert result.message.endswith("are not read. Limit: CONCLAVE_DOCUMENT_MAX_BYTES.")
    assert starts == [], "no process is started for a file that is too large"


def test_a_worker_that_takes_too_long_is_stopped_and_tried_again_next_time(tmp_path, monkeypatch):
    import time

    path = report(tmp_path)
    stand_in(monkeypatch, "import time; time.sleep(30)")
    started = time.monotonic()
    result = extract(path, timeout=0.5)
    assert time.monotonic() - started < 10
    assert result.message == ("Reading this PDF took longer than 0.5 seconds and was stopped. "
                              "The file may be damaged or unusually complex. Limit: CONCLAVE_DOCUMENT_TIMEOUT.")
    stand_in(monkeypatch, 'import json; print(json.dumps({"text": "[Page 1]\\nlater", "pages": 1, "truncated": False}))')
    assert extract(path, timeout=0.5).text == "[Page 1]\nlater", "a timeout is not remembered"


def memory_cap_enforced():
    """Whether this system lets a process lower its address-space limit."""
    if os.name != "posix":
        return False
    import subprocess
    probe = "import resource; resource.setrlimit(resource.RLIMIT_AS, (2 ** 30, 2 ** 30))"
    return subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True).returncode == 0


@pytest.mark.skipif(not memory_cap_enforced(), reason="this system does not enforce an address-space limit")
def test_a_worker_that_needs_too_much_memory_is_stopped(tmp_path, monkeypatch):
    path = report(tmp_path)
    stand_in(monkeypatch, "import json; from conclave.tools.documents import guarded; "
                          "print(json.dumps(guarded(lambda: bytearray(4 * 1024 ** 3))))")
    result = extract(path, max_memory_mb=300)
    assert result.message == "Reading this PDF needed more memory than allowed and was stopped. Limit: CONCLAVE_DOCUMENT_MAX_MEMORY_MB."


@pytest.mark.skipif(os.name != "posix", reason="the limits are rlimits")
def test_a_worker_still_runs_where_the_memory_cap_is_refused(monkeypatch):
    # macOS refuses to lower RLIMIT_AS; the CPU backstop must still be set and
    # the worker must still start.
    import resource
    from conclave.tools.documents import _child_limits
    calls = []

    def setrlimit(kind, value):
        calls.append(kind)
        if kind == resource.RLIMIT_AS:
            raise ValueError("current limit exceeds maximum limit")

    monkeypatch.setattr(resource, "setrlimit", setrlimit)
    _child_limits(limits(timeout=5))()
    assert calls == [resource.RLIMIT_AS, resource.RLIMIT_CPU]


def test_guarded_turns_every_failure_into_an_answer():
    from conclave.tools.documents import guarded

    def memory():
        raise MemoryError()

    def broken():
        raise RuntimeError("private detail")

    assert guarded(memory) == {"failure": "memory"}
    assert guarded(broken) == {"failure": "damaged"}
    assert guarded(lambda: {"text": "x", "pages": 1, "truncated": False}) == {"text": "x", "pages": 1, "truncated": False}


@pytest.mark.parametrize("code", [
    "raise SystemExit(3)",
    "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
    "print('not json')",
    "print('[1, 2]')",
    "import sys; sys.stdout.write('x' * 400000)",
], ids=["exit code", "killed", "garbage", "wrong shape", "too much output"])
def test_a_worker_that_fails_in_any_way_gives_the_damaged_message(tmp_path, monkeypatch, code):
    path = report(tmp_path)
    stand_in(monkeypatch, code)
    result = extract(path, max_chars=10_000)
    assert result.text is None
    assert result.message == "This PDF could not be read: the file is damaged or not a PDF."


def test_the_worker_runs_as_its_own_module_and_prints_one_answer(tmp_path):
    import subprocess
    from conclave.tools.documents import worker_command

    path = report(tmp_path)
    command = worker_command(path, limits())
    # A script in isolated mode: it imports none of Conclave, so it starts in
    # the time pypdf takes to load (importing the package took ~1.8 s here).
    assert command[:2] == [sys.executable, "-I"] and command[2].endswith("documents.py")
    done = subprocess.run(command, capture_output=True, timeout=60)
    answer = json.loads(done.stdout)
    assert answer["pages"] == 3 and answer["text"].startswith("[Page 1]")


def test_the_same_file_is_extracted_once(tmp_path, starts):
    path = report(tmp_path)
    first, second = extract(path), extract(path)
    assert first.text == second.text and len(starts) == 1


def test_a_changed_file_is_extracted_again(tmp_path, starts):
    path = report(tmp_path)
    extract(path)
    path.write_bytes(pdf_bytes([["Revised"]]))
    os.utime(path, ns=(1, 2_000_000_000_000_000_000))
    assert extract(path).text == "[Page 1]\nRevised"
    assert len(starts) == 2


def test_concurrent_reads_of_one_file_start_one_process(tmp_path, starts):
    from conclave.tools.documents import extract as run

    path = report(tmp_path)

    async def both():
        return await asyncio.gather(run(path, limits()), run(path, limits()), run(path, limits()))

    results = asyncio.run(both())
    assert len({result.text for result in results}) == 1 and len(starts) == 1


def test_what_could_not_be_read_is_remembered_too(tmp_path, starts):
    path = tmp_path / "broken.pdf"
    path.write_bytes(pdf_bytes(REPORT)[:300])
    assert extract(path).message == extract(path).message
    assert len(starts) == 1, "an agent that retries does not start another process"


def test_the_cache_stays_within_its_bound(tmp_path, monkeypatch):
    from conclave.tools import documents

    # Each report's text is about 600 characters: room for two, not five.
    monkeypatch.setattr(documents, "CACHE_CHARS", 1500)
    for index in range(5):
        extract(report(tmp_path, f"r{index}.pdf"))
    assert 0 < documents.cache_size() <= 1500
