"""Tests for the command line: parsing, provider choice and entry point."""

import asyncio
import sys

import pytest

from pathlib import Path

from conclave import cli
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
    ):
        calls.append((provider, tools, question))

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

    _, tools, question = captured_run[0]

    assert question == "why now"
    assert "read_file" in tools.tools

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
        captured_run[0][2]
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
