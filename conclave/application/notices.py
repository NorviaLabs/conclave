"""'This run is waiting for you': the setting, and the message.

An application may say where to send one message when a run stops for a human
review or a pause: a Slack channel, an email address or a webhook, through a
connection its owner chose. The message names the application and the step
that waits, and links to the run. Nothing from the run itself goes in it: no
task, inputs, outputs, state or credentials.

This module is pure: it validates the setting and builds the message. Where a
notice is recorded is `NoticeStore` (runs.py), and sending it is `Notifier`.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import quote

CHANNELS = {
    # via: (service id, operation, what it is called, the field it needs)
    "slack": ("slack", "send_message", "Slack", "channel"),
    "email": ("gmail", "send_email", "Email", "address"),
    "webhook": ("webhooks", "send", "Webhook", None),
}
CONNECTION = re.compile(r"[a-z][a-z0-9_]{0,31}")
SLACK_CHANNEL = re.compile(r"[^\s\x00-\x1f\x7f]{1,80}")
ADDRESS = re.compile(r"[^\s@<>,;:\"()\[\]\\\x00-\x1f\x7f]+@[^\s@<>,;:\"()\[\]\\\x00-\x1f\x7f]+\.[^\s@<>,;:\"()\[\]\\\x00-\x1f\x7f]+")
NAME_LIMIT = 100
WAITING = {"waiting_for_human": "review", "paused": "pause"}
# The run a preview's link points at: the host works out the message, but there
# is no run yet to link to.
PREVIEW_RUN = "example"


def validate_notifications(value):
    """The `notifications` block, or a ValueError that says what is wrong."""

    if not isinstance(value, dict) or not isinstance(value.get("waiting"), dict):
        raise ValueError("Say where to send the message when a run waits")
    for key in sorted(set(value) - {"waiting"}):
        raise ValueError(f"Unsupported notification setting: {key}")
    waiting = value["waiting"]
    via = waiting.get("via")
    if via not in CHANNELS:
        raise ValueError("Choose Slack, Email or Webhook")
    needs = CHANNELS[via][3]
    for key in sorted(set(waiting) - {"via", "connection"} - ({needs} if needs else set())):
        raise ValueError(f"Unsupported notification setting: {key}")
    connection = waiting.get("connection")
    if not isinstance(connection, str) or not CONNECTION.fullmatch(connection):
        raise ValueError("Choose one of your connections")
    if via == "slack":
        channel = waiting.get("channel")
        if not isinstance(channel, str) or not SLACK_CHANNEL.fullmatch(channel):
            raise ValueError("Name the Slack channel, such as #reviews")
    if via == "email":
        address = waiting.get("address")
        if not isinstance(address, str) or len(address) > 254 or not ADDRESS.fullmatch(address):
            raise ValueError("Give one email address")
    return value


# -- The message ------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    via: str
    operation: str
    arguments: dict
    target: str
    reason: str
    link: str
    step: dict | None = field(default=None)


def name(value) -> str:
    """A name as a message may quote it: one line, and not too long."""

    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    return text if len(text) <= NAME_LIMIT else text[:NAME_LIMIT - 1].rstrip() + "…"


def slack_text(value: str) -> str:
    """Slack reads `<…>` as a mention or a link and `&` as an entity: neither, here."""

    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_link(studio_url, application_id: str, run_id: str) -> str:
    if not studio_url:
        raise ValueError("This host has no Studio address (CONCLAVE_STUDIO_URL)")
    return f"{str(studio_url).rstrip('/')}/?application={quote(str(application_id), safe='')}&run={quote(str(run_id), safe='')}"


def waiting_message(definition: dict, *, application_id: str, run_id: str, event: dict, studio_url) -> Message:
    """The one message for one waiting point.

    It reads the application's and the step's names from the pinned
    definition, and from the event only whether it is a review or a pause,
    which step, and when. Nothing else the run holds is looked at.
    """

    waiting = validate_notifications(definition.get("notifications"))["waiting"]
    via, (service, operation, label, needs) = waiting["via"], CHANNELS[waiting["via"]]
    reason = WAITING.get(event.get("status"))
    if reason is None:
        raise ValueError("Only a review or a pause is a waiting point")
    stage = event.get("stage") if reason == "review" else event.get("next_stage")
    step = next(({"id": item["id"], "name": name(item.get("name") or item["id"])}
                 for item in definition.get("workflow", []) if item["id"] == stage), None)
    application = name(definition.get("name") or application_id)
    link = run_link(studio_url, application_id, run_id)
    if reason == "review":
        waits = f"is waiting for you at “{step['name']}”" if step else "is waiting for you"
        subject = f"{application} is waiting for you"
    else:
        waits = f"is paused before “{step['name']}”" if step else "is paused"
        subject = f"{application} is paused"
    if via == "slack":
        text = f"⏸ *{slack_text(application)}* {slack_text(waits)}. Open the run: <{link}>"
        return Message(via, operation, {"channel": waiting["channel"], "text": text}, waiting["channel"], reason, link, step)
    if via == "email":
        mail = EmailMessage(policy=SMTP)
        mail["To"] = waiting["address"]
        mail["Subject"] = subject
        mail.set_content(f"⏸ {application} {waits}. Open the run: {link}\n")
        raw = base64.urlsafe_b64encode(mail.as_bytes()).decode().rstrip("=")
        return Message(via, operation, {"raw": raw}, waiting["address"], reason, link, step)
    payload = {"type": "run.waiting", "reason": reason, "application": {"id": application_id, "name": application},
               "step": step, "run_id": run_id, "waiting_since": event.get("timestamp", ""), "link": link}
    return Message(via, operation, {"payload": payload}, waiting["connection"], reason, link, step)


# -- Where notices are kept ------------------------------------------------------

# The hourly limit's count. Only the last hour's notices are read: the index on
# when a notice last changed keeps it from growing with all history.
TOLD_SINCE = ("SELECT COUNT(*) FROM studio_run_notices n INDEXED BY studio_run_notices_sent_at "
              "JOIN studio_runs r ON r.id = n.run_id "
              "WHERE n.updated_at >= ? AND n.state IN ('sending', 'sent', 'unknown') "
              "AND NOT (n.run_id = ? AND n.sequence = ?) AND json_extract(r.request, '$.application_id') = ?")
OUTCOMES = {"sent": "NotificationSent", "failed": "NotificationFailed", "unknown": "NotificationUncertain"}
UNCERTAIN = "The host stopped while sending; it may not have been sent"


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class NoticeStore:
    """One notice per waiting point, in the run store.

    `due` is written by `RunRepository._append` in the same transaction as the
    event that makes the run wait. A notice is claimed (`due → sending`) before
    it is sent and finished once (`sent`, `failed`, or `unknown` when a host
    stopped during the send); the outcome is an event on the run, written in
    the same transaction as the state.
    """

    def __init__(self, repository):
        self.repository = repository

    def connect(self):
        return self.repository._connect()

    @staticmethod
    def insert_due(connection, run_id: str, sequence: int, detail: dict) -> None:
        import json
        now = _now()
        connection.execute("INSERT INTO studio_run_notices (run_id, sequence, state, detail, created_at, updated_at) "
                           "VALUES (?, ?, 'due', ?, ?, ?)", (run_id, sequence, json.dumps(detail), now, now))

    def due(self, run_id: str | None = None) -> list[dict]:
        import json
        query = "SELECT run_id, sequence, state, detail FROM studio_run_notices WHERE state = 'due'"
        with self.connect() as connection:
            rows = connection.execute(query + (" AND run_id = ?" if run_id else "") + " ORDER BY created_at, sequence",
                                      (run_id,) if run_id else ()).fetchall()
        return [{"run_id": row[0], "sequence": row[1], "state": row[2], "detail": json.loads(row[3] or "{}")} for row in rows]

    def waiting_point(self, run_id: str, sequence: int) -> tuple[dict, dict]:
        """The run's pinned request and the event that made it wait, and nothing else."""
        import json
        with self.connect() as connection:
            request = connection.execute("SELECT request FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            event = connection.execute("SELECT payload FROM studio_run_events WHERE run_id = ? AND sequence = ?",
                                       (run_id, sequence)).fetchone()
        if request is None or event is None:
            raise KeyError(run_id)
        return json.loads(request[0]), json.loads(event[0])

    def sender(self, run_id: str) -> str | None:
        """Whose account this run's messages are sent from, fixed when the run was created."""
        with self.connect() as connection:
            row = connection.execute("SELECT sender FROM studio_run_senders WHERE run_id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    def last_told(self, run_id: str, *, besides: int) -> str | None:
        """When this run last told someone (or may have), other than at `besides`."""
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(updated_at) FROM studio_run_notices WHERE run_id = ? AND sequence != ? "
                                     "AND state IN ('sending', 'sent', 'unknown')", (run_id, besides)).fetchone()
        return row[0]

    def told_since(self, application_id: str, since: str, *, besides: tuple[str, int]) -> int:
        """How many messages this application's runs have sent (or may have) since then."""
        with self.connect() as connection:
            return connection.execute(TOLD_SINCE, (since, besides[0], besides[1], application_id)).fetchone()[0]

    def state(self, run_id: str, sequence: int) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT state FROM studio_run_notices WHERE run_id = ? AND sequence = ?",
                                     (run_id, sequence)).fetchone()
        return row[0] if row else None

    def claim(self, run_id: str, sequence: int, detail: dict | None = None) -> bool:
        """Take a due notice to send it. Only one process ever gets it."""
        import json
        with self.connect() as connection:
            if detail is None:
                moved = connection.execute("UPDATE studio_run_notices SET state = 'sending', updated_at = ? "
                                           "WHERE run_id = ? AND sequence = ? AND state = 'due'", (_now(), run_id, sequence)).rowcount
            else:
                moved = connection.execute("UPDATE studio_run_notices SET state = 'sending', detail = ?, updated_at = ? "
                                           "WHERE run_id = ? AND sequence = ? AND state = 'due'",
                                           (json.dumps(detail), _now(), run_id, sequence)).rowcount
        return moved == 1

    def finish(self, run_id: str, sequence: int, state: str, detail: dict) -> bool:
        """Record the outcome once, with its event on the run. A later finish is ignored."""
        import json
        if state not in OUTCOMES:
            raise ValueError("A notice is sent, failed or unknown")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            moved = connection.execute("UPDATE studio_run_notices SET state = ?, detail = ?, updated_at = ? "
                                       "WHERE run_id = ? AND sequence = ? AND state IN ('due', 'sending')",
                                       (state, json.dumps(detail), _now(), run_id, sequence)).rowcount
            if moved:
                self.repository._insert_event(connection, run_id, {"type": OUTCOMES[state], "waiting_sequence": sequence, **detail})
        if moved:
            self.repository._notify(run_id)
        return bool(moved)

    def still_waiting(self, run_id: str, sequence: int) -> bool:
        """The run still waits at that point: it is waiting or paused, and that stop is its latest."""
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM studio_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None or row[0] not in WAITING:
                return False
            latest = connection.execute(
                "SELECT MAX(sequence) FROM studio_run_events WHERE run_id = ? "
                "AND json_extract(payload, '$.type') = 'ApplicationFinished'", (run_id,)).fetchone()[0]
        return latest == sequence

    def recover(self) -> list[tuple[str, int]]:
        """A host opening the store: a send some process was making when it
        stopped may or may not have happened. It is never sent again."""
        import json
        with self.connect() as connection:
            rows = connection.execute("SELECT run_id, sequence, detail FROM studio_run_notices WHERE state = 'sending'").fetchall()
        recovered = []
        for run_id, sequence, detail in rows:
            if self.finish(run_id, sequence, "unknown", {**json.loads(detail or "{}"), "reason": UNCERTAIN}):
                recovered.append((run_id, sequence))
        return recovered


