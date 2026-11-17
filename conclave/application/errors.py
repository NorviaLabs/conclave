"""Errors a host reports with their own status, shared without import cycles."""


class Conflict(ValueError):
    """The request is valid but disagrees with the current state: answered 409."""
