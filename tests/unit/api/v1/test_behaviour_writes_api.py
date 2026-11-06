"""What an operator sets on an agent's or a template's row about its behaviour (#167), through the API.

An agent's ``station`` and ``quest_template``, and a template's
``fixed_route``, ``offered_to_triage``, ``job_kind``, ``delivers`` and
``leader_agent``: each is stored as sent, a malformed one is a 422 naming
its field, and a name that names nothing is refused the same way.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

AGENT = {"type": "scout", "description": "d", "model": "m", "tools": ["Read"], "skills": [], "is_builtin": False}
GRAPH = {
    "nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "s", "agent_type": "scout"}],
    "edges": [{"edge_id": "e1", "source": "t", "target": "s", "edge_type": "flow"}],
}
QA_GRAPH = {
    "nodes": [
        {"node_id": "t", "agent_type": "triage"},
        {"node_id": "d", "agent_type": "developer"},
        {"node_id": "q", "agent_type": "qa"},
    ],
    "edges": [
        {"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"},
        {"edge_id": "e2", "source": "d", "target": "q", "edge_type": "flow"},
    ],
}
TEMPLATE = {"id": "t-1", "name": "scout-pass", "graph_definition": GRAPH, "sandbox_profile": None}


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    agents = MagicMock()
    agents.list_all = AsyncMock(return_value=[AGENT, {**AGENT, "type": "developer"}])
    agents.get = AsyncMock(return_value=AGENT)
    agents.create = AsyncMock(return_value=AGENT)
    agents.update = AsyncMock(return_value=AGENT)
    app.state.agent_defs_repo = agents
    templates = MagicMock()
    templates.list_all = AsyncMock(return_value=[{"id": "t-1", "name": "scout-pass"}])
    templates.get = AsyncMock(return_value=TEMPLATE)
    templates.create = AsyncMock(return_value=TEMPLATE)
    templates.update = AsyncMock(return_value=TEMPLATE)
    app.state.templates_repo = templates
    return app


async def _send(app: Any, method: str, path: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers={"X-Requested-With": "XMLHttpRequest"})


# --- agents ---------------------------------------------------------------------------------------------------

NEW_AGENT = {"type": "scout", "description": "d", "model": "m", "tools": ["Read"]}


async def test_an_agent_is_stored_with_its_station_and_its_task(app: Any) -> None:
    resp = await _send(
        app, "POST", "/api/v1/agents", {**NEW_AGENT, "station": "lectern", "quest_template": "scout-pass"}
    )
    assert resp.status_code == 201, resp.text
    created = app.state.agent_defs_repo.create.call_args.kwargs
    assert (created.get("station"), created.get("quest_template")) == ("lectern", "scout-pass"), created

    resp = await _send(app, "POST", "/api/v1/agents", NEW_AGENT)
    created = app.state.agent_defs_repo.create.call_args.kwargs
    assert (created.get("station"), created.get("quest_template")) == ("desk", None), (
        f"a new agent sits at a desk and the guild gives it no task: {created}"
    )

    for path, body, want in (
        ("scout", {"station": "map_table"}, {"station": "map_table"}),
        ("scout", {"quest_template": "scout-pass"}, {"quest_template": "scout-pass"}),
        ("scout", {"quest_template": None}, {"quest_template": None}),
        ("developer", {"quest_template": ""}, {"quest_template": ""}),
    ):
        resp = await _send(app, "PUT", f"/api/v1/agents/{path}", body)
        assert resp.status_code == 200, resp.text
        assert app.state.agent_defs_repo.update.call_args.kwargs == want, f"{body} reached the repository as sent"


@pytest.mark.parametrize(
    ("method", "path", "base"), [("POST", "/api/v1/agents", NEW_AGENT), ("PUT", "/api/v1/agents/scout", {})]
)
async def test_a_task_naming_no_template_is_a_coding_agents_only(app: Any, method, path, base) -> None:
    """'' runs the route triage picks, whose coding step is the developer's: an agent that does not code
    cannot take it."""
    resp = await _send(app, method, path, {**base, "quest_template": ""})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"].startswith("quest_template: ''"), resp.json()


async def test_a_save_that_keeps_a_stored_task_or_leader_is_not_refused_for_it(app: Any) -> None:
    """The forms send every field: a stored task whose template is gone, or a stored leader that no longer runs
    in jobs, must not stop the rest from saving. Only a value the write changes is checked."""
    app.state.agent_defs_repo.get = AsyncMock(return_value={**AGENT, "quest_template": "deleted-route"})
    resp = await _send(app, "PUT", "/api/v1/agents/scout", {"description": "new", "quest_template": "deleted-route"})
    assert resp.status_code == 200, f"an unchanged task was refused: {resp.text}"
    resp = await _send(app, "PUT", "/api/v1/agents/scout", {"quest_template": "other-route"})
    assert resp.status_code == 422, "a task the write changes is still checked"

    app.state.templates_repo.get = AsyncMock(return_value={**TEMPLATE, "leader_agent": "retired"})
    resp = await _send(
        app, "PUT", "/api/v1/pipelines/templates/t-1", {"leader_agent": "retired", "delivers": "a report"}
    )
    assert resp.status_code == 200, f"an unchanged leader was refused: {resp.text}"
    resp = await _send(app, "PUT", "/api/v1/pipelines/templates/t-1", {"leader_agent": "ghost"})
    assert resp.status_code == 422, "a leader the write changes is still checked"


async def test_an_agent_says_whether_it_codes(app: Any) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = (await client.get("/api/v1/agents")).json()
        one = (await client.get("/api/v1/agents/scout")).json()
    assert {a["type"]: a["coding_agent"] for a in listed} == {"scout": False, "developer": True}, listed
    assert one["coding_agent"] is False


@pytest.mark.parametrize("name", ["qa", "onboard", "pr-review"])
async def test_a_template_cannot_take_a_code_pipelines_name(app: Any, name: str) -> None:
    for method, path, body in (
        ("POST", "/api/v1/pipelines/templates", {"name": name, "graph_definition": GRAPH}),
        ("PUT", "/api/v1/pipelines/templates/t-1", {"name": name}),
        ("POST", "/api/v1/pipelines/templates/t-1/duplicate", {"name": name}),
    ):
        resp = await _send(app, method, path, body)
        assert resp.status_code == 422, f"{method} {path} named {name}: {resp.status_code}"
        assert "a pipeline embry0 defines" in resp.text, resp.text
    app.state.templates_repo.create.assert_not_awaited()
    app.state.templates_repo.update.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "base"),
    [("POST", "/api/v1/agents", NEW_AGENT), ("PUT", "/api/v1/agents/scout", {})],
    ids=["create", "update"],
)
@pytest.mark.parametrize(
    ("field", "value"),
    [("station", "throne"), ("station", 3), ("quest_template", ["scout-pass"]), ("quest_template", "x" * 101)],
)
async def test_a_malformed_agent_value_is_a_422_naming_its_field(app: Any, method, path, base, field, value) -> None:
    resp = await _send(app, method, path, {**base, field: value})
    assert resp.status_code == 422, f"{field}={value!r} must be a 422, got {resp.status_code}"
    assert field in resp.text, f"the 422 must name {field}: {resp.text}"
    app.state.agent_defs_repo.create.assert_not_awaited()
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "base"), [("POST", "/api/v1/agents", NEW_AGENT), ("PUT", "/api/v1/agents/scout", {})]
)
async def test_a_task_naming_no_template_is_refused(app: Any, method, path, base) -> None:
    resp = await _send(app, method, path, {**base, "quest_template": "deleted-route"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "quest_template: no pipeline template named 'deleted-route'"


async def test_a_station_cannot_be_cleared(app: Any) -> None:
    resp = await _send(app, "PUT", "/api/v1/agents/scout", {"station": None})
    assert resp.status_code == 422 and "station cannot be null" in resp.text, resp.text


# --- templates ------------------------------------------------------------------------------------------------

NEW_TEMPLATE = {"name": "scout-pass", "graph_definition": GRAPH}


async def test_a_template_is_stored_with_what_it_says_of_its_jobs(app: Any) -> None:
    behaviour = {
        "fixed_route": True,
        "offered_to_triage": False,
        "job_kind": "analysis",
        "delivers": "a list of dead flags",
        "leader_agent": "scout",
    }
    resp = await _send(app, "POST", "/api/v1/pipelines/templates", {**NEW_TEMPLATE, **behaviour})
    assert resp.status_code == 201, resp.text
    created = app.state.templates_repo.create.call_args.kwargs
    assert {k: created.get(k) for k in behaviour} == behaviour, created

    await _send(app, "POST", "/api/v1/pipelines/templates", NEW_TEMPLATE)
    created = app.state.templates_repo.create.call_args.kwargs
    assert {k: created.get(k) for k in behaviour} == {
        "fixed_route": False,
        "offered_to_triage": True,
        "job_kind": None,
        "delivers": "",
        "leader_agent": None,
    }, f"a template that says nothing behaves as one of the deployment's own did: {created}"


async def test_a_templates_update_writes_what_it_sends_and_clears_what_it_nulls(app: Any) -> None:
    for body, want in (
        ({"fixed_route": True}, {"fixed_route": True}),
        ({"offered_to_triage": False}, {"offered_to_triage": False}),
        ({"job_kind": "ops"}, {"job_kind": "ops"}),
        ({"job_kind": None}, {"job_kind": None}),
        ({"leader_agent": "scout"}, {"leader_agent": "scout"}),
        ({"leader_agent": None}, {"leader_agent": None}),
        ({"delivers": ""}, {"delivers": ""}),
    ):
        resp = await _send(app, "PUT", "/api/v1/pipelines/templates/t-1", body)
        assert resp.status_code == 200, resp.text
        sent = {k: v for k, v in app.state.templates_repo.update.call_args.kwargs.items() if k != "guard"}
        assert sent == want, f"{body}: {app.state.templates_repo.update.call_args}"


@pytest.mark.parametrize(
    ("method", "path", "base"),
    [("POST", "/api/v1/pipelines/templates", NEW_TEMPLATE), ("PUT", "/api/v1/pipelines/templates/t-1", {})],
    ids=["create", "update"],
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_kind", "chores"),
        ("delivers", "two\nlines"),
        ("delivers", "x" * 301),
        ("leader_agent", ""),
        ("fixed_route", "sometimes"),
        ("offered_to_triage", [1]),
    ],
)
async def test_a_malformed_template_value_is_a_422_naming_its_field(app: Any, method, path, base, field, value) -> None:
    resp = await _send(app, method, path, {**base, field: value})
    assert resp.status_code == 422, f"{field}={value!r} must be a 422, got {resp.status_code}"
    assert field in resp.text, f"the 422 must name {field}: {resp.text}"
    app.state.templates_repo.create.assert_not_awaited()
    app.state.templates_repo.update.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "base"),
    [("POST", "/api/v1/pipelines/templates", NEW_TEMPLATE), ("PUT", "/api/v1/pipelines/templates/t-1", {})],
    ids=["create", "update"],
)
async def test_a_leader_naming_no_agent_is_refused(app: Any, method, path, base) -> None:
    resp = await _send(app, method, path, {**base, "leader_agent": "ghost"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "leader_agent: no agent named 'ghost' runs in jobs"


@pytest.mark.parametrize("field", ["fixed_route", "offered_to_triage", "delivers"])
async def test_a_behaviour_column_that_is_never_null_refuses_a_null(app: Any, field: str) -> None:
    resp = await _send(app, "PUT", "/api/v1/pipelines/templates/t-1", {field: None})
    assert resp.status_code == 422 and f"{field} cannot be null" in resp.text, resp.text


async def test_a_fixed_route_with_a_qa_step_is_told_its_qa_never_runs(app: Any) -> None:
    resp = await _send(
        app,
        "POST",
        "/api/v1/pipelines/templates",
        {"name": "fixed-fix", "graph_definition": QA_GRAPH, "fixed_route": True},
    )
    assert resp.status_code == 201, resp.text
    assert any("qa step never runs" in w for w in resp.json()["warnings"]), resp.json()["warnings"]
    app.state.templates_repo.get = AsyncMock(return_value={**TEMPLATE, "graph_definition": QA_GRAPH})
    resp = await _send(app, "PUT", "/api/v1/pipelines/templates/t-1", {"fixed_route": True})
    assert any("qa step never runs" in w for w in resp.json()["warnings"]), resp.json()["warnings"]
    resp = await _send(app, "PUT", "/api/v1/pipelines/templates/t-1", {"fixed_route": False})
    assert not any("qa step" in w for w in resp.json()["warnings"]), resp.json()["warnings"]
