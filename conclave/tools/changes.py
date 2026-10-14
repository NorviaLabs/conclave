"""The bundled tools that change a repository: edits, tests, commits, pushes
and pull requests.

These are the only bundled tools that declare WRITE. Every call passes through
an approver: per-call human approval by default, or the application's explicit
up-front autonomous authorization for declared tools with known capabilities.

Nothing here is switched on by a setting: the application layer registers these
against the source folder a run was authorized to use, exactly as it registers
the read tools, so a run may only change the folder it may already read. Every
path is resolved and checked back into that folder, and hidden entries such as
`.git` are refused.

Nothing here authenticates either. `git push` and `gh` use the accounts this
machine is already signed in to, exactly like the bundled read tools.
"""

import asyncio
import re
import shlex
import subprocess

from pathlib import Path
from typing import Any

from ..spec import (
    Capability,
    Tool,
)

# Writing a file changes the repository and nothing else.
WRITES_FILES = frozenset(
    {
        Capability.WRITE,
    }
)

# Running a command changes the repository through a shell.
WRITES_AND_SHELLS = frozenset(
    {
        Capability.WRITE,
        Capability.SHELL,
    }
)

# Pushing and merging also reach a remote.
WRITES_REMOTELY = frozenset(
    {
        Capability.WRITE,
        Capability.SHELL,
        Capability.NETWORK,
    }
)

BRANCH_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]{0,200}"
)

MERGE_METHODS = (
    "merge",
    "squash",
    "rebase",
)

# A test run may legitimately take minutes; a hung one may not hang the run.
DEFAULT_TIMEOUT = 600
MAXIMUM_TIMEOUT = 1800


class Changes:
    """One repository, as a set of changes a run may propose.

    Every path is resolved against the repository and checked back into it, so
    a model cannot reach outside it with `..`, an absolute path or a symlink.
    Hidden entries are refused for the same reason the source tools refuse
    them: `.git` is not a file an agent should be editing.
    """

    def __init__(
        self,
        root: Path,
    ):
        self.root = Path(root).resolve()

    def _writable(
        self,
        relative: str,
    ) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("Provide a path inside the repository")

        path = (
            self.root / relative
        ).resolve()

        try:
            inside = path.relative_to(
                self.root
            )

        except ValueError as exc:
            raise ValueError(
                "Choose a path inside the repository"
            ) from exc

        if any(
            part.startswith(".")
            for part in inside.parts
        ):
            raise ValueError(
                "Choose a visible path inside the repository;"
                " hidden files such as .git are not writable"
            )

        return path

    async def _execute(
        self,
        command: list[str],
        timeout: int,
    ):
        def run():
            return subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        return await asyncio.to_thread(
            run
        )

    async def _checked(
        self,
        command: list[str],
        timeout: int,
    ) -> str:
        completed = await self._execute(
            command,
            timeout,
        )

        output = (
            completed.stdout or ""
        ).strip()

        error = (
            completed.stderr or ""
        ).strip()

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

    async def write_file(
        self,
        path: str,
        content: str,
    ) -> str:
        if not isinstance(content, str):
            raise ValueError("Provide the file text")

        target = self._writable(
            path
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Text only: a binary blob cannot round-trip through a prompt.
        target.write_text(
            content,
            encoding="utf-8",
        )

        return (
            f"Wrote {len(content.splitlines())} lines to {path}"
        )

    async def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> str:
        # A focused change to a large file. Without it an agent must resend
        # the whole file, or reach for a script through run_tests, which is
        # how a source file once ended up deleted.
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ValueError("Provide the exact text to replace and its replacement")

        target = self._writable(
            path
        )

        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ValueError(f"{path} does not exist; create it with write_file") from None

        found = text.count(old_text)
        if found != 1:
            raise ValueError(
                f"old_text appears {found} times in {path}; it must appear exactly once. "
                "Include more surrounding lines to make it unique."
            )

        target.write_text(
            text.replace(old_text, new_text, 1),
            encoding="utf-8",
        )

        return f"Replaced 1 occurrence in {path}"

    async def run_tests(
        self,
        command: str,
        timeout: int | None = DEFAULT_TIMEOUT,
    ) -> str:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Provide the command to run")

        limit = (
            DEFAULT_TIMEOUT
            if timeout is None
            else max(
                1,
                min(
                    int(timeout),
                    MAXIMUM_TIMEOUT,
                ),
            )
        )

        completed = await self._execute(
            shlex.split(
                command
            ),
            limit,
        )

        # A failing suite is a result, not a tool failure: the agent decides
        # what a red test means, and the exit status travels with the output.
        output = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part and part.strip()
        )

        return (
            f"exit {completed.returncode}\n{output}"
            if output
            else f"exit {completed.returncode}"
        )

    async def _on_branch(
        self,
        branch: str | None,
    ) -> None:
        """Put the commit on its own branch, never on the remote's default.

        A commit on the checked-out default branch leaves that branch ahead of
        its remote with work nobody reviewed, and a branch pushed afterwards
        only copies it. So a named branch is switched to (created from the
        current commit when it is new, carrying the uncommitted changes), and
        a commit onto origin's default branch is refused.
        """

        if branch is not None:
            name = str(branch).strip()

            if not BRANCH_NAME.fullmatch(name):
                raise ValueError("Provide a branch name such as fix/issue-7")

            exists = await self._execute(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    "refs/heads/" + name,
                ],
                30,
            )

            current = (await self._execute(["git", "branch", "--show-current"], 30)).stdout.strip()

            # An existing branch left by earlier work can hold commits this
            # checkout never had; switching to it would put them in the commit's
            # pull request. Continuing on the branch already checked out is fine.
            if exists.returncode == 0 and current != name:
                extra = await self._execute(["git", "rev-list", "--count", "HEAD.." + name], 30)
                if extra.returncode != 0 or extra.stdout.strip() != "0":
                    raise ValueError(
                        f"Branch {name} already exists with commits this checkout does not have; "
                        "committing there would carry them along. Pass a new branch name."
                    )

            await self._checked(
                ["git", "switch", name]
                if exists.returncode == 0
                else ["git", "switch", "--create", name],
                60,
            )

        current = (await self._execute(["git", "branch", "--show-current"], 30)).stdout.strip()
        default = (await self._execute(["git", "symbolic-ref", "--quiet", "--short",
                                        "refs/remotes/origin/HEAD"], 30)).stdout.strip()

        if current and default.startswith("origin/") and current == default.removeprefix("origin/"):
            raise ValueError(
                f"Refusing to commit on {current}, the remote's default branch. "
                "Pass branch, such as fix/issue-7, to commit on a branch of its own."
            )

    async def git_commit(
        self,
        message: str,
        paths: str | None = None,
        branch: str | None = None,
    ) -> str:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Provide a commit message")

        if paths is not None and not isinstance(paths, str):
            raise ValueError("Give comma-separated paths, or omit them to stage every change")

        await self._on_branch(
            branch
        )

        targets = [
            item.strip()
            for item in (paths or "").split(",")
            if item.strip()
        ]

        await self._checked(
            [
                "git",
                "add",
                "--all",
            ]
            if not targets
            else [
                "git",
                "add",
                "--",
                *targets,
            ],
            60,
        )

        await self._checked(
            [
                "git",
                "commit",
                "--message",
                message,
            ],
            120,
        )

        return await self._checked(
            [
                "git",
                "rev-parse",
                "--short",
                "HEAD",
            ],
            60,
        )

    async def push_branch(
        self,
        branch: str,
    ) -> str:
        name = str(
            branch or ""
        ).strip()

        # A leading dash would be read as an option, not a branch.
        if not BRANCH_NAME.fullmatch(name):
            raise ValueError("Provide a branch name such as fix/issue-7")

        exists = await self._execute(
            [
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                "refs/heads/" + name,
            ],
            30,
        )

        # The commits an approver just read are what a new branch points at.
        if (
            exists.returncode
            != 0
        ):
            await self._checked(
                [
                    "git",
                    "switch",
                    "--create",
                    name,
                ],
                60,
            )

        return await self._checked(
            [
                "git",
                "push",
                "--set-upstream",
                "origin",
                name,
            ],
            300,
        )

    async def open_pull_request(
        self,
        title: str,
        body: str | None = None,
        base: str = "main",
        branch: str | None = None,
        draft: Any = None,
    ) -> str:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Provide a pull request title")

        command = [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--base",
            base or "main",
        ]

        if body:
            command += [
                "--body",
                body,
            ]

        if branch:
            command += [
                "--head",
                branch,
            ]

        if draft in (
            True,
            "true",
            "True",
            "yes",
        ):
            command.append(
                "--draft"
            )

        return await self._checked(
            command,
            180,
        )

    async def merge_pull_request(
        self,
        number: int,
        method: str = "squash",
    ) -> str:
        chosen = str(
            method or "squash"
        ).lower()

        if chosen not in MERGE_METHODS:
            raise ValueError(
                "Choose merge, squash or rebase"
            )

        return await self._checked(
            [
                "gh",
                "pr",
                "merge",
                str(number),
                "--" + chosen,
            ],
            300,
        )


