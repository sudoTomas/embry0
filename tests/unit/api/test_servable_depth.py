"""A document nested deeper than the response serializer goes never makes a read answer 500.

The serializer behind a route's response model stops at 255 levels of
containers. A request field embry0 stores and serves back (a job's
``pipeline_config``, an agent's ``mcp_servers``, a template's graph, a
standard) used to take any depth: the write was stored, and every read that
showed it answered 500, the list of every job or agent included. Such a
request is refused with a 422 naming the field (``ServableFields``); an
agent's event too deep to serve is shown as its text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError

from embry0.api.schemas import (
    AgentCreateRequest,
    AgentUpdateRequest,
    JobCreateRequest,
    TemplateCreateRequest,
    TemplateUpdateRequest,
)
from embry0.api.v1.standards import StandardBody
from embry0.api.v1.watcher_stacks import StackBody
from embry0.workflows.row_behaviour import builtin_templates

DEEP = 300
SHALLOW = 150


def _nested(levels: int) -> Any:
    value: Any = "leaf"
    for _ in range(levels):
        value = {"a": value}
    return value


def _graph(extra: Any) -> dict[str, Any]:
    return {**builtin_templates()[0]["graph_definition"], "notes": extra}


REQUESTS: dict[str, tuple[type[BaseModel], Callable[[Any], dict[str, Any]]]] = {
    "job pipeline_config": (JobCreateRequest, lambda v: {"repo": "acme/widgets", "task": "x", "pipeline_config": v}),
    "agent mcp_servers": (
        AgentCreateRequest,
        lambda v: {
            "type": "probe",
            "description": "d",
            "model": "m",
            "mcp_servers": {"docs": {"command": "x", "v": v}},
        },
    ),
    "agent update mcp_servers": (AgentUpdateRequest, lambda v: {"mcp_servers": {"docs": {"command": "x", "v": v}}}),
    "template graph": (TemplateCreateRequest, lambda v: {"name": "deep", "graph_definition": _graph(v)}),
    "template update graph": (TemplateUpdateRequest, lambda v: {"graph_definition": _graph(v)}),
    "standard schema": (StandardBody, lambda v: {"schema": {"roles": v}, "values": {}}),
    "watcher stack tracker_target": (StackBody, lambda v: {"logql": '{app="x"}', "tracker_target": {"team_id": v}}),
}

#: The field each refusal names, as the API spells it (``schema``, not the model's ``schema_``).
REFUSED_FIELD = {
    "job pipeline_config": "pipeline_config",
    "agent mcp_servers": "mcp_servers",
    "agent update mcp_servers": "mcp_servers",
    "template graph": "graph_definition",
    "template update graph": "graph_definition",
    "standard schema": "schema",
    "watcher stack tracker_target": "tracker_target",
}


@pytest.mark.parametrize("name", REQUESTS)
def test_a_request_field_nested_too_deep_to_serve_back_is_refused(name: str) -> None:
    model, body = REQUESTS[name]
    try:
        model.model_validate(body(_nested(DEEP)))
    except ValidationError as exc:
        errors = exc.errors()
        field = REFUSED_FIELD[name]
        assert [e["loc"] for e in errors] == [(field,)], (
            f"the refusal is at {[e['loc'] for e in errors]}, not ({field!r},)"
        )
        assert errors[0]["msg"].endswith(f"{field} nests deeper than 200 levels, more than embry0 can serve back"), (
            errors[0]["msg"]
        )
    else:
        pytest.fail(f"{model.__name__} took a {name} nested {DEEP} levels deep, which no read of it can serve")


@pytest.mark.parametrize("name", REQUESTS)
def test_a_request_field_well_inside_the_depth_is_taken(name: str) -> None:
    model, body = REQUESTS[name]
    model.model_validate(body(_nested(SHALLOW)))


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app, answering an error with its status rather than raising it, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)  # type: ignore[attr-defined]
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


@pytest.mark.parametrize("levels", [DEEP, 5000])
async def test_writing_an_agent_too_deep_to_serve_is_a_422_and_the_list_still_reads(
    client: AsyncClient, levels: int
) -> None:
    body = {
        "type": "probe",
        "description": "d",
        "model": "m",
        "mcp_servers": {"docs": {"command": "x", "v": _nested(levels)}},
    }

    created = await client.post("/api/v1/agents", json=body)
    listed = await client.get("/api/v1/agents")

    assert created.status_code == 422, f"{created.status_code}: {created.text[:300]}"
    assert "nests deeper than 200 levels" in created.text
    assert listed.status_code == 200, f"the agents list answered {listed.status_code}"


@pytest.mark.parametrize("levels", [DEEP, 5000])
async def test_writing_a_template_too_deep_to_serve_is_a_422_without_the_body(client: AsyncClient, levels: int) -> None:
    """The 422 of a route that echoes its input leaves out an input too deep to echo, as it does NaN."""
    created = await client.post(
        "/api/v1/pipelines/templates", json={"name": "deep", "graph_definition": _graph(_nested(levels))}
    )

    assert created.status_code == 422, f"{created.status_code}: {created.text[:300]}"
    assert "nests deeper than 200 levels" in created.text
    assert '"leaf"' not in created.text, "the 422 echoed the body nested too deep to serve"


async def test_an_event_too_deep_to_serve_is_shown_as_its_text(client: AsyncClient) -> None:
    jobs = client.app.state.jobs_repo  # type: ignore[attr-defined]
    job_id = await jobs.create(repo="acme/widgets", task="x")
    await jobs.append_log_event(job_id, {"type": "tool_call", "tool": "Bash", "input": _nested(DEEP)})
    await jobs.append_log_event(job_id, {"type": "tool_result", "tool": "Bash", "output": "ok"})

    page = await client.get(f"/api/v1/jobs/{job_id}/logs/events")

    assert page.status_code == 200, f"the events page answered {page.status_code}"
    raw, result = page.json()["events"]
    assert raw["type"] == "raw" and raw["message"].startswith('{"type": "tool_call"')
    assert result["type"] == "tool_result" and result["output"] == "ok"


@pytest.mark.parametrize("levels", [DEEP, 5000])
async def test_a_watcher_stack_too_deep_to_serve_is_a_422_and_the_list_still_reads(
    client: AsyncClient, levels: int
) -> None:
    """``tracker_target`` took any depth: a 300-level one was stored, and every ``GET /watcher/stacks`` answered 500."""
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    state = client.app.state  # type: ignore[attr-defined]
    if getattr(state, "watcher_stacks_repo", None) is None:  # built only when the watcher is on
        state.watcher_stacks_repo = WatcherStacksRepository(state.db)
    body = {"logql": '{app="widgets"}', "tracker": "linear", "tracker_target": {"team_id": _nested(levels)}}

    stored = await client.put("/api/v1/watcher/stacks/acme/widgets", json=body)
    listed = await client.get("/api/v1/watcher/stacks")

    assert stored.status_code == 422, f"{stored.status_code}: {stored.text[:300]}"
    assert "tracker_target nests deeper than 200 levels" in stored.text
    assert listed.status_code == 200, f"the stacks list answered {listed.status_code}"
