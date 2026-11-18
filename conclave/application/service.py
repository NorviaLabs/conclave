"""Shared application execution service for the Conclave CLI and REST adapter.

Providers, tools and reviewer identity are owned by the host.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import getpass
import json
import math
import os
import re
import sys
from pathlib import Path

from conclave import Capability, ToolRegistry
from conclave.providers import OLLAMA_DEFAULT_URL, LocalBackends, resolve_provider
from .definition import MAX_PARALLEL_STEPS, MAX_PARALLEL_STEPS_CEILING, stage_backend, validate_host_tools
from .runs import RunRepository, validate_run_request
from .extensions import ConnectorAdapter, load_extensions, load_provider_factory
from .planning import propose, questions, refine
from .principal import Principal, resolve_principal
from .profile import LOCAL, resolve_profile
from .resources import load_resources, register_source_tools, bind_resources, working_folder, SourceTools, SOURCE_TOOLS
from .storage import RunStore, StoragePaths, tenant_paths, WorkspaceStore
from .usage import run_usage
from .run_compare import compare_runs
from urllib.parse import quote

LOG = logging.getLogger(__name__)


def setting(name, default=None):
    return os.environ.get("CONCLAVE_" + name, os.environ.get("STUDIO_" + name, default))


# The longest a host may wait before trying a stopped run again, and the most
# times it may try in a row: a day, and a hundred.
RETRY_CEILING_SECONDS = 86400
RETRY_CEILING_ATTEMPTS = 100


@dataclass(frozen=True)
class RetryPolicy:
    """How a host continues a run that a model backend stopped.

    A timeout, an outage or a usage limit stops a run with its checkpoint. The
    host tries it again after `base_seconds`, doubling each time up to
    `max_seconds`, at most `attempts` times in a row; a step that completes
    starts the count again. `attempts=0` leaves every such run for a person.
    """

    attempts: int = 5
    base_seconds: float = 30
    max_seconds: float = 900

    def __post_init__(self):
        if type(self.attempts) is not int or not 0 <= self.attempts <= RETRY_CEILING_ATTEMPTS:
            raise ValueError(f"Automatic retry attempts must be a whole number from 0 to {RETRY_CEILING_ATTEMPTS}")
        for name in ("base_seconds", "max_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= RETRY_CEILING_SECONDS:
                raise ValueError(f"Automatic retry {name.replace('_', ' ')} must be more than 0 and at most {RETRY_CEILING_SECONDS}")
        if self.max_seconds < self.base_seconds:
            raise ValueError("Automatic retry max seconds must not be less than its base seconds")

    def delay(self, attempt: int) -> float:
        """Seconds to wait before attempt `attempt` (1 is the first retry)."""
        return min(self.base_seconds * 2 ** (attempt - 1), self.max_seconds)


def retry_policy_from_settings() -> RetryPolicy:
    """`CONCLAVE_RETRY_ATTEMPTS`, `_BASE_SECONDS` and `_MAX_SECONDS`, or the defaults."""
    default = RetryPolicy()
    values = {}
    for field, name, parse in (("attempts", "RETRY_ATTEMPTS", int), ("base_seconds", "RETRY_BASE_SECONDS", float),
                               ("max_seconds", "RETRY_MAX_SECONDS", float)):
        raw = setting(name)
        if raw is None:
            values[field] = getattr(default, field)
            continue
        try:
            values[field] = parse(raw.strip())
        except ValueError:
            raise ValueError(f"CONCLAVE_{name} must be a number") from None
    try:
        return RetryPolicy(**values)
    except ValueError as error:
        raise ValueError(f"CONCLAVE_RETRY_*: {error}") from None


def pending_retry(record) -> dict | None:
    """The automatic retry a stopped run is waiting for, if any.

    A retry is pending while it is the last thing the run recorded: anything
    after it (a resume, a cancel, a person stopping the retry) supersedes it.
    """
    if not record["events"]:
        return None
    return _pending(record.get("status"), record["events"][-1]["event"])


def _pending(status, last) -> dict | None:
    if status != "stopped" or not last or last.get("type") != "RetryScheduled":
        return None
    return {"at": last["retry_at"], "attempt": last["attempt"], "attempts": last["attempts"], "reason": last.get("reason")}



def answer_standing(record, viewer):
    """Whether `viewer` may answer the review this run waits for, and if not, why.

    `completes` says whether theirs would be the last answer it needs. None
    when nothing waits for an answer.
    """
    from .runs import review_refusal
    if viewer is None or record.get("status") != "waiting_for_human":
        return None
    review = next((item for item in record["reviews"] if item["status"] == "pending"), None)
    if review is None:
        return None
    answered = [item["reviewer"] for item in review.get("answers", [])]
    reason = review_refusal(record["request"]["definition"], review["stage"], starter=record.get("started_by"),
                            answered=answered, reviewer=viewer)
    if reason:
        return {"allowed": False, "reason": reason}
    return {"allowed": True, "reason": "", "completes": len(answered) + 1 >= review.get("required", 1)}


def public_record(record, viewer=None):
    """Keep checkpoint internals on the host; publish actual saved results.

    With `viewer`, also say whether that person may answer the review it waits for.
    """
    record = dict(record)
    events, metrics, resumable = [], {}, False
    for item in record["events"]:
        event = {key: value for key, value in item["event"].items() if key != "checkpoint"}
        checkpoint = item["event"].get("checkpoint")
        # Whether this run can be continued, without handing over the checkpoint
        # itself: a stop that finished its steps can be resumed, and a person is
        # told so rather than left to guess.
        if event.get("type") == "ApplicationFinished":
            resumable = bool(checkpoint) and event.get("status") in {"waiting_for_human", "paused", "stopped"}
            # A run-wide limit is part of the saved run: continuing would only
            # run past it, so a run it stopped is not offered Continue.
            resumable = resumable and not event.get("budget")
            # A run that recorded its actions can continue after an
            # interruption, once whatever was in flight is settled.
            resumable = resumable or (event.get("status") == "interrupted" and bool(record.get("journal")))
        if checkpoint:
            metrics = checkpoint["budget"]["spent"]
            if event["type"] == "ApplicationStateChanged":
                event["patch"] = checkpoint["patches"][event["stage"]]
        if event.get("metrics"):
            metrics = event["metrics"]
        events.append({"sequence": item["sequence"], "event": event})
    record["events"], record["metrics"], record["resumable"] = events, metrics, resumable
    # Files the run stored from its steps' outputs, each with its checked
    # download link; the hash they are checked against stays on the host.
    files = []
    for item in record.get("files") or []:
        shown = {key: item[key] for key in ("id", "stage", "output", "round", "name", "format", "media_type", "size",
                                            "created_at", "preview", "lines", "available")}
        shown["link"] = f"/api/runs/{quote(record['id'], safe='')}/files/{quote(item['id'], safe='')}"
        files.append(shown)
    record["files"] = files
    # What the run used, step by step and against its limits: counts, never text.
    record["usage"] = run_usage([item["event"] for item in events], (record.get("request") or {}).get("definition") or {}, metrics)
    record["retry"] = pending_retry(record)
    record["can_answer"] = answer_standing(record, viewer)
    # The saved version this run ran: its definition is the one stored with the run.
    request = record.get("request") or {}
    if "application_id" in request and "revision" in request:
        record["version"] = {"number": request["revision"], "application_id": request["application_id"],
                             "link": f"/api/applications/{quote(request['application_id'], safe='')}/versions/{request['revision']}"}
    return record


class LocalRunHost:
    def __init__(self, repository, provider, tools, *, reviewer, provider_name=None, provider_source=None,
                 unavailable=None, adapters=(), resources=None, source_extensions=(), profile=None,
                 principal=None, paths=None, sandbox=None, loop=None, backends=None, interactive_approvals=True):
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
        # Runs a person asked to pause; each stops between steps, never mid-call.
        self.pauses = set()
        # A host with an approval surface asks the person before a destructive
        # call runs. One without it — a terminal that only reads a finished run —
        # refuses instead of holding the run open for an answer nobody can give.
        self.interactive_approvals = interactive_approvals
        from .tool_approvals import ToolApprovals
        self.tool_approvals = ToolApprovals(repository)
        self.adapters = tuple(adapters)
        self.resources = resources
        self.source_extensions = source_extensions
        # The scheduler for this host's schedules, while one is running.
        self.schedules = None
        # Who may do what with each application, when an API serves roles (see access.py).
        self.application_access = None
        # "This run is waiting for you": where Studio is served from (for the
        # link), what sends the messages, and the sends still going, which
        # close() waits for a little rather than cutting off.
        self.studio_url = None
        # How often one run, and one application, may tell someone (seconds
        # between a run's messages; messages per application per hour).
        self.notify_run_interval, self.notify_hourly_limit = 600, 20
        from .notifier import Notifier
        self.notifier = Notifier(self)
        self.background = set()
        # Runs a model backend stopped, each waiting for its automatic retry.
        # A timer per run; the record says when it is due, so a host that
        # starts again can arm it again (see rearm_retries).
        self.retry_policy = RetryPolicy()
        self.retries: dict[str, asyncio.Task] = {}
        self.clock = lambda: datetime.now(timezone.utc)

    def run_tools(self, request, *, owner=None):
        """The tools one run may use, and where its calls actually run.

        A sandbox factory is consulted per run, so the executor carries that
        run's grant rather than the host's whole tool set.
        """

        tools = bind_resources(self.tools, self.resources, request, self.source_extensions) if self.resources is not None else self.tools
        tools = tools.copy()
        tools.max_output_chars = request['definition']['run_config']['max_tool_output_chars']
        if getattr(self, 'connections', None) is not None:
            tools = self.connections.bind(tools, request['definition'], self.principal.tenant,
                                          owner or self.principal.name, request['application_id'], sandbox=self.sandbox is not None)

        if self.sandbox is not None:
            # The host's registry is shared between runs; a run's grant is not.
            tools.executor = self.sandbox(tools, request)

        return tools

    # How long close() waits for messages still being sent before it gives up
    # on them (they are then recorded as "may not have been sent").
    NOTICE_GRACE = 10.0

    async def _consume(self, stream):
        try:
            async with aclosing(stream):
                async for _ in stream:
                    pass
        except ValueError:
            # A duplicate claim can lose to another process. The durable owner
            # and its status remain authoritative; never rewrite that run.
            pass

    async def _run_then_notify(self, stream, run_id, notify=True):
        try:
            await self._consume(stream)
            # Stopped by a backend of the moment: try again later, by itself.
            self._schedule_retry(run_id)
        finally:
            # The run has stopped. If it now waits for someone and the
            # application says where to tell them, the message goes out on its
            # own: the run is never held up by it, and never fails because of it.
            # An application without the setting costs nothing here.
            if notify:
                self.spawn(self.notifier.deliver(run_id))

    def spawn(self, work):
        """Run background work the host waits for when it closes."""
        task = asyncio.get_running_loop().create_task(work)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def _schedule(self, run_id, *, resume=False, approvals="ask"):
        """Start (or resume) a run as a task. `approvals="refuse"` is for a run
        nobody is watching — a scheduled one: a destructive call is refused
        unless the application authorized its tools up front."""
        active = self.tasks.get(run_id)
        if active is not None:
            if not active[0].done():
                raise ValueError("This request is already running")
            self.tasks.pop(run_id)
        owner = self.run_actor(run_id)
        saved_request = self.repository.get(run_id)["request"]
        definition = saved_request["definition"]
        tools = self.run_tools(saved_request, owner=owner)
        autonomous = definition.get("autonomous_execution", False)
        grants = {agent["id"]: set(agent["tools"]) for agent in definition["agents"]
                  if agent.get("kind", "ai") == "ai"}
        # A run may mix backends, so it is handed the router when there is one.
        provider = self.backends or self.provider
        async def approver(request):
            if autonomous:
                # Up-front authorization is limited to declared tools with known
                # capabilities. Resource and connection boundaries still apply.
                return (request.tool in grants.get(request.agent, set())
                        and bool(request.capabilities)
                        and request.capabilities <= frozenset(Capability))
            # A destructive call is answered by the person running it: the
            # connection's owner when a connection is involved, the run's owner
            # otherwise. Every write tool asks, not only a connected service.
            if not self.interactive_approvals or approvals == "refuse" or owner is None:
                return False
            return await self.tool_approvals.approver(run_id, owner)(request)
        # A resumed run is no longer paused, or it would stop again immediately.
        self.pauses.discard(run_id)
        stream = (self.repository.resume(run_id, provider, tools, loop=self.loop, approver=approver,
                                         pause_requested=lambda: run_id in self.pauses) if resume
                  else self.repository.execute(run_id, provider, tools, loop=self.loop, approver=approver,
                                               pause_requested=lambda: run_id in self.pauses))
        # Claim and commit the first event before returning success to Node.
        try:
            await anext(stream)
        except BaseException:
            await stream.aclose()
            raise
        if autonomous:
            self.repository._append(run_id, {"type": "ToolAuthorizationGranted",
                "mode": "autonomous", "authorizer": owner or self.principal.name,
                "tools": {agent: sorted(names) for agent, names in grants.items()}})
        notify = bool((definition.get("notifications") or {}).get("waiting"))
        task = asyncio.create_task(self._run_then_notify(stream, run_id, notify))
        self.tasks[run_id] = (task, stream)

        def forget_task(done):
            if self.tasks.get(run_id) == (done, stream):
                self.tasks.pop(run_id)
                # A pause asked for too late to be used must not outlive the run.
                self.pauses.discard(run_id)

        task.add_done_callback(forget_task)

    # -- Automatic retry ----------------------------------------------------------

    def _schedule_retry(self, run_id):
        """Record when a run a backend stopped will be tried again, and arm it.

        Only a stop the run marked transient (a timeout, an outage, a usage
        limit) is retried, only while no action's outcome is uncertain, and
        only `retry_policy.attempts` times since a step last completed.
        """
        policy = self.retry_policy
        if not policy.attempts:
            return
        # The end of the record only: a long run's events are not read back.
        facts = self.repository.retry_facts(run_id)
        finished = facts["stop"]
        if facts["status"] != "stopped" or not finished.get("transient") or finished.get("budget"):
            return
        attempt = facts["tries"] + 1
        if attempt > policy.attempts or self.repository.journal.uncertain(run_id):
            return
        delay = policy.delay(attempt)
        due = (self.clock() + timedelta(seconds=delay)).isoformat()
        self.repository._append(run_id, {"type": "RetryScheduled", "attempt": attempt, "attempts": policy.attempts,
                                         "delay_seconds": delay, "retry_at": due, "reason": finished["transient"]})
        self._arm_retry(run_id, due)

    def _arm_retry(self, run_id, due, start=None):
        """Wait for the retry recorded for `due`; `start` is when to begin, if not then."""
        self._disarm_retry(run_id)
        task = asyncio.get_running_loop().create_task(self._retry_when_due(run_id, due, start))
        self.retries[run_id] = task

        def forget(done):
            if self.retries.get(run_id) is done:
                self.retries.pop(run_id)
        task.add_done_callback(forget)

    def _disarm_retry(self, run_id) -> bool:
        """Stop waiting to retry a run. True when a timer was armed."""
        task = self.retries.pop(run_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        return task is not None

    async def _retry_when_due(self, run_id, due, start=None):
        wait = (datetime.fromisoformat(start or due) - self.clock()).total_seconds()
        if wait > 0:
            await asyncio.sleep(wait)
        # The retry this timer was armed for must still be the run's last word:
        # a resume, a cancel or a person stopping the retry each supersede it.
        latest = self.repository.latest(run_id)
        if (_pending(latest["status"], latest["last"]) or {}).get("at") != due:
            return
        if self.retries.get(run_id) is asyncio.current_task():
            self.retries.pop(run_id)
        # Nobody presses this Continue, so it acts only as someone the
        # deployment still admits, the way a schedule does.
        owner = self.run_actor(run_id)
        admits = getattr(self.schedules, "admits", None)
        if admits is not None and owner is not None and not admits(owner):
            self.repository._append(run_id, {"type": "RetryCancelled", "reason": "owner_not_admitted", "by": "host"})
            return
        # A scheduled run had nobody to approve its writes, and still has none.
        approvals = "refuse" if latest["trigger"] == "schedule" else "ask"
        try:
            await self._schedule(run_id, resume=True, approvals=approvals)
        except Exception as error:
            # It stays stopped and resumable; the person can continue it.
            LOG.warning("Automatic retry of run %s could not start: %s", run_id, error)

    # Seconds between overdue retries armed again when a host starts.
    REARM_SPACING = 2.0

    def rearm_retries(self) -> list[str]:
        """Arm the retries a previous host recorded and did not get to. Returns their runs."""
        armed, overdue = [], 0
        now = self.clock()
        for run_id, event in self.repository.pending_retries():
            try:
                due = datetime.fromisoformat(event["retry_at"])
            except (KeyError, TypeError, ValueError):
                due = None
            if due is None or due.tzinfo is None:
                # A store this host cannot read is left for a person, not a crash.
                LOG.warning("Skipping the automatic retry of run %s: its retry time cannot be read", run_id)
                continue
            if run_id not in self.retries and run_id not in self.tasks:
                start = None
                if due <= now:
                    # Overdue while no host ran: started a little apart, so a
                    # backend that was just failing is not met by all of them at once.
                    start, overdue = (now + timedelta(seconds=self.REARM_SPACING * overdue)).isoformat(), overdue + 1
                self._arm_retry(run_id, event["retry_at"], start)
                armed.append(run_id)
        return armed

    async def until_idle(self, run_id):
        """Wait until a run is neither working nor waiting for an automatic retry."""
        while True:
            active = self.tasks.get(run_id)
            waiting = active[0] if active else self.retries.get(run_id)
            if waiting is None:
                return
            await asyncio.wait({waiting})

    async def prepare_run(self, value, actor):
        """The checks every run passes before it is created, whoever starts it.

        A person's start and a schedule's time both come through here, acting
        as `actor`: the request is validated, the team's tools are bound as
        that person, the backends and host connectors are checked, and the
        source (and its version) is pinned. Returns the request to record.
        """
        value = validate_run_request(value)
        if "resources" not in value and value["definition"].get("resources"):
            value["resources"] = dict(value["definition"]["resources"])
        validate_host_tools(value["definition"], self.run_tools(value, owner=actor.name))
        self._validate_backends(value["definition"])

        self.run_tools(value, owner=actor.name)
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
        return value

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
                         "integrations": self._integration_descriptors(),
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
                         # The most steps of one run that work at once here, so
                         # Studio can offer it as the ceiling of Steps at once.
                         "max_parallel_steps": self.repository.max_parallel_steps,
                         "features": {"dag": True, "durable_reviews": True, "manual": True,
                                      "schedules": self.schedules is not None, "events": streaming, "event_triggers": True, "budget_warnings": True, "run_usage": True, "run_compare": True, "output_files": True, "step_time_limits": True, "application_roles": True,
                                      "notifications": getattr(self, "connections", None) is not None,
                                      "studio_url": bool(self.studio_url)}}
        if command == "refine":
            # One model call that only sharpens the person's own words.
            self._require_provider()
            return 200, await refine(self.provider, value)
        if command == "questions":
            # One model call that asks what only the person can answer. Answering
            # is theirs to skip, so the limit is the host's policy, not theirs.
            self._require_provider()
            return 200, await questions(self.provider, value, limit=goal_question_limit())
        if command == "propose":
            self._require_provider()
            return 200, await propose(self.provider, self.tools, value)
        if command == "start":
            self._require_provider()
            actor = principal or self.principal
            value = await self.prepare_run(value, actor)
            run_id = self.repository.create(value, actor=actor.name)
            if getattr(self, 'connections', None):
                self.connections.pin_run(run_id, actor.tenant, actor.name)
            await self._schedule(run_id)
            return 201, public_record(self.repository.get(run_id), (principal or self.principal).name)
        if command == "hook":
            # Only a transport that verified the delivery signature reaches here.
            if set(value) != {'connection_id', 'payload', 'definition', 'revision', 'owner', 'inputs'}:
                raise ValueError('Signed hook deliveries are the only way to trigger this run')
            store = getattr(self, 'connections', None)
            if store is None:
                raise ValueError('Integration connections are not configured on this host')
            record = store.hook(value['connection_id'])
            if record['owner'] != value['owner']:
                raise ValueError('Hook connection owner does not match the delivery')
            application = record['applications'][0]
            trigger = store.start_hook_run(record, value['payload'])
            request_value = validate_run_request({'version': 1, 'application_id': application,
                'revision': value['revision'], 'definition': value['definition'],
                'task': trigger['task'], 'inputs': value['inputs']})
            run_id = self.repository.create(request_value, actor=record['owner'])
            store.pin_run(run_id, self.principal.tenant, record['owner'])
            await self._schedule(run_id)
            return 201, {'run_id': run_id, 'application_id': application, 'delivery_id': trigger['delivery_id'],
                         'received_fields': trigger['received'], 'connection_id': record['id']}
        if command == "list":
            if set(value) != {"application_id"} or not isinstance(value["application_id"], str):
                raise ValueError("Choose an application")
            runs = self.repository.list(value['application_id'])
            if self.application_access is not None and not self.application_access.exists(value['application_id']):
                # Its application is gone: each run shows only to whoever may still open it.
                runs = [run for run in runs if self._may_open(run['id'], principal)]
            if getattr(self, 'connections', None):
                name = (principal or self.principal).name
                # Their own runs, and any run whose waiting review is for them.
                runs = [run for run in runs if self.connections.run_owner(run['id'], self.principal.tenant) in (None, name)
                        or run['status'] == 'waiting_for_human' and self._may_review(run['id'], name)]
            return 200, {'runs': runs}
        if command in {"actions", "check", "settle"}:
            return 200, await self._actions_command(command, value, principal or self.principal)
        if command == "file":
            # A stored file: read by whoever may open the run, and only if it is
            # still exactly what the run stored.
            if not isinstance(value, dict) or set(value) != {"run_id", "file_id"} or not all(isinstance(value[key], str) for key in value):
                raise ValueError("Choose a run and one of its files")
            self._require_run_owner(value["run_id"], principal, reviewing=True)
            return 200, await asyncio.to_thread(self.repository.read_file, value["run_id"], value["file_id"])
        if command == "compare":
            # Two runs of one application, step by step. Only for whoever may
            # open both: a person a waiting review is for may not compare.
            if not isinstance(value, dict) or not {"a", "b"} <= set(value) <= {"a", "b", "order"} \
                    or not all(isinstance(value[key], str) for key in ("a", "b")):
                raise ValueError("Choose two runs to compare")
            for run_id in (value["a"], value["b"]):
                self._require_run_owner(run_id, principal, reviewing=False, permission="view")
            viewer = (principal or self.principal).name

            def compare():
                # Reading two long runs and weighing their outputs takes a
                # while: done beside the host's loop, so nothing else waits.
                records = [public_record(self.repository.get(run_id), viewer) for run_id in (value["a"], value["b"])]
                return compare_runs(*records, order=value.get("order", "time"))
            return 200, await asyncio.to_thread(compare)
        if command in {"get", "resume", "cancel", "pause", "cancel_retry"}:
            if set(value) != {"run_id"} or not isinstance(value["run_id"], str):
                raise ValueError("Choose a saved request")
            run_id = value["run_id"]
            self._require_run_owner(run_id, principal, reviewing=command == "get", permission="view" if command == "get" else "run")
            record = self.repository.get(run_id)
            by = (principal or self.principal).name
            if command == "cancel_retry" or command == "pause" and run_id not in self.tasks and pending_retry(record):
                # "Don't retry", or a pause while it waits to: the run stays
                # stopped, and Continue still continues it.
                if pending_retry(record) is None:
                    raise ValueError("This run has no automatic retry to stop")
                self._disarm_retry(run_id)
                self.repository._append(run_id, {"type": "RetryCancelled", "reason": "paused" if command == "pause" else "cancelled", "by": by})
            elif command == "pause":
                # Asked between steps: the run finishes the step it is in and
                # stops with a checkpoint, so resuming continues from there.
                if run_id not in self.tasks:
                    raise ValueError("Only a run that is still going can be paused")
                self.pauses.add(run_id)
            elif command == "resume":
                self._require_provider()
                # Continue now rather than at the retry's time.
                self._disarm_retry(run_id)
                # Whatever was left uncertain is checked with its service
                # first; the run continues only once everything is settled.
                if (self.repository.get(run_id)["status"] in {"interrupted", "stopped", "paused", "waiting_for_human"}
                        and self.repository.journal.uncertain(run_id)):
                    await self.repository.check_actions(run_id, self._action_tools(run_id))
                await self._schedule(run_id, resume=True)
            elif command == "cancel":
                self.pauses.discard(run_id)
                self._disarm_retry(run_id)
                active = self.tasks.get(run_id)
                if active is not None:
                    task, stream = active
                    # A person stopping a run cancels it: it is not a run the
                    # host lost, and it is not offered to continue.
                    self.repository.stopping.add(run_id)
                    try:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        await stream.aclose()
                    finally:
                        self.repository.stopping.discard(run_id)
                if active is None or self.repository.get(run_id)["status"] in {"waiting_for_human", "paused"}:
                    self.repository.cancel_waiting(run_id)
            return 200, public_record(self.repository.get(run_id), (principal or self.principal).name)
        if command == "review":
            if set(value) != {"run_id", "review_id", "output"} or not isinstance(value["run_id"], str) or not isinstance(value["review_id"], str):
                raise ValueError("Choose the waiting review and provide your contribution")
            reviewer = (principal or self.principal).name
            self._require_run_owner(value["run_id"], principal, reviewing=True, permission="run")
            self.repository.submit_review(value["run_id"], value["review_id"], reviewer=reviewer, output=value["output"])
            await self._continue_after_review(value["run_id"])
            return 200, public_record(self.repository.get(value["run_id"]), (principal or self.principal).name)
        raise ValueError("Unknown local run command")

    async def _continue_after_review(self, run_id):
        """Continue a run whose review now has every answer it needs.

        Only for an application with a review policy: its reviewers may be
        people who cannot continue the run, so nobody has to. The run continues
        as its owner, as a Continue would. An application without a policy is
        continued by whoever answered, as before; a run with an action still
        uncertain waits for its owner to settle it.
        """
        # Decided from the light read: whether a review still waits needs no events.
        record = self.repository.review_access(run_id)
        if ("review_policy" not in record["request"]["definition"] or record["status"] != "waiting_for_human"
                or record["review"] is not None
                or self.provider is None or self.repository.journal.uncertain(run_id)):
            return
        # Nobody presses this Continue either: it acts only as an owner the
        # deployment still admits, as a retry and a schedule do.
        owner = self.run_actor(run_id)
        admits = getattr(self.schedules, "admits", None)
        if admits is not None and owner is not None and not admits(owner):
            self.repository._append(run_id, {"type": "ContinueWithheld", "reason": "owner_not_admitted"})
            return
        # Nor as someone whose role no longer lets them run this application.
        if owner is not None and self.application_access is not None:
            from .access import role_allows
            try:
                role, _ = self.application_access.role_of(self.repository.application_of(run_id), owner)
            except KeyError:
                role = None
            if not role_allows(role, "run"):
                self.repository._append(run_id, {"type": "ContinueWithheld", "reason": "owner_lacks_role"})
                return
        # A scheduled run had nobody to approve its writes, and still has none.
        approvals = "refuse" if (record["request"].get("trigger") or {}).get("kind") == "schedule" else "ask"
        try:
            await self._schedule(run_id, resume=True, approvals=approvals)
        except Exception as error:
            # The answer is recorded either way; the run waits for its owner to continue it.
            LOG.warning("Run %s could not continue after its review: %s", run_id, error)

    def _may_review(self, run_id, name):
        """Whether `name` is someone this run's waiting review is for: one who may
        answer it, or who already did. Only while it waits. Reads no events."""
        from .runs import review_refusal
        access = self.repository.review_access(run_id)
        review = access["review"]
        if access["status"] != "waiting_for_human" or review is None:
            return False
        if name.strip().casefold() in {item.casefold() for item in review["answered"]}:
            return True
        return review_refusal(access["request"]["definition"], review["stage"], starter=access["started_by"],
                              answered=review["answered"], reviewer=name) is None

    def _require_run_owner(self, run_id, principal, *, reviewing=False, permission="view"):
        """Runs with connections are their owner's. A person a waiting review is
        for may also open it (`reviewing`), and nothing else. Every run is also
        its application's: the caller's role there must allow `permission`."""
        if getattr(self, 'connections', None):
            owner = self.connections.run_owner(run_id, self.principal.tenant)
            name = (principal or self.principal).name
            if owner is not None and owner != name and not (reviewing and self._may_review(run_id, name)):
                raise ValueError('Integration run belongs to another principal')
        self._require_run_role(run_id, principal, permission, reviewing=reviewing)

    def _require_run_role(self, run_id, principal, permission, *, reviewing=False):
        """The caller's role on the run's application, when this host knows roles.

        No role: the run does not exist for them (404), unless a waiting review
        is for them. A role too low: refused, saying why (403). A run whose
        application was deleted stays with whoever started it.
        """
        from .access import RoleRefused, refusal, role_allows
        access = getattr(self, "application_access", None)
        if access is None:
            return
        name = (principal or self.principal).name
        application = self.repository.application_of(run_id)
        try:
            role, _ = access.role_of(application, name)
        except KeyError:
            # Its application is gone (or never was saved here): the run stays
            # with whoever started it, and one nobody is recorded as starting
            # stays as open as it was before roles.
            starter = self.repository.actor(run_id)
            role = "owner" if starter is None or starter == name else None
        if role_allows(role, permission):
            return
        if reviewing and self._may_review(run_id, name):
            return
        if role is None:
            raise KeyError(run_id)
        raise RoleRefused(refusal(role, permission))

    def _may_open(self, run_id, principal):
        from .access import RoleRefused
        try:
            self._require_run_role(run_id, principal, "view")
            return True
        except (KeyError, RoleRefused):
            return False

    def require_run(self, run_id, principal, permission):
        """For a route outside `handle` that acts on a run."""
        self._require_run_owner(run_id, principal, permission=permission)

    def run_actor(self, run_id):
        """Whom a run acts as, and so who answers its approvals and decisions.

        The connection owner it is pinned to; else the person recorded when it
        was created (whoever started it, or the schedule's owner). A run from
        before that was recorded falls back to this host's principal, unless
        the host is not a person (a tenant started for its schedules): then
        nobody is asked, and nothing is approved.
        """
        owner = self.connections.run_owner(run_id, self.principal.tenant) if getattr(self, 'connections', None) else None
        if owner is None:
            owner = self.repository.actor(run_id)
        if owner is None and self.principal.source != "system":
            owner = self.principal.name
        return owner

    def _run_approver(self, run_id):
        return self.run_actor(run_id)

    def _action_tools(self, run_id, request=None):
        """The run's tools as bound now, so grants and revocations are asked again."""
        owner = self.run_actor(run_id)
        try:
            return self.run_tools(request or self.repository.get(run_id)["request"], owner=owner)
        except Exception:
            # A connection that was removed cannot be read through: its checks
            # are unavailable, and a person decides.
            from conclave import ToolRegistry
            return ToolRegistry(None)

    def _action_label(self, tool, run_id):
        """The service's name when the run's own owner has that connection.

        Read as the run's owner within this tenant only, so a label never
        opens another owner's connection record.
        """
        from .definition import action_label
        parts = tool.split(".")
        store = getattr(self, 'connections', None)
        if len(parts) == 3 and parts[0] == "connection" and store is not None:
            try:
                record = store._read(parts[1], self.principal.tenant, self._run_approver(run_id))
                return f"{store.service_for(record).name} · {parts[2].replace('_', ' ').capitalize()}"
            except Exception:
                pass
        return action_label(tool)

    async def _actions_command(self, command, value, principal):
        """List a run's external actions, check one, or record a person's decision.

        Decisions are recorded as the principal making the request, never as
        a name in the payload.
        """
        allowed = {"actions": ({"run_id"}, set()), "check": ({"run_id", "action_id"}, set()),
                   "settle": ({"run_id", "action_id", "decision"}, {"note"})}[command]
        if not allowed[0] <= set(value) <= allowed[0] | allowed[1] or any(
                not isinstance(value[key], str) for key in allowed[0]):
            raise ValueError("Choose the run and the action, and an explicit decision")
        if "note" in value and not isinstance(value["note"], str):
            raise ValueError("A note is text")
        run_id = value["run_id"]
        self._require_run_owner(run_id, principal, permission="run")
        # Deciding an action — "send it again" above all — is approving it, so
        # only the person who answers this run's tool approvals may see the
        # details or decide: the connection's owner, or this host's principal.
        approver = self._run_approver(run_id)
        if approver is None or principal.name != approver:
            raise ValueError("Only the person who approves this run's actions can see or decide them")
        if command == "check":
            await self.repository.check_actions(run_id, self._action_tools(run_id), only=value["action_id"])
        elif command == "settle":
            note = value.get("note") or None
            self.repository.settle(run_id, value["action_id"], value["decision"], by=principal.name, note=note)
        return {"actions": self._action_views(run_id)}

    def _action_views(self, run_id):
        """Each action as a person deciding about it needs to see it.

        The arguments appear only while they could still be sent: for an
        uncertain action, and for one a person chose to send again.
        """
        from .definition import action_label
        from .runs import TERMINAL
        # One record, one list and one read of the details, however many actions.
        record = self.repository.get(run_id)
        tools = self._action_tools(run_id, record["request"])
        details = self.repository.journal.details(run_id)
        views = []
        for action in self.repository.actions(run_id, record=record):
            tool = tools.tool(action["tool"])
            detail = details.get(action["id"], {})
            arguments = detail.get("arguments")
            check = getattr(tool, "check", None)
            described = ""
            if check is not None and action["state"] == "uncertain":
                try:
                    described = check.describe(detail.get("call") or {})
                except Exception:
                    described = ""
            by_person = action["settled_kind"] == "person"
            view = {key: action[key] for key in ("id", "stage", "agent", "tool", "state", "attempts", "started_at",
                                                 "finished_at", "reason", "approval", "settled_by", "settled_kind",
                                                 "settled_at", "final")}
            view.update(
                label=self._action_label(action["tool"], run_id),
                note=detail.get("note") if by_person else None,
                undoable=by_person and action["state"] in {"done", "resend"} and not action["final"]
                and record["status"] not in TERMINAL,
                check={"available": check is not None, "description": described,
                       "unavailable_reason": "" if check is not None else (
                           tool.check_unavailable if tool is not None and tool.check_unavailable
                           else "This host no longer offers this tool" if tool is None
                           else f"{action_label(action['tool'])} cannot be checked automatically"),
                       "result": action["check_result"], "summary": action["check_summary"], "checked_at": action["checked_at"]},
            )
            if arguments is not None:
                view["arguments"] = arguments
            views.append(view)
        return views

    @staticmethod
    def _integration_group(category):
        """A stable family name, so a plan can offer real alternatives."""
        return re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-")

    def _integration_descriptors(self):
        """What the catalogue may advertise, and only what this host can run.

        Availability is whether an execution adapter exists, never whether a
        person has connected an account: a connection belongs to one owner and
        one application, so claiming it here would speak for everyone.
        """
        from conclave.integrations.services import integrations
        store = getattr(self, 'connections', None)
        instances = list(store.adapters.instances.items()) if store and store.adapters else []
        configured = {service.id for _, service in instances}
        # Each host instance is its own catalogue entry: a person picks the
        # account or endpoint, not just the service name.
        choices = [(identifier, service.name, service)
                   for identifier, service in instances
                   if service.operations]
        choices += [(identifier, service.name, service)
                    for identifier, service in (store.catalogue() if store else integrations()).items()
                    if identifier not in configured]
        return [{"id": identifier, "name": name, "group": self._integration_group(service.category), "category": service.category,
                 "description": "Connect an account to use " + name + ".",
                 "default_mode": "read" if {item.capability for item in service.operations} <= {"read"} else "draft",
                 "status": "available" if service.operations else "unavailable",
                 "available": bool(service.operations), "origin": "integration",
                 "operations": [{"name": item.name, "description": item.description, "capability": item.capability}
                                for item in service.operations]}
                for identifier, name, service in choices]

    async def close(self):
        # A retry still waiting stays on the record; a host that starts again arms it.
        waiting = list(self.retries.values())
        self.retries.clear()
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        active = list(self.tasks.values())
        for task, stream in active:
            task.cancel()
        await asyncio.gather(*(task for task, stream in active), return_exceptions=True)
        for task, stream in active:
            await stream.aclose()
        # Messages still being sent get a moment to finish; one cut off here
        # stays "sending", and the next host to open the store records that it
        # may not have gone out. It is never sent twice.
        pending = [task for task in self.background if not task.done()]
        if pending:
            _, late = await asyncio.wait(pending, timeout=self.NOTICE_GRACE)
            for task in late:
                task.cancel()
            await asyncio.gather(*late, return_exceptions=True)


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


def run_journal_key():
    """The key that seals what a run records about its external actions.

    `CONCLAVE_RUN_KEY` when set; otherwise derived from the integration key
    for this one purpose (HKDF-SHA256), so one key never serves two. None when
    neither is set: the run database is then this user's alone, unsealed.
    """
    import base64
    explicit = setting("RUN_KEY")
    if explicit:
        return explicit.encode()
    integration = setting("INTEGRATION_KEY")
    if not integration:
        return None
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"conclave-run-journal-v1").derive(integration.encode())
    return base64.urlsafe_b64encode(derived)


