"""A JSONB value read only in part is marked so readers refuse it, and a whole document replaces it (#527).

Past ``DEEP_DECODE_MAX_CHARS`` the codec stops reading a value too deep for
``json.loads``: every key after the cut is missing. Read as it is, each missing
key would be its default: a settings budget, a job's narrowed tools or its
model, a profile's isolation fields. So the value comes back as a
``PartReadDict`` / ``PartReadList``, the readers that act on it refuse it with
``PartReadError``, and the API still serves what was read. The write guard tells
it by type, so a value that only says what the marker says is written as any
other.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.safety.part_read import TOO_LONG_TO_READ, PartReadError, holds_part_read

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

BUDGET = {"max_budget_per_job_usd": 1.5, "daily_cap_usd": 3.0}


def _cut(after: dict[str, Any]) -> str:
    """``{"a": <12,000 levels, past the read limit>, **after}``: every key in ``after`` is past the cut."""
    junk = '{"p": "xxxxxxxxxxxx", "q": ' * 12_000 + '"leaf"' + "}" * 12_000
    return '{"a": ' + junk + "".join(f", {json.dumps(k)}: {json.dumps(v)}" for k, v in after.items()) + "}"


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app answering an error with its status, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


async def test_a_value_that_only_says_what_the_marker_says_is_taken_in(client: AsyncClient) -> None:
    job = await client.post(
        "/api/v1/jobs", json={"repo": "acme/widgets", "task": "x", "pipeline_config": {"note": TOO_LONG_TO_READ}}
    )
    assert job.status_code == 201, f"a job whose config says what the marker says: {job.status_code} {job.text[:200]}"
    issue = await client.post(
        "/api/v1/issues", json={"title": "t", "repo": "acme/widgets", "labels": [TOO_LONG_TO_READ]}
    )
    assert issue.status_code in (200, 201), f"an issue labelled with the marker's text: {issue.status_code}"


async def test_a_settings_document_read_in_part_is_refused_not_read_as_defaults(client: AsyncClient) -> None:
    from embry0.settings.sections.budget import BUDGET as BUDGET_SECTION
    from embry0.settings.store import build_snapshot

    app = client.app  # type: ignore[attr-defined]
    db, settings = app.state.db, app.state.settings
    await db.execute(
        "UPDATE settings_documents SET data = $1::text::jsonb, generation = generation + 1",
        json.dumps({"budget": BUDGET}),
    )
    assert (await settings.snapshot()).get(BUDGET_SECTION).daily_cap_usd == 3.0
    await db.execute(
        "UPDATE settings_documents SET data = $1::text::jsonb, generation = generation + 1", _cut({"budget": BUDGET})
    )

    rows = await settings._documents.all()
    deployment = next(row for row in rows if row["scope"] == "deployment")
    assert holds_part_read(deployment["data"]), "the planted document was read whole: nothing to refuse"
    with pytest.raises(PartReadError, match="the deployment's settings document is stored nested too deep"):
        build_snapshot(settings.catalog, rows)
    budget = (await settings.snapshot()).get(BUDGET_SECTION)
    assert (budget.max_budget_per_job_usd, budget.daily_cap_usd) == (1.5, 3.0), (
        f"a document read in part is read as its defaults: {budget}"
    )


async def test_a_settings_document_read_in_part_is_replaced_by_a_whole_put(client: AsyncClient) -> None:
    from embry0.settings.sections.budget import BUDGET as BUDGET_SECTION

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    await db.execute(
        "UPDATE settings_documents SET data = $1::text::jsonb, generation = generation + 1", _cut({"budget": BUDGET})
    )
    generation = (await client.get("/api/v1/config/settings")).json()["generation"]

    patched = await client.patch(
        "/api/v1/config/settings/budget", json={"daily_cap_usd": 4.0}, headers={"If-Match": f'"{generation}"'}
    )
    assert patched.status_code == 409, f"PATCH on a document read in part: {patched.status_code} {patched.text[:200]}"
    assert "replace the whole document" in patched.json()["detail"], patched.text

    put = await client.put(
        "/api/v1/config/settings",
        content=b"budget:\n  daily_cap_usd: 4.0\n",
        headers={"Content-Type": "application/yaml", "If-Match": f'"{generation}"'},
    )
    assert put.status_code == 200, (
        f"a whole document does not replace one read in part: {put.status_code} {put.text[:300]}"
    )
    assert len(await db.fetchval("SELECT data::text FROM settings_documents WHERE scope = 'deployment'")) < 100
    assert (await app.state.settings.snapshot()).get(BUDGET_SECTION).daily_cap_usd == 4.0


async def test_a_jobs_config_read_in_part_is_refused_where_a_run_reads_it(client: AsyncClient) -> None:
    from embry0.agents.resolver import resolve_agent_config
    from embry0.services.run_with import job_pins_reader

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    agent = await app.state.agent_defs_repo.get("developer")
    job = await app.state.jobs_repo.create(repo="acme/widgets", task="x")
    config = {"agent_tools": {"developer": ["Read"]}, "agent_models": {"developer": "m-cheap"}}
    await db.execute("UPDATE jobs SET pipeline_config = $1::text::jsonb WHERE job_id = $2", _cut(config), job)
    stored = (await app.state.jobs_repo.get(job))["pipeline_config"]
    assert holds_part_read(stored) and "agent_tools" not in stored

    with pytest.raises(PartReadError, match="pipeline_config is stored nested too deep"):
        resolve_agent_config("developer", agent, None, stored)
    with pytest.raises(PartReadError, match=f"job {job}'s pipeline_config"):
        await job_pins_reader(app.state.jobs_repo)(job)
    with pytest.raises(asyncpg.exceptions.DataError, match="read only in part is not written back"):
        await app.state.jobs_repo.create(repo="acme/widgets", task="copy", pipeline_config=stored)
    one = await client.get(f"/api/v1/jobs/{job}")
    listed = await client.get("/api/v1/jobs")
    assert (one.status_code, listed.status_code) == (200, 200), "the API no longer serves what was read"


async def test_an_agent_definition_read_in_part_is_refused(client: AsyncClient) -> None:
    from embry0.agents.resolver import resolve_agent_config

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    await db.execute(
        "UPDATE agent_definitions SET mcp_servers = $1::text::jsonb WHERE type = 'developer'",
        _cut({"docs": {"command": "x"}}),
    )
    agent = await app.state.agent_defs_repo.get("developer")
    assert holds_part_read(agent["mcp_servers"])
    with pytest.raises(PartReadError, match="the developer agent's definition.mcp_servers is stored"):
        resolve_agent_config("developer", agent)


async def test_a_profile_read_in_part_is_refused_by_the_sandbox_gate(client: AsyncClient) -> None:
    from embry0.execution.profile_isolation import profile_isolation_problems

    app = client.app  # type: ignore[attr-defined]
    db = app.state.db
    await db.execute("INSERT INTO sandbox_profiles (name) VALUES ('cut') ON CONFLICT DO NOTHING")
    junk = '{"p": "xxxxxxxxxxxx", "q": ' * 12_000 + '"leaf"' + "}" * 12_000
    await db.execute(
        "UPDATE sandbox_profiles SET cap_drop = $1::text::jsonb WHERE name = 'cut'", "[" + junk + ', "ALL"]'
    )
    profile = await app.state.profiles_repo.get("cut")
    problems = profile_isolation_problems(profile, "")
    assert any(p.startswith("cap_drop: stored nested too deep") for p in problems), problems
