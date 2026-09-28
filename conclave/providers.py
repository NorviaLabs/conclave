"""Model backends used by the agent runtime."""

import asyncio
import json
import os
import urllib.request

from abc import ABC, abstractmethod
from pathlib import Path

from .config import TIMEOUT_SECONDS


# =============================================================================
# Model providers
# =============================================================================


class ModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        raise NotImplementedError


class OpenCodeProvider(ModelProvider):
    """
    Uses your existing OpenCode authentication.

    OpenCode is used only as a model backend.
    Its own tools are disabled.
    """

    def __init__(
        self,
        cwd: Path,
        model: str | None = None,
    ):
        self.cwd = cwd
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        prompt = "\n\n".join(
            f"{message['role'].upper()}:\n"
            f"{message['content']}"
            for message in messages
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
                timeout=TIMEOUT_SECONDS,
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

        return stdout.decode().strip()


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
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
            }
        ).encode()

        def request() -> str:
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
                timeout=TIMEOUT_SECONDS,
            ) as response:
                data = json.loads(
                    response.read().decode()
                )

                return data["message"]["content"]

        return await asyncio.to_thread(
            request
        )
