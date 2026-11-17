"""D5 and D6 — `read_file` reads PDF and Word documents; source folders list
them and search inside them; text files read exactly as before."""

import asyncio

import pytest

from document_support import POLICY_TEXT, REPORT, REPORT_TEXT, docx_bytes, legacy_doc_bytes, pdf_bytes


@pytest.fixture(autouse=True)
def fresh_cache():
    try:
        from conclave.tools.documents import clear_cache
    except ModuleNotFoundError:
        yield
        return
    clear_cache()
    yield
    clear_cache()


def numbered(text, start=1):
    return "\n".join(f"{number}: {line}" for number, line in enumerate(text.splitlines(), start))


def folder(tmp_path):
    root = tmp_path / "docs"
    (root / "reports").mkdir(parents=True)
    (root / "policies").mkdir()
    (root / "reports" / "q3-review.pdf").write_bytes(pdf_bytes(REPORT))
    (root / "policies" / "refund-policy.docx").write_bytes(docx_bytes())
    (root / "notes.md").write_text("# Team notes\nRefund backlog cleared on Friday.\nNext: review the refund policy draft.\n")
    return root


def local(tmp_path):
    from conclave.tools.local import LocalTools
    return LocalTools(folder(tmp_path))


def source(tmp_path, extensions=None):
    from conclave.application.resources import SourceTools
    return SourceTools(folder(tmp_path), extensions if extensions is not None else {".txt", ".md", ".csv", ".html", ".pdf", ".docx"})


def run(coroutine):
    return asyncio.run(coroutine)


# --- D5: read_file -----------------------------------------------------------------------


def test_read_file_gives_a_pdfs_text_with_numbered_lines(tmp_path):
    tools = local(tmp_path)
    assert run(tools.read_file("reports/q3-review.pdf")) == numbered(REPORT_TEXT)


def test_read_file_gives_a_word_documents_text_by_heading(tmp_path):
    tools = local(tmp_path)
    assert run(tools.read_file("policies/refund-policy.docx")) == numbered(POLICY_TEXT)


def test_paging_a_document_matches_reading_it_whole(tmp_path):
    tools = local(tmp_path)
    whole = run(tools.read_file("reports/q3-review.pdf")).splitlines()
    assert run(tools.read_file("reports/q3-review.pdf", start_line=7, end_line=10)).splitlines() == whole[6:10]
    assert run(tools.read_file("reports/q3-review.pdf", start_line=None, end_line=None)).splitlines() == whole


def test_a_message_is_returned_as_it_is(tmp_path):
    tools = local(tmp_path)
    (tools.repo / "scan.pdf").write_bytes(pdf_bytes([None]))
    assert run(tools.read_file("scan.pdf")) == "This PDF has no text layer (it may be scanned images); there is no text to read."


@pytest.mark.parametrize("name, data, expected", [
    ("old.doc", legacy_doc_bytes(), "This is a Word 97–2003 (.doc) file; it cannot be read. Save it as .docx or PDF."),
    ("deck.pptx", b"PK\x03\x04" + b"\x00" * 64, "This .pptx file cannot be read as text."),
    ("sheet.xlsx", b"PK\x03\x04" + b"\x00" * 64, "This .xlsx file cannot be read as text."),
    ("notes.odt", b"PK\x03\x04" + b"\x00" * 64, "This .odt file cannot be read as text."),
    ("letter.rtf", b"{\\rtf1\\ansi hello}", "This .rtf file cannot be read as text."),
])
def test_other_office_formats_say_so_plainly(tmp_path, name, data, expected):
    tools = local(tmp_path)
    (tools.repo / name).write_bytes(data)
    result = run(tools.read_file(name))
    assert result == expected and "Binary file" not in result


