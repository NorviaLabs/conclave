"""Model backends used by the agent runtime.

`complete` returns a `Completion`: the text, plus a usage record when the
backend can measure one. Backends that cannot measure tokens return
`usage=None`, and the budget skips that dimension instead of guessing.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# =============================================================================
# Model providers
# =============================================================================


class ProviderError(RuntimeError):
    """A model backend call failed, with a category callers may record.

    The message keeps the backend's own text, which is useful to whoever runs
    this machine and can read its logs. It can also carry account, path and
    request data, so a caller persists `kind` and never the message.
    """

    KINDS = ("timeout", "unreachable", "command_failed", "backend_error", "unreadable", "rate_limited")

    def __init__(self, kind: str, detail: str):
        if kind not in self.KINDS:
            raise ValueError("Unsupported provider failure kind")
        super().__init__(detail)
        self.kind = kind


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


class ProviderRouter(Protocol):
    """A provider that serves more than one backend.

    One run can mix backends when a team member or a step asks for one. The
    authority asks `for_call` for the backend a call names, so a single
    provider object can stand for every backend this host can reach. A router
    that cannot serve the choice returns `None`, and the call is refused rather
    than sent somewhere else: a declared backend is honoured or refused, never
    silently replaced.
    """

    def for_call(self, provider: str, model: str) -> ModelProvider | None: ...


def opencode_providers(environ) -> dict:
    """The person's own OpenCode model providers, and nothing else of theirs.

    A provider defined in their config is how a model they named is reached;
    their instructions, plugins and agents are not wanted in the prompt.
    """

    home = Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        config = json.loads((home / "opencode" / "opencode.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    return {key: config[key] for key in ("provider", "enabled_providers", "disabled_providers") if key in config}


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
        # A NUL byte ends the prompt early for OpenCode. Drop them: the
        # alternative is losing the rest of the prompt to an unrepresentable
        # character.
        prompt = (
            "\n\n".join(
                f"{message['role'].upper()}:\n"
                f"{message['content']}"
                for message in messages
            )
            .replace("\x00", "")
        )

        # --pure: no plugins, which can rewrite the prompt.
        # Errors go to stderr as they happen: OpenCode retries a spent usage
        # limit without exiting, so its log is the only place that says so.
        command = [
            "opencode",
            "run",
            "--pure",
            "--print-logs",
            "--log-level",
            "ERROR",
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

        # The prompt goes on stdin, not argv: a long run's transcript passes
        # the operating system's argument-size limit, and the call would fail
        # before reaching the model.

        # OpenCode adds the person's own setup to every prompt: global and
        # project AGENTS.md, ~/.claude instructions, skills, plugins and its
        # own agent framing, in which this agent may call no tools. A model
        # told that beside the runtime's tool list believes its tools are
        # missing. So OpenCode runs from a folder of its own, with a config
        # home of its own that keeps only the person's providers, and a
        # system prompt that says nothing about tools. Sign-in lives in
        # OpenCode's data folder, which is left alone.
        isolated = Path(tempfile.gettempdir()) / "opencode-model-backend"
        (isolated / "config" / "opencode").mkdir(parents=True, exist_ok=True)
        (isolated / "work").mkdir(exist_ok=True)

        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        env["XDG_CONFIG_HOME"] = str(isolated / "config")
        # OpenCode tells the model its working directory from PWD, not from
        # where it was started: inherited, that names the host's own checkout
        # as "the workspace", and an agent whose tools act on another folder
        # concludes it is in the wrong repository.
        env["PWD"] = str(isolated / "work")
        # Its own session store, too: every call is a session, and the
        # person's OpenCode history is neither the place for thousands of
        # them nor a database to contend with while they use OpenCode.
        env["OPENCODE_DB"] = str(isolated / "opencode.db")
        # OpenCode's default plugins stay on: they include its ChatGPT
        # sign-in, without which a model reached only through that sign-in
        # is "not found". --pure already keeps out the person's own plugins.
        for switch in (
            "OPENCODE_DISABLE_PROJECT_CONFIG",
            "OPENCODE_DISABLE_CLAUDE_CODE",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS",
        ):
            env[switch] = "1"

        # Provider-specific implementation detail.
        # The rest of the agent system knows nothing about this.
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                **opencode_providers(os.environ),
                "agent": {
                    "model-only": {
                        "description": (
                            "Model-only backend for an "
                            "external agent runtime"
                        ),
                        "mode": "primary",
                        "prompt": (
                            "Answer the request below exactly as it asks. Any working directory or "
                            "environment details you are given describe this model host, not the "
                            "workspace the request is about."
                        ),
                        "permission": {
                            "*": "deny",
                        },
                    }
                },
            }
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=isolated / "work",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def exchange() -> tuple[bytes, bytes]:
            reading = asyncio.create_task(process.stdout.read())
            try:
                process.stdin.write(prompt.encode())
                await process.stdin.drain()
                process.stdin.close()
                errors = []
                while line := await process.stderr.readline():
                    errors.append(line)
                    # A usage limit is a wait, and OpenCode would otherwise
                    # keep the call open until the timeout says nothing more.
                    text = line.decode(errors="replace").strip()
                    if RATE_LIMITED.search(text):
                        raise ProviderError("rate_limited", f"OpenCode usage limit reached: {text}")
                stdout = await reading
            finally:
                reading.cancel()
            await process.wait()
            return stdout, b"".join(errors)

        try:
            stdout, stderr = await asyncio.wait_for(
                exchange(),
                timeout=self.timeout,
            )

        except (asyncio.TimeoutError, asyncio.CancelledError, ProviderError) as exc:
            process.kill()
            await process.wait()
            if isinstance(exc, (asyncio.CancelledError, ProviderError)):
                raise
            raise ProviderError("timeout", "OpenCode model call timed out") from exc

        if process.returncode != 0:
            raise ProviderError("command_failed", stderr.decode().strip() or "OpenCode failed")

        return Completion(
            text=stdout.decode().strip()
        )


# How a backend says it will not answer until a quota resets.
RATE_LIMITED = re.compile(r"usage limit|rate limit|too many requests", re.IGNORECASE)
# Codex logs a refused call to one of its own tools through its tool router.
NATIVE_TOOL_CALL = re.compile(r"codex_core::tools::router")
NATIVE_TOOL_ATTEMPTS = 3
# Only the backend's own functions are ruled out: a model told not to call
# tools at all stops using the caller's, which reach it as JSON actions.
NATIVE_TOOL_REMINDER = (
    "Your previous reply called a built-in function such as exec. Built-in "
    "functions do not run here. The tools listed in your instructions still "
    "work: use one by writing its JSON tool action as your text reply."
)


def _json_object(text: str) -> bool:
    try:
        return isinstance(json.JSONDecoder().raw_decode(text.strip())[0], dict)
    except (json.JSONDecodeError, ValueError):
        return False


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
            # The sandbox and approval policy above confine Codex's own tools,
            # which are disabled. Codex would still tell the model it may only
            # read files and never ask for approval, and the model reads that
            # as a limit on the caller's tools: a step granted write_file
            # then refuses to write. Keep both notes out of the prompt.
            "-c", "include_permissions_instructions=false",
            "-c", "include_environment_context=false",
        ]
        # Current models are catalogued as code-mode only, so Codex always
        # offers them an `exec` tool however its features are set. Disabling
        # the code-mode host makes that tool fail closed instead of running
        # JavaScript, and `complete` catches a model that reaches for it.
        for feature in (
            "shell_tool", "unified_exec", "apply_patch_freeform",
            "view_image", "multi_agent", "js_repl", "apps", "plugins",
            "hooks", "codex_hooks", "memories", "memory_tool",
            "browser_use", "computer_use", "image_generation",
            "code_mode_host", "multi_agent_v2", "sleep_tool", "goals",
            "tool_suggest", "tool_search", "request_permissions_tool",
            "default_mode_request_user_input",
        ):
            command.extend(["--disable", feature])
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")

        # A reply is one action, and a turn can hold several messages. Whatever
        # the model writes after its first JSON object was written without that
        # action's result, so it is a guess: often a final reply saying no
        # result came back. The first JSON object is therefore the reply.
        # A code-mode model also tries to run its reply through Codex's own
        # exec tool; that call fails, and what follows answers the failure,
        # not the caller. A native call without a JSON object is asked again,
        # reminded how tools run here.
        for _ in range(NATIVE_TOOL_ATTEMPTS):
            process, stdout, stderr = await self._turn(command, prompt)
            messages, usage = self._read(process, stdout, stderr)
            for message in messages:
                if _json_object(message):
                    return Completion(text=message, usage=usage)
            if not NATIVE_TOOL_CALL.search(stderr.decode(errors="replace")):
                return Completion(text=messages[-1], usage=usage)
            prompt = f"{prompt}\n\nSYSTEM:\n{NATIVE_TOOL_REMINDER}"
        raise ProviderError("backend_error", "The Codex model kept calling its own tools, which do not run here")

    @staticmethod
    def _read(process, stdout: bytes, stderr: bytes) -> tuple[list[str], Usage | None]:
        """The turn's assistant messages, in order, and its token usage."""

        # Codex reports why a turn failed as an event and then exits non-zero,
        # so the events are read before the exit status: a usage limit is a
        # wait, not a broken command, and only the event says which it was.
        for line in stdout.decode().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in {"error", "turn.failed"}:
                message = str(event.get("message") or (event.get("error") or {}).get("message") or "")
                if RATE_LIMITED.search(message):
                    raise ProviderError("rate_limited", f"Codex usage limit reached: {message}")

        if process.returncode != 0:
            raise ProviderError("command_failed", stderr.decode().strip() or "Codex failed")

        messages = []
        usage = None
        try:
            for line in stdout.decode().splitlines():
                event = json.loads(line)
                if event["type"] == "item.completed":
                    item = event["item"]
                    # A turn can end with an empty message after its
                    # reply; that blank is not the model's answer.
                    if item["type"] == "agent_message" and item["text"].strip():
                        messages.append(item["text"])
                elif event["type"] == "turn.completed":
                    counts = event.get("usage")
                    if counts is not None:
                        usage = Usage(
                            input_tokens=counts.get("input_tokens"),
                            output_tokens=counts.get("output_tokens"),
                        )
                elif event["type"] in {"error", "turn.failed"}:
                    raise ProviderError("backend_error", f"Codex failed: {event}")
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ProviderError("unreadable", f"Codex output could not be read: {error!r}") from error

        if not messages:
            raise ProviderError("unreadable", "Codex returned no assistant message")
        return messages, usage

    async def _turn(self, command: list[str], prompt: str):
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
                raise ProviderError("timeout", "Codex model call timed out") from exc
        return process, stdout, stderr


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

            try:
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
            except urllib.error.HTTPError as error:
                raise ProviderError("backend_error", f"Ollama returned HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise ProviderError("unreachable", f"Ollama could not be reached: {error}") from error
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise ProviderError("unreadable", f"Ollama reply could not be read: {error!r}") from error

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


# =============================================================================
# Every backend one machine can reach
# =============================================================================


BACKEND_NAMES = {"codex": "Codex", "opencode": "OpenCode", "ollama": "Ollama"}


class LocalBackends(ModelProvider):
    """The backends this machine can reach, served one per call.

    A host hands this to a run instead of a single backend, so a team member or
    a step can name its own. The default backend answers every call that names
    none, which is what a caller who chose nothing gets.

    A backend that is not installed, or a model the backend does not report, is
    refused here rather than replaced: `for_call` returns `None`, and the
    authority turns that into a refused call.
    """

    def __init__(
        self,
        workspace: Path,
        default: ModelProvider,
        name: str | None,
        *,
        model: str | None = None,
        timeout: float = 300.0,
        ollama_url: str = OLLAMA_DEFAULT_URL,
    ):
        self.workspace = workspace
        self.default = default
        self.name = name or ""
        self.model = model or ""
        self.timeout = timeout
        self.ollama_url = ollama_url
        self.built: dict[tuple[str, str], ModelProvider | None] = {}

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> Completion:
        """The default backend, for callers that named none."""

        return await self.default.complete(messages)

    def for_call(self, provider: str, model: str) -> ModelProvider | None:
        """The backend one call asked for, or nothing if it cannot be served."""

        name = provider or self.name
        chosen = model or (self.model if name == self.name else "")

        if not name:
            return None

        # The backend the host already runs is not rebuilt for its own default.
        if name == self.name and chosen == self.model:
            return self.default

        key = (name, chosen)

        if key not in self.built:
            self.built[key] = self._build(name, chosen)

        return self.built[key]

    def _build(self, name: str, model: str) -> ModelProvider | None:
        if name not in BACKEND_NAMES and name not in COMMAND_PROVIDERS:
            return None

        if name == "ollama":
            installed = installed_ollama_models(self.ollama_url)

            # A model name is only a name until the server confirms it has it.
            if model and model not in installed:
                return None

            if not installed:
                return None

        provider, _, _ = resolve_provider(
            self.workspace,
            name,
            model or None,
            self.timeout,
            self.ollama_url,
        )

        return provider

    def catalogue(self) -> list[dict[str, object]]:
        """What to offer in a picker, including why something is unavailable.

        Only backends that can be *chosen* are listed. A model list is only
        reported by a backend that can report one, so a command backend leaves
        it empty and the caller keeps its own default.
        """

        entries = []

        for name in COMMAND_PROVIDERS + ("ollama",):
            reason, models, available = None, [], False

            if name == "ollama":
                models = installed_ollama_models(self.ollama_url)
                available = bool(models)
                if not available:
                    reason = "No models are installed on the Ollama server this host was told about"
            elif shutil.which(name) is None:
                reason = f"The {name} command is not installed on this host"
            else:
                available = True

            entries.append(
                {
                    "id": name,
                    "name": BACKEND_NAMES.get(name, name),
                    "available": available,
                    "models": models,
                    "default_model": models[0] if name == "ollama" and models else None,
                    "reason": reason,
                    "default": name == self.name,
                }
            )

        return entries
