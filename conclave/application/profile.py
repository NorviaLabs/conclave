"""What this host may assume about where it runs.

The same application service runs as a single-user local host and, later, as a
multi-tenant control plane. Several conveniences are only correct locally:
finding the model backend already signed in on this machine, treating the
working folder as the source, and serving the Studio bundle from this process.

They are not defaults the hosted path should inherit by accident, so they are
named here, selected once, and reported to the UI. See
`docs/architecture/hosting.md` for the staging beyond this switch.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HostProfile:
    """The conveniences a host may take for granted.

    `detect_backend`: look for a model backend on this machine instead of
    requiring one to be configured.
    `implicit_source`: treat the folder this host was started in as the
    source when nothing has been authorized.
    `serve_ui`: serve the Studio bundle from this process.
    """

    name: str
    detect_backend: bool
    implicit_source: bool
    serve_ui: bool


LOCAL = HostProfile(name="local", detect_backend=True, implicit_source=True, serve_ui=True)

HOSTED = HostProfile(name="hosted", detect_backend=False, implicit_source=False, serve_ui=False)

PROFILES = {profile.name: profile for profile in (LOCAL, HOSTED)}


def resolve_profile(name: str | None = None) -> HostProfile:
    """The profile a host asked for; local when it asked for nothing."""

    if name is None or name == "":
        return LOCAL

    profile = PROFILES.get(name)

    if profile is None:
        raise ValueError(
            f"Unknown host profile {name!r}; use one of "
            + ", ".join(sorted(PROFILES))
        )

    return profile
