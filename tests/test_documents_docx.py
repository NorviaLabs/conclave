"""D4 — a Word document's text, by heading, read with the standard library only."""

import asyncio
import time

import pytest

from document_support import (BILLION_LAUGHS, POLICY_TEXT, docx_bytes, document_xml, encrypted_docx_bytes,
                              legacy_doc_bytes, p, table)


def limits(**change):
    from conclave.tools.documents import Limits
    values = {"max_bytes": 50 * 1024 * 1024, "max_pages": 1000, "max_chars": 2_000_000, "timeout": 60, "max_memory_mb": 1024}
    return Limits(**{**values, **change})


def read(tmp_path, data, name="policy.docx", **change):
    from conclave.tools.documents import clear_cache, extract
    clear_cache()
    path = tmp_path / name
    path.write_bytes(data)
    return asyncio.run(extract(path, limits(**change)))


def test_a_policy_reads_by_heading_with_its_list_and_table(tmp_path):
    result = read(tmp_path, docx_bytes())
    assert (result.kind, result.message, result.truncated) == ("docx", None, False)
    assert result.text == POLICY_TEXT


def test_every_heading_level_and_a_style_based_on_one(tmp_path):
    body = "".join(p(f"Level {level}", f"Heading{level}") for level in range(1, 7)) + p("A case study", "Custom")
    result = read(tmp_path, docx_bytes(body))
    assert result.text.splitlines() == ["## Level 1", "### Level 2", "#### Level 3", "##### Level 4", "###### Level 5",
                                        "###### Level 6", "### A case study"]


def test_a_numbered_paragraph_is_a_list_item(tmp_path):
    result = read(tmp_path, docx_bytes(p("First step", numbered=True) + p("Plain")))
    assert result.text.splitlines() == ["- First step", "Plain"]


def test_deleted_revisions_are_left_out_and_insertions_kept(tmp_path):
    runs = ('<w:r><w:t xml:space="preserve">Refunds take </w:t></w:r>'
            '<w:del w:id="1" w:author="Ana"><w:r><w:delText>five</w:delText></w:r></w:del>'
            '<w:ins w:id="2" w:author="Ana"><w:r><w:t>two</w:t></w:r></w:ins>'
            '<w:r><w:t xml:space="preserve"> days.</w:t></w:r>')
    result = read(tmp_path, docx_bytes(p(runs=runs)))
    assert result.text == "Refunds take two days."


def test_tabs_and_line_breaks_are_kept(tmp_path):
    runs = '<w:r><w:t>Name</w:t><w:tab/><w:t>Value</w:t><w:br/><w:t>Next line</w:t></w:r>'
    result = read(tmp_path, docx_bytes(p(runs=runs)))
    assert result.text == "Name\tValue\nNext line"


def test_a_table_cell_with_several_paragraphs_stays_on_its_row(tmp_path):
    cell = f"<w:tc>{p('First')}{p('Second')}</w:tc>"
    result = read(tmp_path, docx_bytes(f"<w:tbl><w:tr>{cell}<w:tc>{p('Other')}</w:tc></w:tr></w:tbl>"))
    assert result.text == "First Second | Other"


def test_an_encrypted_word_document_is_not_read(tmp_path):
    result = read(tmp_path, encrypted_docx_bytes())
    assert result.text is None and result.message == "This Word document is password-protected; it cannot be read."


def test_an_old_word_file_says_to_save_it_again(tmp_path):
    result = read(tmp_path, legacy_doc_bytes(), name="policy.docx")
    assert result.message == "This is a Word 97–2003 (.doc) file; it cannot be read. Save it as .docx or PDF."


@pytest.mark.parametrize("data", [docx_bytes(leave_out=("word/document.xml",)), b"PK\x03\x04 broken", b"plain text",
                                  docx_bytes(document="<w:document><w:body><w:p>")],
                         ids=["no document part", "broken zip", "not a zip", "bad xml"])
def test_a_damaged_word_document_gives_one_sentence(tmp_path, data):
    result = read(tmp_path, data)
    assert result.text is None
    assert result.message == "This Word document could not be read: the file is damaged or not a .docx file."


def test_entity_expansion_is_refused_at_once(tmp_path):
    started = time.monotonic()
    result = read(tmp_path, docx_bytes(document=BILLION_LAUGHS))
    assert time.monotonic() - started < 10
    assert result.message == "This Word document could not be read: the file is damaged or not a .docx file."


def test_a_part_that_unpacks_too_large_is_not_unpacked(tmp_path):
    huge = document_xml(p("x" * 2_000_000))
    result = read(tmp_path, docx_bytes(document=huge), max_chars=10_000)
    assert result.text is None
    assert result.message == "This Word document is too large to read once unpacked. Limit: CONCLAVE_DOCUMENT_MAX_CHARS."


def test_reading_stops_at_the_text_limit_and_says_where(tmp_path):
    body = "".join(p(f"Paragraph {number} " + "y" * 40) for number in range(1, 200))
    result = read(tmp_path, docx_bytes(body), max_chars=1_000)
    lines = result.text.splitlines()
    assert result.truncated is True and len(result.text) <= 1_000 + 200
    assert lines[-1].startswith("[Stopped after ") and lines[-1].endswith(
        " characters: the rest is not read. Limit: CONCLAVE_DOCUMENT_MAX_CHARS]")


def test_an_empty_word_document_says_it_has_no_text(tmp_path):
    result = read(tmp_path, docx_bytes(p("") + table([])))
    assert result.text is None and result.message == "This Word document has no text."
