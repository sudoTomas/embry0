"""Repository discovery, to a key with a repository allowlist: its own credentials only, its own repositories only.

``GET /github/discoverable-repos``, ``GET /github/repos`` and
``GET /gitlab/discoverable-projects`` list what the configured forge
accounts can see. Every account answers here from a stand-in forge that sees
the same repositories, so which account was asked, and what came back, is
read off the answer. A key with an allowlist lists with the credentials that
are its own (one only its repositories use, or one it created that nothing
uses yet), never another repository's, an owner's, one it may not see, nor
the environment's token; it sees only the repositories its list covers; and
its listing is never read from or written into the cache every other caller
shares. With no credential of its own it is answered as a deployment with no
token. A key without a list lists as before.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import github, gitlab
from embry0.services.credentials.discovery import ForgeCredentialEntry
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_reads import MARK, repository_reads_app
from tests.fixtures.repository_writes import COVERED, MASTER, call

pytestmark = pytest.mark.requires_postgres

#: A repository on the discovering key's list that nobody registered yet: what discovery is for.
INCOMING = "acme/incoming"
#: What every account sees on the stand-in forge: the key's two repositories, and others it does not cover.
SEEN = (COVERED, INCOMING, "acme/elsewhere", "globex/fresh")
#: The environment's token for one owner, which only a caller without an allowlist lists with.
ENV_KEY = "GITHUB_TOKEN__ACME"


def _raw(name: str) -> dict[str, Any]:
    return {
        "full_name": name,
        "path_with_namespace": name,
        "private": True,
        "html_url": f"https://forge.test/{name}",
        "web_url": f"https://forge.test/{name}",
        "default_branch": "main",
        "permissions": {"push": True},
    }


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The stand-in forge: each account sees :data:`SEEN`; the accounts asked, by key, in order."""
    asked: list[str] = []

    async def github_listing(env_key: str, _token: str, *, per_page: int, sort: str) -> tuple[list[Any], bool]:
        asked.append(env_key)
        return [_raw(name) for name in SEEN], False

    async def gitlab_listing(entries: list[ForgeCredentialEntry], _config: Any) -> tuple[dict[str, Any], list[str]]:
        by_path: dict[str, Any] = {}
        for entry in entries:
            asked.append(entry.key)
            for name in SEEN:
                by_path.setdefault(name, (entry.without_token(), _raw(name)))
        return by_path, []

    monkeypatch.setattr(github, "_fetch_repos_for_token", github_listing)
    monkeypatch.setattr(gitlab, "_projects_for_entries", gitlab_listing)
    monkeypatch.setitem(github._DISCOVERY_CACHE, "by_name", {})  # noqa: SLF001
    monkeypatch.setitem(gitlab._CACHE, "by_path", {})  # noqa: SLF001
    monkeypatch.setenv(ENV_KEY, "env-token-for-acme-000")
    return asked


async def _key(db: DatabasePool, name: str, allowed: list[str]) -> str:
    _row, token = await ApiKeysRepository(db).create(
        name=name, scope="admin", user_id=None, created_by="tests", allowed_repos=allowed
    )
    bust_api_key_cache()
    return token


async def _gitlab_credential(app: Any, token: str, label: str, secret: str) -> None:
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        token,
        {"kind": "forge_token", "provider": "gitlab", "label": label, "secret": secret},
    )
    assert made.status_code == 201, made.text


