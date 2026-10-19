"""A repository's change history in plain words, and one-click undo (ADR 0019).

Driven through the real app (``create_app``) on a real database, with API
keys so the actor and the tier are the ones production sees: an operator key
owned by dev@example.com and a read-scope key.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.deps import bust_api_key_cache
from embry0.api.v1.repo_history import WHY_NOT, change_what
from embry0.config import Embry0Config
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository

REPO = "acme/widgets"
QA = f"/api/v1/repos/{REPO}/qa-config"
PREFS = f"/api/v1/repos/{REPO}/preferences"
HISTORY = f"/api/v1/repos/{REPO}/history"
V1 = "version: 2\nqa_required: never\nworkspace_provider:\n  type: npm-workspaces-turbo\n"
V2 = "version: 2\nqa_required: auto\nworkspace_provider:\n  type: npm-workspaces-turbo\n"

OPERATOR = "emb_" + "o" * 43
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
    hash_token(OPERATOR): _key("key-operator0001", "operate", "usr-dev"),
    hash_token(READER): _key("key-reader000001", "read", None),
}


def _user(email: str) -> Any:
    user = MagicMock()
    user.disabled = False
    user.email = email
    return user


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
async def client(db_with_migrations: DatabasePool) -> AsyncIterator[AsyncClient]:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.qa_configs_repo = RepoQAConfigsRepository(db_with_migrations)
    state.repo_preferences_repo = RepoPreferencesRepository(db_with_migrations)
    state.repo_config_revisions_repo = RepoConfigRevisionsRepository(db_with_migrations)
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    users = MagicMock()
    users.get = AsyncMock(side_effect=lambda uid: _user("dev@example.com") if uid == "usr-dev" else None)
    state.users_repo = users
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(OPERATOR)) as c:
        yield c
    bust_api_key_cache()


async def _history(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get(HISTORY)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _audit(db: DatabasePool, action: str) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT actor, details FROM audit_log WHERE action = $1 ORDER BY id", action)
    return [dict(r) for r in rows]


@pytest.mark.requires_postgres
async def test_two_saves_list_newest_first_in_plain_words_with_the_actor(client) -> None:
    first = await client.put(QA, content=V1)
    second = await client.put(QA, content=V2)
    assert first.status_code == 200 and second.status_code == 200, second.text

    history = await _history(client)
    entries = history["entries"]
    assert [e["id"] for e in entries] == [second.json()["revision_id"], first.json()["revision_id"]]
    assert [e["what"] for e in entries] == ["Test setup changed", "Test setup created"]
    assert {(e["surface"], e["actor"], e["source"]) for e in entries} == {("qa_config", "dev@example.com", "person")}
    assert [(e["undoable"], e["why_not"]) for e in entries] == [(True, None), (False, WHY_NOT)]
    assert all(isinstance(e["created_at"], str) for e in entries)
    assert history["before_history"] is False


@pytest.mark.requires_postgres
async def test_undo_of_the_newest_change_restores_the_text_and_records_a_restore(client) -> None:
    await client.put(QA, content=V1)
    await client.put(QA, content=V2)
    newest = (await _history(client))["entries"][0]

    resp = await client.post(f"{HISTORY}/{newest['id']}/undo")
    assert resp.status_code == 200, resp.text
    assert (await client.get(QA)).text == V1

    entries = (await _history(client))["entries"]
    assert entries[0]["id"] == resp.json()["revision_id"]
    assert (entries[0]["source"], entries[0]["what"], entries[0]["undoable"]) == ("restore", "Test setup changed", True)
    assert [e["undoable"] for e in entries[1:]] == [False, False]


@pytest.mark.requires_postgres
async def test_undo_of_an_older_change_is_refused_in_plain_words(client) -> None:
    await client.put(QA, content=V1)
    await client.put(QA, content=V2)
    older = (await _history(client))["entries"][1]

    resp = await client.post(f"{HISTORY}/{older['id']}/undo")
    assert resp.status_code == 409
    assert resp.json()["detail"] == WHY_NOT == "This changed again later. Undo the newer change first."
    assert (await client.get(QA)).text == V2
    assert len((await _history(client))["entries"]) == 2


@pytest.mark.requires_postgres
async def test_undo_of_a_created_setup_removes_it_and_that_undo_can_be_undone(client) -> None:
    created = (await client.put(QA, content=V1)).json()["revision_id"]
    removal = (await client.post(f"{HISTORY}/{created}/undo")).json()["revision_id"]
    assert (await client.get(QA)).status_code == 404
    assert (await _history(client))["entries"][0]["what"] == "Test setup removed"

    resp = await client.post(f"{HISTORY}/{removal}/undo")
    assert resp.status_code == 200, resp.text
    assert (await client.get(QA)).text == V1


@pytest.mark.requires_postgres
async def test_undo_of_a_reset_brings_pull_request_review_back(client) -> None:
    put = await client.put(PREFS, json={"base_branch": "develop", "pr_review": {"enabled": True}})
    assert put.status_code == 200, put.text
    assert "revision_id" not in put.json(), "the preferences response model is unchanged"
    assert (await client.delete(PREFS)).status_code == 204
    assert (await client.get(PREFS)).json() is None

    reset = (await _history(client))["entries"][0]
    assert reset["what"] == (
        "Settings cleared: Branch embry0 starts from, Pull request review (pull request review turned off)"
    )
    resp = await client.post(f"{HISTORY}/{reset['id']}/undo")
    assert resp.status_code == 200, resp.text
    prefs = (await client.get(PREFS)).json()
    assert prefs["pr_review"]["enabled"] is True
    assert prefs["base_branch"] == "develop"


@pytest.mark.requires_postgres
async def test_a_read_key_cannot_undo_and_an_operator_can(client) -> None:
    await client.put(QA, content=V1)
    await client.put(QA, content=V2)
    readable = await client.get(HISTORY, headers=_as(READER))
    assert readable.status_code == 200
    newest = readable.json()["entries"][0]["id"]

    refused = await client.post(f"{HISTORY}/{newest}/undo", headers=_as(READER))
    assert refused.status_code == 403
    assert (await client.get(QA)).text == V2
    assert (await client.post(f"{HISTORY}/{newest}/undo", headers=_as(OPERATOR))).status_code == 200


@pytest.mark.requires_postgres
async def test_an_unknown_change_or_one_from_another_repo_is_404(client) -> None:
    other = (await client.put("/api/v1/repos/acme/other/qa-config", content=V1)).json()["revision_id"]
    assert (await client.post(f"{HISTORY}/{other}/undo")).status_code == 404
    assert (await client.post(f"{HISTORY}/999999/undo")).status_code == 404
    assert (await client.get("/api/v1/repos/acme/other/qa-config")).text == V1


@pytest.mark.requires_postgres
async def test_an_earlier_version_that_no_longer_parses_is_422_and_nothing_is_written(
    client, db_with_migrations
) -> None:
    await client.put(QA, content=V1)
    changed = (await client.put(QA, content=V2)).json()["revision_id"]
    await db_with_migrations.execute(
        "UPDATE repo_config_revisions SET before = $1::jsonb WHERE id = $2", {"yaml_text": "version: 1\n"}, changed
    )
    resp = await client.post(f"{HISTORY}/{changed}/undo")
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("That earlier version is no longer valid: ")
    assert (await client.get(QA)).text == V2
    assert len((await _history(client))["entries"]) == 2


@pytest.mark.requires_postgres
async def test_a_setup_saved_before_history_is_flagged(client, db_with_migrations) -> None:
    await db_with_migrations.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github')", REPO)
    await db_with_migrations.execute(
        "INSERT INTO repo_qa_configs (repo, yaml_text, updated_by) VALUES ($1, $2, '')", REPO, V1
    )
    assert await _history(client) == {"entries": [], "before_history": True}
    await client.put(QA, content=V2)
    assert (await _history(client))["before_history"] is False


@pytest.mark.requires_postgres
async def test_every_change_and_undo_is_audited(client, db_with_migrations) -> None:
    saved = (await client.put(QA, content=V1)).json()["revision_id"]
    assert (await client.delete(QA)).status_code == 204
    assert (await client.delete(QA)).status_code == 204  # nothing left: nothing removed, nothing audited
    await client.put(PREFS, json={"notes": "n", "base_branch": "develop"})
    await client.delete(PREFS)
    removed = (await _history(client))["entries"][0]["id"]
    undone = (await client.post(f"{HISTORY}/{removed}/undo")).json()["revision_id"]

    set_rows = await _audit(db_with_migrations, "repo.qa_config_set")
    assert set_rows == [{"actor": "dev@example.com", "details": {"repo": REPO, "revision_id": saved}}]
    assert len(await _audit(db_with_migrations, "repo.qa_config_deleted")) == 1
    prefs_set = await _audit(db_with_migrations, "repo.preferences_set")
    assert prefs_set[0]["details"]["fields"] == ["base_branch", "notes"]
    assert len(await _audit(db_with_migrations, "repo.preferences_cleared")) == 1
    undo_rows = await _audit(db_with_migrations, "repo.change_undone")
    assert undo_rows == [
        {
            "actor": "dev@example.com",
            "details": {"repo": REPO, "revision_id": removed, "new_revision_id": undone},
        }
    ]


@pytest.mark.requires_postgres
async def test_the_app_wires_the_history_store(api_client) -> None:
    """Production's _init_app_state must hand the route its store."""
    resp = await api_client.get("/api/v1/repos/acme/wired/history")
    assert resp.status_code == 200, resp.text
    assert resp.json()["entries"] == []


