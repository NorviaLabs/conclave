"""Shared builders for the "this run is waiting for you" tests.

As for the earlier features, nothing here imports the new interfaces at module
level: each test imports what it exercises, so before the feature exists every
test fails on its own.
"""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from conclave import ToolRegistry
from conftest import CONFIG, ScriptedProvider

CASES = json.loads((Path(__file__).parent / "data" / "notification_cases.json").read_text())
STUDIO = "https://studio.acme.example"
ANA, BEA = "ana@acme.test", "bea@acme.test"

# Strings that must never reach a message: what the run was asked, given and
# produced, and a credential.
CANARY = {"task": "CANARY-TASK-7f3", "input": "CANARY-INPUT-91c", "output": "CANARY-OUTPUT-5d2",
          "secret": "CANARY-SECRET-xoxb-44", "instructions": "CANARY-INSTRUCTIONS-0a1"}


def slack(**change):
    return {"via": "slack", "connection": "team_slack", "channel": "#reviews", **change}


def email(**change):
    return {"via": "email", "connection": "ana_gmail", "address": "billing-leads@acme.example", **change}


def webhook(**change):
    return {"via": "webhook", "connection": "ops_hook", **change}


def definition(block=None, *, name="Support digest"):
    """Writer → Support lead (a person) → Sender. `block` is `notifications.waiting`."""
    value = {
        "version": 1, "name": name, "description": "Summarise support tickets " + CANARY["instructions"],
        "inputs": [{"id": "team", "label": "Team", "type": "text", "required": True}],
        "agents": [{"id": "writer", "name": "Writer", "instructions": "Write " + CANARY["instructions"], "tools": []},
                   {"id": "lead", "name": "Support lead", "kind": "human", "instructions": "Check it " + CANARY["instructions"], "tools": []},
                   {"id": "sender", "name": "Sender", "instructions": "Send it", "tools": []}],
        "state": [{"key": "digest", "type": "text", "merge": "replace", "statuses": []},
                  {"key": "review", "type": "text", "merge": "replace", "statuses": []},
                  {"key": "sent", "type": "text", "merge": "replace", "statuses": []}],
        "workflow": [{"id": "write", "name": "Write the digest", "agent": "writer", "task": "Write " + CANARY["instructions"], "output": "digest"},
                     {"id": "review", "name": "Review the digest", "agent": "lead", "task": "Review " + CANARY["instructions"], "output": "review"},
                     {"id": "send", "name": "Send the digest", "agent": "sender", "task": "Send", "output": "sent"}],
        "views": [{"id": "digest_view", "collection": "digest", "layout": "prose"}],
        "run_config": dict(vars(CONFIG)),
        "run_budget": {"max_delegations": None, "max_per_agent": None, "max_depth": None,
                       "tool_calls": None, "wall_clock_seconds": None, "tokens": None, "cost_usd": None},
    }
    if block is not None:
        value["notifications"] = {"waiting": block}
    return value


def run_request(block=None, *, application_id="digest", revision=1, **definition_options):
    return {"version": 1, "application_id": application_id, "revision": revision, "task": "Summarise " + CANARY["task"],
            "inputs": {"team": CANARY["input"]}, "definition": definition(block, **definition_options)}


def provider():
    """The writer and the sender answer at once; the lead is a person."""
    return ScriptedProvider({"writer": [{"type": "final", "content": "Done", "text": CANARY["output"]}] * 4,
                             "sender": [{"type": "final", "content": "Done", "text": "Sent"}] * 4})


SERVICES = {"slack": ("Slack", "send_message"), "gmail": ("Gmail", "send_email"), "webhooks": ("Webhooks", "send")}


