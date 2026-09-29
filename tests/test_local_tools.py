"""Tests for the repository-scoped tools (files, Git, GitHub, web)."""

import asyncio
import json
import sys
import types

import pytest

from pathlib import Path

from conclave.tools import local as local_tools
from conclave.tools.local import LocalTools


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
    ):
        self.payload = payload

    def read(self, size=None) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDDGS:
    calls = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    def text(
        self,
        query,
        max_results=None,
    ):
        FakeDDGS.calls.append(
            (query, max_results)
        )

        return [
            {
                "title": "result",
                "href": "https://example.test",
                "body": query,
            }
        ]


@pytest.fixture
def fake_ddgs(monkeypatch):
    FakeDDGS.calls = []

    module = types.ModuleType("ddgs")
    module.DDGS = FakeDDGS

    monkeypatch.setitem(
        sys.modules,
        "ddgs",
        module,
    )

    return FakeDDGS


# =============================================================================
# Path containment
# =============================================================================


def test_paths_cannot_escape_the_repository(local):
    with pytest.raises(
        RuntimeError,
        match="escapes repository",
    ):
        asyncio.run(
            local.read_file("../outside.txt")
        )


# =============================================================================
# list_files
# =============================================================================


def test_list_files_reports_missing_path(local):
    assert asyncio.run(
        local.list_files("nope")
    ) == "Path does not exist: nope"


def test_list_files_marks_directories_and_ignores_noise(
    local,
    repo,
):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x")

    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.js").write_text("x")

    listing = asyncio.run(
        local.list_files(".")
    )

    assert "a.py" in listing
    assert "pkg/" in listing
    assert "pkg/mod.py" in listing
    assert ".git" not in listing
    assert "node_modules" not in listing


def test_list_files_respects_max_entries(local):
    listing = asyncio.run(
        local.list_files(".", max_entries=1)
    )

    assert len(listing.splitlines()) == 1


def test_list_files_skips_paths_outside_the_repository(
    local,
    repo,
    monkeypatch,
):
    outside = repo.parent / "outside.txt"
    outside.write_text("x")

    def fake_rglob(self, pattern):
        yield outside

    monkeypatch.setattr(
        Path,
        "rglob",
        fake_rglob,
    )

    assert asyncio.run(
        local.list_files(".")
    ) == ""


# =============================================================================
# read_file
# =============================================================================


def test_read_file_numbers_lines(local):
    text = asyncio.run(
        local.read_file("a.py")
    )

    assert text.splitlines()[0].startswith(
        "1: def retry():"
    )


def test_read_file_rejects_directories(local):
    assert asyncio.run(
        local.read_file(".")
    ) == "Not a file: ."


def test_read_file_clamps_the_range(local):
    whole = asyncio.run(
        local.read_file(
            "a.py",
            start_line=0,
            end_line=99,
        )
    )

    assert len(whole.splitlines()) == 2

    reversed_range = asyncio.run(
        local.read_file(
            "a.py",
            start_line=2,
            end_line=1,
        )
    )

    assert reversed_range.startswith("2: ")


# =============================================================================
# search_files
# =============================================================================


def test_search_files_reports_path_and_line(local):
    results = asyncio.run(
        local.search_files("retry")
    )

    assert "a.py:1: def retry():" in results


def test_search_files_reports_no_matches(local):
    assert asyncio.run(
        local.search_files("zzzz")
    ) == "No matches"


def test_search_files_ignores_noise_directories(
    local,
    repo,
):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.js").write_text(
        "retry"
    )

    results = asyncio.run(
        local.search_files("retry")
    )

    assert "node_modules" not in results


def test_search_files_respects_max_results(local):
    results = asyncio.run(
        local.search_files(
            "retry",
            max_results=1,
        )
    )

    assert len(results.splitlines()) == 1


def test_search_files_skips_unreadable_files(
    local,
    monkeypatch,
):
    def unreadable(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(
        Path,
        "read_text",
        unreadable,
    )

    assert asyncio.run(
        local.search_files("retry")
    ) == "No matches"


# =============================================================================
# process runner
# =============================================================================


def test_run_returns_output(local):
    output = asyncio.run(
        local._run(
            [
                sys.executable,
                "-c",
                "print('hello')",
            ]
        )
    )

    assert output == "hello"


def test_run_returns_placeholder_without_output(local):
    assert asyncio.run(
        local._run(
            [
                sys.executable,
                "-c",
                "pass",
            ]
        )
    ) == "(no output)"


def test_run_reports_stderr(local):
    with pytest.raises(
        RuntimeError,
        match="bad flag",
    ):
        asyncio.run(
            local._run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.stderr.write('bad flag'); "
                        "raise SystemExit(2)"
                    ),
                ]
            )
        )


