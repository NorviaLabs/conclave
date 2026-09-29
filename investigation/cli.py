"""Command-line entry point and provider selection.

This is the reference front end: it shows what a caller does with the
library — pick a provider, build a tool registry, set a budget, decide
whether anything is allowed to change the world, and hand the whole thing
to `run_investigation`.
"""

import argparse
import asyncio
import sys

from pathlib import Path

from conclave import (
    Approver,
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
    RunBudget,
    approve_all,
)
from conclave.tools import build_tool_registry

from .config import (
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

DEFAULT_OLLAMA_MODEL = "qwen3.6"


# =============================================================================
# CLI / provider selection
# =============================================================================


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
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
            "opencode",
            "ollama",
        ],
        default="opencode",
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
        type=int,
        default=None,
    )

    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
    )

    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
    )

    return parser.parse_args(argv)


def make_provider(
    args: argparse.Namespace,
    repo: Path,
) -> ModelProvider:
    if args.provider == "opencode":
        return OpenCodeProvider(
            repo,
            model=args.model,
            timeout=TIMEOUT_SECONDS,
        )

    return OllamaProvider(
        model=(
            args.model
            or DEFAULT_OLLAMA_MODEL
        ),
        timeout=TIMEOUT_SECONDS,
        base_url=args.ollama_url,
    )


def make_budget(
    args: argparse.Namespace,
) -> RunBudget:
    """The app's delegation shape, with only the flags that were set."""

    return app_budget(
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

    provider = make_provider(
        args,
        repo,
    )

    budget = make_budget(args)

    render = Renderer()

    render.header(
        question,
        repo,
        args.provider,
    )

    render.budget_plan(budget)

    await run_investigation(
        provider,
        build_tool_registry(
            repo,
            max_output_chars=(
                RUN_CONFIG.max_tool_output_chars
            ),
        ),
        question,
        budget=budget,
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
