"""Repository-scoped tools: files, Git, GitHub and the web."""

import asyncio
import html
import json
import re
import subprocess
import urllib.request

from pathlib import Path
from urllib.parse import urlparse


# =============================================================================
# Reading text out of a repository
# =============================================================================


def read_text_or_none(
    path: Path,
) -> str | None:
    """Read a text file, or answer None for a binary one.

    A NUL byte is valid UTF-8, so `read_text(errors="replace")` hands it
    straight back. That matters more than it looks: a NUL byte cannot
    survive a `subprocess` argument, so a binary file read into a prompt
    used to kill the whole agent run at the model boundary instead of
    costing one tool call.
    """

    data = path.read_bytes()

    if b"\x00" in data:
        return None

    return data.decode(
        "utf-8",
        errors="replace",
    )


# =============================================================================
# Local tools
# =============================================================================


class LocalTools:
    def __init__(
        self,
        repo: Path,
    ):
        self.repo = repo.resolve()

    def _safe_path(
        self,
        relative: str,
    ) -> Path:
        path = (
            self.repo / relative
        ).resolve()

        try:
            path.relative_to(
                self.repo
            )

        except ValueError as exc:
            raise RuntimeError(
                "Path escapes repository"
            ) from exc

        return path

    async def list_files(
        self,
        path: str = ".",
        max_entries: int = 200,
    ) -> str:
        # Models occasionally emit JSON null for optional arguments. Treat it
        # like an omitted argument so the tool's declared default still wins.
        if max_entries is None:
            max_entries = 200
        root = self._safe_path(path)

        if not root.exists():
            return (
                f"Path does not exist: {path}"
            )

        ignored = {
            ".git",
            "node_modules",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        }

        entries: list[str] = []

        for item in root.rglob("*"):
            if any(
                part in ignored
                for part in item.parts
            ):
                continue

            try:
                rel = item.relative_to(
                    self.repo
                )

            except ValueError:
                continue

            entries.append(
                str(rel)
                + (
                    "/"
                    if item.is_dir()
                    else ""
                )
            )

            if (
                len(entries)
                >= max_entries
            ):
                break

        return "\n".join(entries)

    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 250,
    ) -> str:
        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = 250
        file_path = self._safe_path(
            path
        )

        if not file_path.is_file():
            return f"Not a file: {path}"

        text = read_text_or_none(
            file_path
        )

        if text is None:
            return (
                f"Binary file, not readable as "
                f"text: {path} "
                f"({file_path.stat().st_size} "
                "bytes). Do not read it again."
            )

        lines = text.splitlines()

        start = max(
            1,
            start_line,
        )

        end = min(
            len(lines),
            max(
                start,
                end_line,
            ),
        )

        return "\n".join(
            f"{i}: {lines[i - 1]}"
            for i in range(
                start,
                end + 1,
            )
        )

    async def search_files(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        if max_results is None:
            max_results = 50
        root = self._safe_path(path)

        pattern = re.compile(
            query,
            re.IGNORECASE,
        )

        ignored = {
            ".git",
            "node_modules",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        }

        results: list[str] = []

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue

            if any(
                part in ignored
                for part in file_path.parts
            ):
                continue

            try:
                text = read_text_or_none(
                    file_path
                )

            except OSError:
                continue

            if text is None:
                continue

            for (
                line_number,
                line,
            ) in enumerate(
                text.splitlines(),
                start=1,
            ):
                if not pattern.search(
                    line
                ):
                    continue

                rel = (
                    file_path
                    .relative_to(
                        self.repo
                    )
                )

                results.append(
                    f"{rel}:"
                    f"{line_number}: "
                    f"{line.strip()}"
                )

                if (
                    len(results)
                    >= max_results
                ):
                    return "\n".join(
                        results
                    )

        return (
            "\n".join(results)
            or "No matches"
        )

    async def _run(
        self,
        command: list[str],
    ) -> str:
        def run() -> str:
            completed = subprocess.run(
                command,
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=60,
            )

            output = (
                completed.stdout.strip()
            )

            error = (
                completed.stderr.strip()
            )

            if (
                completed.returncode
                != 0
            ):
                raise RuntimeError(
                    error
                    or (
                        "Command failed: "
                        + " ".join(
                            command
                        )
                    )
                )

            return (
                output
                or "(no output)"
            )

        return await asyncio.to_thread(
            run
        )

    async def git_status(
        self,
    ) -> str:
        return await self._run(
            [
                "git",
                "status",
                "--short",
                "--branch",
            ]
        )

    async def git_log(
        self,
        path: str = ".",
        max_count: int = 20,
    ) -> str:
        if max_count is None:
            max_count = 20
        safe = self._safe_path(
            path
        )

        rel = (
            str(
                safe.relative_to(
                    self.repo
                )
            )
            or "."
        )

        return await self._run(
            [
                "git",
                "log",
                (
                    "--max-count="
                    f"{max(1, min(max_count, 100))}"
                ),
                "--date=short",
                (
                    "--pretty=format:"
                    "%h %ad %an %s"
                ),
                "--",
                rel,
            ]
        )

    async def git_diff(
        self,
        revision: str = "HEAD",
        path: str = ".",
    ) -> str:
        safe = self._safe_path(
            path
        )

        rel = (
            str(
                safe.relative_to(
                    self.repo
                )
            )
            or "."
        )

        return await self._run(
            [
                "git",
                "diff",
                revision,
                "--",
                rel,
            ]
        )

    async def git_show(
        self,
        revision: str,
        path: str | None = None,
    ) -> str:
        command = [
            "git",
            "show",
            "--stat",
            "--patch",
            revision,
        ]

        if path:
            safe = self._safe_path(
                path
            )

            command.extend(
                [
                    "--",
                    str(
                        safe.relative_to(
                            self.repo
                        )
                    ),
                ]
            )

        return await self._run(
            command
        )

    async def github_issue(
        self,
        number: int,
    ) -> str:
        return await self._run(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--comments",
                "--json",
                (
                    "number,title,state,"
                    "body,author,comments,"
                    "labels,url"
                ),
            ]
        )

    async def github_pr(
        self,
        number: int,
    ) -> str:
        return await self._run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--comments",
                "--json",
                (
                    "number,title,state,"
                    "body,author,comments,"
                    "reviews,commits,"
                    "files,url"
                ),
            ]
        )

    async def github_search_issues(
        self,
        query: str,
        limit: int = 20,
    ) -> str:
        return await self._run(
            [
                "gh",
                "search",
                "issues",
                query,
                "--limit",
                str(
                    max(
                        1,
                        min(
                            limit,
                            100,
                        ),
                    )
                ),
                "--json",
                (
                    "number,title,state,"
                    "url,repository"
                ),
            ]
        )

    async def github_search_prs(
        self,
        query: str,
        limit: int = 20,
    ) -> str:
        return await self._run(
            [
                "gh",
                "search",
                "prs",
                query,
                "--limit",
                str(
                    max(
                        1,
                        min(
                            limit,
                            100,
                        ),
                    )
                ),
                "--json",
                (
                    "number,title,state,"
                    "url,repository"
                ),
            ]
        )

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
    ) -> str:
        if max_results is None:
            max_results = 5
        try:
            from ddgs import DDGS

        except ImportError as exc:
            raise RuntimeError(
                "web_search requires: "
                "pip install ddgs"
            ) from exc

        def search() -> str:
            results = DDGS(
                timeout=10
            ).text(
                query,
                max_results=max(
                    1,
                    min(
                        max_results,
                        10,
                    ),
                ),
            )

            return json.dumps(
                results,
                indent=2,
                ensure_ascii=False,
            )

        return await asyncio.to_thread(
            search
        )

    async def web_fetch(
        self,
        url: str,
        max_chars: int = 15_000,
    ) -> str:
        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise RuntimeError(
                "Only http/https URLs "
                "are allowed"
            )

        def fetch() -> str:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "toy-multi-agent-poc"
                    )
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                raw = response.read(
                    1_000_000
                ).decode(
                    errors="replace"
                )

            raw = re.sub(
                (
                    r"(?is)<script.*?>"
                    r".*?</script>"
                ),
                " ",
                raw,
            )

            raw = re.sub(
                (
                    r"(?is)<style.*?>"
                    r".*?</style>"
                ),
                " ",
                raw,
            )

            text = re.sub(
                r"(?s)<[^>]+>",
                " ",
                raw,
            )

            text = html.unescape(
                text
            )

            text = re.sub(
                r"\s+",
                " ",
                text,
            ).strip()

            return text[
                :max(
                    1000,
                    min(
                        max_chars,
                        50_000,
                    ),
                )
            ]

        return await asyncio.to_thread(
            fetch
        )
