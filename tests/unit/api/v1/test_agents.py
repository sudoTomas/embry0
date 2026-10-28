from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(
        return_value=[
            {
                "type": "developer",
                "description": "Writes and edits code",
                "model": "claude-opus-4-5",
                "tools": ["bash", "editor"],
                "skills": [],
                "system_prompt": "",
                "is_builtin": True,
            }
        ]
    )
    app.state.agent_defs_repo = mock_repo
    return app


@pytest.mark.asyncio
async def test_list_agents(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    dev = next((a for a in data if a["type"] == "developer"), None)
    assert dev is not None
    assert "description" in dev
    assert "model" in dev


# State-changing requests need the CSRF header the dashboard sends.
_CSRF = {"X-Requested-With": "XMLHttpRequest"}


@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_avatar(app):
    app.state.agent_defs_repo.create = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "lantern", "description": "d", "model": "claude-sonnet-5", "avatar": "wizard"},
        )
    assert resp.status_code == 422
    assert "avatar must be one of" in resp.text
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_accepts_an_adventurer_avatar(app):
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "vanguard"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "vanguard", "description": "d", "model": "claude-sonnet-5", "avatar": "obsidian-warrior"},
        )
    assert resp.status_code == 201, resp.text
    assert app.state.agent_defs_repo.create.await_args.kwargs["avatar"] == "obsidian-warrior"


@pytest.mark.asyncio
async def test_update_agent_rejects_an_unknown_avatar_but_takes_an_adventurer(app):
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "developer"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        bad = await client.put("/api/v1/agents/developer", json={"avatar": "silver-wizard"})
        good = await client.put("/api/v1/agents/developer", json={"avatar": "champion"})
    assert bad.status_code == 422
    assert "avatar must be one of" in bad.text
    assert good.status_code == 200, good.text
    assert app.state.agent_defs_repo.update.await_args.kwargs == {"avatar": "champion"}


@pytest.mark.asyncio
async def test_create_agent_passes_identity_to_repository(app):
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "lantern"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "lantern",
                "description": "d",
                "model": "claude-sonnet-5",
                "display_name": "Lantern",
                "avatar": "bard",
                "color": "sky",
                "tagline": "Sings.",
            },
        )
    assert resp.status_code == 201
    kwargs = app.state.agent_defs_repo.create.await_args.kwargs
    assert (kwargs["display_name"], kwargs["avatar"], kwargs["color"], kwargs["tagline"]) == (
        "Lantern",
        "bard",
        "sky",
        "Sings.",
    )


@pytest.mark.asyncio
async def test_update_agent_rejects_long_tagline(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"tagline": "x" * 141})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["display_name", "avatar", "color", "tagline"])
async def test_update_agent_refuses_null_identity_field(app, field):
    app.state.agent_defs_repo.update = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={field: None})
    assert resp.status_code == 422
    assert f"{field} cannot be null" in resp.text
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_forwards_identity_fields(app):
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "developer"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"avatar": "rogue", "color": "rose"})
    assert resp.status_code == 200
    assert app.state.agent_defs_repo.update.await_args.kwargs == {"avatar": "rogue", "color": "rose"}


def _catalog(*names: str, sources: list[dict] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.catalog = AsyncMock(return_value=[{"name": n, "available": True} for n in names])
    repo.list_all = AsyncMock(return_value=sources or [])
    return repo


# The builtin source before its first sync lands (or forever, when the clone fails): no catalog yet.
_PENDING_SUPERPOWERS = [{"id": "superpowers", "plugin_name": "", "status": "pending", "enabled": True}]


@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_skill_name(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.create = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "lantern",
                "description": "d",
                "model": "claude-sonnet-5",
                "skills": ["superpowers:writing-plan"],
            },
        )
    assert resp.status_code == 422
    assert "superpowers:writing-plans" in resp.text, "the 422 names the closest known skill"
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_accepts_known_skill_names(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "lantern"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "lantern",
                "description": "d",
                "model": "claude-sonnet-5",
                "skills": ["superpowers:writing-plans"],
            },
        )
    assert resp.status_code == 201, resp.text
    assert app.state.agent_defs_repo.create.await_args.kwargs["skills"] == ["superpowers:writing-plans"]


@pytest.mark.asyncio
async def test_update_agent_rejects_unknown_skill_name(app):
    app.state.skill_sources_repo = _catalog("superpowers:brainstorming")
    app.state.agent_defs_repo.get = AsyncMock(return_value={"type": "developer", "skills": []})
    app.state.agent_defs_repo.update = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"skills": ["superpowers:brainstormin"]})
    assert resp.status_code == 422
    assert "superpowers:brainstorming" in resp.text
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_agent_lists_skills_missing_from_the_catalog(app):
    app.state.skill_sources_repo = _catalog("superpowers:brainstorming")
    app.state.agent_defs_repo.get = AsyncMock(
        return_value={
            "type": "developer",
            "model": "claude-sonnet-5",
            "skills": ["superpowers:brainstorming", "gone:skill", "commit-standard"],
        }
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/developer")
    assert resp.status_code == 200
    assert resp.json()["orphan_skills"] == ["gone:skill"], "a repository skill (no colon) is never an orphan"


_AUDIT = "embry0.api.v1._audit.emit_audit"


def _lantern(*skills: str) -> dict:
    return {"type": "lantern", "model": "claude-sonnet-5", "skills": list(skills)}


@pytest.mark.asyncio
async def test_add_agent_skill_appends_and_audits(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans", "superpowers:brainstorming")
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern("superpowers:brainstorming"))
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "lantern"})
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
            resp = await client.post("/api/v1/agents/lantern/skills/superpowers:writing-plans")
    assert resp.status_code == 200, resp.text
    app.state.agent_defs_repo.update.assert_awaited_once_with(
        "lantern", skills=["superpowers:brainstorming", "superpowers:writing-plans"]
    )
    assert [c.args[1] for c in audit.await_args_list] == ["agent.skill_added"]


