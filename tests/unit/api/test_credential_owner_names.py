"""The credential routes compare owner names exactly, case aside, on the real app over a real database.

``other-grp``, ``other.grp`` and ``other_grp`` are three GitLab groups. A credential bound to one
is stored, listed and resolved as that owner's alone: ``PUT /credentials/bindings`` answers the
owner as spelled, ``GET /credentials/bindings``, ``GET /credentials/resolve`` and
``GET /run-with/keys`` read it for that owner's repositories and for no other's, whoever asks.

The app and its keys are :mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

DASH, DOT, UNDERSCORE = "other-grp", "other.grp", "other_grp"
OWNERS = (DASH, DOT, UNDERSCORE)


async def _credential(app: Any, label: str, kind: str, provider: str) -> str:
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        MASTER,
        {"kind": kind, "provider": provider, "label": label, "secret": "x" * 30},
    )
    assert made.status_code in (200, 201), made.text
    return str(made.json()["id"])


async def _bind(app: Any, scope_kind: str, scope_value: str, cid: str, role: str) -> list[dict[str, Any]]:
    put = await call(
        app,
        "PUT",
        "/api/v1/credentials/bindings",
        MASTER,
        {"scope_kind": scope_kind, "scope_value": scope_value, "bindings": [{"credential_id": cid, "role": role}]},
    )
    assert put.status_code == 200, put.text
    return list(put.json()["bindings"])


async def _resolved(app: Any, token: str, repo: str) -> str:
    r = await call(app, "GET", f"/api/v1/credentials/resolve?repo={repo}", token)
    assert r.status_code == 200, f"{repo}: {r.status_code} {r.text}"
    return str(r.json()["label"])


async def test_an_owner_binding_is_stored_listed_and_resolved_for_its_owner_alone(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        for owner in OWNERS:
            await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'gitlab')", f"{owner}/app")
        dash = await _credential(app, "dash-bot", "forge_token", "gitlab")
        everyone = await _credential(app, "gitlab-everyone", "forge_token", "gitlab")
        await _bind(app, "global", "", everyone, "forge")
        stored = await _bind(app, "owner", "gitlab:Other-Grp", dash, "forge")
        assert [b["scope_value"] for b in stored] == ["gitlab:other-grp"], (
            f"the binding is answered as the owner is spelled, lowercased: {[b['scope_value'] for b in stored]}"
        )
        for token in (MASTER, keys.open):
            got = {owner: await _resolved(app, token, f"{owner}/app") for owner in OWNERS}
            assert got == {DASH: "dash-bot", DOT: "gitlab-everyone", UNDERSCORE: "gitlab-everyone"}, (
                f"other-grp's credential resolves for other-grp's repository alone: {got}"
            )
            listed = {}
            for owner in OWNERS:
                r = await call(
                    app, "GET", f"/api/v1/credentials/bindings?scope_kind=owner&scope_value=gitlab:{owner}", token
                )
                listed[owner] = [b["label"] for b in r.json()["bindings"]]
            assert listed == {DASH: ["dash-bot"], DOT: [], UNDERSCORE: []}, (
                f"each owner scope lists its own bindings alone: {listed}"
            )


async def test_a_key_covering_another_owners_repository_reads_none_of_this_owners_credentials(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key whose list names other_grp/app reads, for it, no credential bound to other-grp."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        for owner in (DASH, UNDERSCORE):
            await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'gitlab')", f"{owner}/app")
        _row, grp_key = await ApiKeysRepository(db).create(
            name="grp", scope="admin", user_id=None, created_by="tests", allowed_repos=[f"{UNDERSCORE}/app"]
        )
        bust_api_key_cache()
        dash = await _credential(app, "dash-bot", "forge_token", "gitlab")
        dash_llm = await _credential(app, "dash-llm", "llm_api_key", "anthropic")
        everyone = await _credential(app, "gitlab-everyone", "forge_token", "gitlab")
        await _bind(app, "global", "", everyone, "forge")
        await _bind(app, "owner", f"gitlab:{DASH}", dash, "forge")
        await _bind(app, "owner", f"gitlab:{DASH}", dash_llm, "llm")

        assert await _resolved(app, grp_key, f"{UNDERSCORE}/app") == "(the deployment's)", (
            "other_grp/app resolves the credential every repository falls back to, which such a key reads unnamed "
            "(#445), not other-grp's"
        )
        keys = await call(app, "GET", f"/api/v1/run-with/keys?repo={UNDERSCORE}/app", grp_key)
        assert keys.status_code == 200, keys.text
        assert [k["label"] for k in keys.json()["keys"]] == [], (
            f"a job on other_grp/app may not be run with other-grp's model account: {keys.json()['keys']}"
        )
        bindings = await call(
            app, "GET", f"/api/v1/credentials/bindings?scope_kind=owner&scope_value=gitlab:{UNDERSCORE}", grp_key
        )
        assert bindings.json()["bindings"] == [], f"other_grp's owner scope lists other-grp's: {bindings.text}"
        own = await call(app, "GET", f"/api/v1/run-with/keys?repo={DASH}/app", MASTER)
        assert [k["label"] for k in own.json()["keys"]] == ["dash-llm"], own.text


async def test_a_github_owners_model_account_is_not_offered_to_a_gitlab_group_of_its_name(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """github:team and gitlab:team are two owners: a key whose list names the GitLab group's repository is not
    offered, and does not resolve, the GitHub organization's model account."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        await db.execute("INSERT INTO repos (repo, forge) VALUES ('team/app', 'gitlab'), ('team/web', 'github')")
        _row, team_key = await ApiKeysRepository(db).create(
            name="team-gitlab", scope="admin", user_id=None, created_by="tests", allowed_repos=["team/app"]
        )
        bust_api_key_cache()
        await _bind(app, "global", "", await _credential(app, "pool", "llm_api_key", "anthropic"), "llm")
        await _bind(
            app, "owner", "github:team", await _credential(app, "team-github", "llm_api_key", "anthropic"), "llm"
        )

        offered = {}
        for repo, token in (("team/app", team_key), ("team/web", MASTER)):
            r = await call(app, "GET", f"/api/v1/run-with/keys?repo={repo}", token)
            assert r.status_code == 200, r.text
            offered[repo] = [k["label"] for k in r.json()["keys"]]
        assert offered == {"team/app": ["pool"], "team/web": ["team-github", "pool"]}, (
            f"the GitHub organization's account is offered to its own repository alone: {offered}"
        )
        r = await call(app, "GET", "/api/v1/credentials/resolve?repo=team/app&role=llm&provider=anthropic", team_key)
        assert (r.status_code, r.json().get("label")) == (200, "pool"), f"team/app resolves the pool: {r.text}"
