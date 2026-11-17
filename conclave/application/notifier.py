"""Sends the one 'this run is waiting for you' message for each waiting point.

A notice is due from the moment the run stops (it is written with that event).
The notifier claims it, checks the run still waits there and that the owner
can send through the chosen connection, sends it through the connection store
like any other call, and records what happened as an event on the run.

It runs after the run has stopped, in a task of its own, and never raises:
a message that cannot be sent is recorded, and the run is not affected.
"""

from __future__ import annotations

import logging

from .notices import CHANNELS, waiting_message

LOG = logging.getLogger(__name__)
MOVED_ON = "The run had moved on before the message could be sent"
NOT_SENT = "The message could not be sent"


def host_problem(host, owner: str | None) -> str:
    """What stops every message on this host, which anyone may be told."""

    if getattr(host, "connections", None) is None:
        return "This host has no connections"
    if not getattr(host, "studio_url", None):
        return "This host has no Studio address (CONCLAVE_STUDIO_URL)"
    if not owner:
        return "Nobody owns these notifications yet: save the application to make them yours"
    return ""


def problem(host, block: dict, owner: str | None, application_id: str) -> str:
    """Why no message could be sent now, or "" when one could. Beyond what is
    true of the host, the reasons describe the owner's own connection."""

    general = host_problem(host, owner)
    if general:
        return general
    store = host.connections
    service, operation, label, _ = CHANNELS[block["via"]]
    connection = block["connection"]
    try:
        record = store._read(connection, host.principal.tenant, owner)
    except Exception:
        return f"{connection} is not {owner}'s connection"
    if record.get("integration") != service:
        return f"{connection} is not {'an' if label[0] in 'AEIOU' else 'a'} {label} connection"
    if application_id not in record.get("applications", []) or operation not in record.get("operations", []):
        return f"{connection} is not granted {operation} for this application"
    return ""


def target_label(host, block: dict, owner: str | None, *, named: bool = False) -> str:
    """Where the message goes: the channel, the address, or a webhook's
    connection. A connection's own name is its owner's to see (`named`);
    everyone else, and the run's record, get its id."""

    if block["via"] == "slack":
        return block["channel"]
    if block["via"] == "email":
        return block["address"]
    if not named:
        return block["connection"]
    try:
        return host.connections._read(block["connection"], host.principal.tenant, owner)["name"]
    except Exception:
        return block["connection"]


class Notifier:
    def __init__(self, host):
        self.host = host

    @property
    def repository(self):
        return self.host.repository

    def owner(self, application_id: str, run_id: str) -> str | None:
        """Whose account sends: the application's notification owner, or,
        for a definition nobody saved here (run from a file), whoever the run
        acts as."""

        pinned = self.repository.notices.sender(run_id)
        if pinned:
            return pinned
        recorded = self.repository.notification_owners.owner(application_id)
        return recorded["owner"] if recorded else self.repository.actor(run_id)

    async def deliver(self, run_id: str) -> list[dict]:
        return [outcome for notice in self.repository.notices.due(run_id) if (outcome := await self._deliver(notice))]

    async def deliver_all(self) -> list[dict]:
        return [outcome for notice in self.repository.notices.due() if (outcome := await self._deliver(notice))]

    async def _deliver(self, notice: dict) -> dict | None:
        run_id, sequence = notice["run_id"], notice["sequence"]
        try:
            request, event = self.repository.notices.waiting_point(run_id, sequence)
            definition, application_id = request["definition"], request["application_id"]
            block = definition["notifications"]["waiting"]
            owner = self.owner(application_id, run_id)
            stage = event.get("stage") if event.get("status") == "waiting_for_human" else event.get("next_stage")
            step = next(({"id": item["id"], "name": item.get("name") or item["id"]}
                         for item in definition["workflow"] if item["id"] == stage), None)
            detail = {"via": block["via"], "target": target_label(self.host, block, owner), "connection": block["connection"],
                      "sent_as": owner, "step": step,
                      "waiting": "review" if event.get("status") == "waiting_for_human" else "pause"}
        except Exception as error:  # a notice that cannot even be read is not sent
            LOG.warning("Notice %s/%s could not be read: %s", run_id, sequence, type(error).__name__)
            detail, block = {}, None
        if not self.repository.notices.claim(run_id, sequence, detail):
            return None  # another process has it
        if block is None:
            return self._finish(run_id, sequence, "failed", {**detail, "reason": NOT_SENT})
        if not self.repository.notices.still_waiting(run_id, sequence):
            return self._finish(run_id, sequence, "failed", {**detail, "reason": MOVED_ON})
        reason = problem(self.host, block, owner, application_id) or self.over_limit(run_id, sequence, application_id, detail)
        if reason:
            return self._finish(run_id, sequence, "failed", {**detail, "reason": reason})
        try:
            message = waiting_message(definition, application_id=application_id, run_id=run_id,
                                      event={**event, "sequence": sequence}, studio_url=self.host.studio_url)
        except ValueError as error:
            return self._finish(run_id, sequence, "failed", {**detail, "reason": str(error)})
        return self._finish(run_id, sequence, *(await self._send(run_id, sequence, block, message, owner, application_id)), detail)

    def over_limit(self, run_id: str, sequence: int, application_id: str, detail: dict) -> str:
        """Anyone who can start, pause and resume runs makes waiting points; the
        owner's channel is not theirs to flood (security review NS2)."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        interval = getattr(self.host, "notify_run_interval", 600)
        last = self.repository.notices.last_told(run_id, besides=sequence)
        if last and interval and now - datetime.fromisoformat(last) < timedelta(seconds=interval):
            minutes = int((now - datetime.fromisoformat(last)).total_seconds() // 60)
            ago = "less than a minute ago" if minutes < 1 else f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            return f"Not sent: {detail.get('target') or 'the channel'} was told about this run {ago}"
        limit = getattr(self.host, "notify_hourly_limit", 20)
        told = self.repository.notices.told_since(application_id, (now - timedelta(hours=1)).isoformat(),
                                                  besides=(run_id, sequence))
        if told >= limit:
            return f"Not sent: this application has sent {limit} messages in the last hour"
        return ""

    async def _send(self, run_id, sequence, block, message, owner, application_id):
        from conclave.actions import current_action
        from conclave.integrations.transport import IntegrationError

        # A webhook's receiver can drop a repeat by this key (see Idempotency-Key).
        token = current_action.set(f"notice-{run_id}-{sequence}")
        try:
            await self.host.connections.invoke(block["connection"], message.operation, message.arguments,
                                               self.host.principal.tenant, owner, application_id)
            return "sent", {}
        except IntegrationError as error:
            return "failed", {"reason": (str(error) or NOT_SENT)[:200]}
        except Exception as error:
            LOG.warning("Notice %s/%s could not be sent: %s", run_id, sequence, type(error).__name__)
            return "failed", {"reason": NOT_SENT}
        finally:
            current_action.reset(token)

    def _finish(self, run_id, sequence, state, extra, detail=None):
        recorded = {**(detail or {}), **extra}
        self.repository.notices.finish(run_id, sequence, state, recorded)
        return {"run_id": run_id, "sequence": sequence, "state": state, **recorded}