class FakeConnections:
    """Stands in for `ConnectionStore`: who owns which connection, what it is
    granted, and what would have been sent. Nothing leaves the test."""

    adapters = None  # no host-configured instances

    def __init__(self):
        self.records, self.sent, self.pins = {}, [], {}
        self.fail = None          # an exception the next sends raise
        self.gate = None          # an asyncio.Event a send waits on

    def add(self, identifier, *, owner, integration, tenant="local", applications=(), operations=None, name=None):
        self.records[identifier] = {"id": identifier, "integration": integration, "name": name or identifier, "owner": owner,
                                    "tenant": tenant, "applications": list(applications), "scopes": [],
                                    "operations": list(operations if operations is not None else [SERVICES[integration][1]]),
                                    "credentials": {"token": CANARY["secret"]}, "config": {}}
        return self

    def grant(self, identifier, application):
        self.records[identifier]["applications"].append(application)

    def revoke(self, identifier, application):
        self.records[identifier]["applications"].remove(application)

    # -- what the host calls ------------------------------------------------------

    def _read(self, identifier, tenant, owner):
        from conclave.integrations.transport import IntegrationError
        record = self.records.get(identifier)
        if record is None or record["tenant"] != tenant or record["owner"] != owner:
            raise IntegrationError("Connection is missing or not authorized")
        return copy.deepcopy(record)

    def service_for(self, record):
        return SimpleNamespace(id=record["integration"], name=SERVICES[record["integration"]][0])

    def public(self, record):
        return {key: record[key] for key in ("id", "integration", "name", "applications", "operations", "scopes")}

    def list(self, tenant, owner):
        return [self.public(record) for record in self.records.values() if record["tenant"] == tenant and record["owner"] == owner]

    async def invoke(self, identifier, operation, arguments, tenant, owner, application):
        from conclave.actions import current_action
        from conclave.integrations.transport import IntegrationError
        record = self._read(identifier, tenant, owner)
        if application not in record["applications"] or operation not in record["operations"]:
            raise IntegrationError("Connection operation is not granted to this application")
        entry = {"connection": identifier, "operation": operation, "arguments": copy.deepcopy(arguments),
                 "owner": owner, "application": application, "action": current_action.get()}
        self.sent.append(entry)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return json.dumps({"ok": True})

    def catalogue(self):
        from conclave.integrations.services import integrations
        return integrations()

    def pin_run(self, run_id, tenant, owner):
        self.pins[run_id] = owner

    def run_owner(self, run_id, tenant):
        return self.pins.get(run_id)

    def bind(self, registry, definition, tenant, owner, application, *, sandbox=False):
        return registry.copy()


def host(tmp_path, *, connections=None, studio_url=STUDIO, principal=None, profile=None):
    from conclave.application.principal import Principal
    from conclave.application.runs import RunRepository
    from conclave.application.service import LocalRunHost

    options = {"profile": profile} if profile is not None else {}
    made = LocalRunHost(RunRepository(tmp_path / "runs.sqlite"), provider(), ToolRegistry(20000), reviewer=ANA,
                        principal=principal or Principal(ANA), **options)
    made.connections = connections
    made.studio_url = studio_url
    return made


def api(tmp_path, **options):
    from conclave.api import ApplicationAPI
    from conclave.application.workspace import Workspace

    return ApplicationAPI(host(tmp_path, **options), Workspace(tmp_path / "applications.sqlite"))


async def settle(made, *, seconds=5):
    """Wait until the host's runs and its background sends have finished."""
    async with asyncio.timeout(seconds):
        while True:
            pending = [task for task, _ in list(made.tasks.values()) if not task.done()]
            pending += [task for task in list(getattr(made, "background", ())) if not task.done()]
            if not pending:
                return
            await asyncio.sleep(0.01)


def events(made, run_id, kind=None):
    listed = [row["event"] for row in made.repository.get(run_id)["events"]]
    return [event for event in listed if kind is None or event["type"] == kind]


def waiting_event(made, run_id):
    return next(row for row in reversed(made.repository.get(run_id)["events"])
                if row["event"]["type"] == "ApplicationFinished" and row["event"]["status"] in {"waiting_for_human", "paused"})
