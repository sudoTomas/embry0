from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

# A minimal EXECUTABLE graph — since RAV-601, templates must pass
# validate_graph_definition (linear chain of known agent types) to save.
SAMPLE_GRAPH = {
    "graph_id": "g1",
    "name": "Test",
    "nodes": [
        {"node_id": "t", "agent_type": "triage"},
        {"node_id": "d", "agent_type": "developer"},
    ],
    "edges": [{"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"}],
}

SAMPLE_TEMPLATE = {
    "id": "tmpl-1",
    "name": "My Template",
    "graph_definition": SAMPLE_GRAPH,
    "sandbox_profile": None,
}


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=[SAMPLE_TEMPLATE])
    mock_repo.get = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.create = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.update = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.delete = AsyncMock()
    mock_repo.duplicate = AsyncMock(return_value={**SAMPLE_TEMPLATE, "id": "tmpl-2", "name": "Copy of My Template"})
    app.state.templates_repo = mock_repo
    return app


@pytest.mark.asyncio
async def test_list_templates(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_get_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates/tmpl-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "tmpl-1"
    assert resp.json()["name"] == "My Template"


@pytest.mark.asyncio
async def test_get_template_not_found(app):
    app.state.templates_repo.get = AsyncMock(return_value=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Template not found"


@pytest.mark.asyncio
async def test_create_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "My Template", "graph_definition": SAMPLE_GRAPH},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_update_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/pipelines/templates/tmpl-1",
            json={"name": "Updated Template"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_delete_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/pipelines/templates/tmpl-1",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"id": "tmpl-1", "status": "deleted"}


@pytest.mark.asyncio
async def test_duplicate_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pipelines/templates/tmpl-1/duplicate",
            json={"name": "Copy of My Template"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "tmpl-2"
    assert resp.json()["name"] == "Copy of My Template"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "put"])
async def test_a_template_agent_models_map_is_refused(app, method):
    """#468: the map was stored and never read; the API now says so instead."""
    body = {"name": "t", "graph_definition": SAMPLE_GRAPH, "agent_models": {"developer": "m"}}
    url = "/api/v1/pipelines/templates" + ("/tmpl-1" if method == "put" else "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await getattr(client, method)(url, json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 422, f"a template agent_models map must be refused, got {r.status_code}"
    assert "config.model" in r.text


def _graph_with_developer(node: dict) -> dict:
    return {
        **SAMPLE_GRAPH,
        "nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "d", "agent_type": "developer", **node}],
    }


def _catalog(*names: str, sources: list[dict] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.catalog = AsyncMock(return_value=[{"name": n, "available": True} for n in names])
    repo.list_all = AsyncMock(return_value=sources or [])
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "put"])
@pytest.mark.parametrize(
    "node",
    [
        {"config": {"skills": {"add": ["superpowers:writing-plan"]}}},
        {"skills": ["superpowers:writing-plan"]},  # the editor's legacy top-level shape (#78)
    ],
    ids=["config-delta", "legacy-top-level"],
)
async def test_a_template_naming_an_unknown_skill_is_refused(app, method, node):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans")
    body = {"name": "t", "graph_definition": _graph_with_developer(node)}
    url = "/api/v1/pipelines/templates" + ("/tmpl-1" if method == "put" else "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await getattr(client, method)(url, json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 422, f"an unknown skill must be refused, got {r.status_code}"
    assert "superpowers:writing-plans" in r.text, "the 422 names the closest known skill"
    app.state.templates_repo.create.assert_not_awaited()
    app.state.templates_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_template_with_known_skills_saves(app):
    app.state.skill_sources_repo = _catalog("superpowers:writing-plans", "local:house-style")
    node = {"config": {"skills": {"add": ["superpowers:writing-plans"], "remove": ["local:house-style"]}}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _graph_with_developer(node)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_a_malformed_template_skills_delta_is_refused(app):
    node = {"config": {"skills": {"add": "superpowers:writing-plans"}}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _graph_with_developer(node)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 422
    assert "skills delta" in r.text
    app.state.templates_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unpriced_model_at_the_legacy_top_level_is_refused(app):
    """#78: a model stored at the node's top level is the model that runs, so it is checked too."""
    node = {"model": "grok-9-unpriced"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _graph_with_developer(node)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 422, f"a legacy-shaped unpriced model must be refused, got {r.status_code}"
    assert "grok-9-unpriced" in r.text
    app.state.templates_repo.create.assert_not_awaited()


_PENDING_SUPERPOWERS = [{"id": "superpowers", "plugin_name": "", "status": "pending", "enabled": True}]


@pytest.mark.asyncio
async def test_a_template_update_keeping_its_skills_saves_with_an_empty_catalog(app):
    node = {"config": {"skills": {"add": ["superpowers:brainstorming"], "remove": ["superpowers:writing-plans"]}}}
    graph = _graph_with_developer(node)
    app.state.templates_repo.get = AsyncMock(return_value={**SAMPLE_TEMPLATE, "graph_definition": graph})
    app.state.skill_sources_repo = _catalog()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/v1/pipelines/templates/tmpl-1",
            json={"name": "t", "graph_definition": graph},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    app.state.templates_repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_template_update_adding_an_unknown_skill_is_still_refused(app):
    kept = _graph_with_developer({"config": {"skills": {"add": ["superpowers:brainstorming"]}}})
    app.state.templates_repo.get = AsyncMock(return_value={**SAMPLE_TEMPLATE, "graph_definition": kept})
    app.state.skill_sources_repo = _catalog()
    graph = _graph_with_developer({"config": {"skills": {"add": ["superpowers:brainstorming", "nope:x"]}}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/v1/pipelines/templates/tmpl-1",
            json={"graph_definition": graph},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 422, r.text
    assert "nope:x" in r.text and "superpowers:brainstorming" not in r.text.split("did you mean")[0]
    app.state.templates_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_template_adding_a_pending_source_skill_saves_with_a_warning(app):
    app.state.skill_sources_repo = _catalog(sources=_PENDING_SUPERPOWERS)
    node = {"config": {"skills": {"add": ["superpowers:brainstorming", "commit-standard"]}}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "t", "graph_definition": _graph_with_developer(node)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 201, r.text
    assert [w.split()[0] for w in r.json()["warnings"]] == ["superpowers:brainstorming"]
