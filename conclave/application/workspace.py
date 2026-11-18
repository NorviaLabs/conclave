"""Application persistence shared by CLI and HTTP, using the existing local schema."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .capture import redact_values
from .definition import validate_definition
from .versions import summarize_changes


def kept_form(definition) -> str:
    """A definition as history keeps it: secret-looking values redacted, so a
    credential someone pasted and then removed is not kept, or restored."""
    return json.dumps(redact_values(definition), allow_nan=False)
from .catalogue import validate_member, validate_catalogue
from . import access as access_rules

# How many people one application may name, and how many changes to them are kept.
MAX_PEOPLE = 200
LOG_KEEP = 500

from .errors import Conflict  # noqa: F401  (re-exported: callers import it from here)


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
                -- Every saved change to what an application does, kept. A version
                -- also keeps where its cards sat when it was last saved.
                CREATE TABLE IF NOT EXISTS application_versions (
                    application_id TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL,
                    saved_at TEXT NOT NULL, saved_by TEXT, restored_from INTEGER, recovered_from_run TEXT, summary TEXT,
                    PRIMARY KEY (application_id, version));
                -- Who may do what with each application. One role per person;
                -- an application with no rows is open to its whole tenant.
                CREATE TABLE IF NOT EXISTS application_access (
                    application_id TEXT NOT NULL, person TEXT NOT NULL COLLATE NOCASE, role TEXT NOT NULL,
                    added_by TEXT, added_at TEXT NOT NULL, PRIMARY KEY (application_id, person));
                -- Every change to who may do what, kept: who, to whom, when.
                CREATE TABLE IF NOT EXISTS application_access_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL, at TEXT NOT NULL,
                    by TEXT, person TEXT NOT NULL, change TEXT NOT NULL, role TEXT);
                CREATE INDEX IF NOT EXISTS application_access_log_application ON application_access_log (application_id, id);
            """)
            # A person is one person whatever the capitals of their sign-in name:
            # a table made before people were matched that way is rebuilt once.
            made = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'application_access'").fetchone()
            if made and "COLLATE NOCASE" not in made["sql"]:
                connection.executescript("""
                    ALTER TABLE application_access RENAME TO application_access_exact;
                    CREATE TABLE application_access (
                        application_id TEXT NOT NULL, person TEXT NOT NULL COLLATE NOCASE, role TEXT NOT NULL,
                        added_by TEXT, added_at TEXT NOT NULL, PRIMARY KEY (application_id, person));
                    INSERT OR IGNORE INTO application_access SELECT * FROM application_access_exact;
                    DROP TABLE application_access_exact;
                """)
            # Added with stored summaries: a store from before keeps its versions,
            # and their summaries are filled in the first time they are listed.
            if "summary" not in {row["name"] for row in connection.execute("PRAGMA table_info(application_versions)")}:
                connection.execute("ALTER TABLE application_versions ADD COLUMN summary TEXT")
            # Where the cards sit has its own version. A database written before
            # it existed keeps every saved application and starts at zero.
            if "layout_revision" not in {row["name"] for row in connection.execute("PRAGMA table_info(applications)")}:
                connection.execute("ALTER TABLE applications ADD COLUMN layout_revision INTEGER NOT NULL DEFAULT 0")
            for starter in starters:
                if not connection.execute("INSERT OR IGNORE INTO seeded_applications VALUES (?)", (starter["id"],)).rowcount:
                    continue
                connection.execute("INSERT OR IGNORE INTO applications (id, definition, revision, updated_at) VALUES (?, ?, 1, ?)", (starter["id"], json.dumps(starter["definition"]), "2026-10-04T00:00:00.000Z"))
            # An application saved before versions were kept (or a starter) keeps
            # its current definition as a version; who saved it is not known.
            for row in connection.execute("SELECT a.id, a.revision, a.definition, a.updated_at FROM applications a WHERE NOT EXISTS "
                                          "(SELECT 1 FROM application_versions v WHERE v.application_id = a.id AND v.version = a.revision)").fetchall():
                connection.execute("INSERT INTO application_versions (application_id, version, definition, saved_at) VALUES (?, ?, ?, ?)",
                                   (row["id"], row["revision"], kept_form(json.loads(row["definition"])), row["updated_at"]))

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

    def save(self, definition, *, identifier=None, revision=None, by=None, restored_from=None):
        """Save an application. A change to what it does is a new version, kept
        with who saved it (`by`) and, for a restore, the version it came from;
        moving cards updates the current version's layout and makes none."""
        definition = {key: value for key, value in definition.items() if key not in {"sample_request", "sample_outputs", "sample_sources"}} if isinstance(definition, dict) else definition
        validate_definition(definition)
        if definition.get("start_mode") == "schedule" and "schedule" not in definition:
            raise ValueError("Set when this application runs, or start it manually")
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if identifier is None:
                identifier, revision = str(uuid.uuid4()), 1
                connection.execute("INSERT INTO applications (id, definition, revision, layout_revision, updated_at) VALUES (?, ?, ?, 0, ?)", (identifier, json.dumps(definition, allow_nan=False), revision, updated_at))
                self._keep(connection, identifier, revision, definition, updated_at, by, restored_from, summarize_changes(None, definition))
                if by:
                    # Whoever creates an application owns it, so it is theirs from the start.
                    self._grant(connection, identifier, by, "owner", by, updated_at)
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
                if version:
                    # Said once, against the version it follows, and kept with it.
                    self._keep(connection, identifier, revision, definition, updated_at, by, restored_from,
                               summarize_changes(json.loads(stored["definition"]), definition))
                else:
                    # Only the cards moved: the current version keeps where they sit now.
                    connection.execute("UPDATE application_versions SET definition = ? WHERE application_id = ? AND version = ?",
                                       (kept_form(definition), identifier, revision))
        return {"id": identifier, "definition": definition, "revision": revision, "layout_revision": stored["layout_revision"] + layouts, "updated_at": updated_at}

    def delete(self, identifier, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("Provide a positive saved revision")
        with self.connect() as connection:
            changed = connection.execute("DELETE FROM applications WHERE id = ? AND revision = ?", (identifier, revision)).rowcount
            if changed:
                connection.execute("DELETE FROM application_versions WHERE application_id = ?", (identifier,))
                connection.execute("DELETE FROM application_access WHERE application_id = ?", (identifier,))
                connection.execute("DELETE FROM application_access_log WHERE application_id = ?", (identifier,))
            if not changed:
                if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                    raise KeyError(identifier)
                raise Conflict("This application changed. Reload before deleting.")

    # --- Who may do what -----------------------------------------------------------------

    def access(self, identifier) -> list[dict]:
        """The people an application names, owners first; empty when it is open."""
        with self.connect() as connection:
            rows = connection.execute("SELECT person, role, added_by, added_at FROM application_access WHERE application_id = ?",
                                      (identifier,)).fetchall()
        return sorted((dict(row) for row in rows), key=lambda item: (-access_rules.rank(item["role"]), item["added_at"], item["person"]))

    def exists(self, identifier) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone() is not None

    def role_of(self, identifier, person):
        """(role, open): the person's role, or owner for everyone when the application is open."""
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                raise KeyError(identifier)
            named = connection.execute("SELECT 1 FROM application_access WHERE application_id = ? LIMIT 1", (identifier,)).fetchone()
            if named is None:
                return "owner", True
            row = connection.execute("SELECT role FROM application_access WHERE application_id = ? AND person = ?",
                                     (identifier, person)).fetchone()
        return (row["role"] if row else None), False

    def roles_of(self, person) -> dict:
        """Every application this person may open: {id: (role, open)}."""
        with self.connect() as connection:
            named = {row["application_id"] for row in connection.execute("SELECT DISTINCT application_id FROM application_access")}
            mine = {row["application_id"]: row["role"] for row in connection.execute(
                "SELECT application_id, role FROM application_access WHERE person = ?", (person,))}
            everyone = [row["id"] for row in connection.execute("SELECT id FROM applications")]
        return {identifier: ((mine[identifier], False) if identifier in mine else ("owner", True))
                for identifier in everyone if identifier in mine or identifier not in named}

    @staticmethod
    def _grant(connection, identifier, person, role, by, at):
        previous = connection.execute("SELECT role FROM application_access WHERE application_id = ? AND person = ?",
                                      (identifier, person)).fetchone()
        connection.execute("INSERT INTO application_access (application_id, person, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) "
                           "ON CONFLICT (application_id, person) DO UPDATE SET role = excluded.role", (identifier, person, role, by, at))
        connection.execute("INSERT INTO application_access_log (application_id, at, by, person, change, role) VALUES (?, ?, ?, ?, ?, ?)",
                           (identifier, at, by, person, "changed" if previous else "added", role))
        Workspace._prune_log(connection, identifier)

    @staticmethod
    def _owners(connection, identifier) -> set:
        """The owners, as one name each whatever its capitals."""
        return {row["person"].casefold() for row in connection.execute(
            "SELECT person FROM application_access WHERE application_id = ? AND role = 'owner'", (identifier,))}

    def grant(self, identifier, person, role, *, by):
        """Give someone a role, or change theirs. An open application is limited
        starting with whoever limits it, as owner; the last owner stays one."""
        person, role = access_rules.valid_person(person), access_rules.valid_role(role)
        at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                raise KeyError(identifier)
            owners = self._owners(connection, identifier)
            named = connection.execute("SELECT 1 FROM application_access WHERE application_id = ? LIMIT 1", (identifier,)).fetchone()
            if named is None and (person.casefold() != str(by).casefold() or role != "owner"):
                raise ValueError("Limit access by adding yourself as its owner first")
            listed = connection.execute("SELECT 1 FROM application_access WHERE application_id = ? AND person = ?",
                                        (identifier, person)).fetchone()
            if listed is None and connection.execute("SELECT COUNT(*) FROM application_access WHERE application_id = ?",
                                                     (identifier,)).fetchone()[0] >= MAX_PEOPLE:
                raise ValueError(f"An application may name at most {MAX_PEOPLE} people")
            if owners == {person.casefold()} and role != "owner":
                raise ValueError(f"{person} is this application’s only owner. Make someone else an owner first.")
            if named is None:
                connection.execute("INSERT INTO application_access_log (application_id, at, by, person, change, role) VALUES (?, ?, ?, ?, ?, ?)",
                                   (identifier, at, by, by, "limited", None))
            self._grant(connection, identifier, person, role, by, at)

    def revoke(self, identifier, person, *, by):
        """Take someone's role away; the last owner stays."""
        person = access_rules.valid_person(person)
        at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT role FROM application_access WHERE application_id = ? AND person = ?",
                                     (identifier, person)).fetchone()
            if row is None:
                raise KeyError(person)
            if self._owners(connection, identifier) == {person.casefold()}:
                raise ValueError(f"{person} is this application’s only owner. Make someone else an owner first.")
            connection.execute("DELETE FROM application_access WHERE application_id = ? AND person = ?", (identifier, person))
            connection.execute("INSERT INTO application_access_log (application_id, at, by, person, change, role) VALUES (?, ?, ?, ?, ?, ?)",
                               (identifier, at, by, person, "removed", None))
            self._prune_log(connection, identifier)

    @staticmethod
    def _prune_log(connection, identifier) -> None:
        """Keep the latest changes; older ones go."""
        connection.execute("DELETE FROM application_access_log WHERE application_id = ? AND id NOT IN "
                           "(SELECT id FROM application_access_log WHERE application_id = ? ORDER BY id DESC LIMIT ?)",
                           (identifier, identifier, LOG_KEEP))

    def access_log(self, identifier, limit=20) -> list[dict]:
        """The latest changes to who may do what, newest first."""
        with self.connect() as connection:
            rows = connection.execute("SELECT at, by, person, change, role FROM application_access_log WHERE application_id = ? "
                                      "ORDER BY id DESC LIMIT ?", (identifier, limit)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _keep(connection, identifier, version, definition, saved_at, by, restored_from, summary=None):
        connection.execute("INSERT INTO application_versions (application_id, version, definition, saved_at, saved_by, restored_from, summary) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)", (identifier, version, kept_form(definition), saved_at, by, restored_from,
                                                            redact_values(summary) if summary else None))

    def keep_summary(self, identifier, number, summary):
        """Fill in the summary of a version kept before summaries were."""
        with self.connect() as connection:
            connection.execute("UPDATE application_versions SET summary = ? WHERE application_id = ? AND version = ? AND summary IS NULL",
                               (redact_values(summary), identifier, number))

    VERSION_FIELDS = ("version", "saved_at", "saved_by", "restored_from", "recovered_from_run")

    def versions(self, identifier):
        """An application's kept versions, newest first, without their definitions."""
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM applications WHERE id = ?", (identifier,)).fetchone():
                raise KeyError(identifier)
            rows = connection.execute("SELECT version, saved_at, saved_by, restored_from, recovered_from_run, summary FROM application_versions "
                                      "WHERE application_id = ? ORDER BY version DESC", (identifier,)).fetchall()
        return [dict(row) for row in rows]

    def version(self, identifier, number):
        """One kept version, with its definition."""
        with self.connect() as connection:
            row = connection.execute("SELECT version, saved_at, saved_by, restored_from, recovered_from_run, definition FROM application_versions "
                                     "WHERE application_id = ? AND version = ?", (identifier, number)).fetchone()
        if row is None:
            raise KeyError(f"{identifier} version {number}")
        return {**{key: row[key] for key in self.VERSION_FIELDS}, "definition": json.loads(row["definition"])}

    def kept_numbers(self, identifier) -> set[int]:
        """The version numbers kept for an application: one indexed query, no definitions."""
        with self.connect() as connection:
            return {row[0] for row in connection.execute("SELECT version FROM application_versions WHERE application_id = ?", (identifier,))}

    def recover(self, identifier, found):
        """Keep versions recovered from runs that stored them: `found` maps a
        version number to (definition, run id). Versions already kept stay as
        they are, and nothing is made up for a number no run stored."""
        with self.connect() as connection:
            current = connection.execute("SELECT revision FROM applications WHERE id = ?", (identifier,)).fetchone()
            if current is None:
                raise KeyError(identifier)
            for number, (definition, run_id) in found.items():
                if type(number) is int and 1 <= number <= current[0]:
                    connection.execute("INSERT OR IGNORE INTO application_versions (application_id, version, definition, saved_at, recovered_from_run) "
                                       "VALUES (?, ?, ?, ?, ?)", (identifier, number, kept_form(definition), "", run_id))

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