# The write tools, as one table: name, description, parameters, capabilities.
# The application layer registers these against the folder a run was authorized
# to use, which is where every implementation below is rooted.
CHANGE_TOOLS = (
    (
        "write_file",
        "Create or replace a text file inside the repository.",
        {
            "path": "string",
            "content": "string",
        },
        WRITES_FILES,
    ),
    (
        "edit_file",
        "Replace one exact passage of a text file inside the repository. old_text must appear "
        "exactly once; include surrounding lines to make it unique.",
        {
            "path": "string",
            "old_text": "string",
            "new_text": "string",
        },
        WRITES_FILES,
    ),
    (
        "run_tests",
        "Run a command in the repository and return its exit status and output.",
        {
            "command": "string",
            "timeout": "integer, optional",
        },
        WRITES_AND_SHELLS,
    ),
    (
        "git_commit",
        "Stage the repository's changes and commit them on a branch of their own. "
        "Pass branch to switch to it, creating it when new; a commit on the remote's default branch is refused.",
        {
            "message": "string",
            "paths": "string, optional",
            "branch": "string, optional",
        },
        WRITES_AND_SHELLS,
    ),
    (
        "push_branch",
        "Put the committed work on a branch of the origin remote, creating the branch if it does not exist.",
        {
            "branch": "string",
        },
        WRITES_REMOTELY,
    ),
    (
        "open_pull_request",
        "Open a pull request from a pushed branch.",
        {
            "title": "string",
            "body": "string, optional",
            "base": "string, optional",
            "branch": "string, optional",
            "draft": "boolean, optional",
        },
        WRITES_REMOTELY,
    ),
    (
        "merge_pull_request",
        "Merge a pull request on GitHub.",
        {
            "number": "integer",
            "method": "string, optional",
        },
        WRITES_REMOTELY,
    ),
)

CHANGE_TOOL_NAMES = frozenset(
    name for name, *_ in CHANGE_TOOLS
)


def build_change_tools(
    changes: Changes,
) -> tuple[Tool, ...]:
    """One repository's write tools; `changes` supplies every implementation."""

    return tuple(
        Tool(
            name,
            description,
            parameters,
            getattr(
                changes,
                name,
            ),
            capabilities,
        )
        for name, description, parameters, capabilities in CHANGE_TOOLS
    )
