"""Tests for the command line: parsing, provider choice and entry point."""

import asyncio
import sys

import pytest

from pathlib import Path

from conclave import approve_all

from investigation import cli
from conclave.providers import (
    OllamaProvider,
    OpenCodeProvider,
)


class FakeAsyncio:
    """Stands in for the asyncio module inside cli.run()."""

    def __init__(self, exception=None):
        self.exception = exception
        self.ran = False

    def run(self, coroutine):
        self.ran = True
        coroutine.close()

        if self.exception is not None:
            raise self.exception


@pytest.fixture
def captured_run(monkeypatch):
    calls = []

    async def fake_run_investigation(
        provider,
        tools,
        question,
        **kwargs,
    ):
        calls.append(
            {
                "provider": provider,
                "tools": tools,
                "question": question,
                **kwargs,
            }
        )

    monkeypatch.setattr(
        cli,
        "run_investigation",
        fake_run_investigation,
    )

    return calls


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["conclave"],
    )

    args = cli.parse_args()

    assert args.question == []
    assert args.repo == "."
    assert args.provider == "opencode"
    assert args.model is None
    assert args.ollama_url == (
        "http://127.0.0.1:11434"
    )


def test_parse_args_overrides(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "why",
            "now",
            "--repo",
            "/x",
            "--provider",
            "ollama",
            "--model",
            "m",
            "--ollama-url",
            "http://host:1",
        ],
    )

    args = cli.parse_args()

    assert args.question == ["why", "now"]
    assert args.repo == "/x"
    assert args.provider == "ollama"
    assert args.model == "m"
    assert args.ollama_url == "http://host:1"


def test_parse_args_rejects_unknown_provider(
    monkeypatch,
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["conclave", "--provider", "claude"],
    )

    with pytest.raises(SystemExit):
        cli.parse_args()


def test_make_provider_opencode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "--model",
            "fast",
        ],
    )

    provider = cli.make_provider(
        cli.parse_args(),
        Path("/repo"),
    )

    assert isinstance(provider, OpenCodeProvider)
    assert provider.model == "fast"
    assert provider.cwd == Path("/repo")


def test_make_provider_ollama(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "--provider",
            "ollama",
            "--model",
            "qwen3.6",
            "--ollama-url",
            "http://host:9",
        ],
    )

    provider = cli.make_provider(
        cli.parse_args(),
        Path("/repo"),
    )

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3.6"
    assert provider.base_url == "http://host:9"


def test_make_provider_ollama_default_model(
    monkeypatch,
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["conclave", "--provider", "ollama"],
    )

    provider = cli.make_provider(
        cli.parse_args(),
        Path("/repo"),
    )

    assert provider.model == "qwen3.6"


def test_main_runs_the_investigation(
    monkeypatch,
    captured_run,
    repo,
    capsys,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "why",
            "now",
            "--repo",
            str(repo),
        ],
    )

    asyncio.run(cli.main())

    assert len(captured_run) == 1

    run = captured_run[0]

    assert run["question"] == "why now"
    assert "read_file" in run["tools"].tools

    # No limits and no approver were asked for, so nothing is bounded
    # beyond the defaults and destructive calls stay refused.
    assert run["budget"].tool_calls is None
    assert run["approver"] is None
    assert run["render"].stream is sys.stdout

    output = capsys.readouterr().out

    assert "QUESTION\nwhy now" in output
    assert f"WORKSPACE\n{repo}" in output
    assert "MODEL PROVIDER\nopencode" in output


def test_main_uses_the_default_question(
    monkeypatch,
    captured_run,
    repo,
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["conclave", "--repo", str(repo)],
    )

    asyncio.run(cli.main())

    assert "architectural weakness" in (
        captured_run[0]["question"]
    )


def test_main_rejects_a_missing_repository(
    monkeypatch,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "--repo",
            "/definitely/not/here",
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="Repository does not exist",
    ):
        asyncio.run(cli.main())


def test_run_executes_main(monkeypatch):
    fake = FakeAsyncio()

    monkeypatch.setattr(
        cli,
        "asyncio",
        fake,
    )

    cli.run()

    assert fake.ran


def test_run_handles_keyboard_interrupt(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli,
        "asyncio",
        FakeAsyncio(KeyboardInterrupt()),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.run()

    assert exit_info.value.code == 130
    assert "Cancelled." in capsys.readouterr().out


def test_run_handles_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "asyncio",
        FakeAsyncio(RuntimeError("kaboom")),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.run()

    assert exit_info.value.code == 1
    assert "ERROR: kaboom" in (
        capsys.readouterr().err
    )


def test_parse_args_reads_the_budget_flags():
    args = cli.parse_args(
        [
            "--max-tool-calls",
            "5",
            "--max-seconds",
            "2.5",
            "--max-tokens",
            "1000",
            "--max-cost",
            "0.25",
            "--approve-destructive",
        ]
    )

    budget = cli.make_budget(args)

    assert budget.tool_calls == 5
    assert budget.wall_clock_seconds == 2.5
    assert budget.tokens == 1000
    assert budget.cost_usd == 0.25

    assert cli.make_approver(args) is approve_all


def test_the_defaults_leave_every_dimension_unbounded():
    args = cli.parse_args([])

    budget = cli.make_budget(args)

    assert budget.limit("tool_calls") is None
    assert budget.limit("wall_clock_seconds") is None
    assert budget.limit("tokens") is None
    assert budget.limit("cost_usd") is None

    # Without an approver, a destructive call is refused.
    assert cli.make_approver(args) is None


def test_the_cli_passes_its_limits_to_the_run(
    monkeypatch,
    captured_run,
    repo,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "conclave",
            "why",
            "--repo",
            str(repo),
            "--max-tool-calls",
            "3",
            "--approve-destructive",
        ],
    )

    asyncio.run(cli.main())

    run = captured_run[0]

    assert run["budget"].tool_calls == 3
    assert run["approver"] is approve_all
