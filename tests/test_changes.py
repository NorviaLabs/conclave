"""Tests for the bundled write tools and the repository they stay inside."""

import asyncio
import subprocess

import pytest

from conclave import Capability, ToolRegistry
from conclave.tools.changes import (
    CHANGE_TOOLS,
    Changes,
    build_change_tools,
)


def test_write_file_stays_inside_the_repository(tmp_path):
    changes = Changes(tmp_path)

    async def exercise():
        assert "Wrote 2 lines" in await changes.write_file("src/app.py", "one\ntwo\n")
        assert (tmp_path / "src/app.py").read_text() == "one\ntwo\n"
        (tmp_path / "link").symlink_to(tmp_path.parent)
        for path in ("../outside.py", "/etc/passwd", ".git/config", "src/../.hidden", "link/escaped.py"):
            with pytest.raises(ValueError):
                await changes.write_file(path, "private")
        with pytest.raises(ValueError):
            await changes.write_file("", "private")

    asyncio.run(exercise())
    assert not (tmp_path.parent / "outside.py").exists()
    assert not (tmp_path / ".hidden").exists()


def test_run_tests_reports_the_exit_status_and_clamps_its_timeout(monkeypatch, tmp_path):
    changes = Changes(tmp_path)
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs, command=command)
        return subprocess.CompletedProcess(command, 1, "FAILED test_x", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert asyncio.run(changes.run_tests("pytest -q tests/test_x.py")) == "exit 1\nFAILED test_x\nboom"
    assert seen["command"] == ["pytest", "-q", "tests/test_x.py"]
    assert seen["cwd"] == tmp_path and seen["timeout"] == 600
    asyncio.run(changes.run_tests("pytest", 99_999))
    assert seen["timeout"] == 1800
    with pytest.raises(ValueError):
        asyncio.run(changes.run_tests("   "))


def test_commits_pushes_pull_requests_and_merges_use_declared_commands(monkeypatch, tmp_path):
    changes = Changes(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        # A branch that does not exist yet is what push_branch creates.
        missing = 1 if command[1:3] == ["rev-parse", "--verify"] else 0
        return subprocess.CompletedProcess(command, missing, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    async def exercise():
        assert await changes.git_commit("Fix the parser", paths="src/app.py") == "ok"
        assert await changes.push_branch("fix/issue-7") == "ok"
        assert await changes.open_pull_request("Fix 7", body="Closes #7", base="main",
                                              branch="fix/issue-7", draft=True) == "ok"
        assert await changes.merge_pull_request(12) == "ok"
        with pytest.raises(ValueError):
            await changes.push_branch("-x")
        with pytest.raises(ValueError):
            await changes.merge_pull_request(12, "fast-forward")
        with pytest.raises(ValueError):
            await changes.git_commit("   ")
        with pytest.raises(ValueError):
            await changes.open_pull_request("   ")
        with pytest.raises(ValueError):
            await changes.write_file("notes.txt", b"bytes")
        with pytest.raises(ValueError):
            await changes.git_commit("Fix", paths=["src/app.py"])

    asyncio.run(exercise())
    assert ["git", "add", "--", "src/app.py"] in calls
    assert ["git", "commit", "--message", "Fix the parser"] in calls
    assert ["git", "switch", "--create", "fix/issue-7"] in calls
    assert ["git", "push", "--set-upstream", "origin", "fix/issue-7"] in calls
    assert ["gh", "pr", "create", "--title", "Fix 7", "--base", "main", "--body", "Closes #7",
            "--head", "fix/issue-7", "--draft"] in calls
    assert ["gh", "pr", "merge", "12", "--squash"] in calls
    assert ["git", "add", "--all"] not in calls


def test_a_failed_command_reports_its_own_message(monkeypatch, tmp_path):
    changes = Changes(tmp_path)

    def refusing(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, "", "fatal: not a git repository")

    monkeypatch.setattr(subprocess, "run", refusing)
    with pytest.raises(RuntimeError, match="fatal: not a git repository"):
        asyncio.run(changes.git_commit("Fix the parser"))

    def silent(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(subprocess, "run", silent)
    with pytest.raises(RuntimeError, match="Command failed: git add --all"):
        asyncio.run(changes.git_commit("Fix the parser"))


def test_the_write_tools_are_one_declared_table(tmp_path):
    tools = build_change_tools(Changes(tmp_path))
    assert {tool.name for tool in tools} == {name for name, *_ in CHANGE_TOOLS}
    assert all(tool.destructive and Capability.WRITE in tool.capabilities for tool in tools)
    assert all(tool.description and tool.parameters for tool in tools)
    # Only the tools that reach a remote need the network capability.
    reaching = {tool.name for tool in tools if Capability.NETWORK in tool.capabilities}
    assert reaching == {"push_branch", "open_pull_request", "merge_pull_request"}
    registry = ToolRegistry(2000)
    for tool in tools:
        registry.register(tool)
    asyncio.run(registry.execute("write_file", {"path": "notes.txt", "content": "hello"}))
    assert (tmp_path / "notes.txt").read_text() == "hello"


def test_an_approved_run_commits_into_the_folder_it_was_authorized_to_read(tmp_path):
    """The whole path, with no deployment setting: registered tools, a bound
    source folder, a person's approval, and a real commit."""
    import subprocess as shell
    from conclave.application.resources import register_source_tools, working_folder
    from conclave.application.runs import RunRepository
    from conclave.application.service import LocalRunHost
    from conftest import ScriptedProvider
    from test_studio_runs import request

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for command in (["git", "init", "-q"],
                    ["git", "config", "user.email", "agent@example.com"],
                    ["git", "config", "user.name", "Agent"]):
        shell.run(command, cwd=checkout, check=True)

    tools = ToolRegistry(20000)
    register_source_tools(tools)
    value = request()
    value["definition"]["workflow"] = value["definition"]["workflow"][:1]
    value["definition"]["agents"] = value["definition"]["agents"][:1]
    value["definition"]["agents"][0]["tools"] = ["write_file", "git_commit"]
    value["resources"] = {"source": "default"}
    provider = ScriptedProvider(scripts={"writer": [
        {"type": "tool", "name": "write_file", "arguments": {"path": "app.py", "content": 'print("hi")\n'}},
        {"type": "tool", "name": "git_commit", "arguments": {"message": "Add app.py"}},
        {"type": "final", "content": "Done", "text": "Committed"},
    ]})
    host = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider, tools, reviewer="operator",
                        resources=working_folder(checkout), source_extensions={".md"})

    async def exercise():
        _, run = await host.handle("start", value)
        record = host.repository.get(run["id"])
        for _ in range(400):
            for pending in host.tool_approvals.list(run["id"], "operator"):
                host.tool_approvals.decide(run["id"], pending["id"], "operator", True)
            record = host.repository.get(run["id"])
            if record["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(.005)
        assert record["status"] == "completed"
        await host.close()

    asyncio.run(exercise())
    assert (checkout / "app.py").read_text() == 'print("hi")\n'
    shown = shell.run(["git", "show", "--stat", "--oneline", "HEAD"], cwd=checkout,
                      capture_output=True, text=True, check=True).stdout
    assert "app.py" in shown and "Add app.py" in shown
    assert shell.run(["git", "status", "--porcelain"], cwd=checkout,
                     capture_output=True, text=True, check=True).stdout == ""


def _git(checkout, *command):
    return subprocess.run(["git", *command], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()


def test_a_commit_never_lands_on_the_remotes_default_branch(tmp_path):
    origin, checkout = tmp_path / "origin.git", tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    for command in (("config", "user.email", "agent@example.com"), ("config", "user.name", "Agent"),
                    ("commit", "-q", "--allow-empty", "-m", "start"), ("push", "-q", "origin", "main"),
                    ("remote", "set-head", "origin", "main")):
        _git(checkout, *command)
    changes = Changes(checkout)
    (checkout / "app.py").write_text("print('hi')\n")

    with pytest.raises(ValueError, match="remote's default branch"):
        asyncio.run(changes.git_commit("Add app.py"))
    assert _git(checkout, "rev-list", "--count", "main") == "1"

    asyncio.run(changes.git_commit("Add app.py", branch="fix/issue-7"))
    assert _git(checkout, "branch", "--show-current") == "fix/issue-7"
    assert _git(checkout, "rev-list", "--count", "main") == "1"
    assert _git(checkout, "log", "-1", "--format=%s", "fix/issue-7") == "Add app.py"

    # A later commit on the same branch switches to it rather than creating it.
    (checkout / "app.py").write_text("print('bye')\n")
    asyncio.run(changes.git_commit("Say bye", branch="fix/issue-7"))
    assert _git(checkout, "rev-list", "--count", "fix/issue-7") == "3"
    with pytest.raises(ValueError):
        asyncio.run(changes.git_commit("Bad", branch="-x"))

    # Back on main, a later run reuses the branch name: the old branch's commits
    # are not this checkout's, so committing there is refused rather than
    # carrying them into a new pull request.
    _git(checkout, "switch", "-q", "main")
    (checkout / "other.py").write_text("print('other')\n")
    with pytest.raises(ValueError, match="already exists with commits"):
        asyncio.run(changes.git_commit("Add other.py", branch="fix/issue-7"))
    assert _git(checkout, "branch", "--show-current") == "main"
    assert _git(checkout, "rev-list", "--count", "fix/issue-7") == "3"
    asyncio.run(changes.git_commit("Add other.py", branch="fix/issue-8"))
    assert _git(checkout, "rev-list", "--count", "fix/issue-8") == "2"


def test_edit_file_replaces_one_exact_passage(tmp_path):
    changes = Changes(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def a():\n    return 1\n\ndef b():\n    return 1\n")

    async def exercise():
        assert await changes.edit_file("src/app.py", "def a():\n    return 1", "def a():\n    return 2") == "Replaced 1 occurrence in src/app.py"
        assert (tmp_path / "src/app.py").read_text() == "def a():\n    return 2\n\ndef b():\n    return 1\n"
        # Ambiguous or missing text changes nothing.
        for old in ("return", "return 9"):
            with pytest.raises(ValueError, match="exactly once"):
                await changes.edit_file("src/app.py", old, "x")
        with pytest.raises(ValueError, match="does not exist"):
            await changes.edit_file("src/missing.py", "a", "b")
        with pytest.raises(ValueError):
            await changes.edit_file("src/app.py", "", "b")
        for path in ("../outside.py", ".git/config"):
            with pytest.raises(ValueError):
                await changes.edit_file(path, "a", "b")

    asyncio.run(exercise())
    assert (tmp_path / "src/app.py").read_text() == "def a():\n    return 2\n\ndef b():\n    return 1\n"
