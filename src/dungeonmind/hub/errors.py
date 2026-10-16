"""Hub errors. Each maps to one HTTP status in hub/api.py; messages are shown to users."""

from __future__ import annotations


class HubError(Exception):
    status = 400


class NotFound(HubError):
    """Doesn't exist, or the caller may not know it exists (non-member, unreadable entry)."""

    status = 404


class Forbidden(HubError):
    """The caller can see the thing but their role doesn't allow the action."""

    status = 403


class Invalid(HubError):
    """Input breaks a rule or a limit; the message names it."""

    status = 422


class Conflict(HubError):
    status = 409


class AuthFailed(HubError):
    status = 401


class RateLimited(HubError):
    status = 429
