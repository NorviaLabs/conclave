"""Tests for the model backends, with processes and HTTP faked."""

import asyncio
import json

import pytest

from conftest import TIMEOUT

from pathlib import Path

from conclave import providers
from conclave.providers import (
    CodexProvider,
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
    ProviderError,
)


class FakeStream:
    """The pipes of a process that streams: stdout read whole, stderr by line."""

    def __init__(self, process, data: bytes):
        self.process, self.data = process, data

    async def read(self):
        if self.process.hang:
            await asyncio.sleep(5)
        data, self.data = self.data, b""
        return data

    async def readline(self):
        if not self.data:
            if self.process.hang_after_stderr:
                await asyncio.sleep(5)
            return b""
        line, newline, rest = self.data.partition(b"\n")
        self.data = rest
        return line + newline


class FakeWriter:
    def __init__(self, process):
        self.process = process

    def write(self, data):
        self.process.input = (self.process.input or b"") + data

    async def drain(self):
        pass

    def close(self):
        pass


class FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
        hang_after_stderr: bool = False,
    ):
        self.output, self.errors = stdout, stderr
        self.returncode = returncode
        self.hang = hang
        self.hang_after_stderr = hang_after_stderr
        self.killed = False
        self.reaped = False
        self.calls = 0
        self.input = None
        self.stdin = FakeWriter(self)
        self.stdout = FakeStream(self, stdout)
        self.stderr = FakeStream(self, stderr)

    async def communicate(self, input=None):
        self.input = input
        self.calls += 1

        if self.hang and self.calls == 1:
            await asyncio.sleep(5)

        return self.output, self.errors

    async def wait(self):
        # A streamed process is reaped by waiting, as communicate reaps the other.
        self.reaped = True
        return self.returncode

    def kill(self):
        self.killed = True


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
    ):
        self.payload = payload

    def read(self, size=None) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_model_provider_is_abstract():
    class Bare(ModelProvider):
        pass

    with pytest.raises(
        TypeError,
        match="abstract",
    ):
        Bare()


def codex_events(*events):
    return "\n".join(json.dumps(event) for event in events).encode()


@pytest.mark.parametrize("model", [None, "gpt-5.2-codex"])
def test_codex_command_and_completion(monkeypatch, model):
    captured = {}
    process = FakeProcess(stdout=codex_events(
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok": true}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}},
    ))

    async def fake_exec(*command, **kwargs):
        captured.update(command=command, **kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(CodexProvider(model, TIMEOUT).complete([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello\x00world"},
    ]))
    assert result.text == '{"ok": true}'
    assert result.usage.total_tokens == 110  # Cached tokens are part of input.
    command = captured["command"]
    assert command[:3] == ("codex", "exec", "--json")
    assert command[-1] == "-"
    assert 'forced_login_method="chatgpt"' in command
    assert 'model_provider="openai"' in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "read-only" in command
    assert 'web_search="disabled"' in command
    # Codex's read-only sandbox must not reach the model as a limit on its tools.
    assert "include_permissions_instructions=false" in command
    assert "include_environment_context=false" in command
    for feature in ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks", "code_mode_host"):
        index = command.index(feature)
        assert command[index - 1] == "--disable"
    if model:
        assert command[command.index("--model") + 1] == model
    else:
        assert "--model" not in command
    assert process.input == b"SYSTEM:\nsys\n\nUSER:\nhello\x00world"
    assert captured["stdin"] == asyncio.subprocess.PIPE
    assert not Path(captured["cwd"]).exists()


@pytest.mark.parametrize("events,kind,message", [
    ([], "unreadable", "no assistant message"),
    ([{"type": "error", "message": "login required"}], "backend_error", "login required"),
    ([{"type": "turn.failed", "error": {"message": "quota"}}], "backend_error", "quota"),
])
def test_codex_event_failures(monkeypatch, events, kind, message):
    async def fake_exec(*command, **kwargs):
        return FakeProcess(stdout=codex_events(*events))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match=message) as failure:
        asyncio.run(CodexProvider(None, TIMEOUT).complete([]))
    assert failure.value.kind == kind


