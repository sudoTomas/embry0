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

import json
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


# ---- a row stored too deep to serve still reads (#431) ----
#
# The write paths refuse such a document, but a row stored before they did, or written by something other than the
# API, still holds one. Each case writes one through its repository, as such a writer would, beside a row that is
# fine, and reads it back the way the dashboard does: the read of the row and every list that shows it answer 200,
# the deep document as its JSON text, the rest as it is.

#: Where a case's deep document shows in an answer, or None for a list that shows no document of the row.
Pick = Callable[[Any], Any] | None


async def _a_job(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    stored = {"notes": _nested(DEEP)}
    await state.db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-deep', 'Deep', 'acme/widgets')")
    deep = await state.jobs_repo.create(repo="acme/widgets", task="x", issue_id="iss-deep", pipeline_config=stored)
    await state.jobs_repo.create(repo="acme/widgets", task="y", issue_id="iss-deep", pipeline_config={"notes": 1})

    def in_jobs(jobs: list[dict[str, Any]]) -> Any:
        others = [j["pipeline_config"] for j in jobs if j["job_id"] != deep]
        assert others == [{"notes": 1}], f"the job beside it is not served as it is: {others}"
        return next(j for j in jobs if j["job_id"] == deep)["pipeline_config"]

    return stored, [
        (f"/api/v1/jobs/{deep}", lambda body: body["pipeline_config"]),
        ("/api/v1/jobs", lambda body: in_jobs(body["jobs"])),
        ("/api/v1/issues/iss-deep", lambda body: in_jobs(body["jobs"])),
    ]


async def _an_agent(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    stored = {"docs": {"command": "x", "v": _nested(DEEP)}}
    await state.agent_defs_repo.create("deep-agent", "d", "m", mcp_servers=stored)
    return stored, [
        ("/api/v1/agents/deep-agent", lambda body: body["mcp_servers"]),
        ("/api/v1/agents", lambda body: next(a for a in body if a["type"] == "deep-agent")["mcp_servers"]),
    ]


async def _a_template(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    stored = _graph(_nested(DEEP))
    template = await state.templates_repo.create("deep-template", graph_definition=stored)
    return stored, [
        (f"/api/v1/pipelines/templates/{template['id']}", lambda body: body["graph_definition"]),
        ("/api/v1/pipelines/templates", None),
    ]


async def _a_standard(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    stored = {"roles": _nested(DEEP)}
    await state.standards_repo.put("acme", "design", schema=stored, values={})
    return stored, [
        ("/api/v1/standards/acme/design", lambda body: body["schema"]),
        ("/api/v1/standards/acme", lambda body: body["standards"][0]["schema"]),
        ("/api/v1/standards/acme/design/versions", lambda body: body["versions"][0]["schema"]),
    ]


async def _a_watcher_stack(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    from embry0.services.watcher_stacks import WatcherStack
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    if getattr(state, "watcher_stacks_repo", None) is None:  # built only when the watcher is on
        state.watcher_stacks_repo = WatcherStacksRepository(state.db)
    stored = {"team_id": _nested(DEEP)}
    await state.watcher_stacks_repo.upsert(WatcherStack(repo="acme/widgets", logql='{app="x"}', tracker_target=stored))
    await state.watcher_stacks_repo.upsert(
        WatcherStack(repo="acme/lib", logql='{app="y"}', tracker_target={"team_id": 1})
    )

    def in_stacks(stacks: list[dict[str, Any]]) -> Any:
        others = [s["tracker_target"] for s in stacks if s["repo"] != "acme/widgets"]
        assert others == [{"team_id": 1}], f"the stack beside it is not served as it is: {others}"
        return next(s for s in stacks if s["repo"] == "acme/widgets")["tracker_target"]

    return stored, [
        ("/api/v1/watcher/stacks/acme/widgets", lambda body: body["tracker_target"]),
        ("/api/v1/watcher/stacks", lambda body: in_stacks(body["stacks"])),
    ]


async def _a_map_part(state: Any) -> tuple[Any, list[tuple[str, Pick]]]:
    from datetime import UTC, datetime

    node = {"id": "part-1", "label": "Part", "notes": _nested(DEEP)}
    await state.knowledge_maps_repo.put(
        slug="deep-map",
        owner="acme",
        title="Deep",
        repo="acme/widgets",
        commit_sha=None,
        source="upload",
        generated_at=None,
        uploaded_by="someone",
        graph={"format": "embry0.map/1", "nodes": [node], "edges": []},
        details={},
        now=datetime.now(UTC),
    )
    return node, [
        ("/api/v1/maps/deep-map/parts/part-1?owner=acme", lambda body: body["node"]),
        ("/api/v1/maps", None),
    ]


STORED_TOO_DEEP: dict[str, Callable[[Any], Any]] = {
    "a job's pipeline_config": _a_job,
    "an agent's mcp_servers": _an_agent,
    "a template's graph_definition": _a_template,
    "a standard's schema": _a_standard,
    "a watcher stack's tracker_target": _a_watcher_stack,
    "a knowledge map's part": _a_map_part,
}


@pytest.mark.parametrize("stored_as", STORED_TOO_DEEP)
async def test_a_row_stored_too_deep_to_serve_reads_with_its_document_as_text(
    client: AsyncClient, stored_as: str
) -> None:
    stored, reads = await STORED_TOO_DEEP[stored_as](client.app.state)  # type: ignore[attr-defined]

    problems: list[str] = []
    for path, pick in reads:
        answer = await client.get(path)
        if answer.status_code != 200:
            problems.append(f"GET {path} answered {answer.status_code}")
            continue
        shown = None if pick is None else pick(answer.json())
        if pick is not None and not (isinstance(shown, str) and json.loads(shown) == stored):
            problems.append(f"GET {path} showed {str(shown)[:120]}, not the document as its JSON text")
    assert not problems, f"{stored_as}: " + "; ".join(problems)


#: A write that changes another field of a row stored too deep, and answers the row it stored: (method, path, body,
#: the answer's field that holds the deep document).
ECHOING_WRITES: dict[str, tuple[str, str, dict[str, Any] | None, str]] = {
    "an agent's description": ("PUT", "/api/v1/agents/deep-agent", {"description": "new"}, "mcp_servers"),
    "an agent's skill added": ("POST", "/api/v1/agents/deep-agent/skills/house-style", None, "mcp_servers"),
    "an agent's skill removed": ("DELETE", "/api/v1/agents/deep-agent/skills/house-style", None, "mcp_servers"),
    "a template's description": (
        "PUT",
        "/api/v1/pipelines/templates/{template}",
        {"description": "new"},
        "graph_definition",
    ),
    "a template duplicated": (
        "POST",
        "/api/v1/pipelines/templates/{template}/duplicate",
        {"name": "copy"},
        "graph_definition",
    ),
}


@pytest.mark.parametrize("write", ECHOING_WRITES)
async def test_a_write_to_a_row_stored_too_deep_answers_the_row_with_its_document_as_text(
    client: AsyncClient, write: str
) -> None:
    """The write is stored either way; its answer, the row as stored, must not be a 500 because of a field it left."""
    state = client.app.state  # type: ignore[attr-defined]
    servers = {"docs": {"command": "x", "v": _nested(DEEP)}}
    await state.agent_defs_repo.create("deep-agent", "d", "m", skills=["house-style"], mcp_servers=servers)
    graph = _graph(_nested(DEEP))
    template = await state.templates_repo.create("deep-template", graph_definition=graph)
    method, path, body, field = ECHOING_WRITES[write]

    answer = await client.request(method, path.format(template=template["id"]), json=body)

    assert answer.status_code in (200, 201), f"{write}: {method} {path} answered {answer.status_code}"
    shown = answer.json()[field]
    stored = servers if field == "mcp_servers" else graph
    assert isinstance(shown, str) and json.loads(shown) == stored, (
        f"{write}: the answer must show {field} as its JSON text, not {str(shown)[:120]}"
    )