class NotificationOwners:
    """Whose account sends an application's notices: whoever last saved a
    change to what runs, as for a schedule's owner."""

    def __init__(self, repository):
        self.repository = repository

    def record(self, application_id: str, by: str, *, take_over: bool) -> None:
        with self.repository._connect() as connection:
            row = connection.execute("SELECT owner FROM studio_notification_owners WHERE application_id = ?",
                                     (application_id,)).fetchone()
            if row is None:
                connection.execute("INSERT INTO studio_notification_owners (application_id, owner, owner_since) VALUES (?, ?, ?)",
                                   (application_id, by, _now()))
            elif take_over and row[0] != by:
                connection.execute("UPDATE studio_notification_owners SET owner = ?, owner_since = ? WHERE application_id = ?",
                                   (by, _now(), application_id))

    def owner(self, application_id: str) -> dict | None:
        with self.repository._connect() as connection:
            row = connection.execute("SELECT owner, owner_since FROM studio_notification_owners WHERE application_id = ?",
                                     (application_id,)).fetchone()
        return {"owner": row[0], "owner_since": row[1]} if row else None

    def forget(self, application_id: str) -> None:
        with self.repository._connect() as connection:
            connection.execute("DELETE FROM studio_notification_owners WHERE application_id = ?", (application_id,))
