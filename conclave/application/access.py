"""Who may do what with one application.

Each application names its people, each with one role. Roles are ranked, and
each includes everything below it:

- viewer: open the application, its versions, runs, results and files;
- runner: start, continue, pause and stop runs, decide actions, answer reviews;
- editor: change the plan, restore versions, test steps, set schedules;
- owner: choose who has access, turn events on, delete it.

An application that names nobody — saved before roles existed — is open: everyone
in its tenant has owner access until someone limits it.
"""

from __future__ import annotations

ROLES = ("viewer", "runner", "editor", "owner")
# Each permission, and the least role that has it.
PERMISSIONS = {"view": "viewer", "run": "runner", "edit": "editor", "own": "owner"}
# What a refused request was trying to do, in words.
DOING = {"view": "opening it", "run": "running it", "edit": "changing it"}
MAX_PERSON = 100


class RoleRefused(ValueError):
    """The caller has a role on the application, but not one that allows this."""


def rank(role: str | None) -> int:
    return ROLES.index(role) if role in ROLES else -1


def role_allows(role: str | None, permission: str) -> bool:
    return rank(role) >= rank(PERMISSIONS[permission])


def refusal(role: str, permission: str) -> str:
    needed = PERMISSIONS[permission]
    if permission in DOING:
        return f"Your role on this application is {role}; {DOING[permission]} needs {needed}"
    return f"Your role on this application is {role}; that needs {needed}"


def valid_person(person) -> str:
    if not isinstance(person, str) or not person.strip() or len(person.strip()) > MAX_PERSON:
        raise ValueError(f"Name a person by their sign-in name, at most {MAX_PERSON} characters")
    return person.strip()


def valid_role(role) -> str:
    if role not in ROLES:
        raise ValueError("Choose a role: " + ", ".join(ROLES))
    return role
