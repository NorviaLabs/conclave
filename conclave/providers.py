"""Model backends used by the agent runtime.

`complete` returns a `Completion`: the text, plus a usage record when the
backend can measure one. Backends that cannot measure tokens return
`usage=None`, and the budget skips that dimension instead of guessing.
"""

import asyncio
import json
import os
import urllib.request

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .config import TIMEOUT_SECONDS


# =============================================================================
# Model providers
# =============================================================================


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if (
            self.input_tokens is None
            and self.output_tokens is None
        ):
            return None

        return (
            self.input_tokens or 0
        ) + (
            self.output_tokens or 0
        )


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage | None = None


class ModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> Completion:
        raise NotImplementedError


class OpenCodeProvider(ModelProvider):
    """
    Uses your existing OpenCode authentication.

    OpenCode is used only as a model backend.
    Its own tools are disabled.

    OpenCode reports no token usage, so completions
    from this backend carry `usage=None`.
    """

    def __init__(
        self,
        cwd: Path,
        model: str | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ):
        self.cwd = cwd
        self.model = model
        self.timeout = timeout

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> Completion:
        # argv cannot carry a NUL byte, so a prompt that somehow contains
        # one would fail here rather than at the model. Drop them: the
        # alternative is losing the whole run to an unrepresentable
        # character.
        prompt = (
            "\n\n".join(
                f"{message['role'].upper()}:\n"
                f"{message['content']}"
                for message in messages
            )
            .replace("\x00", "")
        )

        command = [
            "opencode",
            "run",
            "--agent",
            "model-only",
        ]

        if self.model:
            command.extend(
                [
                    "--model",
                    self.model,
                ]
            )

        command.append(prompt)

        env = os.environ.copy()
        env["NO_COLOR"] = "1"

        # Provider-specific implementation detail.
        # The rest of the agent system knows nothing about this.
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "agent": {
                    "model-only": {
                        "description": (
                            "Model-only backend for an "
                            "external agent runtime"
                        ),
                        "mode": "primary",
                        "permission": {
                            "*": "deny",
                        },
                    }
                }
            }
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()

            raise RuntimeError(
                "OpenCode model call timed out"
            )

        if process.returncode != 0:
            raise RuntimeError(
                stderr.decode().strip()
                or "OpenCode failed"
            )

        return Completion(
            text=stdout.decode().strip()
        )


class OllamaProvider(ModelProvider):
    """
    Optional second provider.

    This exists mainly to demonstrate that the multi-agent
    runtime itself is not coupled to OpenCode.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = TIMEOUT_SECONDS,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> Completion:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
            }
        ).encode()

        def request() -> Completion:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(
                req,
                timeout=self.timeout,
            ) as response:
                data = json.loads(
                    response.read().decode()
                )

                return Completion(
                    text=data["message"][
                        "content"
                    ],
                    usage=read_usage(data),
                )

        return await asyncio.to_thread(
            request
        )


def read_usage(
    data: dict,
) -> Usage:
    """Ollama reports token counts; keep them instead of dropping them."""

    return Usage(
        input_tokens=data.get(
            "prompt_eval_count"
        ),
        output_tokens=data.get(
            "eval_count"
        ),
    )
