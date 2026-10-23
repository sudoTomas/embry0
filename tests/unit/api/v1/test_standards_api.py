"""Standards API (ADR 0014) against a real Postgres: versions, repository modes, and audits.

The executor is replaced with a recorder, so an audit's job row is real and
nothing runs. The admin gate is tested on a stub app with the real auth
chain, since the shared client runs in dev mode.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from embry0.services.standards import Standard

pytestmark = [pytest.mark.requires_postgres]

SCHEMA: dict[str, Any] = {
    "roles": {"color.base": {"type": "color"}, "color.accent": {"type": "color"}, "font.body": {"type": "font"}},
    "type_scale": {"body": {"size": "14px", "weight": 400}},
    "rules": [{"id": "one-accent", "text": "One accent colour only."}],
}
VALUES: dict[str, Any] = {"color.base": "#111111", "color.accent": "#3366ff", "font.body": "system-ui"}
TEMPLATE_GRAPH: dict[str, Any] = {
    "graph_id": "design-audit",
    "name": "design-audit",
    "nodes": [
        {"node_id": "triage", "agent_type": "triage", "label": "triage", "position": {"x": 0, "y": 0}},
        {"node_id": "analysis", "agent_type": "analysis", "label": "analysis", "position": {"x": 240, "y": 0}},
        {"node_id": "output", "agent_type": "output", "label": "output", "position": {"x": 480, "y": 0}},
    ],
    "edges": [
        {"edge_id": "e1", "source": "triage", "target": "analysis", "edge_type": "flow"},
        {"edge_id": "e2", "source": "analysis", "target": "output", "edge_type": "flow"},
    ],
    "metadata": {},
}


class _Executor:
    """Records what the audit route hands the executor; runs nothing."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any]]] = []
        self.cancelled: list[str] = []

    def submit_job(self, job_id: str, **kwargs: Any) -> None:
        self.submitted.append((job_id, kwargs))

    async def cancel_job(self, job_id: str, *, actor: str = "system") -> None:
        self.cancelled.append(job_id)


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    # The API fixture keeps jobs between tests, and a test here counts the
    # audit jobs, so each one starts with none.
    await db.execute("TRUNCATE standards, repo_standard_state")
    await db.execute("DELETE FROM jobs WHERE pipeline_template = 'design-audit'")
    await db.execute("DELETE FROM pipeline_templates WHERE name = 'design-audit'")
    yield api_client
    await db.execute("TRUNCATE standards, repo_standard_state")
    await db.execute("DELETE FROM jobs WHERE pipeline_template = 'design-audit'")


@pytest.fixture
def executor(client: AsyncClient) -> _Executor:
    recorder = _Executor()
    client.app.state.issue_executor = recorder  # type: ignore[attr-defined]
    return recorder


async def _register(client: AsyncClient, repo: str = "acme/widgets") -> None:
    resp = await client.post("/api/v1/repos", json={"repo": repo, "forge": "github"})
    assert resp.status_code == 201, resp.text


async def _put(client: AsyncClient, owner: str = "acme", **body: Any) -> Any:
    return await client.put(f"/api/v1/standards/{owner}/design", json={"schema": SCHEMA, "values": VALUES, **body})


async def _seed_template(client: AsyncClient) -> None:
    await client.app.state.templates_repo.upsert_builtin(  # type: ignore[attr-defined]
        name="design-audit", graph_definition=TEMPLATE_GRAPH, description="audit", sandbox_profile="slim"
    )


# ---- hold: versions ----


