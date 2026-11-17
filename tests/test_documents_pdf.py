"""D3 — a PDF's text, page by page, and a clear sentence when there is none."""

import asyncio
import json
import subprocess
import sys

import pytest

from document_support import REPORT, REPORT_TEXT, pdf_bytes


def limits(**change):
    from conclave.tools.documents import Limits
    values = {"max_bytes": 50 * 1024 * 1024, "max_pages": 1000, "max_chars": 2_000_000, "timeout": 60, "max_memory_mb": 1024}
    return Limits(**{**values, **change})


def read(tmp_path, data, name="document.pdf", **change):
    from conclave.tools.documents import clear_cache, extract
    clear_cache()
    path = tmp_path / name
    path.write_bytes(data)
    return asyncio.run(extract(path, limits(**change)))


def test_a_report_reads_page_by_page(tmp_path):
    result = read(tmp_path, pdf_bytes(REPORT))
    assert (result.kind, result.message, result.pages, result.truncated) == ("pdf", None, 3, False)
    assert result.text == REPORT_TEXT


def test_a_page_without_text_says_so_in_place(tmp_path):
    result = read(tmp_path, pdf_bytes([REPORT[0], None, REPORT[2]]))
    assert "[Page 1]\nQ3 Support Review" in result.text
    assert "\n[Page 2: no text layer]\n[Page 3]\n3. Recommendations" in result.text


def test_a_scanned_pdf_has_no_text_to_read(tmp_path):
    result = read(tmp_path, pdf_bytes([None, None]))
    assert result.text is None
    assert result.message == "This PDF has no text layer (it may be scanned images); there is no text to read."


def test_a_pdf_locked_only_against_printing_is_read(tmp_path):
    result = read(tmp_path, pdf_bytes(REPORT, owner_only=True))
    assert result.text == REPORT_TEXT


def test_a_pdf_that_needs_a_password_is_not_read(tmp_path):
    result = read(tmp_path, pdf_bytes(REPORT, password="s3cret"))
    assert result.text is None and result.message == "This PDF is password-protected; it cannot be read."


@pytest.mark.parametrize("data", [pdf_bytes(REPORT)[:300], b"This is not a PDF at all.\n" * 20, b""],
                         ids=["truncated", "not a pdf", "empty"])
def test_a_damaged_pdf_gives_one_sentence(tmp_path, data):
    result = read(tmp_path, data)
    assert result.text is None and result.message == "This PDF could not be read: the file is damaged or not a PDF."


def test_reading_stops_at_the_page_limit_and_says_where(tmp_path):
    result = read(tmp_path, pdf_bytes([[f"Claim {number}"] for number in range(1, 31)]), max_pages=20)
    assert result.truncated is True and result.pages == 30
    assert result.text.splitlines()[-3:] == [
        "[Page 20]", "Claim 20",
        "[Stopped after page 20 of 30: the rest is not read. Limits: CONCLAVE_DOCUMENT_MAX_PAGES, CONCLAVE_DOCUMENT_MAX_CHARS]"]


def test_reading_stops_at_the_text_limit_on_a_page_boundary(tmp_path):
    pages = [[f"Claim {number} " + "x" * 60] for number in range(1, 31)]
    result = read(tmp_path, pdf_bytes(pages), max_chars=500)
    lines = result.text.splitlines()
    assert result.truncated is True and len(result.text) <= 500 + 200
    assert lines[-1].startswith("[Stopped after page ") and lines[-1].endswith(
        "of 30: the rest is not read. Limits: CONCLAVE_DOCUMENT_MAX_PAGES, CONCLAVE_DOCUMENT_MAX_CHARS]")
    assert lines[-2].startswith("Claim "), "a page is given whole or not at all"


def test_the_worker_writes_nothing_but_its_answer(tmp_path):
    from conclave.tools.documents import worker_command

    path = tmp_path / "broken.pdf"
    path.write_bytes(pdf_bytes(REPORT)[:700])
    done = subprocess.run(worker_command(path, limits()), capture_output=True, timeout=60)
    assert json.loads(done.stdout) == {"failure": "damaged"}
    assert done.stderr == b"", "pypdf's warnings stay inside the worker"


def test_the_core_does_not_load_pypdf():
    done = subprocess.run([sys.executable, "-c", "import sys, conclave, conclave.tools.documents; print('pypdf' in sys.modules)"],
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "False"
