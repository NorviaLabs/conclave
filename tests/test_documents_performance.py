"""Step 9 — performance fixes for document reading.

DP1: a search starts extracting every new document at once, within the worker limit.
DP3: a cached document is split into lines once, not on every read.
"""

import asyncio
import sys
import time

import pytest

from document_support import REPORT, pdf_bytes


@pytest.fixture(autouse=True)
def fresh_cache():
    from conclave.tools.documents import clear_cache
    clear_cache()
    yield
    clear_cache()


def test_a_first_search_extracts_documents_side_by_side(tmp_path, monkeypatch):
    from conclave.application.resources import SourceTools
    from conclave.tools import documents

    for index in range(6):
        (tmp_path / f"report-{index}.pdf").write_bytes(pdf_bytes([[f"Report {index}"]]))
    monkeypatch.setattr(documents, "worker_command", lambda path, chosen: [
        sys.executable, "-c", 'import json, time; time.sleep(0.3); '
                              'print(json.dumps({"text": "[Page 1]\\nA refund line", "pages": 1, "truncated": False}))'])
    monkeypatch.setenv("CONCLAVE_DOCUMENT_WORKERS", "3")
    tools = SourceTools(tmp_path, {".pdf"})
    started = time.monotonic()
    hits = asyncio.run(tools.search_files("refund"))
    elapsed = time.monotonic() - started
    assert hits.splitlines() == [f"report-{index}.pdf:2: A refund line" for index in range(6)], "the same hits, in the same order"
    assert elapsed < 1.4, f"{elapsed:.2f}s: six 0.3 s reads, three at a time, take about 0.6 s; one at a time 1.8 s"


def test_searching_side_by_side_still_respects_the_worker_limit(tmp_path, monkeypatch):
    from conclave.application.resources import SourceTools
    from conclave.tools import documents

    running, peak = 0, 0

    async def worker(path, chosen):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return {"text": "[Page 1]\nrefund", "pages": 1, "truncated": False}

    for index in range(8):
        (tmp_path / f"r{index}.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(documents, "_run_worker", worker)
    monkeypatch.setenv("CONCLAVE_DOCUMENT_WORKERS", "2")
    assert len(asyncio.run(SourceTools(tmp_path, {".pdf"}).search_files("refund")).splitlines()) == 8
    assert peak == 2


def test_a_cached_document_is_split_into_lines_once(tmp_path, monkeypatch):
    from conclave.tools import documents
    from conclave.tools.local import LocalTools

    (tmp_path / "report.pdf").write_bytes(pdf_bytes(REPORT))
    splits = []
    original = documents.split_lines
    monkeypatch.setattr(documents, "split_lines", lambda text: splits.append(1) or original(text))
    tools = LocalTools(tmp_path)
    pages = [asyncio.run(tools.read_file("report.pdf", start_line=start, end_line=start + 4)) for start in (1, 6, 11)]
    assert pages[1].splitlines()[0] == "6: the September price change. First response time held at 3h 10m."
    assert len(splits) == 1, "split when extracted, then sliced"
