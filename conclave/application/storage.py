"""Where one tenant's data lives, and what a host needs from it.

Storage is scoped by tenant rather than by a column: a host is built for one
principal, and its repository and workspace can only open that tenant's files.
Isolation is then structural — a request cannot read another tenant's runs even
if a route forgets to check, because the connection it holds cannot see them.

The protocols below are the surface a host actually uses, so a hosted
deployment can implement them over a managed database or one schema per tenant
without touching the application service. `RunRepository` and `Workspace` are
the local SQLite implementations.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from .principal import require_tenant


@dataclass(frozen=True)
class StoragePaths:
    """One tenant's files: what it writes beside them, and what it owns."""

    root: Path
    runs: Path
    applications: Path


def tenant_paths(data_dir, tenant: str) -> StoragePaths:
    """Where one tenant's runs, applications and saved settings live.

    The `local` tenant keeps the files this checkout already has, so an
    existing installation is not moved. Every other tenant gets its own
    directory, which is also why no schema migration is needed to introduce
    tenancy: no existing row has to learn a tenant it did not have.
    """

    root = Path(data_dir)
    tenant = require_tenant(tenant)

    if tenant == "local":
        return StoragePaths(root=root, runs=root / "runs.sqlite", applications=root / "studio.sqlite")

    owned = root / "tenants" / tenant
    return StoragePaths(root=owned, runs=owned / "runs.sqlite", applications=owned / "applications.sqlite")


class RunStore(Protocol):
    """Saved runs, their events, reviews and checkpoints."""

    path: Path
    history_limit: int

    def create(self, request: dict) -> str: ...

    def get(self, run_id: str) -> dict: ...

    def list(self, application_id: str) -> list[dict]: ...

    def cancel_waiting(self, run_id: str) -> None: ...

    def submit_review(self, run_id: str, review_id: str, *, reviewer: str, output: dict) -> None: ...

    def resume(self, run_id: str, provider: Any, tools: Any, *, approver: Any = None) -> AsyncIterator[dict]: ...

    def execute(self, run_id: str, provider: Any, tools: Any) -> AsyncIterator[dict]: ...


class WorkspaceStore(Protocol):
    """Saved applications, reusable members and the workspace catalogue.

    Application records carry a content ``revision`` and a separate
    ``layout_revision``. A save differing only in canvas layout keeps the
    content revision, so rearranging cards does not age recorded runs.
    Saves still require the content revision they read.
    """

    path: Path

    def applications(self) -> list[dict]: ...

    def get(self, identifier: str) -> dict: ...

    def save(self, definition: dict, *, identifier: str | None = None, revision: int | None = None) -> dict: ...

    def delete(self, identifier: str, revision: int) -> None: ...

    def members(self) -> list[dict]: ...

    def add_member(self, member: dict) -> dict: ...

    def delete_member(self, identifier: str) -> None: ...

    def catalogue(self) -> dict: ...

    def save_catalogue(self, value: dict, revision: int, bundled: dict) -> dict: ...
