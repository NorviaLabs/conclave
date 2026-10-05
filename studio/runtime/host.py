"""Development-only JSON-lines host, reachable through the Node preview's pipe.

There is no listening Python port. Browser definitions and identities are not
accepted: the Node host supplies the saved snapshot and the local OS operator
owns reviews. Production authentication/workspaces require another host.
"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
import getpass
import json
import math
import os
import shutil
import sys
from pathlib import Path

from conclave import CodexProvider, OpenCodeProvider, OllamaProvider, Tool, ToolRegistry, Capability
from .application import validate_host_tools
from .runs import RunRepository, validate_run_request


def public_record(record):
    """Keep checkpoint internals on the host; publish actual saved results."""
    record = dict(record)
    events, metrics = [], {}
    for item in record["events"]:
        event = {key: value for key, value in item["event"].items() if key != "checkpoint"}
        checkpoint = item["event"].get("checkpoint")
        if checkpoint:
            metrics = checkpoint["budget"]["spent"]
            if event["type"] == "ApplicationStateChanged":
                event["patch"] = checkpoint["patches"][event["stage"]]
        events.append({"sequence": item["sequence"], "event": event})
    record["events"], record["metrics"] = events, metrics
    return record


class LocalRunHost:
    def __init__(self, repository, provider, tools, *, reviewer, provider_name=None, unavailable=None):
        self.repository, self.provider, self.tools = repository, provider, tools
        self.reviewer, self.provider_name, self.unavailable = reviewer, provider_name, unavailable
        self.tasks = {}

    async def _consume(self, stream):
        try:
            async with aclosing(stream):
                async for _ in stream:
                    pass
        except ValueError:
            # A duplicate claim can lose to another process. The durable owner
            # and its status remain authoritative; never rewrite that run.
            pass

    async def _schedule(self, run_id, *, resume=False):
        if run_id in self.tasks:
            raise ValueError("This request is already running")
        stream = self.repository.resume(run_id, self.provider, self.tools) if resume else self.repository.execute(run_id, self.provider, self.tools)
        # Claim and commit the first event before returning success to Node.
        try:
            await anext(stream)
        except BaseException:
            await stream.aclose()
            raise
        task = asyncio.create_task(self._consume(stream))
        self.tasks[run_id] = (task, stream)
        task.add_done_callback(lambda done: self.tasks.pop(run_id, None))

    def _require_provider(self):
        if self.provider is None:
            raise ValueError(self.unavailable or "Configure an AI provider on the local host")

    async def handle(self, command, value):
        if not isinstance(value, dict):
            raise ValueError("Provide a JSON request object")
        if command == "runtime":
            return 200, {"local": True, "service": True, "available": self.provider is not None, "provider": self.provider_name,
                         "reason": self.unavailable, "tools": sorted(self.tools.tools), "reviewer": self.reviewer}
        if command == "start":
            self._require_provider()
            value = validate_run_request(value)
            validate_host_tools(value["definition"], self.tools)
            run_id = self.repository.create(value)
            await self._schedule(run_id)
            return 201, public_record(self.repository.get(run_id))
        if command == "list":
            if set(value) != {"application_id"} or not isinstance(value["application_id"], str):
                raise ValueError("Choose an application")
            return 200, {"runs": self.repository.list(value["application_id"])}
        if command in {"get", "resume", "cancel"}:
            if set(value) != {"run_id"} or not isinstance(value["run_id"], str):
                raise ValueError("Choose a saved request")
            run_id = value["run_id"]
            self.repository.get(run_id)
            if command == "resume":
                self._require_provider()
                await self._schedule(run_id, resume=True)
            elif command == "cancel":
                active = self.tasks.get(run_id)
                if active is not None:
                    task, stream = active
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    await stream.aclose()
                else:
                    self.repository.cancel_waiting(run_id)
            return 200, public_record(self.repository.get(run_id))
        if command == "review":
            if set(value) != {"run_id", "review_id", "output"} or not isinstance(value["run_id"], str) or not isinstance(value["review_id"], str):
                raise ValueError("Choose the waiting review and provide your contribution")
            self.repository.submit_review(value["run_id"], value["review_id"], reviewer=self.reviewer, output=value["output"])
            return 200, public_record(self.repository.get(value["run_id"]))
        raise ValueError("Unknown local run command")

    async def close(self):
        active = list(self.tasks.values())
        for task, stream in active:
            task.cancel()
        await asyncio.gather(*(task for task, stream in active), return_exceptions=True)
        for task, stream in active:
            await stream.aclose()


def configured_host():
    studio = Path(__file__).resolve().parents[1]
    source = os.environ.get("STUDIO_SOURCE_DIR")
    tools = ToolRegistry(20000)
    if source:
        from conclave.tools.local import LocalTools
        root = Path(source).resolve()
        if not root.is_dir():
            raise ValueError("STUDIO_SOURCE_DIR must be an existing source folder")
        local = LocalTools(root)

        async def read_source(path, start_line=1, end_line=200):
            resolved = local._safe_path(path)
            if any(part.startswith('.') for part in resolved.relative_to(root).parts) or resolved.suffix.lower() not in {".txt", ".md", ".csv", ".html"}:
                raise ValueError("Choose a plain-text source document in the source folder")
            return await local.read_file(path, start_line=start_line, end_line=end_line)

        tools.register(Tool("read_file", "Read a supplied source document in the host's source folder", {"path": "string", "start_line": "integer, optional", "end_line": "integer, optional"}, read_source, frozenset({Capability.READ})))
    if os.environ.get("STUDIO_WEB") == "1":
        from conclave.tools import build_tool_registry
        bundled = build_tool_registry(Path(source or studio), 20000)
        for name in ("web_search", "web_fetch"):
            tools.register(bundled.tool(name))
    name, model = os.environ.get("STUDIO_PROVIDER"), os.environ.get("STUDIO_MODEL") or None
    provider, reason = None, None
    timeout = float(os.environ.get("STUDIO_MODEL_TIMEOUT", "300"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("STUDIO_MODEL_TIMEOUT must be finite and positive")
    if name in {"codex", "opencode"} and shutil.which(name) is None:
        reason = f"The configured {name} command is unavailable on this host"
    elif name == "codex":
        provider = CodexProvider(model, timeout)
    elif name == "opencode":
        provider = OpenCodeProvider(studio, model, timeout)
    elif name == "ollama" and model:
        provider = OllamaProvider(model, timeout, os.environ.get("STUDIO_OLLAMA_URL", "http://127.0.0.1:11434"))
    else:
        reason = "Set STUDIO_PROVIDER to codex, opencode or ollama; Ollama also needs STUDIO_MODEL"
    return LocalRunHost(RunRepository(studio / ".studio-local" / "runs.sqlite"), provider, tools,
                        reviewer=getpass.getuser(), provider_name=name, unavailable=reason)


async def main():
    host = configured_host()
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            message_id = None
            try:
                message = json.loads(line)
                message_id = message["id"]
                status, result = await host.handle(message["command"], message.get("value", {}))
            except KeyError:
                status, result = 404, {"error": "Saved request not found"}
            except ValueError as error:
                status, result = 400, {"error": str(error)}
            except TypeError:
                status, result = 400, {"error": "The local request could not be accepted. Check its fields, review status and available tools."}
            except Exception:
                status, result = 503, {"error": "The local runtime is unavailable"}
            print(json.dumps({"id": message_id, "status": status, "result": result}, allow_nan=False), flush=True)
    finally:
        await host.close()


if __name__ == "__main__":
    asyncio.run(main())
