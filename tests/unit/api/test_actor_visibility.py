"""Who may see who changed something (embry0.api.actor_visibility): the audit log's rule.

A caller sees an actor it wrote itself, and an admin sees every actor. The
first half holds only if the strings the rule matches are the strings
``request_actor`` records, for every kind of caller. Everyone sees embry0's
own actors (``embry0:``): they name no person.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from embry0.api.actor_visibility import actor_shown, caller_scope
from embry0.api.deps import Principal, request_actor
from embry0.api.session import TIER_ADMIN, TIER_OPERATOR, SessionData
from embry0.config import Embry0Config
from embry0.orchestration import dispatch_actors


def _request(*, session: SessionData | None = None, principal: Principal | None = None) -> Request:
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    request = Request({"type": "http", "app": app, "method": "GET", "path": "/", "headers": [], "query_string": b""})
    if session is not None:
        request.state.session = session
    if principal is not None:
        request.state.principal = principal
    return request


def _key(scope_tier: str, **fields: Any) -> Principal:
    return Principal(kind="key", tier=scope_tier, key_id="key-000000000001", **fields)


SESSION = SessionData(
    sub="auth0|u1", email="ada@example.com", name="Ada", roles=["embry0-operator"], tier=TIER_OPERATOR
)

#: Every kind of caller that has an identity of its own, by name.
CALLERS: dict[str, dict[str, Any]] = {
    "operator session": {"session": SESSION},
    "owned read key": {"principal": _key("read", key_name="laptop", email="ada@example.com")},
    "unowned named read key": {"principal": _key("read", key_name="ci-reader")},
    "unowned key with an empty name": {"principal": _key("read", key_name="")},
    "unowned key with no name": {"principal": _key("read", key_name=None)},
    "unowned operate key": {"principal": _key(TIER_OPERATOR, key_name="bot")},
}


@pytest.mark.parametrize("caller", list(CALLERS))
async def test_every_caller_sees_the_actor_it_is_recorded_under(caller: str) -> None:
    request = _request(**CALLERS[caller])
    recorded = request_actor(request)
    tier, own = await caller_scope(request)
    assert recorded in own, f"{caller}: recorded as {recorded!r}, but the visibility rule matches only {own!r}"
    shown = await actor_shown(request)
    assert shown(recorded) == recorded, f"{caller}: cannot see its own actor {recorded!r}"
    assert shown("someone-else@example.com") is None, f"{caller} (tier {tier}) sees someone else's actor"


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize(
    "actor", [dispatch_actors.MASTER_KEY, dispatch_actors.SETTINGS_BOOTSTRAP, dispatch_actors.WATCHER]
)
async def test_every_caller_sees_embry0_s_own_actors(caller: str, actor: str) -> None:
    """An ``embry0:`` actor names one of embry0's own service actors, never a person (the request-body rule refuses it)."""
    shown = await actor_shown(_request(**CALLERS[caller]))
    assert shown(actor) == actor, f"{caller} cannot see {actor!r}, which names no person"


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize("actor", ["key:embry0-bot", "ops@embry0.example.com"])
async def test_only_the_reserved_prefix_itself_is_embry0_s(caller: str, actor: str) -> None:
    """A name that only looks like embry0's is someone's, and stays hidden."""
    shown = await actor_shown(_request(**CALLERS[caller]))
    assert shown(actor) is None, f"{caller} sees {actor!r}, which is not in embry0's own namespace"


async def test_a_key_with_no_name_is_matched_by_its_id() -> None:
    """``request_actor`` names a nameless key by its id; the rule must match that same string."""
    request = _request(principal=_key("read", key_name=""))
    assert request_actor(request) == "key:key-000000000001"
    assert (await caller_scope(request))[1] == ["key:key-000000000001"]


@pytest.mark.parametrize(
    "caller",
    [
        pytest.param({"principal": Principal(kind="master", tier=TIER_ADMIN)}, id="master key"),
        pytest.param({"principal": _key(TIER_ADMIN, key_name="ops")}, id="admin-scope key"),
        pytest.param(
            {
                "session": SessionData(
                    sub="auth0|a", email="ada@example.com", name="Ada", roles=["embry0-admin"], tier=TIER_ADMIN
                )
            },
            id="admin session",
        ),
    ],
)
async def test_an_admin_sees_every_actor(caller: dict[str, Any]) -> None:
    shown = await actor_shown(_request(**caller))
    assert shown("someone-else@example.com") == "someone-else@example.com", "an admin must see every actor"
    assert shown(None) is None


async def test_a_caller_with_no_identity_is_refused_outside_dev_mode() -> None:
    with pytest.raises(HTTPException) as refused:
        await caller_scope(_request())
    assert refused.value.status_code == 401


async def test_a_repository_refusal_is_recorded_out_of_the_key_s_own_view() -> None:
    """The refusal row is the deployment's record: the key reads nothing back that a missing item would not have
    left. Who was refused is in its details, for the admins."""
    from embry0.api.repo_allowlist import enforce_repo_allowlist
    from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST

    request = _request(
        principal=_key("operate", key_name="laptop", email="ada@example.com", allowed_repos=("acme/widgets",))
    )
    with patch("embry0.api.repo_allowlist.emit_audit", AsyncMock()) as audit, pytest.raises(HTTPException):
        await enforce_repo_allowlist(request, "acme/gadgets", action="start a job")
    actor = audit.await_args.kwargs["actor"]
    assert actor == REPO_ALLOWLIST, f"the refusal was recorded as {actor!r}"
    assert actor not in (await caller_scope(request))[1], "out of the key's own audit view"
    assert audit.await_args.kwargs["details"]["caller"] == request_actor(request), "naming who was refused"
