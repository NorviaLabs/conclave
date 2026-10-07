"""Application persistence shared by CLI and HTTP, using the existing local schema."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .definition import validate_definition
from .catalogue import validate_member, validate_catalogue


class Conflict(ValueError):
    pass


class Workspace:
    def __init__(self, path, *, starters=(), member_limit=200):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if type(member_limit) is not int or member_limit < 1:
            raise ValueError("Member limit must be positive")
        self.member_limit = member_limit
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS seeded_applications (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS applications (id TEXT PRIMARY KEY, definition TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, layout_revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS member_templates (id TEXT PRIMARY KEY, member TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_catalogue (id INTEGER PRIMARY KEY, catalogue TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
            """)
            # Where the cards sit has its own version. A database written before
            # it existed keeps every saved application and starts at zero.
            if "layout_revision" not in {row["name"] for row in connection.execute("PRAGMA table_info(applications)")}:
                connection.execute("ALTER TABLE applications ADD COLUMN layout_revision INTEGER NOT NULL DEFAULT 0")
            for starter in starters:
                if not connection.execute("INSERT OR IGNORE INTO seeded_applications VALUES (?)", (starter["id"],)).rowcount:
                    continue
                connection.execute("INSERT OR IGNORE INTO applications (id, definition, revision, updated_at) VALUES (?, ?, 1, ?)", (starter["id"], json.dumps(starter["definition"]), "2026-10-04T00:00:00.000Z"))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def application(row):
        return {**dict(row), "definition": {key: value for key, value in json.loads(row["definition"]).items() if key not in {"sample_request", "sample_outputs", "sample_sources"}}}

    def applications(self):
        with self.connect() as connection:
            return [self.application(row) for row in connection.execute("SELECT * FROM applications ORDER BY updated_at DESC, id")]

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM applications WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return self.application(row)

    @staticmethod
    def content_of(definition):
        """A definition without where its cards sit: what a saved version means."""
        return json.dumps({key: value for key, value in definition.items() if key != "canvas"}, sort_keys=True, allow_nan=False)

    def save(self, definition, *, identifier=None, revision=None):
        definition = {key: value for key, value in definition.items() if key not in {"sample_request", "sample_outputs", "sample_sources"}} if isinstance(definition, dict) else definition
        validate_definition(definition)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if identifier is None:
                identifier, revision = str(uuid.uuid4()), 1
                connection.execute("INSERT INTO applications (id, definition, revision, layout_revision, updated_at) VALUES (?, ?, ?, 0, ?)", (identifier, json.dumps(definition, allow_nan=False), revision, updated_at))
                return {"id": identifier, "definition": definition, "revision": revision, "layout_revision": 0, "updated_at": updated_at}
            else:
                if type(revision) is not int or revision < 1:
                    raise ValueError("Provide a positive saved revision")
                stored = connection.execute("SELECT definition, layout_revision FROM applications WHERE id = ?", (identifier,)).fetchone()
                # The arrangement travels with the definition, so a save that only
                # moves cards is recorded on its own: it keeps the application's
                # version, which is what a request reads, and advances the layout.
                layout = stored is not None and self.content_of(json.loads(stored["definition"])) == self.content_of(definition)
                version, layouts = (0, 1) if layout else (1, 0)
                changed = connection.execute(
                    "UPDATE applications SET definition = ?, revision = revision + ?, layout_revision = layout_revision + ?, updated_at = ? WHERE id = ? AND revision = ?",
                    (json.dumps(definition, allow_nan=False), version, layouts, updated_at, identifier, revision),
                ).rowcount
                if not changed:
                    if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                        raise KeyError(identifier)
                    raise Conflict("This application changed. Reload its saved version before saving.")
                revision += version
        return {"id": identifier, "definition": definition, "revision": revision, "layout_revision": stored["layout_revision"] + layouts, "updated_at": updated_at}

    def delete(self, identifier, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("Provide a positive saved revision")
        with self.connect() as connection:
            changed = connection.execute("DELETE FROM applications WHERE id = ? AND revision = ?", (identifier, revision)).rowcount
            if not changed:
                if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                    raise KeyError(identifier)
                raise Conflict("This application changed. Reload before deleting.")

    def members(self):
        with self.connect() as connection:
            return [{**dict(row), "member": json.loads(row["member"])} for row in connection.execute("SELECT * FROM member_templates ORDER BY updated_at DESC, id LIMIT ?", (self.member_limit,))]

    def add_member(self, member):
        member = validate_member(member)
        identifier, updated_at = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("INSERT INTO member_templates VALUES (?, ?, ?)", (identifier, json.dumps(member), updated_at))
        return {"id": identifier, "member": member, "updated_at": updated_at}

    def delete_member(self, identifier):
        with self.connect() as connection:
            if not connection.execute("DELETE FROM member_templates WHERE id = ?", (identifier,)).rowcount:
                raise KeyError(identifier)

    def catalogue(self):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM workspace_catalogue WHERE id = 1").fetchone()
        return {"catalogue": {key: value for key, value in json.loads(row["catalogue"]).items() if key != "fixtures"}, "revision": row["revision"]} if row else {"catalogue": {"version": 1, "roles": [], "starters": [], "outputs": []}, "revision": 0}

    def save_catalogue(self, value, revision, bundled):
        value = validate_catalogue(value, bundled)
        if type(revision) is not int or revision < 0:
            raise ValueError("Provide the catalog revision")
        with self.connect() as connection:
            if revision == 0:
                changed = connection.execute("INSERT OR IGNORE INTO workspace_catalogue VALUES (1, ?, 1)", (json.dumps(value),)).rowcount
            else:
                changed = connection.execute("UPDATE workspace_catalogue SET catalogue = ?, revision = revision + 1 WHERE id = 1 AND revision = ?", (json.dumps(value), revision)).rowcount
            if not changed:
                raise Conflict("The catalog changed. Export your edits and reload before saving.")
        return {"catalogue": value, "revision": revision + 1}
