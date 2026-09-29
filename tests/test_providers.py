"""Tests for the model backends, with processes and HTTP faked."""

import asyncio
import json

import pytest

from pathlib import Path

from conclave import providers
from conclave.providers import (
    ModelProvider,
    OllamaProvider,
    OpenCodeProvider,
)


class FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.hang = hang
        self.killed = False
        self.calls = 0

    async def communicate(self):
        self.calls += 1

        if self.hang and self.calls == 1:
            await asyncio.sleep(5)

        return self.stdout, self.stderr

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


def test_model_provider_complete_raises():
    with pytest.raises(NotImplementedError):
        asyncio.run(
            ModelProvider.complete(
                object(),
                [],
            )
        )


def test_opencode_commands_and_config(monkeypatch):
    captured = {}

    async def fake_exec(*command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]

        return FakeProcess(stdout=b"  answer  \n")

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    provider = OpenCodeProvider(
        Path("/tmp/repo"),
        model="fast-model",
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

    assert command[:5] == (
        "opencode",
        "run",
        "--agent",
        "model-only",
        "--model",
    )

    assert command[5] == "fast-model"

    assert "SYSTEM:\nsys" in command[-1]
    assert "USER:\nusr" in command[-1]

    assert captured["env"]["NO_COLOR"] == "1"

    config = json.loads(
        captured["env"]["OPENCODE_CONFIG_CONTENT"]
    )

    assert config["agent"]["model-only"][
        "permission"
    ] == {"*": "deny"}

    assert captured["cwd"] == Path("/tmp/repo")


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
            Path(".")
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
            OpenCodeProvider(Path(".")).complete(
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
            OpenCodeProvider(Path(".")).complete(
                [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ]
            )
        )


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
    assert process.calls == 2


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

    assert captured["timeout"] == (
        providers.TIMEOUT_SECONDS
    )


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
            model="qwen3.6"
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
    """argv cannot carry a NUL byte, so the prompt cannot either."""

    captured = {}

    async def fake_exec(*command, **kwargs):
        captured["prompt"] = command[-1]

        return FakeProcess(stdout=b"ok")

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    asyncio.run(
        OpenCodeProvider(Path("/tmp/repo")).complete(
            [
                {
                    "role": "user",
                    "content": "before\x00after",
                }
            ]
        )
    )

    assert "\x00" not in captured["prompt"]
    assert "beforeafter" in captured["prompt"]
