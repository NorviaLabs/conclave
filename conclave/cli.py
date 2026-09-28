"""Command-line entry point and provider selection."""

import argparse
import asyncio
import sys

from pathlib import Path

from .investigation import run_investigation
from .providers import (
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
)
from .registry import build_tool_registry


# =============================================================================
# CLI / provider selection
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

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

    return parser.parse_args()


def make_provider(
    args: argparse.Namespace,
    repo: Path,
) -> ModelProvider:
    if (
        args.provider
        == "opencode"
    ):
        return OpenCodeProvider(
            repo,
            model=args.model,
        )

    model = (
        args.model
        or "qwen3.6"
    )

    return OllamaProvider(
        model=model,
        base_url=args.ollama_url,
    )


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
        question = """
Investigate this repository and identify one important
architectural weakness, using repository, GitHub and web
evidence where useful.
""".strip()

    provider = make_provider(
        args,
        repo,
    )

    tools = build_tool_registry(
        repo
    )

    print(
        f"QUESTION\n{question}\n"
    )

    print(
        f"WORKSPACE\n{repo}\n"
    )

    print(
        f"MODEL PROVIDER\n"
        f"{args.provider}\n"
    )

    await run_investigation(
        provider,
        tools,
        question,
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
