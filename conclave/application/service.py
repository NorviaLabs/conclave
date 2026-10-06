"""Shared application execution service for the Conclave CLI and REST adapter.

Providers, tools and reviewer identity are owned by the host.
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

from conclave import CodexProvider, OpenCodeProvider, OllamaProvider, ToolRegistry
from .definition import validate_host_tools
from .runs import RunRepository, validate_run_request
from .extensions import ConnectorAdapter, load_extensions, load_provider_factory
from .planning import propose
from .resources import load_resources, register_source_tools, bind_resources, SourceTools, SOURCE_TOOLS

def setting(name, default=None):
    return os.environ.get("CONCLAVE_" + name, os.environ.get("STUDIO_" + name, default))



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
        if event.get("metrics"):
            metrics = event["metrics"]
        events.append({"sequence": item["sequence"], "event": event})
    record["events"], record["metrics"] = events, metrics
    return record


class LocalRunHost:
    def __init__(self, repository, provider, tools, *, reviewer, provider_name=None, unavailable=None, adapters=(), resources=None, source_extensions=()):
        self.repository, self.provider, self.tools = repository, provider, tools
        self.reviewer, self.provider_name, self.unavailable = reviewer, provider_name, unavailable
        self.tasks = {}
        self.adapters = tuple(adapters)
        self.resources = resources
        self.source_extensions = source_extensions

    def run_tools(self, request):
        return bind_resources(self.tools, self.resources, request, self.source_extensions) if self.resources is not None else self.tools

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
        tools = self.run_tools(self.repository.get(run_id)["request"])
        stream = self.repository.resume(run_id, self.provider, tools) if resume else self.repository.execute(run_id, self.provider, tools)
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
            connectors = []
            for adapter in self.adapters:
                try:
                    connectors.append(await adapter.describe(self.tools))
                except Exception:
                    connectors.append({"id": adapter.id, "name": adapter.name, "group": adapter.group,
                                       "category": "Host connections", "description": "Connection check failed",
                                       "default_mode": adapter.mode, "status": "unavailable", "available": False,
                                       "tools": list(adapter.tools), "origin": "host"})
            return 200, {"local": True, "service": True, "available": self.provider is not None, "provider": self.provider_name,
                         "connectors": connectors, "reason": self.unavailable, "tools": sorted(self.tools.tools), "reviewer": self.reviewer,
                         "resources": [{"id": key, "name": item["name"], "applications": item["applications"],
                                        "tools": ["list_files", "read_file", "search_files", "knowledge_search"]}
                                       for key, item in (self.resources or {}).items()],
                         "resource_tools": sorted(name for name in SOURCE_TOOLS if self.tools.tool(name) is not None
                                                  and getattr(self.tools.tool(name).func, "__module__", None) == SourceTools.__module__) if self.resources is not None else [],
                         "tool_descriptors": [{"id": tool.name, "name": tool.name, "description": tool.description,
                                               "parameters": tool.parameters, "capabilities": sorted(tool.capabilities),
                                               "requires_approval": tool.destructive,
                                               "permission": "write" if tool.destructive else "read",
                                               "origin": "host", "category": "Host tools", "available": True}
                                              for tool in self.tools.tools.values()],
                         "features": {"dag": True, "durable_reviews": True, "manual": True,
                                      "schedules": False, "events": False, "budget_warnings": True}}
        if command == "propose":
            self._require_provider()
            return 200, await propose(self.provider, self.tools, value)
        if command == "start":
            self._require_provider()
            value = validate_run_request(value)
            validate_host_tools(value["definition"], self.tools)
            self.run_tools(value)
            # Pin the compatibility default just like an explicit selection.
            if self.resources is not None and not value.get("resources") and "default" in self.resources:
                if any(SOURCE_TOOLS.intersection(agent["tools"]) for agent in value["definition"]["agents"]):
                    value["resources"] = {"source": "default"}
            if self.resources is not None and value.get("resources", {}).get("source"):
                value["resources"]["source_revision"] = self.resources[value["resources"]["source"]]["revision"]
            selected = {item["connector"]: item for item in value["definition"].get("integrations", [])}
            for adapter in self.adapters:
                if adapter.id in selected:
                    try:
                        descriptor = await adapter.describe(self.tools)
                    except Exception:
                        raise ValueError("Selected host connector is unavailable: " + adapter.id) from None
                    if not descriptor["available"]:
                        raise ValueError("Selected host connector is unavailable: " + adapter.id)
                    mode = selected[adapter.id]["mode"]
                    if adapter.mode == "read" and mode != "read" or adapter.mode == "model" and mode != "model":
                        raise ValueError("Selected connector does not support these actions: " + adapter.id)
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


def configured_host(*, workspace=None):
    studio = Path(__file__).resolve().parents[2] / "studio"
    workspace = Path(workspace).resolve() if workspace is not None else Path.cwd()
    source = setting("SOURCE_DIR")
    defaults_path = Path(setting("DEFAULTS_FILE", Path(__file__).parent / "data" / "defaults.json"))
    defaults = json.loads(defaults_path.read_text())
    from .definition import validate_definition
    validate_definition(defaults)
    output_limit = defaults["run_config"]["max_tool_output_chars"]
    tools = ToolRegistry(output_limit)
    adapters = []
    source_extensions = {item.strip().lower() for item in setting("SOURCE_EXTENSIONS", ".txt,.md,.csv,.html").split(',')}
    import re
    if not source_extensions or any(not re.fullmatch(r"\.[a-z0-9_-]+", item) for item in source_extensions):
        raise ValueError("STUDIO_SOURCE_EXTENSIONS must contain comma-separated file suffixes")
    resources = load_resources(setting("RESOURCES_FILE"), source)
    register_source_tools(tools)
    if source:
        # Retain direct trusted-host access for legacy callers. Run execution
        # always builds its own registry from the pinned resource selection.
        register_source_tools(tools, SourceTools(resources["default"]["path"], source_extensions))
        adapters.append(ConnectorAdapter("local_files", "Local source folder", "files", ("read_file",), lambda: resources["default"]["path"].is_dir()))
    if setting("WEB") == "1":
        from conclave.tools import build_tool_registry
        bundled = build_tool_registry(Path(source or studio), output_limit)
        for name in ("web_search", "web_fetch"):
            tools.register(bundled.tool(name))
        adapters.extend([ConnectorAdapter("web_search", "Public web search", "research", ("web_search",), lambda: True),
                         ConnectorAdapter("web_reader", "Public web reader", "research", ("web_fetch",), lambda: True)])
    adapters.extend(load_extensions(setting("EXTENSIONS", ""), tools))
    name, model = setting("PROVIDER"), setting("MODEL") or None
    provider, reason = None, None
    timeout = float(setting("MODEL_TIMEOUT", "300"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("STUDIO_MODEL_TIMEOUT must be finite and positive")
    factory = setting("PROVIDER_FACTORY")
    if factory:
        provider = load_provider_factory(factory)(model=model, timeout=timeout, workspace=workspace)
        if not callable(getattr(provider, "complete", None)):
            raise ValueError("The configured provider must implement complete(messages)")
        name = name or factory.split(':')[0]
    elif name in {"codex", "opencode"} and shutil.which(name) is None:
        reason = f"The configured {name} command is unavailable on this host"
    elif name == "codex":
        provider = CodexProvider(model, timeout)
    elif name == "opencode":
        provider = OpenCodeProvider(workspace, model, timeout)
    elif name == "ollama" and model:
        provider = OllamaProvider(model, timeout, setting("OLLAMA_URL", "http://127.0.0.1:11434"))
    else:
        reason = "Set STUDIO_PROVIDER to codex, opencode or ollama, or set STUDIO_PROVIDER_FACTORY; Ollama also needs STUDIO_MODEL"
    history_limit = int(setting("HISTORY_LIMIT", "30"))
    if history_limit < 1:
        raise ValueError("STUDIO_HISTORY_LIMIT must be positive")
    repository = RunRepository(Path(setting("RUN_DATABASE") or Path(setting("DATA_DIR", studio / ".studio-local" if studio.is_dir() else Path.cwd() / ".conclave")) / "runs.sqlite"))
    repository.history_limit = history_limit
    return LocalRunHost(repository, provider, tools,
                        reviewer=getpass.getuser(), provider_name=name, unavailable=reason, adapters=adapters,
                        resources=resources, source_extensions=source_extensions)
