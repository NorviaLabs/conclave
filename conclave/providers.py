"""Model backends used by the agent runtime.

`complete` returns a `Completion`: the text, plus a usage record when the
backend can measure one. Backends that cannot measure tokens return
`usage=None`, and the budget skips that dimension instead of guessing.
"""

import asyncio
import json
import os
import shutil
import tempfile
import urllib.request

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


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
        model: str | None,
        timeout: float,
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

        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(
                "OpenCode model call timed out"
            ) from exc

        if process.returncode != 0:
            raise RuntimeError(
                stderr.decode().strip()
                or "OpenCode failed"
            )

        return Completion(
            text=stdout.decode().strip()
        )


class CodexProvider(ModelProvider):
    """Use the Codex CLI's ChatGPT login without exposing credentials.

    Each completion is an isolated, ephemeral model call. Codex's native
    tools are disabled: the caller's runtime owns all tool execution.
    """

    def __init__(self, model: str | None, timeout: float):
        self.model = model
        self.timeout = timeout

    async def complete(self, messages: list[dict[str, str]]) -> Completion:
        prompt = "\n\n".join(
            f"{message['role'].upper()}:\n{message['content']}"
            for message in messages
        )
        command = [
            "codex", "exec", "--json", "--ephemeral",
            "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only",
            "--color", "never",
            "-c", 'model_provider="openai"',
            "-c", 'forced_login_method="chatgpt"',
            "-c", 'approval_policy="never"',
            "-c", 'web_search="disabled"',
        ]
        for feature in (
            "shell_tool", "unified_exec", "apply_patch_freeform",
            "view_image", "multi_agent", "js_repl", "apps", "plugins",
            "hooks", "codex_hooks", "memories", "memory_tool",
            "browser_use", "computer_use", "image_generation",
        ):
            command.extend(["--disable", feature])
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")

        # Never load a caller repository's config, instructions, or MCP servers.
        with tempfile.TemporaryDirectory(prefix="conclave-codex-") as cwd:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()), timeout=self.timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                process.kill()
                await process.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeError("Codex model call timed out") from exc

        if process.returncode != 0:
            raise RuntimeError(stderr.decode().strip() or "Codex failed")

        text = None
        usage = None
        for line in stdout.decode().splitlines():
            event = json.loads(line)
            if event["type"] == "item.completed":
                item = event["item"]
                if item["type"] == "agent_message":
                    text = item["text"]
            elif event["type"] == "turn.completed":
                counts = event.get("usage")
                if counts is not None:
                    usage = Usage(
                        input_tokens=counts.get("input_tokens"),
                        output_tokens=counts.get("output_tokens"),
                    )
            elif event["type"] in {"error", "turn.failed"}:
                raise RuntimeError(f"Codex failed: {event}")

        if text is None:
            raise RuntimeError("Codex returned no assistant message")
        return Completion(text=text, usage=usage)


class OllamaProvider(ModelProvider):
    """
    Optional second provider.

    This exists mainly to demonstrate that the multi-agent
    runtime itself is not coupled to OpenCode.
    """

    def __init__(
        self,
        model: str,
        timeout: float,
        base_url: str = "http://127.0.0.1:11434",
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


# =============================================================================
# Choosing a backend
# =============================================================================


OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"

COMMAND_PROVIDERS = ("codex", "opencode")


def installed_ollama_models(
    base_url: str = OLLAMA_DEFAULT_URL,
    timeout: float = 1.0,
) -> list[str]:
    """What the local Ollama server has pulled, if anything.

    An unreachable server is "no models" rather than an error: the caller
    asked what this machine can run right now, and the answer is nothing.
    """

    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/api/tags",
            timeout=timeout,
        ) as response:
            entries = json.loads(
                response.read().decode()
            ).get("models", [])

    except Exception:
        return []

    return [
        entry["name"]
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
    ]


def resolve_provider(
    workspace: Path,
    name: str | None = None,
    model: str | None = None,
    timeout: float = 300.0,
    ollama_url: str = OLLAMA_DEFAULT_URL,
) -> tuple[ModelProvider | None, str | None, str | None]:
    """The backend this machine can run: provider, its name, why there is none.

    A named backend is honoured or refused, never silently replaced. With
    no name, the first backend that answers is used, so a caller needs no
    configuration to start. Detection never invents a model: a command
    backend keeps whatever default it was authenticated with, and Ollama
    is asked which models it actually has installed.
    """

    if name in COMMAND_PROVIDERS:
        if shutil.which(name) is None:
            return (
                None,
                name,
                f"The configured {name} command is unavailable on this host; install it and sign in",
            )

        provider = (
            CodexProvider(model, timeout)
            if name == "codex"
            else OpenCodeProvider(workspace, model, timeout)
        )

        return provider, name, None

    if name == "ollama":
        chosen = model or next(
            iter(installed_ollama_models(ollama_url)),
            None,
        )

        if chosen is None:
            return (
                None,
                name,
                f"The Ollama server at {ollama_url} has no models installed; pull one to run it",
            )

        return (
            OllamaProvider(chosen, timeout, ollama_url),
            name,
            None,
        )

    if name:
        return None, name, f"Unknown backend {name!r}"

    for candidate in COMMAND_PROVIDERS:
        if shutil.which(candidate) is not None:
            return resolve_provider(
                workspace,
                candidate,
                model,
                timeout,
                ollama_url,
            )

    installed = installed_ollama_models(ollama_url)

    if installed:
        return (
            OllamaProvider(installed[0], timeout, ollama_url),
            "ollama",
            None,
        )

    return None, None, "No AI backend was found on this computer; install codex or opencode, or start Ollama"