async def test_put_stores_a_version_and_a_second_put_supersedes_it(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/standards/acme/design")).status_code == 404
    first = await _put(client, "Acme", prose="Dense tables.", references=["acme/site"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["owner"], body["kind"], body["version"], body["status"]) == ("acme", "design", 1, "active")
    assert (body["schema"], body["values"], body["prose"], body["references"]) == (
        SCHEMA,
        VALUES,
        "Dense tables.",
        ["acme/site"],
    )
    second = await _put(client, values={**VALUES, "color.accent": "#cc3333"})
    assert second.status_code == 200, second.text
    assert (second.json()["version"], second.json()["prose"]) == (2, "")
    active = (await client.get("/api/v1/standards/acme/design")).json()
    assert (active["version"], active["values"]["color.accent"]) == (2, "#cc3333")
    versions = (await client.get("/api/v1/standards/acme/design/versions")).json()
    assert [(v["version"], v["status"]) for v in versions["versions"]] == [(2, "active"), (1, "superseded")]
    listing = (await client.get("/api/v1/standards/acme")).json()
    assert (listing["kinds"], listing["modes"]) == (["design"], ["off", "advise", "enforce"])
    assert [s["version"] for s in listing["standards"]] == [2]


async def test_put_writes_an_audit_row(client: AsyncClient) -> None:
    await _put(client)
    await _put(client)
    rows = await client.app.state.db.fetch(  # type: ignore[attr-defined]
        "SELECT details FROM audit_log WHERE action = 'standard.updated' ORDER BY created_at"
    )
    details = [json.loads(r["details"]) if isinstance(r["details"], str) else r["details"] for r in rows][-2:]
    assert [(d["version"], d["superseded_version"]) for d in details] == [(1, None), (2, 1)]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"schema": [], "values": {}}, "dict"),
        ({"schema": {}, "values": "x"}, "dict"),
        ({"schema": SCHEMA, "values": {**VALUES, "color.accent2": "#22aa88"}}, "values.color.accent2 names no role"),
        ({"schema": {"role": {}}, "values": {}}, "unknown keys"),
        ({"schema": SCHEMA, "values": VALUES, "owner": "acme"}, "extra"),
        ({"values": VALUES}, "schema"),
    ],
)
async def test_put_refuses_a_standard_that_cannot_be_stored(
    client: AsyncClient, body: dict[str, Any], fragment: str
) -> None:
    resp = await client.put("/api/v1/standards/acme/design", json=body)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text
    assert (await client.get("/api/v1/standards/acme/design/versions")).json()["versions"] == []


