"""Who acted, and whose data they acted on.

Locally there is one operator in one tenant. Hosted, authentication names the
principal, and a principal's tenant decides which storage instance serves the
request, so a request cannot reach another tenant's runs or applications.

The name is what gets recorded as the reviewer of a human contribution; the
tenant is what scopes storage. Neither is trusted from a browser: the transport
establishes the principal before anything else runs.
"""

import getpass
import re

from dataclasses import dataclass


# A tenant becomes a directory name and a storage scope, so it is a bounded
# slug rather than free text.
TENANT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")

MAX_NAME = 100


@dataclass(frozen=True)
class Principal:
    """One actor, in one tenant."""

    name: str
    tenant: str = "local"
    source: str = "local"

    @property
    def label(self) -> str:
        """How the UI names this actor, without repeating the local tenant."""

        return self.name if self.tenant == "local" else f"{self.name} · {self.tenant}"


def require_tenant(tenant: str) -> str:
    if not isinstance(tenant, str) or not TENANT_PATTERN.fullmatch(tenant):
        raise ValueError("Tenant names are lowercase identifiers of up to 64 characters")

    return tenant


def operator_name() -> str:
    """The local OS user, when this host can tell us."""

    try:
        return getpass.getuser() or "operator"
    except Exception:
        return "operator"


def resolve_principal(name=None, tenant=None, *, source="local") -> Principal:
    """The principal this host acts as when nothing else names one.

    `CONCLAVE_PRINCIPAL` and `CONCLAVE_TENANT` name it explicitly; locally it
    falls back to the OS user in the `local` tenant, which is the tenant whose
    existing files this checkout already has.
    """

    name = name if name is not None else operator_name()
    tenant = tenant if tenant is not None else "local"

    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise ValueError(f"Principal names are required and at most {MAX_NAME} characters")

    return Principal(name=name.strip(), tenant=require_tenant(tenant), source=source)