def test_run_reports_silent_failure(local):
    with pytest.raises(
        RuntimeError,
        match="Command failed",
    ):
        asyncio.run(
            local._run(
                [
                    sys.executable,
                    "-c",
                    "raise SystemExit(1)",
                ]
            )
        )


# =============================================================================
# Git tools
# =============================================================================


def test_git_status(local):
    assert "##" in asyncio.run(
        local.git_status()
    )


def test_git_log(local):
    assert "first" in asyncio.run(
        local.git_log()
    )

    assert "first" in asyncio.run(
        local.git_log(".", max_count=0)
    )


def test_git_diff(local):
    assert asyncio.run(
        local.git_diff()
    ) == "(no output)"


def test_git_show(local):
    assert "first" in asyncio.run(
        local.git_show("HEAD")
    )

    assert "a.py" in asyncio.run(
        local.git_show("HEAD", "a.py")
    )


# =============================================================================
# GitHub tools
# =============================================================================


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    async def fake_run(self, command):
        calls.append(command)

        return "ok"

    monkeypatch.setattr(
        LocalTools,
        "_run",
        fake_run,
    )

    return calls


def test_github_issue(local, recorded):
    assert asyncio.run(
        local.github_issue(7)
    ) == "ok"

    assert recorded[0][:4] == [
        "gh",
        "issue",
        "view",
        "7",
    ]


def test_github_pr(local, recorded):
    asyncio.run(
        local.github_pr(3)
    )

    assert recorded[0][:4] == [
        "gh",
        "pr",
        "view",
        "3",
    ]


def test_github_search_issues_clamps_limit(
    local,
    recorded,
):
    asyncio.run(
        local.github_search_issues("q", 0)
    )

    assert "--limit" in recorded[0]
    assert recorded[0][5] == "1"


def test_github_search_prs_clamps_limit(
    local,
    recorded,
):
    asyncio.run(
        local.github_search_prs("q", 1000)
    )

    assert recorded[0][5] == "100"


# =============================================================================
# Web tools
# =============================================================================


def test_web_search_returns_json(
    local,
    fake_ddgs,
):
    payload = asyncio.run(
        local.web_search("retry semantics")
    )

    assert json.loads(payload)[0]["title"] == "result"

    assert fake_ddgs.calls[0][0] == (
        "retry semantics"
    )


def test_web_search_clamps_max_results(
    local,
    fake_ddgs,
):
    asyncio.run(
        local.web_search("q", max_results=0)
    )

    assert fake_ddgs.calls[0][1] == 1

    asyncio.run(
        local.web_search("q", max_results=99)
    )

    assert fake_ddgs.calls[1][1] == 10


def test_web_search_requires_ddgs(
    local,
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "ddgs",
        None,
    )

    with pytest.raises(
        RuntimeError,
        match="pip install ddgs",
    ):
        asyncio.run(
            local.web_search("q")
        )


def test_web_fetch_rejects_other_schemes(local):
    with pytest.raises(
        RuntimeError,
        match="Only http/https",
    ):
        asyncio.run(
            local.web_fetch("ftp://example.test")
        )


def test_web_fetch_strips_markup(
    local,
    monkeypatch,
):
    page = (
        "<html><head>"
        "<style>body{color:red}</style>"
        "<script>var x = 1;</script>"
        "</head><body>retry &amp; backoff"
        "<p>more   text</p></body></html>"
    )

    monkeypatch.setattr(
        local_tools.urllib.request,
        "urlopen",
        lambda request, timeout=None: (
            FakeResponse(page.encode())
        ),
    )

    text = asyncio.run(
        local.web_fetch("https://example.test")
    )

    assert "retry & backoff" in text
    assert "<" not in text
    assert "color:red" not in text
    assert "var x" not in text
    assert "more text" in text


def test_web_fetch_clamps_max_chars(
    local,
    monkeypatch,
):
    page = "x" * 60_000

    monkeypatch.setattr(
        local_tools.urllib.request,
        "urlopen",
        lambda request, timeout=None: (
            FakeResponse(page.encode())
        ),
    )

    small = asyncio.run(
        local.web_fetch(
            "https://example.test",
            max_chars=10,
        )
    )

    assert len(small) == 1000

    large = asyncio.run(
        local.web_fetch(
            "https://example.test",
            max_chars=60_000,
        )
    )

    assert len(large) == 50_000