async def test_github_discovery_lists_a_key_s_own_repositories_with_its_own_credentials(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, forge: list[str]
) -> None:
    """The key covers ``acme/web`` and ``acme/incoming``; the one credential its own is ``acme/web``'s. It lists
    ``acme/incoming`` (``acme/web`` is registered), with that credential alone and outside the shared cache; a key
    without a list lists every account's, the environment's token included, from the cache it fills."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        bot = await _key(db, "discovering", [COVERED, INCOMING])
        everyone = (await call(app, "GET", "/api/v1/github/discoverable-repos", keys.open)).json()
        asked_by_everyone, forge[:] = list(forge), []
        limited = (await call(app, "GET", "/api/v1/github/discoverable-repos", bot)).json()
        asked_by_bot, forge[:] = list(forge), []
        again = (await call(app, "GET", "/api/v1/github/discoverable-repos", keys.open)).json()
        none_own = await call(app, "GET", "/api/v1/github/discoverable-repos", await _key(db, "bare", ["acme/none"]))
        asked_with_none = list(forge)
    own = f"credential:{MARK[COVERED]}-forge"
    assert ENV_KEY in asked_by_everyone and own in asked_by_everyone and len(asked_by_everyone) > 3, asked_by_everyone
    assert asked_by_bot == [own], f"the key lists with its own credential alone: {asked_by_bot}"
    assert [(r["full_name"], r["credential_label"]) for r in limited["repos"]] == [
        (INCOMING, f"{MARK[COVERED]}-forge")
    ], f"the key sees only its own unregistered repository: {limited}"
    assert limited["cached"] is False and limited["truncated_env_keys"] == [] and limited["failed"] == [], limited
    assert {r["full_name"] for r in everyone["repos"]} == {INCOMING, "acme/elsewhere", "globex/fresh"}, everyone
    assert again["cached"] is True and again["repos"] == everyone["repos"], (
        f"the key's listing neither read nor replaced the shared cache: {again}"
    )
    assert none_own.status_code == 400 and none_own.json() == {"detail": "No GitHub token configured"}, none_own.text
    assert asked_with_none == [], f"a key with no credential of its own asks the forge nothing: {asked_with_none}"


async def test_the_create_issue_listing_lists_a_key_s_own_repositories_with_its_own_credentials(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, forge: list[str]
) -> None:
    """``GET /github/repos`` lists with the environment's tokens. To a key with an allowlist: its own credentials
    instead, and its own repositories, registered or not."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        bot = await _key(db, "discovering", [COVERED, INCOMING])
        everyone = (await call(app, "GET", "/api/v1/github/repos", keys.open)).json()
        asked_by_everyone, forge[:] = list(forge), []
        limited = (await call(app, "GET", "/api/v1/github/repos", bot)).json()
        asked_by_bot = list(forge)
    assert asked_by_everyone == [ENV_KEY] and {r["full_name"] for r in everyone["repos"]} == set(SEEN), everyone
    assert asked_by_bot == [f"credential:{MARK[COVERED]}-forge"], asked_by_bot
    assert [r["full_name"] for r in limited["repos"]] == sorted([COVERED, INCOMING]), limited


async def test_gitlab_discovery_lists_a_key_s_own_projects_with_its_own_credentials(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, forge: list[str]
) -> None:
    """A GitLab credential the key created and nothing uses yet is its own; one the deployment's admin created is not
    there for it. The key lists its own unregistered project with its own credential alone; a key with none of its
    own is answered as a deployment with no GitLab account."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        bot = await _key(db, "discovering", [COVERED, INCOMING])
        await _gitlab_credential(app, MASTER, "deployment-gitlab", "y" * 30)
        bare = await call(app, "GET", "/api/v1/gitlab/discoverable-projects", bot)
        asked_bare, forge[:] = list(forge), []
        await _gitlab_credential(app, bot, "bot-gitlab", "z" * 30)
        limited = (await call(app, "GET", "/api/v1/gitlab/discoverable-projects", bot)).json()
        asked_by_bot, forge[:] = list(forge), []
        everyone = (await call(app, "GET", "/api/v1/gitlab/discoverable-projects", keys.open)).json()
        asked_by_everyone = list(forge)
    assert bare.json() == {"projects": [], "cached": False, "configured": False, "failed": []} and asked_bare == [], (
        f"with no GitLab credential of its own: {bare.text} {asked_bare}"
    )
    assert asked_by_bot == ["credential:bot-gitlab"], asked_by_bot
    assert [p["full_name"] for p in limited["projects"]] == [INCOMING] and limited["cached"] is False, limited
    assert sorted(asked_by_everyone) == ["credential:bot-gitlab", "credential:deployment-gitlab"], asked_by_everyone
    assert {p["full_name"] for p in everyone["projects"]} == {INCOMING, "acme/elsewhere", "globex/fresh"}, everyone