def configured_host(*, workspace=None, source_dir=None, principal=None, interactive_approvals=True):
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
    # PDF and Word documents are read as text (conclave/tools/documents.py).
    source_extensions = {item.strip().lower() for item in setting("SOURCE_EXTENSIONS", ".txt,.md,.csv,.html,.pdf,.docx").split(',')}
    from conclave.tools.documents import Limits
    Limits.from_environment()  # a bad CONCLAVE_DOCUMENT_* setting stops the host here, by name
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
    run_key = run_journal_key()
    repository = RunRepository(paths.runs, **({"key": run_key} if run_key else {}))
    repository.history_limit = history_limit
    repository.max_parallel_steps = parallel_step_cap(setting("MAX_PARALLEL_STEPS", str(MAX_PARALLEL_STEPS)))
    # This host is taking over the store, so nothing in it can still be running.
    repository.sweep_abandoned()
    host = LocalRunHost(repository, provider, tools,
                        reviewer=principal.name, provider_name=name, unavailable=reason, adapters=adapters,
                        resources=resources, source_extensions=source_extensions, provider_source=source,
                        profile=profile, principal=principal, paths=paths, backends=backends,
                        interactive_approvals=interactive_approvals)
    host.studio_url = studio_address(setting("STUDIO_URL"))
    host.retry_policy = retry_policy_from_settings()
    host.notify_run_interval = whole_number("NOTIFY_RUN_INTERVAL", setting("NOTIFY_RUN_INTERVAL", "600"), minimum=0)
    host.notify_hourly_limit = whole_number("NOTIFY_HOURLY_LIMIT", setting("NOTIFY_HOURLY_LIMIT", "20"), minimum=1)
    key = setting('INTEGRATION_KEY')
    if key:
        from conclave.integrations import ConnectionStore
        from conclave.integrations.host import HostAdapters
        adapter_file = setting('INTEGRATION_ADAPTERS_FILE')
        adapters = HostAdapters(json.loads(Path(adapter_file).read_text())) if adapter_file else None
        oauth_file = setting('INTEGRATION_OAUTH_FILE')
        oauth = None
        if oauth_file:
            from conclave.integrations.oauth import OAuth
            from conclave.integrations.transport import Transport
            providers = json.loads(Path(oauth_file).read_text())
            oauth = OAuth(providers, Transport({host for config in providers.values() for host in config['hosts']}))
        host.connections = ConnectionStore(paths.root / 'connections.sqlite', key.encode(),
                                           hosts=filter(None, setting('INTEGRATION_HOSTS', '').split(',')), adapters=adapters, oauth=oauth)
    return host