@pytest.mark.parametrize("returncode", [1, 0])
def test_codex_usage_limit_is_a_wait_not_a_broken_command(monkeypatch, returncode):
    """Codex reports a usage limit as an event and exits non-zero; the run must
    learn it can continue later, not that the backend is broken."""
    limit = "You've hit your usage limit. Try again at 12:39 AM."
    async def fake_exec(*command, **kwargs):
        return FakeProcess(stdout=codex_events({"type": "error", "message": limit},
                                               {"type": "turn.failed", "error": {"message": limit}}),
                           stderr=b"", returncode=returncode)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match="usage limit") as failure:
        asyncio.run(CodexProvider(None, TIMEOUT).complete([]))
    assert failure.value.kind == "rate_limited"


@pytest.mark.parametrize("stderr,message", [(b"login required", "login required"), (b"", "Codex failed")])
def test_codex_process_failure(monkeypatch, stderr, message):
    async def fake_exec(*command, **kwargs):
        return FakeProcess(stderr=stderr, returncode=1)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match=message) as failure:
        asyncio.run(CodexProvider(None, TIMEOUT).complete([]))
    assert failure.value.kind == "command_failed"


@pytest.mark.parametrize("event", [
    {"type": "turn.completed"},
    {"type": "turn.completed", "usage": None},
])
def test_codex_unreported_usage(monkeypatch, event):
    async def fake_exec(*command, **kwargs):
        return FakeProcess(stdout=codex_events(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
            event,
        ))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(CodexProvider(None, TIMEOUT).complete([]))
    assert result.usage is None


NATIVE_CALL = b"ERROR codex_core::tools::router: error=code-mode host is disabled\n"


def test_codex_keeps_the_reply_written_before_a_native_tool_call(monkeypatch):
    """A code-mode model writes its JSON action, then tries to run it through
    Codex's exec tool, and answers that failure. The action is the reply."""
    action = '{"type":"tool","name":"read_issue","arguments":{}}'
    process = FakeProcess(stdout=codex_events(
        {"type": "item.completed", "item": {"type": "agent_message", "text": action}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "The tools are unavailable."}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}},
    ), stderr=NATIVE_CALL)
    started = []
    async def fake_exec(*command, **kwargs):
        started.append(command)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(CodexProvider(None, TIMEOUT).complete([{"role": "user", "content": "go"}]))
    assert result.text == action
    assert result.usage.total_tokens == 7
    assert len(started) == 1


def test_codex_keeps_the_first_action_of_a_turn(monkeypatch):
    """A model can write a tool action and then, in the same turn and without
    its result, a final reply saying no result came back. The action is the
    reply; what follows it was written blind."""
    process = FakeProcess(stdout=codex_events(
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"type":"tool","name":"read_file"}'}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"type":"final","content":"no result"}'}},
    ))
    async def fake_exec(*command, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(CodexProvider(None, TIMEOUT).complete([])).text == '{"type":"tool","name":"read_file"}'


def test_codex_without_a_json_message_keeps_its_last_message(monkeypatch):
    process = FakeProcess(stdout=codex_events(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "thinking aloud"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "the answer"}},
    ))
    async def fake_exec(*command, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(CodexProvider(None, TIMEOUT).complete([])).text == "the answer"


def test_codex_ignores_a_blank_message_after_the_reply(monkeypatch):
    process = FakeProcess(stdout=codex_events(
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"type":"tool"}'}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": ""}},
    ))
    async def fake_exec(*command, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(CodexProvider(None, TIMEOUT).complete([])).text == '{"type":"tool"}'


def test_codex_native_tool_call_is_asked_again(monkeypatch):
    """A model that calls Codex's own exec tool gets a failure back and then
    reports the caller's tools as unavailable. That reply is discarded and the
    model asked again, reminded that only a text reply reaches the caller."""
    answer = codex_events({"type": "item.completed", "item": {"type": "agent_message", "text": '{"type":"tool"}'}})
    blocked = codex_events({"type": "item.completed", "item": {"type": "agent_message", "text": "tools are unavailable"}})
    processes = [FakeProcess(stdout=blocked, stderr=NATIVE_CALL), FakeProcess(stdout=answer)]
    started = []
    async def fake_exec(*command, **kwargs):
        started.append(command)
        return processes[len(started) - 1]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(CodexProvider(None, TIMEOUT).complete([{"role": "user", "content": "go"}]))
    assert result.text == '{"type":"tool"}'
    assert len(started) == 2
    assert processes[0].input == b"USER:\ngo"
    assert processes[1].input.startswith(b"USER:\ngo\n\nSYSTEM:\n")
    assert b"do not run here" in processes[1].input
    # The caller's own tools stay available; only Codex's are ruled out.
    assert b"still work" in processes[1].input
    assert b"Do not call any tool" not in processes[1].input


def test_codex_native_tool_calls_that_persist_fail_the_call(monkeypatch):
    blocked = codex_events({"type": "item.completed", "item": {"type": "agent_message", "text": "tools are unavailable"}})
    started = []
    async def fake_exec(*command, **kwargs):
        started.append(command)
        return FakeProcess(stdout=blocked, stderr=NATIVE_CALL)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match="its own tools") as failure:
        asyncio.run(CodexProvider(None, TIMEOUT).complete([]))
    assert failure.value.kind == "backend_error"
    assert len(started) == 3


