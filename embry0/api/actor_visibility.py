"""Who may see who did something: the audit log's rule, shared by every route that names an actor.

``GET /audit`` shows an admin (an admin session, the master key, an
admin-scope key) every row, and anyone else only the rows they wrote
themselves. A route that answers *who* changed a document (a settings
revision, a version of the content pack) follows the same rule for that
one field: an admin sees every actor, anyone else sees their own and
``null`` for everyone else's, so a read-tier key cannot list who changed
what.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from embry0.api.deps import unidentified_caller_tier
from embry0.api.session import TIER_ADMIN


async def caller_scope(request: Request) -> tuple[str, list[str]]:
    """Resolve ``(tier, own_actor_strings)``: the caller's tier and the actor strings it can have written.

    Mirrors the api_keys ``_caller`` pattern: session state wins, then key
    principal, then the AUTH_DEV_MODE fallback (full access, no identity —
    the unit-test conftest relies on it). Outside AUTH_DEV_MODE a caller with
    neither is refused (#518).
    """
    session = getattr(request.state, "session", None)
    if session is not None:
        return session.tier, [session.email]
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        if principal.kind == "master":
            return TIER_ADMIN, []
        own = [principal.email] if principal.email else [f"key:{principal.key_name}"]
        return principal.tier, own
    return unidentified_caller_tier(request), []


async def actor_shown(request: Request) -> Callable[[str | None], str | None]:
    """A function that answers an actor as this caller may see it: itself, or None when it is someone else."""
    tier, own = await caller_scope(request)
    if tier == TIER_ADMIN:
        return lambda actor: actor
    mine = set(own)
    return lambda actor: actor if actor in mine else None
