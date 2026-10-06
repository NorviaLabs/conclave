"""Tests for the command line: parsing, provider choice and entry point."""

import asyncio
import sys

import pytest

from pathlib import Path

from conftest import make_budget

from conclave import approve_all

from investigation import cli
from conclave.providers import (
    CodexProvider,
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
        ["investigation"],
    )

    args = cli.parse_args()

    assert args.question == []
    assert args.repo == "."
    # No provider flag means "whichever backend this computer has",
    # so a first run needs no configuration at all.
    assert args.provider is None
    assert args.model is None
    assert args.ollama_url == (
        "http://127.0.0.1:11434"
    )


def test_parse_args_overrides(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "investigation",
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
        ["investigation", "--provider", "claude"],
    )

    with pytest.raises(SystemExit):
        cli.parse_args()


def test_help_belongs_to_the_application(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.parse_args(["--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: investigation " in output
    assert "--repo" in output


def installed_backend(monkeypatch, *names):
    """The backends this computer has, for provider-selection tests."""

    from conclave import providers

    monkeypatch.setattr(providers.shutil, "which", lambda name: name if name in names else None)


@pytest.mark.parametrize(
    "model",
    ["fast", "openai/gpt-5.2-codex"],
)
def test_make_provider_opencode(monkeypatch, model):
    installed_backend(monkeypatch, "opencode")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "investigation",
            "--provider",
            "opencode",
            "--model",
            model,
        ],
    )

    provider, name = cli.make_provider(
        cli.parse_args(),
        Path("/repo"),
    )

    assert isinstance(provider, OpenCodeProvider)
    assert name == "opencode"
    assert provider.model == model
    assert provider.cwd == Path("/repo")


def test_make_provider_ollama(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "investigation",
            "--provider",
            "ollama",
            "--model",
            "qwen3.6",
            "--ollama-url",
            "http://host:9",
        ],
    )

    provider, name = cli.make_provider(cli.parse_args(), Path("/repo"))

    assert isinstance(provider, OllamaProvider) and name == "ollama"
    assert provider.model == "qwen3.6"
    assert provider.base_url == "http://host:9"


@pytest.mark.parametrize("model", [None, "gpt-5.2-codex"])
def test_make_provider_codex(monkeypatch, model):
    installed_backend(monkeypatch, "codex")

    argv = ["--provider", "codex", "--model-timeout", "42"]
    if model:
        argv.extend(["--model", model])
    provider, name = cli.make_provider(cli.parse_args(argv), Path("."))
    assert isinstance(provider, CodexProvider)
    assert name == "codex"
    assert provider.model == model
    assert provider.timeout == 42


def test_make_provider_uses_an_installed_ollama_model(monkeypatch):
    from conclave import providers

    monkeypatch.setattr(providers, "installed_ollama_models", lambda *args, **kwargs: ["local-model:8b"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["investigation", "--provider", "ollama"],
    )

    provider, name = cli.make_provider(cli.parse_args(), Path("/repo"))

    # Never a guessed model: Ollama is asked what it has installed.
    assert (name, provider.model) == ("ollama", "local-model:8b")


def test_make_provider_detects_what_is_installed(monkeypatch):
    installed_backend(monkeypatch, "codex", "opencode")

    args = cli.parse_args(["investigation"])

    provider, name = cli.make_provider(args, Path("/repo"))

    assert name == "codex" and isinstance(provider, CodexProvider)


def test_make_provider_says_what_to_install_when_there_is_nothing(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["investigation"])

    with pytest.raises(RuntimeError, match="No AI backend was found"):
        cli.make_provider(cli.parse_args(), Path("/repo"))


def test_main_runs_the_investigation(
    monkeypatch,
    captured_run,
    repo,
    capsys,
):
    installed_backend(monkeypatch, "opencode")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "investigation",
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
    installed_backend(monkeypatch, "opencode")

    monkeypatch.setattr(
        sys,
        "argv",
        ["investigation", "--repo", str(repo)],
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
            "investigation",
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
    installed_backend(monkeypatch, "opencode")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "investigation",
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


def test_cli_passes_all_application_overrides(monkeypatch, captured_run, repo):
    installed_backend(monkeypatch, "opencode")

    overrides = {
        "max-rounds": "16",
        "max-delegations-per-round": "5",
        "max-delegations": "60",
        "max-per-agent": "12",
        "max-depth": "4",
        "max-tool-steps": "30",
        "max-notes": "24",
        "max-note-chars": "600",
        "max-tool-output-chars": "40000",
        "fan-out-limit": "2",
        "model-timeout": "600",
    }
    argv = ["investigation", "--repo", str(repo)]
    for name, value in overrides.items():
        argv.extend(["--" + name, value])
    monkeypatch.setattr(sys, "argv", argv)

    asyncio.run(cli.main())
    run = captured_run[0]
    assert run["provider"].timeout == 600
    assert run["max_rounds"] == 16
    assert run["max_delegations_per_round"] == 5
    assert run["tools"].max_output_chars == 40000
    for name in ("max_delegations", "max_per_agent", "max_depth"):
        assert getattr(run["budget"], name) == int(overrides[name.replace("_", "-")])
    for name in (
        "max_tool_steps", "max_notes", "max_note_chars",
        "max_tool_output_chars", "fan_out_limit",
    ):
        assert getattr(run["config"], name) == int(overrides[name.replace("_", "-")])

    # A subsequent run gets the original defaults, not the last run's overrides.
    defaults = cli.parse_args([])
    assert cli.make_run_config(defaults) == cli.RUN_CONFIG
    assert cli.make_budget(defaults) == cli.make_budget(cli.parse_args([]))
    assert cli.make_budget(defaults).max_delegations is None
    from conclave import providers

    monkeypatch.setattr(providers, "installed_ollama_models", lambda *args, **kwargs: ["local-model"])
    ollama, name = cli.make_provider(
        cli.parse_args(["--provider", "ollama", "--model-timeout", "42"]), repo
    )
    assert (name, ollama.timeout, ollama.model) == ("ollama", 42, "local-model")


@pytest.mark.parametrize("flag,value", [
    ("--max-rounds", "0"),
    ("--fan-out-limit", "0"),
    ("--max-notes", "-1"),
    ("--max-delegations", "-1"),
    ("--model-timeout", "0"),
    ("--model-timeout", "nan"),
    ("--max-seconds", "inf"),
    ("--max-cost", "-0.1"),
])
def test_cli_rejects_invalid_limits(flag, value):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args([flag, value])
    assert exc.value.code == 2
