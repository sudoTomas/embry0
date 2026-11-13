"""Reads of a value stored past the interpreter's recursion limit answer, never 500 (#479).

#449 serves a stored value too deep for the response serializer as its JSON
text. A few reads failed before that point, with ``RecursionError``: a route
with no response model is serialized by FastAPI's ``jsonable_encoder``, which
recurses once per level (about 980 levels), and the JSONB codec's ``json.loads``
recursed too (about 10,000 levels; Postgres stores up to about 20,000).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _deep_text(levels: int) -> str:
    return '{"x": ' + '{"a": ' * levels + '"leaf"' + "}" * (levels + 1)


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app answering an error with its status, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)  # type: ignore[attr-defined]
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


async def _plant(db: Any, table: str, column: str, levels: int) -> None:
    # Sent as text and cast by Postgres: the codec's own json.dumps would recurse as deep.
    await db.execute(f'UPDATE {table} SET "{column}" = $1::text::jsonb', _deep_text(levels))


@pytest.mark.parametrize(
    ("table", "column", "path"),
    [
        ("settings_documents", "data", "/api/v1/config/settings"),
        ("progression_packs", "document", "/api/v1/progression/pack"),
        ("audit_log", "details", "/api/v1/audit"),
    ],
)
async def test_a_route_with_no_response_model_answers_a_value_2000_levels_deep(
    client: AsyncClient, table: str, column: str, path: str
) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    if table == "progression_packs":
        await db.execute(
            "INSERT INTO progression_packs (status, schema_version, source, follows_default, text, document) "
            "VALUES ('active', 1, 'person', false, 'x', '{}')"
        )
    if table == "audit_log":
        await db.execute("INSERT INTO audit_log (action, actor, details) VALUES ('job.created', 'someone', '{}')")
    assert (await client.get(path)).status_code == 200, f"{path} answers before anything is planted"
    await _plant(db, table, column, 2_000)
    answer = await client.get(path)
    assert answer.status_code == 200, f"{table}.{column} 2,000 deep: {path} answered {answer.status_code}"


@pytest.mark.parametrize("levels", [2_000, 12_000])
async def test_a_job_whose_pipeline_config_is_deeper_than_json_loads_goes_is_read(
    client: AsyncClient, levels: int
) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    await db.execute(
        "INSERT INTO jobs (job_id, task, repo, status) VALUES ('job-deep', 't', 'acme/widgets', 'completed')"
    )
    await _plant(db, "jobs", "pipeline_config", levels)
    for path in ("/api/v1/jobs/job-deep", "/api/v1/jobs"):
        answer = await client.get(path)
        assert answer.status_code == 200, f"pipeline_config {levels} deep: {path} answered {answer.status_code}"
    job = (await client.get("/api/v1/jobs/job-deep")).json()
    assert isinstance(job["pipeline_config"], str), "a value too deep to serve is served as text"
