"""T8 — the bundled write tools can each be checked, without changing anything.

A check reads the source folder, Git, or GitHub through `gh`, exactly as the
tools do, and never writes.
"""

import asyncio
import json
import subprocess
from datetime import datetime, timezone

import pytest

from conclave.tools import processes

from conclave.tools.changes import Changes, build_change_tools

REAL_RUN = subprocess.run
REAL_COMMAND = processes.run_command


def tools_for(root):
    return {tool.name: tool for tool in build_change_tools(Changes(root))}


def check(tool, arguments, started=None):
    from conclave.actions import ActionContext

    started = started or datetime.now(timezone.utc).isoformat()
    return asyncio.run(tool.check.run(arguments, ActionContext(action_id="a1", started_at=started, arguments=arguments)))


def git(root, *arguments):
    return REAL_RUN(["git", *arguments], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def test_every_write_tool_but_a_command_has_a_check(tmp_path):
    tools = tools_for(tmp_path)
    assert {name for name, tool in tools.items() if tool.check is not None} == {
        "write_file", "edit_file", "git_commit", "push_branch", "open_pull_request", "merge_pull_request"}
    assert tools["run_tests"].check is None
    assert "command" in tools["run_tests"].check_unavailable
    assert tools["push_branch"].check.describe({"branch": "fix/x"})


def test_a_written_file_is_found_by_its_text(tmp_path):
    tool = tools_for(tmp_path)["write_file"]
    arguments = {"path": "notes/release.md", "content": "Fixed duplicates\n"}
    result = check(tool, arguments)
    assert result.result == "absent" and result.exact
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/release.md").write_text("Fixed duplicates\n")
    assert check(tool, arguments).result == "found"
    (tmp_path / "notes/release.md").write_text("Something else\n")
    result = check(tool, arguments)
    assert result.result == "absent" and result.exact
    assert check(tool, {"path": "../outside.md", "content": "x"}).result == "inconclusive"


def test_an_edit_is_found_when_the_new_text_replaced_the_old(tmp_path):
    tool = tools_for(tmp_path)["edit_file"]
    target = tmp_path / "app.py"
    arguments = {"path": "app.py", "old_text": "send(twice)", "new_text": "send(once)"}
    target.write_text("def go():\n    send(twice)\n")
    result = check(tool, arguments)
    assert result.result == "absent" and result.exact
    target.write_text("def go():\n    send(once)\n")
    assert check(tool, arguments).result == "found"
    target.write_text("def go():\n    pass\n")
    assert check(tool, arguments).result == "inconclusive"


def test_a_commit_is_found_by_its_message_and_time(repo):
    tool = tools_for(repo)["git_commit"]
    started = datetime.now(timezone.utc).isoformat()
    arguments = {"message": "Fix the parser"}
    assert check(tool, arguments, started).result == "inconclusive", "a missing commit cannot be told from an amended one"
    (repo / "a.py").write_text("changed\n")
    git(repo, "commit", "-qam", "Fix the parser")
    result = check(tool, arguments, started)
    assert result.result == "found" and git(repo, "rev-parse", "--short", "HEAD") in result.summary
    old = "2020-01-01T00:00:00+00:00"
    assert check(tool, {"message": "first"}, datetime.now(timezone.utc).isoformat()).result == "inconclusive"
    assert check(tool, arguments, old).result == "found"


@pytest.fixture
def remote(repo, tmp_path_factory):
    bare = tmp_path_factory.mktemp("remote") / "origin.git"
    REAL_RUN(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "switch", "-qc", "fix/x")
    return bare


def test_a_push_is_found_when_the_remote_branch_matches_the_local_one(repo, remote):
    tool = tools_for(repo)["push_branch"]
    result = check(tool, {"branch": "fix/x"})
    assert result.result == "absent" and result.exact
    git(repo, "push", "-q", "origin", "fix/x")
    result = check(tool, {"branch": "fix/x"})
    assert result.result == "found" and "fix/x" in result.summary
    (repo / "a.py").write_text("newer\n")
    git(repo, "commit", "-qam", "Newer")
    result = check(tool, {"branch": "fix/x"})
    assert result.result == "absent" and result.exact, "the remote is behind: the push did not land"
    assert check(tool, {"branch": "no/such-branch"}).result == "inconclusive"


def fake_gh(monkeypatch, answers, seen):
    def run(command, **kwargs):
        if command[0] == "gh":
            seen.append(command)
            answer = answers.get(command[2])
            if isinstance(answer, int):
                return subprocess.CompletedProcess(command, answer, "", "gh: failure")
            return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")
        return REAL_COMMAND(command, **kwargs)

    monkeypatch.setattr(processes, "run_command", run)


def test_a_pull_request_is_found_by_its_branches(repo, remote, monkeypatch):
    seen = []
    tool = tools_for(repo)["open_pull_request"]
    fake_gh(monkeypatch, {"list": [{"number": 866, "title": "Fix", "state": "OPEN", "url": "https://github.com/o/r/pull/866"}]}, seen)
    result = check(tool, {"title": "Fix", "base": "main", "branch": "fix/x"})
    assert result.result == "found" and "#866" in result.summary
    assert seen[-1] == ["gh", "pr", "list", "--head", "fix/x", "--base", "main", "--state", "all",
                        "--json", "number,title,state,url", "--limit", "5"]
    check(tool, {"title": "Fix"})
    assert seen[-1][3:7] == ["--head", "fix/x", "--base", "main"], "the current branch and main by default"
    fake_gh(monkeypatch, {"list": []}, seen)
    result = check(tool, {"title": "Fix", "branch": "fix/x"})
    assert result.result == "absent" and result.exact
    fake_gh(monkeypatch, {"list": 1}, seen)
    assert check(tool, {"title": "Fix", "branch": "fix/x"}).result == "inconclusive"


@pytest.mark.parametrize("state, result, exact", [("MERGED", "found", False), ("OPEN", "absent", True),
                                                  ("CLOSED", "inconclusive", False)])
def test_a_merge_is_found_when_github_says_merged(repo, monkeypatch, state, result, exact):
    seen = []
    fake_gh(monkeypatch, {"view": {"state": state, "mergedAt": "2026-11-16T10:41:00Z" if state == "MERGED" else None}}, seen)
    answer = check(tools_for(repo)["merge_pull_request"], {"number": 12})
    assert (answer.result, answer.exact) == (result, exact)
    assert seen[-1] == ["gh", "pr", "view", "12", "--json", "state,mergedAt"]


def test_checks_change_nothing(repo, remote, monkeypatch):
    fake_gh(monkeypatch, {"list": [], "view": {"state": "OPEN"}}, [])
    before = (git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current"))
    tools = tools_for(repo)
    for name, arguments in [("write_file", {"path": "a.py", "content": "x"}),
                            ("edit_file", {"path": "a.py", "old_text": "retry", "new_text": "once"}),
                            ("git_commit", {"message": "m"}), ("push_branch", {"branch": "fix/x"}),
                            ("open_pull_request", {"title": "t"}), ("merge_pull_request", {"number": 1})]:
        check(tools[name], arguments)
    assert (git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current")) == before
