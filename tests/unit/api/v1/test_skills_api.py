"""Skills routes: catalog, sources, inline skills (agent identity and skills spec 3.4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg.exceptions import UniqueViolationError
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.skills.sync import SkillSyncBusy
from embry0.storage.repositories.skills import SkillSourceRefused

_CSRF = {"X-Requested-With": "XMLHttpRequest"}
_AUDIT = "embry0.api.v1._audit.emit_audit"


def _source(source_id: str = "mine", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": source_id,
        "kind": "git",
        "url": "https://example.com/skills.git",
        "ref": "main",
        "enabled": True,
        "is_builtin": False,
        "status": "ready",
        "error": "",
        "resolved_sha": "abc",
        "plugin_name": "",
        "skills": [{"name": "x", "description": "d"}],
        "synced_at": datetime(2026, 10, 18, tzinfo=UTC),
    }
    row.update(overrides)
    return row


class _Recording(set):
    """A background-task set that remembers every task added, after the task has left it."""

    def __init__(self) -> None:
        super().__init__()
        self.added: list[asyncio.Task[Any]] = []

    def add(self, task: asyncio.Task[Any]) -> None:
        self.added.append(task)
        super().add(task)


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    sources = MagicMock()
    sources.list_all = AsyncMock(return_value=[_source()])
    sources.get = AsyncMock(return_value=_source())
    sources.create = AsyncMock(return_value=_source())
    sources.update = AsyncMock(return_value=_source())
    sources.delete = AsyncMock()
    sources.catalog = AsyncMock(return_value=[])
    inline = MagicMock()
    inline.list_all = AsyncMock(return_value=[])
    inline.upsert = AsyncMock(return_value={"name": "house-style", "description": "d", "body": "b"})
    inline.delete = AsyncMock()
    sync = MagicMock()
    sync.sync = AsyncMock(return_value={})
    sync.is_syncing = MagicMock(return_value=False)
    agents = MagicMock()
    agents.list_all = AsyncMock(return_value=[])
    templates = MagicMock()
    templates.list_all = AsyncMock(return_value=[])
    templates.get = AsyncMock(return_value=None)
    app.state.skill_sources_repo = sources
    app.state.inline_skills_repo = inline
    app.state.skill_sync = sync
    app.state.skill_store = MagicMock()
    app.state.agent_defs_repo = agents
    app.state.templates_repo = templates
    app.state.background_tasks = set()
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF)


def _actions(audit: AsyncMock) -> list[str]:
    return [c.args[1] for c in audit.await_args_list]


@pytest.mark.asyncio
async def test_catalog_is_the_repository_catalog_verbatim(app):
    catalog = [
        {"name": "mine:x", "source_id": "mine", "description": "d", "available": True, "status": "ready"},
    ]
    app.state.skill_sources_repo.catalog = AsyncMock(return_value=catalog)
    async with _client(app) as client:
        resp = await client.get("/api/v1/skills")
    assert resp.status_code == 200, resp.text
    assert resp.json() == catalog


@pytest.mark.asyncio
async def test_sources_list_carries_syncing_flag(app):
    app.state.skill_sources_repo.list_all = AsyncMock(return_value=[_source("mine"), _source("other")])
    app.state.skill_sync.is_syncing = MagicMock(side_effect=lambda sid: sid == "other")
    async with _client(app) as client:
        resp = await client.get("/api/v1/skills/sources")
    assert resp.status_code == 200, resp.text
    assert [(r["id"], r["syncing"]) for r in resp.json()] == [("mine", False), ("other", True)]
    assert resp.json()[0]["synced_at"] == "2026-10-18T00:00:00+00:00"


@pytest.mark.asyncio
async def test_get_source_404_when_missing(app):
    app.state.skill_sources_repo.get = AsyncMock(return_value=None)
    async with _client(app) as client:
        resp = await client.get("/api/v1/skills/sources/nope")
    assert resp.status_code == 404
    assert "no skill source 'nope'" in resp.text


@pytest.mark.asyncio
async def test_create_source_stores_audits_and_schedules_a_sync(app):
    body = {"id": "mine", "kind": "git", "url": "https://example.com/skills.git", "ref": "main"}
    app.state.background_tasks = _Recording()
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.post("/api/v1/skills/sources", json=body)
        await asyncio.sleep(0)
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == "mine"
    app.state.skill_sources_repo.create.assert_awaited_once_with(
        source_id="mine", kind="git", url="https://example.com/skills.git", ref="main", enabled=True
    )
    assert len(app.state.background_tasks.added) == 1, "the sync runs as a tracked background task"
    app.state.skill_sync.sync.assert_awaited_once_with("mine")
    assert _actions(audit) == ["skill_source.created"]


@pytest.mark.asyncio
async def test_create_source_refuses_a_credential_id(app):
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/skills/sources",
            json={"id": "mine", "kind": "git", "url": "https://example.com/s.git", "ref": "main", "credential_id": "c"},
        )
    assert resp.status_code == 422
    assert "credential_id is reserved" in resp.text
    app.state.skill_sources_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_source_refused_value_is_422_with_the_message(app):
    app.state.skill_sources_repo.create = AsyncMock(
        side_effect=SkillSourceRefused("a git source is cloned anonymously over https")
    )
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/skills/sources", json={"id": "mine", "kind": "git", "url": "http://x.com/s.git", "ref": "main"}
        )
    assert resp.status_code == 422
    assert "a git source is cloned anonymously over https" in resp.text
    app.state.skill_sync.sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_source_duplicate_id_is_409(app):
    app.state.skill_sources_repo.create = AsyncMock(side_effect=UniqueViolationError("dup"))
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/skills/sources", json={"id": "mine", "kind": "git", "url": "https://x.com/s.git", "ref": "main"}
        )
    assert resp.status_code == 409
    assert "skill source 'mine' already exists" in resp.text


@pytest.mark.asyncio
async def test_patch_builtin_refuses_ref(app):
    app.state.skill_sources_repo.get = AsyncMock(return_value=_source("superpowers", is_builtin=True))
    async with _client(app) as client:
        resp = await client.patch("/api/v1/skills/sources/superpowers", json={"ref": "v2"})
    assert resp.status_code == 403
    assert "built-in sources accept enabled only" in resp.text
    app.state.skill_sources_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_builtin_accepts_enabled(app):
    app.state.skill_sources_repo.get = AsyncMock(return_value=_source("superpowers", is_builtin=True))
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.patch("/api/v1/skills/sources/superpowers", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    app.state.skill_sources_repo.update.assert_awaited_once_with("superpowers", enabled=False)
    assert _actions(audit) == ["skill_source.updated"]
    app.state.skill_sync.sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_ref_on_a_non_git_source_is_422(app):
    app.state.skill_sources_repo.get = AsyncMock(return_value=_source("mine", kind="path", ref=""))
    async with _client(app) as client:
        resp = await client.patch("/api/v1/skills/sources/mine", json={"ref": "main"})
    assert resp.status_code == 422
    assert "a path source has no ref" in resp.text
    app.state.skill_sources_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_null_fields_change_nothing(app):
    async with _client(app) as client:
        resp = await client.patch("/api/v1/skills/sources/mine", json={"ref": None, "enabled": None})
    assert resp.status_code == 200, resp.text
    app.state.skill_sources_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_ref_on_a_git_source_resyncs(app):
    with patch(_AUDIT, new_callable=AsyncMock):
        async with _client(app) as client:
            resp = await client.patch("/api/v1/skills/sources/mine", json={"ref": "v2"})
        await asyncio.sleep(0)
    assert resp.status_code == 200, resp.text
    app.state.skill_sources_repo.update.assert_awaited_once_with("mine", ref="v2")
    app.state.skill_sync.sync.assert_awaited_once_with("mine")


@pytest.mark.asyncio
async def test_sync_route_schedules_and_answers_202(app):
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.post("/api/v1/skills/sources/mine/sync")
        await asyncio.sleep(0)
    assert resp.status_code == 202, resp.text
    app.state.skill_sync.sync.assert_awaited_once_with("mine")
    assert _actions(audit) == ["skill_source.synced"]


@pytest.mark.asyncio
async def test_sync_route_409_when_a_sync_is_running(app):
    app.state.skill_sync.is_syncing = MagicMock(return_value=True)
    async with _client(app) as client:
        resp = await client.post("/api/v1/skills/sources/mine/sync")
    assert resp.status_code == 409
    app.state.skill_sync.sync.assert_not_called()


@pytest.mark.asyncio
async def test_a_busy_background_sync_does_not_leak_an_exception(app):
    app.state.background_tasks = _Recording()
    app.state.skill_sync.sync = AsyncMock(side_effect=SkillSyncBusy("mine"))
    with patch(_AUDIT, new_callable=AsyncMock):
        async with _client(app) as client:
            resp = await client.post("/api/v1/skills/sources/mine/sync")
        await asyncio.sleep(0)
    assert resp.status_code == 202
    tasks = app.state.background_tasks.added
    assert len(tasks) == 1, "the sync task is tracked in app.state.background_tasks"
    assert tasks[0].done() and tasks[0].exception() is None
    assert not app.state.background_tasks, "a finished sync task leaves the set"


@pytest.mark.asyncio
async def test_delete_source_409_when_an_agent_references_it(app):
    app.state.agent_defs_repo.list_all = AsyncMock(
        return_value=[{"type": "lantern", "skills": ["mine:x"]}, {"type": "other", "skills": ["minesweeper:y"]}]
    )
    async with _client(app) as client:
        resp = await client.delete("/api/v1/skills/sources/mine")
    assert resp.status_code == 409
    assert resp.json()["detail"]["agents"] == ["lantern"]
    app.state.skill_sources_repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_source_409_when_a_template_node_adds_its_skill(app):
    app.state.templates_repo.list_all = AsyncMock(return_value=[{"id": "t1", "name": "review"}])
    app.state.templates_repo.get = AsyncMock(
        return_value={
            "id": "t1",
            "name": "review",
            "graph_definition": {"nodes": [{"node_id": "a", "config": {"skills": {"add": ["mine:x"]}}}]},
        }
    )
    async with _client(app) as client:
        resp = await client.delete("/api/v1/skills/sources/mine")
    assert resp.status_code == 409
    assert resp.json()["detail"]["templates"] == ["review"]


@pytest.mark.asyncio
async def test_delete_builtin_source_is_403(app):
    app.state.skill_sources_repo.delete = AsyncMock(side_effect=ValueError("cannot delete built-in skill source"))
    async with _client(app) as client:
        resp = await client.delete("/api/v1/skills/sources/mine")
    assert resp.status_code == 403
    app.state.skill_store.remove_source.assert_not_called()


@pytest.mark.asyncio
async def test_delete_referenced_builtin_source_is_403_not_409(app):
    app.state.skill_sources_repo.get = AsyncMock(
        return_value=_source("superpowers", is_builtin=True, skills=[{"name": "writing-plans"}])
    )
    app.state.agent_defs_repo.list_all = AsyncMock(
        return_value=[{"type": "developer", "skills": ["superpowers:writing-plans"]}]
    )
    async with _client(app) as client:
        resp = await client.delete("/api/v1/skills/sources/superpowers")
    assert resp.status_code == 403, resp.text
    assert "built-in" in resp.text
    app.state.skill_sources_repo.delete.assert_not_awaited()
    app.state.skill_store.remove_source.assert_not_called()


@pytest.mark.asyncio
async def test_delete_source_removes_its_tree_and_audits(app):
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.delete("/api/v1/skills/sources/mine")
    assert resp.status_code == 200, resp.text
    app.state.skill_sources_repo.delete.assert_awaited_once_with("mine")
    app.state.skill_store.remove_source.assert_called_once_with("mine")
    assert _actions(audit) == ["skill_source.deleted"]


@pytest.mark.asyncio
async def test_list_inline_skills(app):
    app.state.inline_skills_repo.list_all = AsyncMock(return_value=[{"name": "house-style", "description": "d"}])
    async with _client(app) as client:
        resp = await client.get("/api/v1/skills/inline")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "house-style", "description": "d"}]


@pytest.mark.asyncio
async def test_put_inline_skill_upserts_audits_and_resyncs_local(app):
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.put("/api/v1/skills/inline/house-style", json={"description": "d", "body": "b"})
        await asyncio.sleep(0)
    assert resp.status_code == 200, resp.text
    app.state.inline_skills_repo.upsert.assert_awaited_once_with(name="house-style", description="d", body="b")
    app.state.skill_sync.sync.assert_awaited_once_with("local")
    assert _actions(audit) == ["inline_skill.written"]


@pytest.mark.asyncio
async def test_put_inline_skill_bad_name_is_422(app):
    app.state.inline_skills_repo.upsert = AsyncMock(side_effect=SkillSourceRefused("name must match ^[a-z]"))
    async with _client(app) as client:
        resp = await client.put("/api/v1/skills/inline/Bad_Name", json={"description": "d", "body": "b"})
    assert resp.status_code == 422
    assert "name must match" in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["description", "body"])
async def test_put_inline_skill_refuses_a_nul_byte(app, field):
    payload = {"description": "d", "body": "b"}
    payload[field] = "a\x00b"
    async with _client(app) as client:
        resp = await client.put("/api/v1/skills/inline/house-style", json=payload)
    assert resp.status_code == 422
    assert "NUL" in resp.text
    app.state.inline_skills_repo.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_inline_skill_409_when_an_agent_references_it(app):
    app.state.agent_defs_repo.list_all = AsyncMock(return_value=[{"type": "lantern", "skills": ["local:house-style"]}])
    async with _client(app) as client:
        resp = await client.delete("/api/v1/skills/inline/house-style")
    assert resp.status_code == 409
    assert resp.json()["detail"]["agents"] == ["lantern"]
    app.state.inline_skills_repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_inline_skill_audits_and_resyncs_local(app):
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with _client(app) as client:
            resp = await client.delete("/api/v1/skills/inline/house-style")
        await asyncio.sleep(0)
    assert resp.status_code == 200, resp.text
    app.state.inline_skills_repo.delete.assert_awaited_once_with("house-style")
    app.state.skill_sync.sync.assert_awaited_once_with("local")
    assert _actions(audit) == ["inline_skill.deleted"]


@pytest.mark.asyncio
async def test_routes_answer_503_without_skill_storage(app):
    app.state.skill_sources_repo = None
    async with _client(app) as client:
        resp = await client.get("/api/v1/skills")
    assert resp.status_code == 503
