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
import sys
from pathlib import Path

from conclave import ToolRegistry
from conclave.providers import OLLAMA_DEFAULT_URL, LocalBackends, resolve_provider
from .definition import stage_backend, validate_host_tools
from .runs import RunRepository, validate_run_request
from .extensions import ConnectorAdapter, load_extensions, load_provider_factory
from .planning import propose
from .principal import Principal, resolve_principal
from .profile import LOCAL, resolve_profile
from .resources import load_resources, register_source_tools, bind_resources, working_folder, SourceTools, SOURCE_TOOLS
from .storage import RunStore, StoragePaths, tenant_paths, WorkspaceStore

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
    def __init__(self, repository, provider, tools, *, reviewer, provider_name=None, provider_source=None,
                 unavailable=None, adapters=(), resources=None, source_extensions=(), profile=None,
                 principal=None, paths=None, sandbox=None, loop=None, backends=None):
        self.repository, self.provider, self.tools = repository, provider, tools
        # `provider` is the backend this host runs by default. `backends` is the
        # router a run gets when a member or a step names its own, and it is
        # absent when this host can only run the one backend it was told about.
        self.backends = backends
        # `reviewer` names the actor recorded on a human contribution; the
        # principal also carries the tenant whose storage this host holds.
        self.principal = principal or resolve_principal(reviewer, source="host")
        self.reviewer, self.provider_name, self.unavailable = self.principal.name, provider_name, unavailable
        # Where this host writes what it owns. A host built by hand keeps the
        # files beside its repository, which is what it did before tenancy.
        self.paths = paths or StoragePaths(root=Path(repository.path).parent, runs=Path(repository.path),
                                           applications=Path(repository.path).parent / "studio.sqlite")
        # A deployment that runs tool calls behind a boundary supplies a
        # factory; each run then gets an executor scoped to that run's grant.
        self.sandbox = sandbox
        # Where the agent loop runs: this process by default, a sandbox when a
        # deployment says so. The budget and the store stay here either way.
        self.loop = loop
        # "configured" when a host setting chose this backend, "detected"
        # when it was the backend already set up on this machine.
        self.provider_source = provider_source
        self.profile = profile or LOCAL
        self.tasks = {}
        self.adapters = tuple(adapters)
        self.resources = resources
        self.source_extensions = source_extensions

    def run_tools(self, request):
        """The tools one run may use, and where its calls actually run.

        A sandbox factory is consulted per run, so the executor carries that
        run's grant rather than the host's whole tool set.
        """

        tools = bind_resources(self.tools, self.resources, request, self.source_extensions) if self.resources is not None else self.tools

        if self.sandbox is not None:
            # The host's registry is shared between runs; a run's grant is not.
            tools = tools.copy()
            tools.executor = self.sandbox(tools, request)

        return tools

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
        # A run may mix backends, so it is handed the router when there is one.
        provider = self.backends or self.provider
        stream = (self.repository.resume(run_id, provider, tools, loop=self.loop) if resume
                  else self.repository.execute(run_id, provider, tools, loop=self.loop))
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

    def _validate_backends(self, definition):
        """Refuse a run whose team asks for a backend this host cannot run.

        A declared backend is honoured or refused, never silently replaced, so
        this is where the person finds out — before the run starts, told which
        member and step asked, and what this host has instead.
        """

        members = {item["id"]: item for item in definition["agents"]}
        catalogue = {item["id"]: item for item in self.backends.catalogue()} if self.backends is not None else {}

        for stage in definition["workflow"]:
            member = members[stage["agent"]]

            if member.get("kind", "ai") == "human":
                continue

            provider, model = stage_backend(member, stage)

            if not provider and not model:
                continue

            where = f"Step “{stage.get('name') or stage['id']}” ({member.get('name') or member['id']})"
            wanted = provider or self.provider_name or ""

            if not catalogue:
                raise ValueError(f"{where} asks for {provider or model}, but this host runs one configured backend and cannot choose"
                                 + (f" ({self.provider_name})" if self.provider_name else ""))

            entry = catalogue.get(wanted)

            if entry is None:
                raise ValueError(f"{where} asks for the {wanted} backend, which this host does not offer")

            if not entry["available"]:
                raise ValueError(f"{where} asks for {entry['name']}, which this host cannot run: {entry['reason']}")

            if model and entry["models"] and model not in entry["models"]:
                raise ValueError(f"{where} asks for the model {model}, which {entry['name']} does not report"
                                 + f"; it has {', '.join(entry['models'][:6])}")

    async def handle(self, command, value, *, principal=None, streaming=False):
        """Answer one command. `principal` names the actor for this request,
        which matters inside a tenant where several people act; `streaming`
        says whether this transport can push a run's events instead of being
        asked for them."""
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
            # `local` is the older name for the same question the profile
            # answers, so the two can never disagree.
            return 200, {"local": self.profile.name == "local", "service": True,
                         "available": self.provider is not None, "provider": self.provider_name,
                         "provider_source": self.provider_source,
                         # What this host can be asked to run, per member and
                         # per step. Empty when the host runs one backend it
                         # was configured with, which cannot be chosen.
                         "backends": self.backends.catalogue() if self.backends is not None else [],

                         "profile": self.profile.name,
                         "tenant": self.principal.tenant,
                         "connectors": connectors, "reason": self.unavailable, "tools": sorted(self.tools.tools), "reviewer": self.reviewer,
                         "resources": [{"id": key, "name": item["name"], "applications": item["applications"],
                                        "tools": sorted(SOURCE_TOOLS)}
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
                                      "schedules": False, "events": streaming, "budget_warnings": True}}
        if command == "propose":
            self._require_provider()
            return 200, await propose(self.provider, self.tools, value)
        if command == "start":
            self._require_provider()
            value = validate_run_request(value)
            if "resources" not in value and value["definition"].get("resources"):
                value["resources"] = dict(value["definition"]["resources"])
            validate_host_tools(value["definition"], self.tools)
            self._validate_backends(value["definition"])

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
            reviewer = (principal or self.principal).name
            self.repository.submit_review(value["run_id"], value["review_id"], reviewer=reviewer, output=value["output"])
            return 200, public_record(self.repository.get(value["run_id"]))
        raise ValueError("Unknown local run command")

    async def close(self):
        active = list(self.tasks.values())
        for task, stream in active:
            task.cancel()
        await asyncio.gather(*(task for task, stream in active), return_exceptions=True)
        for task, stream in active:
            await stream.aclose()