def parallel_step_cap(value):
    """How many steps of one run this host lets work at once.

    An application's "Steps at once" may be blank, meaning as many as the host
    allows; this is that number, so a blank never means as many as a plan has.
    """
    cap = whole_number("MAX_PARALLEL_STEPS", value, minimum=1)
    if cap > MAX_PARALLEL_STEPS_CEILING:
        raise ValueError(f"CONCLAVE_MAX_PARALLEL_STEPS must be at most {MAX_PARALLEL_STEPS_CEILING}")
    return cap


def whole_number(name, value, *, minimum):
    try:
        number = int(str(value).strip())
    except ValueError:
        number = None
    if number is None or number < minimum:
        raise ValueError(f"CONCLAVE_{name} must be a whole number of at least {minimum}")
    return number


def studio_address(value):
    """Where Studio is served from, for the link in a 'waiting for you' message."""
    if not value:
        return None
    from urllib.parse import urlsplit
    parts = urlsplit(str(value).strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment or "#" in str(value):
        raise ValueError("CONCLAVE_STUDIO_URL must be an http(s) address with no query or fragment, such as https://studio.example.com")
    return str(value).strip().rstrip("/")


def request_body_limit():
    """Host transport policy, separate from application work/output limits."""
    value = setting("MAX_REQUEST_BYTES", "200000")
    if str(value).lower() in {"none", "unlimited"}:
        return None
    limit = int(value)
    if limit < 1:
        raise ValueError("CONCLAVE_MAX_REQUEST_BYTES must be positive or unlimited")
    return limit


def goal_question_limit():
    """How many questions a host may ask about one goal, at most.

    A host policy, not part of a definition: it bounds what this deployment asks
    of the person, and every application here is asked the same way.
    """

    value = str(setting("MAX_GOAL_QUESTIONS", "5")).strip()
    if not value.isdigit() or not 1 <= int(value) <= 20:
        raise ValueError("CONCLAVE_MAX_GOAL_QUESTIONS must be between 1 and 20")
    return int(value)