@pytest.mark.asyncio
async def test_add_agent_skill_is_idempotent(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern("superpowers:writing-plans"))
    app.state.agent_defs_repo.update = AsyncMock()
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
            resp = await client.post("/api/v1/agents/lantern/skills/superpowers:writing-plans")
    assert resp.status_code == 200, resp.text
    assert resp.json()["skills"] == ["superpowers:writing-plans"]
    app.state.agent_defs_repo.update.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_skill_unknown_is_422(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern())
    app.state.agent_defs_repo.update = AsyncMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.post("/api/v1/agents/lantern/skills/superpowers:writing-plan")
    assert resp.status_code == 422
    assert "superpowers:writing-plans" in resp.text
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_skill_missing_agent_is_404(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.get = AsyncMock(return_value=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.post("/api/v1/agents/lantern/skills/superpowers:writing-plans")
    assert resp.status_code == 404
    assert "Agent type not found" in resp.text


@pytest.mark.asyncio
async def test_remove_agent_skill_drops_it_and_audits(app):
    app.state.agent_defs_repo.get = AsyncMock(
        return_value=_lantern("superpowers:brainstorming", "superpowers:writing-plans")
    )
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "lantern"})
    with patch(_AUDIT, new_callable=AsyncMock) as audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
            resp = await client.delete("/api/v1/agents/lantern/skills/superpowers:writing-plans")
    assert resp.status_code == 200, resp.text
    app.state.agent_defs_repo.update.assert_awaited_once_with("lantern", skills=["superpowers:brainstorming"])
    assert [c.args[1] for c in audit.await_args_list] == ["agent.skill_removed"]


@pytest.mark.asyncio
async def test_remove_agent_skill_absent_is_200_without_a_write(app):
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern("superpowers:brainstorming"))
    app.state.agent_defs_repo.update = AsyncMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.delete("/api/v1/agents/lantern/skills/superpowers:writing-plans")
    assert resp.status_code == 200, resp.text
    assert resp.json()["skills"] == ["superpowers:brainstorming"]
    app.state.agent_defs_repo.update.assert_not_awaited()


_SEEDED = ["superpowers:brainstorming", "superpowers:writing-plans"]


@pytest.mark.asyncio
async def test_update_agent_keeps_seeded_skills_while_the_catalog_is_empty(app):
    """The form sends skills on every save; names the row already has are not re-validated."""
    app.state.skill_sources_repo = _catalog(sources=_PENDING_SUPERPOWERS)
    app.state.agent_defs_repo.get = AsyncMock(return_value={"type": "developer", "skills": list(_SEEDED)})
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "developer", "skills": list(_SEEDED)})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"description": "d", "skills": list(_SEEDED)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == [], "names the row already had are not reported"
    app.state.agent_defs_repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_agent_keeps_orphan_skills_no_source_knows(app):
    app.state.skill_sources_repo = _catalog()
    app.state.agent_defs_repo.get = AsyncMock(return_value={"type": "developer", "skills": ["gone:old"]})
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "developer", "skills": ["gone:old"]})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"skills": ["gone:old"]})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_update_agent_still_refuses_an_unknown_name_it_adds(app):
    app.state.skill_sources_repo = _catalog(sources=_PENDING_SUPERPOWERS)
    app.state.agent_defs_repo.get = AsyncMock(return_value={"type": "developer", "skills": list(_SEEDED)})
    app.state.agent_defs_repo.update = AsyncMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"skills": [*_SEEDED, "nope:thing"]})
    assert resp.status_code == 422, resp.text
    assert "nope:thing" in resp.text
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_agent_skill_from_a_pending_source_is_accepted_with_a_warning(app):
    app.state.skill_sources_repo = _catalog(sources=_PENDING_SUPERPOWERS)
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern())
    app.state.agent_defs_repo.update = AsyncMock(return_value=_lantern("superpowers:x"))
    with patch(_AUDIT, new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
            resp = await client.post("/api/v1/agents/lantern/skills/superpowers:x")
    assert resp.status_code == 200, resp.text
    app.state.agent_defs_repo.update.assert_awaited_once_with("lantern", skills=["superpowers:x"])
    warnings = resp.json()["warnings"]
    assert len(warnings) == 1 and warnings[0].startswith("superpowers:x"), warnings


@pytest.mark.asyncio
async def test_create_agent_with_a_pending_source_skill_is_accepted_with_a_warning(app):
    app.state.skill_sources_repo = _catalog(sources=_PENDING_SUPERPOWERS)
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "lantern"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "lantern", "description": "d", "model": "claude-sonnet-5", "skills": ["superpowers:x"]},
        )
    assert resp.status_code == 201, resp.text
    assert [w.split()[0] for w in resp.json()["warnings"]] == ["superpowers:x"]


@pytest.mark.asyncio
async def test_add_agent_skill_accepts_a_repository_skill(app):
    """A bare name is the target repository's own .claude/skills entry: never checked, never warned about."""
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    app.state.agent_defs_repo.get = AsyncMock(return_value=_lantern())
    app.state.agent_defs_repo.update = AsyncMock(return_value=_lantern("commit-standard"))
    with patch(_AUDIT, new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF) as client:
            resp = await client.post("/api/v1/agents/lantern/skills/commit-standard")
    assert resp.status_code == 200, resp.text
    app.state.agent_defs_repo.update.assert_awaited_once_with("lantern", skills=["commit-standard"])
    assert resp.json()["warnings"] == []
