"""Command-line entry point and provider selection.

This is the reference front end: it shows what a caller does with the
library — pick a provider, build a tool registry, set a budget, decide
whether anything is allowed to change the world, and hand the whole thing
to `run_investigation`.
"""

import argparse
import asyncio
import math
import sys

from dataclasses import fields, replace
from pathlib import Path

from conclave import (
    Approver,
    ModelProvider,
    RunBudget,
    RunConfig,
    approve_all,
    resolve_provider,
)
from conclave.tools import build_tool_registry

from .config import (
    MAX_DELEGATIONS_PER_ROUND,
    MAX_ROUNDS,
    RUN_CONFIG,
    TIMEOUT_SECONDS,
    make_budget as app_budget,
)
from .loop import run_investigation
from .render import Renderer


DEFAULT_QUESTION = """
Investigate this repository and identify one important
architectural weakness, using repository, GitHub and web
evidence where useful.
""".strip()


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def positive_int(value: str) -> int:
    number = nonnegative_int(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def positive_float(value: str) -> float:
    number = nonnegative_float(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


# =============================================================================
# CLI / provider selection
# =============================================================================


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="investigation",
        description=(
            "Investigate a question with a "
            "team of agents."
        )
    )

    parser.add_argument(
        "question",
        nargs="*",
    )

    parser.add_argument(
        "--repo",
        default=".",
    )

    parser.add_argument(
        "--provider",
        choices=[
            "codex",
            "opencode",
            "ollama",
        ],
        default=None,
        help=(
            "model backend; by default whichever "
            "one this computer already has set up"
        ),
    )

    parser.add_argument(
        "--model",
        default=None,
    )

    parser.add_argument(
        "--ollama-url",
        default=(
            "http://127.0.0.1:11434"
        ),
    )

    parser.add_argument(
        "--approve-destructive",
        action="store_true",
        help=(
            "approve tools that declare WRITE; "
            "without this, such calls are "
            "refused"
        ),
    )

    parser.add_argument(
        "--max-tool-calls",
        type=nonnegative_int,
        default=None,
    )

    parser.add_argument(
        "--max-seconds",
        type=nonnegative_float,
        default=None,
    )

    parser.add_argument(
        "--max-tokens",
        type=nonnegative_int,
        default=None,
    )

    parser.add_argument(
        "--max-cost",
        type=nonnegative_float,
        default=None,
    )

    limits = parser.add_argument_group("investigation limits")
    for name, default in (
        ("max_rounds", MAX_ROUNDS),
        ("max_delegations_per_round", MAX_DELEGATIONS_PER_ROUND),
    ):
        limits.add_argument(
            "--" + name.replace("_", "-"),
            type=positive_int,
            default=default,
            help=f"default: {default}",
        )

    defaults = app_budget()
    for name in ("max_delegations", "max_per_agent", "max_depth"):
        default = getattr(defaults, name)
        limits.add_argument(
            "--" + name.replace("_", "-"),
            type=nonnegative_int,
            default=default,
            help=f"default: {default}",
        )

    for field in fields(RunConfig):
        default = getattr(RUN_CONFIG, field.name)
        limits.add_argument(
            "--" + field.name.replace("_", "-"),
            type=positive_int,
            default=default,
            help=f"default: {default}",
        )

    limits.add_argument(
        "--model-timeout",
        type=positive_float,
        default=TIMEOUT_SECONDS,
        help=f"seconds per model call (default: {TIMEOUT_SECONDS})",
    )

    return parser.parse_args(argv)


def make_run_config(args: argparse.Namespace) -> RunConfig:
    return replace(
        RUN_CONFIG,
        **{field.name: getattr(args, field.name) for field in fields(RunConfig)},
    )


def make_provider(
    args: argparse.Namespace,
    repo: Path,
) -> tuple[ModelProvider, str]:
    """The backend to use, and the name to report for it.

    `--provider` overrides; without it, the backend already set up on
    this computer is used, so a first run needs no configuration.
    """

    provider, name, reason = resolve_provider(
        repo,
        args.provider,
        args.model,
        args.model_timeout,
        args.ollama_url,
    )

    if provider is None:
        raise RuntimeError(
            f"{reason}. Pass --provider codex or "
            "--provider opencode, or start Ollama with "
            "an installed model."
        )

    return provider, name


def make_budget(
    args: argparse.Namespace,
) -> RunBudget:
    """The app's delegation shape, with only the flags that were set."""

    return app_budget(
        max_delegations=args.max_delegations,
        max_per_agent=args.max_per_agent,
        max_depth=args.max_depth,
        tool_calls=args.max_tool_calls,
        wall_clock_seconds=args.max_seconds,
        tokens=args.max_tokens,
        cost_usd=args.max_cost,
    )


def make_approver(
    args: argparse.Namespace,
) -> Approver | None:
    if args.approve_destructive:
        return approve_all

    return None


async def main() -> None:
    args = parse_args()

    repo = (
        Path(args.repo)
        .expanduser()
        .resolve()
    )

    if not repo.exists():
        raise RuntimeError(
            "Repository does not exist: "
            f"{repo}"
        )

    question = " ".join(
        args.question
    ).strip()

    if not question:
        question = DEFAULT_QUESTION

    provider, provider_name = make_provider(
        args,
        repo,
    )

    budget = make_budget(args)
    config = make_run_config(args)

    render = Renderer()

    render.header(
        question,
        repo,
        provider_name,
        getattr(provider, "model", None),
    )

    render.budget_plan(budget)

    await run_investigation(
        provider,
        build_tool_registry(
            repo,
            max_output_chars=(
                config.max_tool_output_chars
            ),
        ),
        question,
        budget=budget,
        config=config,
        max_rounds=args.max_rounds,
        max_delegations_per_round=args.max_delegations_per_round,
        approver=make_approver(args),
        render=render,
    )


def run() -> None:
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print(
            "\nCancelled."
        )

        sys.exit(130)

    except Exception as exc:
        print(
            f"\nERROR: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)


if __name__ == "__main__":
    run()