def test_codex_timeout_reaps_process(monkeypatch):
    process = FakeProcess(hang=True)
    async def fake_exec(*command, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match="timed out") as failure:
        asyncio.run(CodexProvider(None, 0.001).complete([]))
    assert failure.value.kind == "timeout"
    assert process.killed
    assert process.calls == 2


@pytest.mark.parametrize("backend", ["codex", "opencode"])
def test_cli_cancellation_reaps_process(monkeypatch, backend):
    process = FakeProcess(hang=True)
    async def fake_exec(*command, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    async def cancel_call():
        provider = CodexProvider(None, TIMEOUT) if backend == "codex" else OpenCodeProvider(Path("."), None, TIMEOUT)
        task = asyncio.create_task(provider.complete([]))
        await asyncio.sleep(0.01)
        task.cancel()
        await task
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancel_call())
    assert process.killed
    assert process.calls == 2 if backend == "codex" else process.reaped


def test_model_provider_complete_raises():
    with pytest.raises(NotImplementedError):
        asyncio.run(
            ModelProvider.complete(
                object(),
                [],
            )
        )


@pytest.mark.parametrize(
    "model",
    ["fast-model", "openai/gpt-5.2-codex"],
)
def test_opencode_commands_and_config(monkeypatch, tmp_path, model):
    captured = {}
    # The person's own OpenCode setup: a provider, which is kept, and a plugin,
    # which is not.
    personal = tmp_path / "home"
    (personal / "opencode").mkdir(parents=True)
    (personal / "opencode" / "opencode.json").write_text(json.dumps(
        {"plugin": ["caveman"], "provider": {"mine": {"name": "Mine"}}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(personal))

    async def fake_exec(*command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        captured["stdin"] = kwargs["stdin"]
        captured["process"] = FakeProcess(stdout=b"  answer  \n")

        return captured["process"]

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    provider = OpenCodeProvider(
        Path("/tmp/repo"),
        model=model,
        timeout=TIMEOUT,
    )

    assert asyncio.run(
        provider.complete(
            [
                {
                    "role": "system",
                    "content": "sys",
                },
                {
                    "role": "user",
                    "content": "usr",
                },
            ]
        )
    ).text == "answer"

    command = captured["command"]

    assert command[:9] == (
        "opencode",
        "run",
        "--pure",
        "--print-logs",
        "--log-level",
        "ERROR",
        "--agent",
        "model-only",
        "--model",
    )

    assert command[9] == model

    # The prompt travels on stdin: argv has a size limit a long run passes.
    assert len(command) == 10
    assert captured["stdin"] == asyncio.subprocess.PIPE
    assert captured["process"].input == b"SYSTEM:\nsys\n\nUSER:\nusr"

    assert captured["env"]["NO_COLOR"] == "1"

    config = json.loads(
        captured["env"]["OPENCODE_CONFIG_CONTENT"]
    )

    assert config["agent"]["model-only"][
        "permission"
    ] == {"*": "deny"}

    # Nothing of the person's setup reaches the prompt but their providers.
    env = captured["env"]
    assert config["provider"] == {"mine": {"name": "Mine"}}
    assert "plugin" not in config
    assert env["XDG_CONFIG_HOME"] != str(personal)
    for switch in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE",
                   "OPENCODE_DISABLE_EXTERNAL_SKILLS"):
        assert env[switch] == "1"
    # The default plugins carry OpenCode's ChatGPT sign-in; --pure keeps the
    # person's own plugins out.
    assert env.get("OPENCODE_DISABLE_DEFAULT_PLUGINS") != "1"
    assert "--pure" in captured["command"]
    assert config["agent"]["model-only"]["prompt"]
    assert captured["cwd"] != Path("/tmp/repo")
    # OpenCode names its working directory from PWD; it must be its own folder.
    assert env["PWD"] == str(captured["cwd"])
    assert env["OPENCODE_DB"].startswith(str(captured["cwd"].parent))


def test_opencode_without_model_flag(monkeypatch):
    captured = {}

    async def fake_exec(*command, **kwargs):
        captured["command"] = command

        return FakeProcess(stdout=b"ok")

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    asyncio.run(
        OpenCodeProvider(
            Path("."),
            model=None,
            timeout=TIMEOUT,
        ).complete(
            [
                {
                    "role": "user",
                    "content": "hi",
                }
            ]
        )
    )

    assert "--model" not in captured["command"]


def test_opencode_reports_stderr(monkeypatch):
    async def fake_exec(*command, **kwargs):
        return FakeProcess(
            stderr=b"auth required",
            returncode=1,
        )

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(
        RuntimeError,
        match="auth required",
    ):
        asyncio.run(
            OpenCodeProvider(
                Path("."),
                model=None,
                timeout=TIMEOUT,
            ).complete(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ]
            )
        )


def test_opencode_reports_generic_failure(monkeypatch):
    async def fake_exec(*command, **kwargs):
        return FakeProcess(returncode=2)

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(
        RuntimeError,
        match="OpenCode failed",
    ):
        asyncio.run(
            OpenCodeProvider(
                Path("."),
                model=None,
                timeout=TIMEOUT,
            ).complete(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ]
            )
        )