_PR_ON = {"enabled": True, "depth": "full", "model": None, "ready_authors": "team"}
_PR_OFF = {**_PR_ON, "enabled": False}


@pytest.mark.parametrize(
    ("surface", "before", "after", "what"),
    [
        ("qa_config", None, {"yaml_text": V1}, "Test setup created"),
        ("qa_config", {"yaml_text": V1}, {"yaml_text": V2}, "Test setup changed"),
        ("qa_config", {"yaml_text": V1}, None, "Test setup removed"),
        ("preferences", None, {"base_branch": "develop", "notes": ""}, "Settings created: Branch embry0 starts from"),
        (
            "preferences",
            {"base_branch": None, "notes": ""},
            {"base_branch": "develop", "notes": ""},
            "Branch embry0 starts from changed from (repository default) to develop",
        ),
        (
            "preferences",
            {"sandbox_profile": "slim"},
            {"sandbox_profile": None},
            "Work machine changed from slim to (embry0's default)",
        ),
        ("preferences", {"pr_review": None}, {"pr_review": _PR_ON}, "Pull request review turned on"),
        ("preferences", {"pr_review": _PR_ON}, {"pr_review": _PR_OFF}, "Pull request review turned off"),
        (
            "preferences",
            {"pr_review": _PR_ON, "notes": "", "git_author_email": None},
            {"pr_review": {**_PR_ON, "depth": "targeted"}, "notes": "x", "git_author_email": "bot@example.com"},
            "Settings changed: Commit author email, Notes, Pull request review",
        ),
        (
            "preferences",
            {"base_branch": "main", "dev_checks": [], "verification": None},
            {"base_branch": "develop", "dev_checks": [{"name": "t"}], "verification": {"x": 1}},
            "Branch embry0 starts from changed from main to develop. "
            "Settings changed: Checks before a pull request, Test gate",
        ),
        (
            "preferences",
            {"execution_mode": None, "auth_mode": None, "language_hint": None},
            {"execution_mode": "sdk", "auth_mode": "api_key", "language_hint": "python"},
            "Settings changed: Language label, How agents run, How agents sign in",
        ),
        ("preferences", {"notes": "", "pr_review": None}, None, "Settings cleared"),
        (
            "preferences",
            {"git_author_name": "Bot", "pr_review": _PR_OFF},
            None,
            "Settings cleared: Commit author name, Pull request review",
        ),
    ],
)
def test_what_each_change_reads(surface: str, before: Any, after: Any, what: str) -> None:
    assert change_what(surface, before, after) == what