def test_an_unknown_binary_still_reads_as_it_did(tmp_path):
    tools = local(tmp_path)
    (tools.repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert run(tools.read_file("image.png")).startswith("Binary file, not readable as text: image.png")


def test_a_text_file_reads_byte_for_byte_as_before(tmp_path):
    tools = local(tmp_path)
    assert run(tools.read_file("notes.md")) == "1: # Team notes\n2: Refund backlog cleared on Friday.\n3: Next: review the refund policy draft."
    assert run(tools.read_file("notes.md", start_line=2, end_line=2)) == "2: Refund backlog cleared on Friday."


def test_the_tool_descriptions_say_what_an_agent_gets(tmp_path):
    from conclave import ToolRegistry
    from conclave.application.resources import register_source_tools
    from conclave.tools import build_tool_registry

    sourced = ToolRegistry(20000)
    register_source_tools(sourced)
    for registry in (build_tool_registry(tmp_path, 20000), sourced):
        description = registry.tool("read_file").description
        for words in ("PDF", "Word", "[Page N]", "#", "start_line"):
            assert words in description, (words, description)


# --- D6: source folders -------------------------------------------------------------------


def test_search_finds_text_inside_documents_with_read_files_line_numbers(tmp_path):
    tools = source(tmp_path)
    hits = run(tools.search_files("refund")).splitlines()
    assert hits == [
        "notes.md:2: Refund backlog cleared on Friday.",
        "notes.md:3: Next: review the refund policy draft.",
        "policies/refund-policy.docx:1: # Refund policy",
        "policies/refund-policy.docx:3: ## Who can ask for a refund",
        "policies/refund-policy.docx:7: Charge | Refund | Approval",
        "policies/refund-policy.docx:11: Annual plans are refunded pro rata after the first 30 days.",
        "reports/q3-review.pdf:5: Ticket volume rose 12% over Q2, driven by refund requests after",
        "reports/q3-review.pdf:8: 2. Refunds",
        "reports/q3-review.pdf:9: Refund requests: 1,284 (Q2: 902). Median time to resolve: 2.1 days.",
        "reports/q3-review.pdf:14: - Add a self-service refund form for charges under $50.",
    ]
    for hit in hits:
        path, number, text = hit.split(":", 2)
        assert run(tools.read_file(path, start_line=int(number), end_line=int(number))) == f"{number}:{text}"


def test_knowledge_search_finds_words_in_a_pdf(tmp_path):
    tools = source(tmp_path)
    assert "reports/q3-review.pdf:10: Most common cause" in run(tools.knowledge_search("charged twice"))


def test_a_document_without_text_is_skipped_by_search(tmp_path):
    tools = source(tmp_path)
    (tools.root / "locked.pdf").write_bytes(pdf_bytes(REPORT, password="s3cret"))
    assert "locked.pdf" not in run(tools.search_files("Refund"))


def test_a_document_is_extracted_once_across_searches_and_a_read(tmp_path, monkeypatch):
    from conclave.tools import documents

    started = []
    original = documents.worker_command
    monkeypatch.setattr(documents, "worker_command", lambda path, chosen: started.append(path) or original(path, chosen))
    tools = source(tmp_path)
    run(tools.search_files("refund"))
    run(tools.knowledge_search("charged twice"))
    run(tools.read_file("reports/q3-review.pdf"))
    assert sorted(str(item).rsplit("/", 1)[-1] for item in started) == ["q3-review.pdf", "refund-policy.docx"]


def test_source_folders_list_documents_by_default(tmp_path, monkeypatch):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    monkeypatch.delenv("CONCLAVE_SOURCE_EXTENSIONS", raising=False)
    made = configured_host()
    assert {".pdf", ".docx", ".txt", ".md", ".csv", ".html"} == set(made.source_extensions)
    asyncio.run(made.close())
    monkeypatch.setenv("CONCLAVE_SOURCE_EXTENSIONS", ".md")
    made = configured_host()
    assert set(made.source_extensions) == {".md"}, "an explicit setting is kept as it is"
    asyncio.run(made.close())


def test_a_folders_own_extensions_win(tmp_path):
    tools = source(tmp_path, extensions={".md"})
    assert run(tools.list_files()) == "notes.md"
    with pytest.raises(ValueError):
        run(tools.read_file("reports/q3-review.pdf"))
    listed = run(source(tmp_path / "defaults").list_files()).splitlines()
    assert listed == ["notes.md", "policies/refund-policy.docx", "reports/q3-review.pdf"]


def test_containment_and_hidden_files_still_refuse_documents(tmp_path):
    tools = source(tmp_path)
    (tools.root / ".private").mkdir()
    (tools.root / ".private" / "secret.pdf").write_bytes(pdf_bytes(REPORT))
    (tmp_path / "outside.pdf").write_bytes(pdf_bytes(REPORT))
    for path in (".private/secret.pdf", "../outside.pdf"):
        with pytest.raises((ValueError, RuntimeError)):
            run(tools.read_file(path))
    assert ".private" not in run(tools.search_files("Refund"))


def test_a_bad_document_setting_stops_the_host(tmp_path, monkeypatch):
    from conclave.application.service import configured_host

    monkeypatch.setenv("CONCLAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONCLAVE_PROFILE", "hosted")
    monkeypatch.setenv("CONCLAVE_PROVIDER", "ollama")
    monkeypatch.setenv("CONCLAVE_DOCUMENT_TIMEOUT", "never")
    with pytest.raises(ValueError, match="CONCLAVE_DOCUMENT_TIMEOUT"):
        configured_host()


def test_repository_search_stays_text_only(tmp_path):
    tools = local(tmp_path)
    result = run(tools.search_files("Refund"))
    assert "q3-review.pdf" not in result and "refund-policy.docx" not in result
    assert "notes.md" in result
