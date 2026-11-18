"""Incoming addresses, claims and the log of runs started by events.

One row per application names its endpoint and signing secret (sealed with the
host key when there is one). A claim is how the same event starts one run: it is
taken, with the run it started, in the same transaction that creates the run,
and it lasts seven days. Refused events take no claim. The log keeps the latest
events each application received, and what became of each.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .journal import Sealer

CLAIM_DAYS = 7
LOG_LIMIT = 100


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, repository, key=None):
        self.repository = repository
        self.sealer = Sealer(key)

    def endpoint(self, application_id: str) -> dict | None:
        """An application's incoming address, without its secret."""
        with self.repository._connect() as connection:
            row = connection.execute("SELECT endpoint_id, owner, created_at, rotated_at FROM studio_event_endpoints WHERE application_id = ?",
                                     (application_id,)).fetchone()
        return {"endpoint_id": row[0], "owner": row[1], "created_at": row[2], "rotated_at": row[3]} if row else None

    def create(self, application_id: str, owner: str) -> dict:
        """A new address and secret. The secret is returned this once."""
        secret, endpoint = "ces_" + secrets.token_urlsafe(32), "ev_" + secrets.token_urlsafe(16)
        with self.repository._connect() as connection:
            if connection.execute("SELECT 1 FROM studio_event_endpoints WHERE application_id = ?", (application_id,)).fetchone():
                raise ValueError("Events are already on for this application; rotate the secret to get a new one")
            connection.execute("INSERT INTO studio_event_endpoints VALUES (?, ?, ?, ?, ?, NULL)",
                               (application_id, endpoint, self.sealer.seal(secret), owner, now()))
        return {"endpoint_id": endpoint, "secret": secret, "owner": owner}

    def take_over(self, application_id: str, owner: str, why: str) -> bool:
        """Make `owner` the one event runs act as, and say so in the log.

        Whoever holds the secret, or last changed what runs, is who the runs act
        as: the same rule as a schedule's owner, so nobody can start runs with
        someone else's accounts by rotating a secret or editing a task.
        """
        with self.repository._connect() as connection:
            changed = connection.execute("UPDATE studio_event_endpoints SET owner = ? WHERE application_id = ? AND owner != ?",
                                         (owner, application_id, owner)).rowcount
            if changed:
                self.log(application_id, "", "owner", reason=f"Owner is now {owner} ({why})", connection=connection)
        return bool(changed)

    def rotate(self, application_id: str, by: str) -> dict:
        """A new secret for the same address; the old one stops working at once,
        and whoever rotated it now holds it, so the runs act as them."""
        secret = "ces_" + secrets.token_urlsafe(32)
        with self.repository._connect() as connection:
            changed = connection.execute("UPDATE studio_event_endpoints SET secret = ?, rotated_at = ? WHERE application_id = ?",
                                         (self.sealer.seal(secret), now(), application_id)).rowcount
        if not changed:
            raise ValueError("Turn on events for this application first")
        self.take_over(application_id, by, "rotated the secret")
        return {**self.endpoint(application_id), "secret": secret}

    def resolve(self, endpoint_id: str) -> dict | None:
        """The application, owner and secret behind an address, or None."""
        if not isinstance(endpoint_id, str) or not endpoint_id.startswith("ev_") or len(endpoint_id) > 64:
            return None
        with self.repository._connect() as connection:
            row = connection.execute("SELECT application_id, owner, secret FROM studio_event_endpoints WHERE endpoint_id = ?",
                                     (endpoint_id,)).fetchone()
        if row is None:
            return None
        return {"application_id": row[0], "owner": row[1], "secret": self.sealer.open(row[2])}

    def claimed(self, connection, application_id: str, event_key: str, *, prune: bool = False) -> str | None:
        """The run this event already started within the last seven days, if any.

        An expired claim is never answered. Expired claims are removed (`prune`)
        only by the transaction that may add one, so a duplicate or a refusal
        changes nothing here.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=CLAIM_DAYS)).isoformat()
        if prune:
            connection.execute("DELETE FROM studio_event_claims WHERE received_at < ?", (cutoff,))
        row = connection.execute("SELECT run_id FROM studio_event_claims WHERE application_id = ? AND event_key = ? AND received_at >= ?",
                                 (application_id, event_key, cutoff)).fetchone()
        return row[0] if row else None

    def claim(self, connection, application_id: str, event_key: str, run_id: str, received_at: str) -> None:
        connection.execute("INSERT INTO studio_event_claims VALUES (?, ?, ?, ?)", (application_id, event_key, run_id, received_at))

    def log(self, application_id: str, event_key: str, outcome: str, *, run_id=None, reason=None, connection=None) -> None:
        def write(db):
            db.execute("INSERT INTO studio_event_log (application_id, event_key, received_at, outcome, run_id, reason) VALUES (?, ?, ?, ?, ?, ?)",
                       (application_id, event_key, now(), outcome, run_id, reason))
            db.execute("DELETE FROM studio_event_log WHERE application_id = ? AND id NOT IN "
                       "(SELECT id FROM studio_event_log WHERE application_id = ? ORDER BY id DESC LIMIT ?)",
                       (application_id, application_id, LOG_LIMIT))
        if connection is not None:
            write(connection)
        else:
            with self.repository._connect() as db:
                write(db)

    def recent(self, application_id: str) -> list[dict]:
        with self.repository._connect() as connection:
            rows = connection.execute("SELECT event_key, received_at, outcome, run_id, reason FROM studio_event_log "
                                      "WHERE application_id = ? ORDER BY id DESC LIMIT ?", (application_id, LOG_LIMIT)).fetchall()
        return [{"event_id": row[0], "received_at": row[1], "outcome": row[2], "run_id": row[3], "reason": row[4]} for row in rows]
