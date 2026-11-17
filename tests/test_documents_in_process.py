"""The worker's reading code, run in this process.

The D3 and D4 tests read every document through the worker process, as the
host does. Coverage cannot see inside that process, so the same documents are
read here through `main`, the worker's own entry point. Each answer must match
what the process gave, so the two paths can never drift apart.
"""

import asyncio
import json
import subprocess

import pytest

from document_support import (BILLION_LAUGHS, REPORT, docx_bytes, document_xml, encrypted_docx_bytes,
                              legacy_doc_bytes, p, pdf_bytes, table)

LIMITS = {"max_bytes": 50 * 1024 * 1024, "max_pages": 1000, "max_chars": 2_000_000, "timeout": 60, "max_memory_mb": 1024}

CASES = [
    ("report.pdf", lambda: pdf_bytes(REPORT), {}),
    ("mixed.pdf", lambda: pdf_bytes([REPORT[0], None, REPORT[2]]), {}),
    ("scan.pdf", lambda: pdf_bytes([None, None]), {}),
    ("locked.pdf", lambda: pdf_bytes(REPORT, owner_only=True), {}),
    ("secret.pdf", lambda: pdf_bytes(REPORT, password="s3cret"), {}),
    ("broken.pdf", lambda: pdf_bytes(REPORT)[:300], {}),
    ("pages.pdf", lambda: pdf_bytes([[f"Claim {number}"] for number in range(1, 31)]), {"max_pages": 20}),
    ("chars.pdf", lambda: pdf_bytes([[f"Claim {number} " + "x" * 60] for number in range(1, 31)]), {"max_chars": 500}),
    ("first-page.pdf", lambda: pdf_bytes([[f"Line {number} " + "z" * 50 for number in range(40)]]), {"max_chars": 400}),
    ("policy.docx", lambda: docx_bytes(), {}),
    ("headings.docx", lambda: docx_bytes("".join(p(f"Level {n}", f"Heading{n}") for n in range(1, 7)) + p("Case", "Custom")), {}),
    ("revisions.docx", lambda: docx_bytes(p(runs='<w:r><w:t>Keep </w:t></w:r><w:del w:id="1" w:author="A"><w:r><w:delText>gone</w:delText></w:r></w:del>'
                                             '<w:ins w:id="2" w:author="A"><w:r><w:t>added</w:t></w:r></w:ins>')), {}),
    ("tabs.docx", lambda: docx_bytes(p(runs="<w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r>")), {}),
    ("cells.docx", lambda: docx_bytes(f"<w:tbl><w:tr><w:tc>{p('One')}{p('Two')}</w:tc><w:tc>{p('Three')}</w:tc></w:tr></w:tbl>"), {}),
    ("control.docx", lambda: docx_bytes(f"<w:sdt><w:sdtContent>{p('Inside a content control')}</w:sdtContent></w:sdt>"), {}),
    ("bare-styles.docx", lambda: docx_bytes(p("Plain", "Heading1"), styles='<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'), {}),
    ("encrypted.docx", encrypted_docx_bytes, {}),
    ("legacy.docx", legacy_doc_bytes, {}),
    ("missing.docx", lambda: docx_bytes(leave_out=("word/document.xml",)), {}),
    ("nozip.docx", lambda: b"plain text", {}),
    ("laughs.docx", lambda: docx_bytes(document=BILLION_LAUGHS), {}),
    ("no-body.docx", lambda: docx_bytes(document='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'), {}),
    ("huge.docx", lambda: docx_bytes(document=document_xml(p("x" * 2_000_000))), {"max_chars": 10_000}),
    ("long.docx", lambda: docx_bytes("".join(p(f"Paragraph {n} " + "y" * 40) for n in range(1, 200))), {"max_chars": 1_000}),
    ("empty.docx", lambda: docx_bytes(p("") + table([])), {}),
    # Security review: hidden text (DS2) and a DTD in UTF-16 (DS3).
    ("hidden.docx", lambda: docx_bytes(p(runs='<w:r><w:t>Shown. </w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden.</w:t></w:r>'
                                          '<w:r><w:rPr><w:vanish w:val="0"/></w:rPr><w:t>Not hidden.</w:t></w:r>')), {}),
    ("hidden-style.docx", lambda: docx_bytes(
        p(runs='<w:r><w:rPr><w:rStyle w:val="Quiet"/></w:rPr><w:t>Hidden.</w:t></w:r><w:r><w:rPr><w:rStyle w:val="Loud"/></w:rPr><w:t>Shown.</w:t></w:r>'),
        styles=('<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="character" w:styleId="Secret"><w:rPr><w:vanish/></w:rPr></w:style>'
                '<w:style w:type="character" w:styleId="Quiet"><w:basedOn w:val="Secret"/></w:style>'
                '<w:style w:type="character" w:styleId="Loud"/></w:styles>')), {}),
    ("dtd16.docx", lambda: docx_bytes(document=('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY e "x">]>'
                                                 '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                                 '<w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p></w:body></w:document>').encode("utf-16")), {}),
]


@pytest.mark.parametrize("name, build, change", CASES, ids=[case[0] for case in CASES])
def test_the_worker_reads_the_same_in_this_process(tmp_path, capsys, name, build, change):
    from conclave.tools.documents import main, worker_command, Limits

    path = tmp_path / name
    path.write_bytes(build())
    limits = {**LIMITS, **change}
    main([str(path), json.dumps(limits)])
    here = json.loads(capsys.readouterr().out)
    there = json.loads(subprocess.run(worker_command(path, Limits(**limits)), capture_output=True, timeout=60).stdout)
    assert here == there


def test_a_worker_given_nonsense_answers_damaged(capsys):
    from conclave.tools.documents import main

    main(["somewhere.pdf", "not json"])
    assert json.loads(capsys.readouterr().out) == {"failure": "damaged"}


def test_only_documents_are_extracted(tmp_path):
    from conclave.tools.documents import extract

    path = tmp_path / "notes.md"
    path.write_text("text")
    with pytest.raises(ValueError):
        asyncio.run(extract(path))


def test_sizes_read_the_way_people_say_them():
    from conclave.tools.documents import size_text

    assert [size_text(value) for value in (512, 2048, 73 * 1024 * 1024)] == ["512 bytes", "2 KB", "73 MB"]