def configured_paths(principal) -> StoragePaths:
    """The storage this host uses, scoped to the principal's tenant.

    `CONCLAVE_RUN_DATABASE` and `CONCLAVE_DATABASE` are single-operator
    overrides, so they apply to the local tenant only: pointing every tenant
    at one file would put their data in the same place.
    """

    studio = Path(__file__).resolve().parents[2] / "studio"
    run_database = setting("RUN_DATABASE")
    data_dir = Path(setting("DATA_DIR", Path(run_database).parent if run_database else
                            studio / ".studio-local" if studio.is_dir() else Path.cwd() / ".conclave"))
    paths = tenant_paths(data_dir, principal.tenant)

    if principal.tenant != "local":
        return paths

    runs = Path(run_database) if run_database else paths.runs

    return StoragePaths(root=paths.root,
                        runs=runs,
                        applications=Path(setting("DATABASE")) if setting("DATABASE") else runs.parent / "studio.sqlite")


def configured_host(*, workspace=None, source_dir=None, principal=None):
    profile = resolve_profile(setting("PROFILE"))
    principal = principal or resolve_principal(setting("PRINCIPAL"), setting("TENANT"))
    paths = configured_paths(principal)
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
    saved_resources = paths.root / "sources.json"
    resources = (load_resources(saved_resources) if saved_resources.exists()
                 else load_resources(setting("RESOURCES_FILE"), source))
    if not resources:
        # Nothing has been authorized, so the folder this host was started
        # in is the source: a first run needs no settings, and every file
        # path stays contained in that folder. A hosted control plane has no
        # such folder to assume, so it requires an authorized source.
        resources = working_folder(workspace) if profile.implicit_source else {}
    if source_dir is not None:
        resources = {**resources, **working_folder(source_dir)}
    register_source_tools(tools)
    if 'default' in resources:
        # Retain direct trusted-host access for legacy callers. Run execution
        # always builds its own registry from the pinned resource selection.
        register_source_tools(tools, SourceTools(resources["default"]["path"], source_extensions))
        adapters.append(ConnectorAdapter("local_files", "Local source folder", "files", ("read_file",), lambda: resources["default"]["path"].is_dir()))
    from conclave.tools import build_tool_registry
    bundled = build_tool_registry(workspace, output_limit)
    for name in ("web_search", "web_fetch"):
        tools.register(bundled.tool(name))
    adapters.extend([ConnectorAdapter("web_search", "Public web search", "research", ("web_search",), lambda: True),
                     ConnectorAdapter("web_reader", "Public web reader", "research", ("web_fetch",), lambda: True)])
    adapters.extend(load_extensions(setting("EXTENSIONS", ""), tools))
    name, model = setting("PROVIDER") or None, setting("MODEL") or None
    provider, reason, source, backends = None, None, None, None
    timeout = float(setting("MODEL_TIMEOUT", "300"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("CONCLAVE_MODEL_TIMEOUT must be finite and positive")
    factory = setting("PROVIDER_FACTORY")
    if factory:
        provider = load_provider_factory(factory)(model=model, timeout=timeout, workspace=workspace)
        if not callable(getattr(provider, "complete", None)):
            raise ValueError("The configured provider must implement complete(messages)")
        name, source = name or factory.split(':')[0], "configured"
    else:
        if name is None and not profile.detect_backend:
            # A hosted control plane has no machine to inspect: it is told
            # which backend to use, and gets credentials from its own store.
            reason = ("No model backend is configured for this host. Set CONCLAVE_PROVIDER or "
                      "CONCLAVE_PROVIDER_FACTORY.")
        else:
            # Locally, no configuration is required: the backend already
            # signed in on this machine is used, and CONCLAVE_PROVIDER
            # only overrides it.
            chosen = name is not None
            provider, name, reason = resolve_provider(workspace, name, model, timeout,
                                                     setting("OLLAMA_URL", OLLAMA_DEFAULT_URL))
            source = "configured" if chosen else ("detected" if provider is not None else None)
            if provider is not None and profile.detect_backend:
                # A host that inspects its own machine can offer a choice, so a
                # run is handed every backend it can reach; the default one
                # answers whatever names nothing. A hosted host is told which
                # backend to use and offers nothing to choose.
                backends = LocalBackends(workspace, provider, name, model=model, timeout=timeout,
                                         ollama_url=setting("OLLAMA_URL", OLLAMA_DEFAULT_URL))
            if provider is None:
                reason = f"{reason}. Set CONCLAVE_PROVIDER to choose a backend explicitly."
    history_limit = int(setting("HISTORY_LIMIT", "30"))
    if history_limit < 1:
        raise ValueError("CONCLAVE_HISTORY_LIMIT must be positive")
    repository = RunRepository(paths.runs)
    repository.history_limit = history_limit
    return LocalRunHost(repository, provider, tools,
                        reviewer=principal.name, provider_name=name, unavailable=reason, adapters=adapters,
                        resources=resources, source_extensions=source_extensions, provider_source=source,
                        profile=profile, principal=principal, paths=paths, backends=backends)
