"""Who changed history is shown the way ``GET /audit`` shows actors, on every change-history route.

Each case below arranges one route's data with someone else's name, the
caller's own and one of embry0's own actors, and reads the route as three
callers: an admin-scope key sees every name, an operate key owned by
``own@example.com`` sees its own, and an unowned read key sees no person's.
Everyone sees an ``embry0:`` actor: it names no person. :data:`HISTORY_ROUTES`
is the set of GET routes these cases cover; ``test_actor_inventory.py``
holds every GET route to one of three kinds.

Driven through the real app (``create_app``) with API keys, so the tier and
the caller's own name are the ones production resolves; the storage behind
each route is a stand-in returning fixed rows.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.orchestration.dispatch_actors import MASTER_KEY
from embry0.services.organizations import Organization
from embry0.services.standards import Standard
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

OWN = "own@example.com"
OTHER = "other@example.com"
#: One of embry0's own actors: what a change made with the master key is recorded under.
EMBRY0 = MASTER_KEY
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

ADMIN = "emb_" + "a" * 43
MINE = "emb_" + "m" * 43
READER = "emb_" + "r" * 43


def _key(key_id: str, scope: str, user_id: str | None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=user_id,
        name=f"{scope}-key",
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


KEYS = {
    hash_token(ADMIN): _key("key-admin0000001", "admin", None),
    hash_token(MINE): _key("key-mine00000001", "operate", "usr-own"),
    hash_token(READER): _key("key-reader000001", "read", None),
}

#: Each caller, and the one person's name it may see (None: every name; "": no one's).
CALLERS: dict[str, tuple[str, str | None]] = {
    "admin key": (ADMIN, None),
    "own operate key": (MINE, OWN),
    "read key": (READER, ""),
}


def seen(actor: str | None, own: str | None) -> str | None:
    """What the caller whose own name is ``own`` sees of ``actor``: the rule this module pins."""
    if actor is None:
        return None
    if own is None or actor == own or actor.startswith("embry0:"):
        return actor
    return None


def keyed_app() -> Any:
    from embry0.api.app import create_app

    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    users = MagicMock()
    owner = SimpleNamespace(email=OWN, disabled=False)
    users.get = AsyncMock(side_effect=lambda uid: owner if uid == "usr-own" else None)
    app.state.users_repo = users
    return app


async def read(app: Any, token: str, url: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, f"GET {url}: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.fixture(autouse=True)
def _fresh_key_cache() -> Any:
    bust_api_key_cache()
    yield
    bust_api_key_cache()


# ---- the cases ----------------------------------------------------------------------------------------------------

Arrange = Callable[[Any, pytest.MonkeyPatch, tuple[str, ...]], None]


@dataclass(frozen=True)
class Case:
    """One route: arrange its data with ``actors``, read ``url``, and pull those names back out in the same order."""

    route: str
    url: str
    arrange: Arrange
    extract: Callable[[Any], list[Any]]
    actors: tuple[str, ...] = (OTHER, OWN, EMBRY0)


def _merge_row(actor: str, n: int) -> dict[str, Any]:
    return {
        "id": f"mrg-{n}",
        "repo": "acme/widgets",
        "pr_number": n,
        "pr_url": f"https://github.com/acme/widgets/pull/{n}",
        "head_sha": "a" * 40,
        "job_id": None,
        "case_id": None,
        "status": "approved",
        "dry_run": True,
        "decided_by": actor,
        "decided_at": NOW,
        "chat_id": "dashboard",
        "detail": None,
        "merge_sha": None,
        "ask": {},
        "posted_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _merge_list(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    rows = [_merge_row(a, n) for n, a in enumerate(actors, 1)]
    app.state.merge_approvals = SimpleNamespace(
        mode="on", list_recent=AsyncMock(return_value=rows), get=AsyncMock(return_value=rows[0])
    )


def _history(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    rows = [
        {
            "id": n,
            "surface": "qa_config",
            "before": None,
            "after": {"yaml_text": f"v{n}"},
            "actor": a,
            "source": "person",
            "created_at": NOW,
        }
        for n, a in enumerate(actors, 1)
    ]
    app.state.repo_config_revisions_repo = SimpleNamespace(
        list_for_repo=AsyncMock(return_value=rows), latest=AsyncMock(return_value=rows[0])
    )
    app.state.qa_configs_repo = SimpleNamespace(get=AsyncMock(return_value=None))
    app.state.repo_preferences_repo = SimpleNamespace(get=AsyncMock(return_value=None))


def _repo_detail(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    app.state.repos_repo = SimpleNamespace(get=AsyncMock(return_value={"repo": "acme/widgets", "active": True}))
    config_row = {"repo": "acme/widgets", "yaml_text": "x", "updated_by": actors[0], "updated_at": NOW}
    app.state.qa_configs_repo = SimpleNamespace(get=AsyncMock(return_value=config_row))
    app.state.repo_preferences_repo = SimpleNamespace(get=AsyncMock(return_value=None))
    app.state.env_repo = SimpleNamespace(get_repo=AsyncMock(return_value=[]))


def _setup_proposal(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    from embry0.api.v1 import repo_setup

    view = {
        "id": "rsp-1",
        "repo": "acme/widgets",
        "status": "applied",
        "created_by": actors[0],
        "decided_by": actors[1],
    }
    mp.setattr(repo_setup, "proposal_view", AsyncMock(return_value=view))


def _bindings(actors: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"id": n, "role": "forge", "credential_id": f"crd-{n}", "created_by": a} for n, a in enumerate(actors, 1)]


def _scope_bindings(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    app.state.credentials_repo = SimpleNamespace(bindings_for_scope=AsyncMock(return_value=_bindings(actors)))


def _repo_bindings(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    from embry0.api.v1 import credentials
    from embry0.services.credentials.resolver import CredentialMissingError

    _scope_bindings(app, mp, actors)
    missing = AsyncMock(side_effect=CredentialMissingError("none bound"))
    app.state.credential_resolver = SimpleNamespace(resolve_forge=missing, resolve_llm=missing)
    app.state.repos_repo = SimpleNamespace(forge_for=AsyncMock(return_value="github"))
    mp.setattr(credentials, "llm_providers", lambda: [])


def _orgs(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    orgs = [Organization(owner=f"acme{n}", forge="github", created_by=a) for n, a in enumerate(actors, 1)]
    app.state.organizations_repo = SimpleNamespace(
        list_all=AsyncMock(return_value=orgs), get=AsyncMock(return_value=orgs[0])
    )


def _standards(app: Any, mp: pytest.MonkeyPatch, actors: tuple[str, ...]) -> None:
    versions = [
        Standard(id=f"std-{n}", owner="acme", kind="design", version=n, status="active", created_by=a)
        for n, a in enumerate(actors, 1)
    ]
    app.state.standards_repo = SimpleNamespace(
        list_active=AsyncMock(return_value=versions),
        get_active=AsyncMock(return_value=versions[0]),
        list_versions=AsyncMock(return_value=versions),
    )
    app.state.repo_standard_state_repo = SimpleNamespace(list_for_owner=AsyncMock(return_value=[]))


CASES: list[Case] = [
    Case(
        "/api/v1/merge-approvals",
        "/api/v1/merge-approvals",
        _merge_list,
        lambda body: [i["decided_by"] for i in body["items"]],
    ),
    Case(
        "/api/v1/merge-approvals/{approval_id}",
        "/api/v1/merge-approvals/mrg-1",
        _merge_list,
        lambda b: [b["decided_by"]],
        actors=(OTHER,),
    ),
    Case(
        "/api/v1/repos/{full_repo:path}/history",
        "/api/v1/repos/acme/widgets/history",
        _history,
        lambda body: [e["actor"] for e in body["entries"]],
    ),
    Case(
        "/api/v1/repos/{full_repo:path}",
        "/api/v1/repos/acme/widgets",
        _repo_detail,
        lambda body: [body["qa_config_updated_by"]],
        actors=(OTHER,),
    ),
    Case(
        "/api/v1/repos/{full_repo:path}/setup/proposal",
        "/api/v1/repos/acme/widgets/setup/proposal",
        _setup_proposal,
        lambda body: [body["created_by"], body["decided_by"]],
        actors=(OTHER, OWN),
    ),
    Case(
        "/api/v1/repos/{full_repo:path}/setup/proposal",
        "/api/v1/repos/acme/widgets/setup/proposal",
        _setup_proposal,
        lambda body: [body["created_by"], body["decided_by"]],
        actors=(EMBRY0, OTHER),
    ),
    Case(
        "/api/v1/credentials/bindings",
        "/api/v1/credentials/bindings?scope_kind=global",
        _scope_bindings,
        lambda body: [b["created_by"] for b in body["bindings"]],
    ),
    Case(
        "/api/v1/repos/{full_repo:path}/credentials",
        "/api/v1/repos/acme/widgets/credentials",
        _repo_bindings,
        lambda body: [b["created_by"] for b in body["bindings"]],
    ),
    Case("/api/v1/orgs", "/api/v1/orgs", _orgs, lambda body: [o["created_by"] for o in body["organizations"]]),
    Case("/api/v1/orgs/{owner}", "/api/v1/orgs/acme1", _orgs, lambda body: [body["created_by"]], actors=(OTHER,)),
    Case(
        "/api/v1/standards/{owner}",
        "/api/v1/standards/acme",
        _standards,
        lambda b: [s["created_by"] for s in b["standards"]],
    ),
    Case(
        "/api/v1/standards/{owner}/{kind}",
        "/api/v1/standards/acme/design",
        _standards,
        lambda b: [b["created_by"]],
        actors=(OTHER,),
    ),
    Case(
        "/api/v1/standards/{owner}/{kind}/versions",
        "/api/v1/standards/acme/design/versions",
        _standards,
        lambda body: [v["created_by"] for v in body["versions"]],
    ),
]


def _expanded() -> list[Any]:
    """Each case with its arranged names; a case that answers one name is read again with the others."""
    out = []
    for n, case in enumerate(CASES):
        out.append(pytest.param(case, case.actors, id=f"{n}:{case.url}"))
        if case.actors == (OTHER,):
            out.append(pytest.param(case, (OWN,), id=f"{n}:{case.url} (own)"))
            out.append(pytest.param(case, (EMBRY0,), id=f"{n}:{case.url} (embry0)"))
    return out


#: Every change-history GET route: the ones that show who changed something as GET /audit shows actors.
HISTORY_ROUTES = frozenset(case.route for case in CASES) | {
    # Already on the rule before this module: tests/unit/api/test_settings_api.py,
    # tests/unit/api/v1/test_progression_pack_api.py and tests/unit/api/v1/test_audit_api.py.
    "/api/v1/audit",
    "/api/v1/config/settings",
    "/api/v1/config/settings/effective",
    "/api/v1/config/settings/revisions",
    "/api/v1/config/settings/revisions/{revision_id}",
    "/api/v1/progression/pack",
    "/api/v1/progression/pack/versions",
    "/api/v1/progression/pack/versions/{version}",
    "/api/v1/progression/pack/draft",
}


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize(("case", "actors"), _expanded())
async def test_each_history_route_shows_who_changed_it_as_the_audit_log_does(
    case: Case, actors: tuple[str, ...], caller: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = keyed_app()
    case.arrange(app, monkeypatch, actors)
    token, own = CALLERS[caller]
    got = case.extract(await read(app, token, case.url))
    expected = [seen(a, own) for a in actors]
    assert len(got) == len(actors), f"{case.url}: the case reads {len(got)} names of the {len(actors)} it arranged"
    assert got == expected, f"{case.url} as the {caller}: expected {expected}, got {got}"


# ---- a write that answers with someone else's name --------------------------------------------------------------


@pytest.mark.parametrize("caller", ["admin key", "own operate key"])
async def test_deciding_an_ask_someone_else_decided_does_not_name_them(caller: str) -> None:
    """The dashboard's form: the hand-off's person (here, the operate key's owner) or an admin decides."""
    app = keyed_app()
    decided = {**_merge_row(OTHER, 1), "job_id": "job-1"}
    result = {"approval_id": "mrg-1", "outcome": "already_decided", "status": "approved", "dry_run": True}
    app.state.merge_approvals = SimpleNamespace(
        get=AsyncMock(return_value=decided),
        decide=AsyncMock(return_value={**result, "detail": None, "pr_url": "", "head_sha": "", "decided_by": OTHER}),
    )
    open_to_me = {"done:job-1": {"to_email": OWN}}
    app.state.handoffs_repo = SimpleNamespace(
        open_for=AsyncMock(return_value=open_to_me), close=AsyncMock(return_value=None)
    )
    token, own = CALLERS[caller]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/merge-approvals/mrg-1/decide",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decided_by"] == (OTHER if own is None else None), f"as the {caller}: {resp.json()}"