async def test_an_unknown_kind_or_a_bad_owner_is_422(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/standards/acme/layout")).status_code == 422
    resp = await client.put("/api/v1/standards/acme/layout", json={"schema": {}, "values": {}})
    assert resp.status_code == 422 and "unknown standard kind" in resp.text
    assert (await client.get("/api/v1/standards/-acme")).status_code == 422


async def test_writes_keep_the_csrf_guard(client: AsyncClient) -> None:
    bare = {"X-Requested-With": ""}
    resp = await client.put("/api/v1/standards/acme/design", json={"schema": {}, "values": {}}, headers=bare)
    assert resp.status_code == 403


# ---- sustain: a repository's mode ----


async def test_a_repository_defaults_to_mode_off_and_an_admin_sets_it(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/standards/acme/design/repos/acme/widgets")).status_code == 404
    await _register(client, "Acme/Widgets")
    state = (await client.get("/api/v1/standards/acme/design/repos/Acme/Widgets")).json()
    assert (state["repo"], state["mode"], state["score"], state["findings_open"]) == ("acme/widgets", "off", None, 0)
    exceptions = [{"rule": "one-accent", "reason": "charts need a second hue", "paths": ["src/charts"]}]
    resp = await client.put(
        "/api/v1/standards/acme/design/repos/Acme/Widgets", json={"mode": "advise", "exceptions": exceptions}
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["mode"], resp.json()["exceptions"]) == ("advise", exceptions)
    kept = (await client.put("/api/v1/standards/acme/design/repos/Acme/Widgets", json={"mode": "enforce"})).json()
    assert (kept["mode"], kept["exceptions"]) == ("enforce", exceptions)
    detail = (await client.get("/api/v1/repos/Acme/Widgets")).json()
    assert [(s["kind"], s["mode"]) for s in detail["standards"]] == [("design", "enforce")]
    listing = (await client.get("/api/v1/standards/acme")).json()
    assert [(s["repo"], s["mode"]) for s in listing["repos"]] == [("acme/widgets", "enforce")]


async def test_repo_detail_shows_mode_off_for_a_repository_never_set(client: AsyncClient) -> None:
    await _register(client)
    detail = (await client.get("/api/v1/repos/acme/widgets")).json()
    assert detail["standards"] == [
        {
            "repo": "acme/widgets",
            "owner": "acme",
            "kind": "design",
            "mode": "off",
            "last_audit_at": None,
            "last_audit_job_id": None,
            "score": None,
            "findings_open": 0,
            "exceptions": [],
            "updated_at": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/api/v1/standards/acme/design/repos/other/widgets", {"mode": "advise"}, 422),
        ("/api/v1/standards/acme/design/repos/widgets", {"mode": "advise"}, 422),
        ("/api/v1/standards/acme/design/repos/acme/gadgets", {"mode": "advise"}, 404),
        ("/api/v1/standards/acme/design/repos/acme/widgets", {"mode": "strict"}, 422),
        ("/api/v1/standards/acme/design/repos/acme/widgets", {"mode": "advise", "score": 90}, 422),
        ("/api/v1/standards/acme/design/repos/acme/widgets", {"mode": "advise", "exceptions": [{"rule": "x"}]}, 422),
    ],
)
async def test_a_mode_write_is_refused_off_its_owner_unregistered_or_malformed(
    client: AsyncClient, path: str, body: dict[str, Any], status: int
) -> None:
    await _register(client)
    await _register(client, "other/widgets")
    resp = await client.put(path, json=body)
    assert resp.status_code == status, resp.text


# ---- enforce: audits ----


async def _audit(client: AsyncClient, repo: str = "acme/widgets") -> Any:
    return await client.post(f"/api/v1/standards/acme/design/audit/{repo}")


async def _audit_jobs(client: AsyncClient) -> list[dict[str, Any]]:
    rows, _ = await client.app.state.jobs_repo.list_all(pipeline_template="design-audit")  # type: ignore[attr-defined]
    return list(rows)


async def test_a_repository_in_mode_off_is_never_audited(client: AsyncClient, executor: _Executor) -> None:
    await _register(client)
    await _put(client)
    await _seed_template(client)
    never_set = await _audit(client)
    assert never_set.status_code == 409 and "mode off" in never_set.text
    await client.put("/api/v1/standards/acme/design/repos/acme/widgets", json={"mode": "off"})
    switched_off = await _audit(client)
    assert switched_off.status_code == 409 and "mode off" in switched_off.text
    assert executor.submitted == [] and await _audit_jobs(client) == []


async def test_an_audit_needs_an_active_standard_and_the_template(client: AsyncClient, executor: _Executor) -> None:
    await _register(client)
    await client.put("/api/v1/standards/acme/design/repos/acme/widgets", json={"mode": "advise"})
    no_standard = await _audit(client)
    assert no_standard.status_code == 409 and "no active design standard for acme" in no_standard.text
    await _put(client)
    no_template = await _audit(client)
    assert no_template.status_code == 409
    assert "no pipeline template named 'design-audit'" in no_template.json()["detail"]
    assert executor.submitted == []
    assert (await _audit(client, "acme/gadgets")).status_code == 404
    assert (await _audit(client, "other/widgets")).status_code == 422


async def test_an_audit_starts_a_job_on_the_template_carrying_the_standard(
    client: AsyncClient, executor: _Executor
) -> None:
    await _register(client)
    await _put(client, prose="Dense tables.")
    await _seed_template(client)
    exceptions = [{"rule": "one-accent", "reason": "charts", "paths": []}]
    await client.put(
        "/api/v1/standards/acme/design/repos/acme/widgets", json={"mode": "advise", "exceptions": exceptions}
    )
    resp = await _audit(client)
    assert resp.status_code == 202, resp.text
    started = resp.json()
    job_id = started["job_id"]
    assert (started["repo"], started["version"], started["template"]) == ("acme/widgets", 1, "design-audit")

    job = await client.app.state.jobs_repo.get(job_id)  # type: ignore[attr-defined]
    assert (job["repo"], job["pipeline_template"], job["status"]) == ("acme/widgets", "design-audit", "pending")
    assert job["context"] == {"type": "git", "repo": "acme/widgets"}
    assert json.dumps(VALUES, indent=2, sort_keys=True) in job["task"]
    assert "Dense tables." in job["task"] and "`one-accent`: charts" in job["task"]

    assert len(executor.submitted) == 1
    submitted_id, kwargs = executor.submitted[0]
    assert submitted_id == job_id
    assert (kwargs["repo"], kwargs["body"], kwargs["kind"]) == ("acme/widgets", job["task"], "standard_audit")
    assert kwargs["title"] == job["task"].splitlines()[0]

    state = (await client.get("/api/v1/standards/acme/design/repos/acme/widgets")).json()
    assert state["last_audit_job_id"] == job_id

    again = await _audit(client)
    assert again.status_code == 409 and f"already pending: job {job_id}" in again.text
    assert len(executor.submitted) == 1, "a second audit while one is pending must not start"

    for status in ("running", "completed"):
        await client.app.state.jobs_repo.update(job_id, status=status)  # type: ignore[attr-defined]
    assert (await _audit(client)).status_code == 202, "a finished audit does not block the next"


async def test_a_mode_switched_off_mid_start_cancels_the_job(
    client: AsyncClient, executor: _Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client)
    await _put(client)
    await _seed_template(client)
    await client.put("/api/v1/standards/acme/design/repos/acme/widgets", json={"mode": "advise"})
    jobs = client.app.state.jobs_repo  # type: ignore[attr-defined]
    states = client.app.state.repo_standard_state_repo  # type: ignore[attr-defined]
    create = jobs.create

    async def create_then_switch_off(**kwargs: Any) -> str:
        job_id: str = await create(**kwargs)
        await states.set_mode("acme/widgets", "design", "off")
        return job_id

    monkeypatch.setattr(jobs, "create", create_then_switch_off)
    resp = await _audit(client)
    assert resp.status_code == 409 and "switched to mode off" in resp.text
    assert executor.submitted == []
    assert len(executor.cancelled) == 1
    state = await states.get("acme/widgets", "design")
    assert state is not None and state.last_audit_job_id is None


async def test_audits_started_at_once_start_one_job(
    client: AsyncClient, executor: _Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A double click or a retried tool call: one audit runs, the others are 409 and their jobs cancelled.

    The four requests meet at a barrier inside job creation, so each has read
    and checked the state before any names its job: the worst interleaving,
    every time.
    """
    await _register(client)
    await _put(client)
    await _seed_template(client)
    await client.put("/api/v1/standards/acme/design/repos/acme/widgets", json={"mode": "advise"})
    jobs = client.app.state.jobs_repo  # type: ignore[attr-defined]
    create, barrier = jobs.create, asyncio.Barrier(4)

    async def create_together(**kwargs: Any) -> str:
        job_id: str = await create(**kwargs)
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return job_id

    monkeypatch.setattr(jobs, "create", create_together)
    responses = await asyncio.gather(*(_audit(client) for _ in range(4)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409, 409, 409], [r.text for r in responses]
    (winner,) = [r.json()["job_id"] for r in responses if r.status_code == 202]
    for refused in (r for r in responses if r.status_code == 409):
        assert f"an audit of acme/widgets is already pending: job {winner}" in refused.text
    assert [job_id for job_id, _ in executor.submitted] == [winner]
    assert len(executor.cancelled) == 3 and winner not in executor.cancelled
    state = (await client.get("/api/v1/standards/acme/design/repos/acme/widgets")).json()
    assert state["last_audit_job_id"] == winner


# ---- tiers ----


def test_writes_are_admin_tier_and_reads_and_audits_are_not() -> None:
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from tests.fixtures.routes import app_routes

    gated: dict[tuple[str, str], bool] = {}
    for route in app_routes(create_app()):
        if not route.path.startswith("/api/v1/standards") or route.dependant is None:
            continue
        calls = {d.call for d in route.dependant.dependencies}
        for method in route.methods or ():
            gated[(method, route.path)] = _admin_dependency in calls
    assert gated == {
        ("GET", "/api/v1/standards/{owner}"): False,
        ("GET", "/api/v1/standards/{owner}/{kind}"): False,
        ("PUT", "/api/v1/standards/{owner}/{kind}"): True,
        ("GET", "/api/v1/standards/{owner}/{kind}/versions"): False,
        ("GET", "/api/v1/standards/{owner}/{kind}/repos/{repo:path}"): False,
        ("PUT", "/api/v1/standards/{owner}/{kind}/repos/{repo:path}"): True,
        ("POST", "/api/v1/standards/{owner}/{kind}/audit/{repo:path}"): False,
    }


def test_a_non_admin_session_gets_403_on_every_write() -> None:
    """Operator session: reads 200, writes 403; admin session: writes 200. The real auth chain, outside dev mode."""
    import os
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embry0.api.deps import bust_user_gate_cache, require_auth
    from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
    from embry0.api.v1 import standards as standards_v1
    from embry0.config import Embry0Config

    secret = "s" * 40
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://t.example.com/",
            auth0_client_id="cid",
            auth0_client_secret="sec",
            session_secret=secret,
            sso_public_origin="https://e.example.com",
            sso_roles_claim="https://example.com/roles",
        )
    app = FastAPI()
    app.state.config = cfg
    app.state.db = None
    standard = Standard(id="std-0123456789ab", owner="acme", kind="design", version=1, status="active")
    standards_repo = MagicMock()
    standards_repo.get_active = AsyncMock(return_value=standard)
    standards_repo.put = AsyncMock(return_value=(standard, None))
    app.state.standards_repo = standards_repo
    states_repo = MagicMock()
    states_repo.get = AsyncMock(return_value=None)
    states_repo.set_mode = AsyncMock(
        return_value=MagicMock(repo="acme/widgets", mode="advise", exceptions=[], to_dict=lambda: {"mode": "advise"})
    )
    app.state.repo_standard_state_repo = states_repo
    repos_repo = MagicMock()
    repos_repo.get = AsyncMock(return_value={"repo": "acme/widgets"})
    app.state.repos_repo = repos_repo
    user = MagicMock(session_epoch=1, disabled=False)
    app.state.users_repo = MagicMock(get_by_sub=AsyncMock(return_value=user))
    app.include_router(standards_v1.router, prefix="/api/v1", dependencies=[require_auth])

    def cookie(tier: str) -> str:
        return encode_session(
            secret, SessionData(sub="auth0|u", email="u@example.com", name="U", roles=[], tier=tier, epoch=1)
        )

    writes = [
        ("/api/v1/standards/acme/design", {"schema": {}, "values": {}}),
        ("/api/v1/standards/acme/design/repos/acme/widgets", {"mode": "advise"}),
    ]
    headers = {"X-Requested-With": "XMLHttpRequest"}
    bust_user_gate_cache()
    try:
        operator = TestClient(app)
        operator.cookies.set(SESSION_COOKIE, cookie("operator"))
        assert operator.get("/api/v1/standards/acme/design").status_code == 200
        for path, body in writes:
            assert operator.put(path, json=body, headers=headers).status_code == 403, path
        standards_repo.put.assert_not_awaited()
        states_repo.set_mode.assert_not_awaited()

        bust_user_gate_cache()
        admin = TestClient(app)
        admin.cookies.set(SESSION_COOKIE, cookie("admin"))
        for path, body in writes:
            assert admin.put(path, json=body, headers=headers).status_code == 200, path
    finally:
        bust_user_gate_cache()