def test_opencode_usage_limit_stops_the_call_instead_of_waiting_it_out(monkeypatch):
    """OpenCode retries a spent usage limit without exiting; its error log is
    read as it comes, so the call ends as a wait rather than a timeout."""
    process = FakeProcess(stderr=b'level=ERROR message="stream error" error.error="AI_APICallError: Go usage limit exceeded"\n',
                          hang_after_stderr=True)

    async def fake_exec(*command, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ProviderError, match="usage limit") as failure:
        asyncio.run(OpenCodeProvider(Path("."), model=None, timeout=2).complete([{"role": "user", "content": "hi"}]))
    assert failure.value.kind == "rate_limited"
    assert process.killed


def test_opencode_times_out_and_kills(monkeypatch):
    process = FakeProcess(hang=True)

    async def fake_exec(*command, **kwargs):
        return process

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(
        RuntimeError,
        match="timed out",
    ):
        asyncio.run(
            OpenCodeProvider(
                Path("."),
                model=None,
                timeout=0.01,
            ).complete(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ]
            )
        )

    assert process.killed
    assert process.reaped


def test_ollama_posts_chat_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(
            request.data.decode()
        )
        captured["timeout"] = timeout

        return FakeResponse(
            json.dumps(
                {
                    "message": {
                        "content": "local answer"
                    }
                }
            ).encode()
        )

    monkeypatch.setattr(
        providers.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    provider = OllamaProvider(
        model="qwen3.6",
        timeout=TIMEOUT,
        base_url="http://127.0.0.1:11434/",
    )

    assert provider.base_url == (
        "http://127.0.0.1:11434"
    )

    answer = asyncio.run(
        provider.complete(
            [
                {
                    "role": "user",
                    "content": "hi",
                }
            ]
        )
    )

    assert answer.text == "local answer"

    # Ollama did not report counts in this payload, so the budget will
    # simply skip the token dimension instead of guessing.
    assert answer.usage.total_tokens is None

    assert captured["url"].endswith(
        "/api/chat"
    )

    assert captured["body"] == {
        "model": "qwen3.6",
        "messages": [
            {
                "role": "user",
                "content": "hi",
            }
        ],
        "stream": False,
    }

    assert captured["timeout"] == TIMEOUT


def test_ollama_keeps_reported_token_counts(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return FakeResponse(
            json.dumps(
                {
                    "message": {
                        "content": "local answer"
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            ).encode()
        )

    monkeypatch.setattr(
        providers.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    answer = asyncio.run(
        providers.OllamaProvider(
            model="qwen3.6",
            timeout=TIMEOUT,
        ).complete([])
    )

    assert answer.usage.input_tokens == 10
    assert answer.usage.output_tokens == 5
    assert answer.usage.total_tokens == 15


def test_usage_total_needs_one_known_side():
    from conclave import Usage

    assert Usage().total_tokens is None
    assert Usage(input_tokens=2).total_tokens == 2
    assert Usage(output_tokens=3).total_tokens == 3


def test_read_usage_tolerates_missing_counts():
    usage = providers.read_usage({})

    assert usage.input_tokens is None
    assert usage.total_tokens is None


def test_opencode_drops_null_bytes_from_the_prompt(
    monkeypatch,
):
    """A NUL byte would cut the prompt short, so the prompt carries none."""

    captured = {}

    async def fake_exec(*command, **kwargs):
        captured["process"] = FakeProcess(stdout=b"ok")

        return captured["process"]

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    asyncio.run(
        OpenCodeProvider(
            Path("/tmp/repo"),
            model=None,
            timeout=TIMEOUT,
        ).complete(
            [
                {
                    "role": "user",
                    "content": "before\x00after",
                }
            ]
        )
    )

    assert b"\x00" not in captured["process"].input
    assert b"beforeafter" in captured["process"].input


# =============================================================================
# Choosing a backend
# =============================================================================


def only_installed(monkeypatch, *names):
    monkeypatch.setattr(
        providers.shutil,
        "which",
        lambda name: "/usr/bin/" + name if name in names else None,
    )


def test_installed_ollama_models_reads_names_and_survives_no_server(monkeypatch):
    monkeypatch.setattr(
        providers.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(
            json.dumps({"models": [{"name": "local-a"}, {"name": "local-b"}, {"size": 1}]}).encode()
        ),
    )

    assert providers.installed_ollama_models() == ["local-a", "local-b"]

    def refuse(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(providers.urllib.request, "urlopen", refuse)

    # No server is "no models", not an error: the caller asked what this
    # machine can run right now.
    assert providers.installed_ollama_models() == []


def test_resolve_provider_honours_a_named_backend(monkeypatch):
    only_installed(monkeypatch, "opencode")

    provider, name, reason = providers.resolve_provider(
        Path("/repo"), "codex", timeout=5.0
    )

    # The requested backend is never silently replaced by the installed one.
    assert provider is None and name == "codex"
    assert "codex" in reason and reason

    provider, name, reason = providers.resolve_provider(Path("/repo"), "claude")
    assert provider is None and name == "claude" and "Unknown backend" in reason


def test_resolve_provider_detects_the_backend_already_on_this_computer(monkeypatch):
    only_installed(monkeypatch, "codex", "opencode")

    provider, name, reason = providers.resolve_provider(Path("/repo"), timeout=7.0)

    assert (name, reason) == ("codex", None)
    assert isinstance(provider, CodexProvider) and provider.timeout == 7.0


def test_resolve_provider_falls_back_to_an_installed_ollama_model(monkeypatch):
    only_installed(monkeypatch)
    monkeypatch.setattr(providers, "installed_ollama_models", lambda base_url, timeout=1.0: ["local:8b"])

    provider, name, reason = providers.resolve_provider(Path("/repo"), timeout=5.0)

    assert (name, reason) == ("ollama", None)
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "local:8b"

    # A named Ollama with no installed model reports what is missing and
    # never guesses a model name.
    monkeypatch.setattr(providers, "installed_ollama_models", lambda base_url, timeout=1.0: [])
    provider, name, reason = providers.resolve_provider(Path("/repo"), "ollama")
    assert provider is None and name == "ollama" and "no models installed" in reason


def test_resolve_provider_reports_a_computer_with_nothing_installed(monkeypatch):
    only_installed(monkeypatch)
    monkeypatch.setattr(providers, "installed_ollama_models", lambda base_url, timeout=1.0: [])

    provider, name, reason = providers.resolve_provider(Path("/repo"))

    assert (provider, name) == (None, None)
    assert "No AI backend was found" in reason
