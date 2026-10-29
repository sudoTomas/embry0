"""Who may see who changed something: the audit log's rule, shared by every change-history route.

``GET /audit`` shows an admin (an admin session, the master key, an
admin-scope key) every row, and anyone else only the rows they wrote
themselves. A route that answers *who* changed something, a revision in a
history or the person behind a recorded decision (a settings revision, a
version of the content pack, a repository's history, a merge decision, a
setup proposal, a standard's version, a credential binding, an organization's
registration), follows the same rule for that field: an admin sees every
actor, anyone else sees their own and ``null`` for everyone else's, so a
read-tier key cannot list who changed what.

An actor in embry0's own namespace (``embry0:``,
:func:`~embry0.orchestration.dispatch_actors.is_reserved`) is shown to
everyone: it names one of embry0's own service actors (the master key among
them), never a person, and the request-body rule
(:mod:`embry0.api.schemas.person_name`) refuses it as a person's name.

Collaboration fields stay visible to every signed-in key by design, outside
this rule: who started a job, who answered a question, who handed a decision
over, a map's marks. ``docs/api.md`` lists both sides.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from embry0.api.deps import own_actor, unidentified_caller_tier
from embry0.api.session import TIER_ADMIN
from embry0.orchestration.dispatch_actors import is_reserved

#: A function answering an actor as this caller may see it: itself, or None.
Shown = Callable[[str | None], str | None]


async def caller_scope(request: Request) -> tuple[str, list[str]]:
    """Resolve ``(tier, own_actor_strings)``: the caller's tier and the actor strings it can have written.

    The own strings are what :func:`~embry0.api.deps.own_actor` records for
    this caller, so they match its rows by construction. Mirrors the
    api_keys ``_caller`` pattern: session state wins, then key principal,
    then the AUTH_DEV_MODE fallback (full access, no identity — the
    unit-test conftest relies on it). Outside AUTH_DEV_MODE a caller with
    neither is refused (#518).
    """
    actor = own_actor(request)
    own = [actor] if actor is not None else []
    session = getattr(request.state, "session", None)
    if session is not None:
        return session.tier, own
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        if principal.kind == "master":
            return TIER_ADMIN, []
        return principal.tier, own
    return unidentified_caller_tier(request), []


@dataclass(frozen=True, slots=True)
class ActorView:
    """How one caller may see actors: call it with an actor to get the actor, or None when it is hidden."""

    sees_all: bool
    own: frozenset[str]

    def __call__(self, actor: str | None) -> str | None:
        if actor is None:
            return None
        if self.sees_all or actor in self.own or is_reserved(actor):
            return actor
        return None


def with_actors(row: Mapping[str, Any], shown: Shown, *fields: str) -> dict[str, Any]:
    """A copy of ``row`` with each of ``fields`` it holds answered as ``shown`` answers it."""
    return {**row, **{field: shown(row[field]) for field in fields if field in row}}


async def actor_shown(request: Request) -> ActorView:
    """This caller's :class:`ActorView`: an admin sees every actor, anyone else its own and embry0's."""
    tier, own = await caller_scope(request)
    return ActorView(sees_all=tier == TIER_ADMIN, own=frozenset(own))
