"""A read of a row holding a number past a ``float``'s range answers, with the number as its text (#537).

The JSONB codec read such a number as ``inf``, which JSON cannot write: a route
with no response model answered 500, and one with a response model served it
as ``null``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

PAST = "1" + "0" * 400 + ".5"  # what Postgres writes for (1e400 + 0.5)::numeric
PLANTED = "jsonb_build_object('n', (1e400 + 0.5)::numeric)"


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app answering an error with its status, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)  # type: ignore[attr-defined]
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


@pytest.mark.parametrize(
    ("table", "column", "path"),
    [
        ("settings_documents", "data", "/api/v1/config/settings"),
        ("progression_packs", "document", "/api/v1/progression/pack"),
        ("audit_log", "details", "/api/v1/audit"),
    ],
)
async def test_a_read_serves_the_number_as_its_text(client: AsyncClient, table: str, column: str, path: str) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    if table == "progression_packs":
        await db.execute(
            "INSERT INTO progression_packs (status, schema_version, source, follows_default, text, document) "
            "VALUES ('active', 1, 'person', false, 'x', '{}')"
        )
    if table == "audit_log":
        await db.execute("INSERT INTO audit_log (action, actor, details) VALUES ('job.created', 'someone', '{}')")
    assert (await client.get(path)).status_code == 200, f"{path} answers before anything is planted"
    await db.execute(f'UPDATE {table} SET "{column}" = "{column}" || {PLANTED}')
    assert await db.fetchval(f"SELECT \"{column}\"->>'n' FROM {table} LIMIT 1") == PAST, "Postgres keeps it whole"

    answer = await client.get(path)
    assert answer.status_code == 200, f"{table}.{column}: {path} answered {answer.status_code}"
    assert f'"n":"{PAST}"' in answer.text.replace(" ", ""), f"{path} does not serve the number as its text"


async def test_a_job_holding_one_is_read_and_listed(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    for job_id in ("job-past", "job-other"):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, status) VALUES ($1, 't', 'acme/widgets', 'completed')", job_id
        )
    await db.execute(f"UPDATE jobs SET pipeline_config = {PLANTED} WHERE job_id = 'job-past'")

    one = await client.get("/api/v1/jobs/job-past")
    assert one.status_code == 200, f"GET /jobs/job-past answered {one.status_code}"
    assert one.json()["pipeline_config"] == {"n": PAST}, f"served as {str(one.json()['pipeline_config'])[:40]}"
    listed = await client.get("/api/v1/jobs")
    assert listed.status_code == 200, f"GET /jobs answered {listed.status_code}"
    configs = {job["job_id"]: job.get("pipeline_config") for job in listed.json()["jobs"]}
    assert configs.get("job-past") == {"n": PAST} and "job-other" in configs, "the list loses or changes the job"


async def test_a_negative_one_is_read_as_its_text(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    value = await db.fetchval("SELECT jsonb_build_object('n', -(1e400 + 0.5)::numeric)")
    assert value == {"n": f"-{PAST}"}, f"read as {str(value)[:40]}"


async def test_written_back_it_keeps_its_digits(client: AsyncClient) -> None:
    """A value read with such a number and written back stores it as a JSON string with the same digits, as an
    integer past ``int``'s digit limit is (#523): the codec cannot write the number back as a number."""
    db = client.app.state.db  # type: ignore[attr-defined]
    await db.execute(
        "INSERT INTO jobs (job_id, task, repo, status) VALUES ('job-past', 't', 'acme/widgets', 'completed')"
    )
    await db.execute(
        f"UPDATE jobs SET pipeline_config = {PLANTED} || '{{\"m\": 2.5}}'::jsonb WHERE job_id = 'job-past'"
    )
    read = await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = 'job-past'")

    await db.execute("UPDATE jobs SET pipeline_config = $1 WHERE job_id = 'job-past'", read)
    stored = await db.fetchrow(
        "SELECT pipeline_config->>'n' AS n, jsonb_typeof(pipeline_config->'n') AS kind, "
        "jsonb_typeof(pipeline_config->'m') AS m FROM jobs WHERE job_id = 'job-past'"
    )
    assert (stored["n"], stored["kind"], stored["m"]) == (PAST, "string", "number"), dict(stored)
    assert await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = 'job-past'") == read


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT to_jsonb('NaN'::numeric)",
        "SELECT to_jsonb('Infinity'::numeric)",
        "SELECT to_jsonb('-Infinity'::numeric)",
        "SELECT to_jsonb('NaN'::float8)",
        "SELECT to_jsonb('Infinity'::float8)",
        "SELECT jsonb_build_object('n', 'NaN'::numeric) -> 'n'",
    ],
)
async def test_postgres_stores_no_nan_or_infinity_as_a_number(client: AsyncClient, sql: str) -> None:
    """Neither reaches the codec as a number: Postgres writes each as a JSON string, so it reads as one."""
    db = client.app.state.db  # type: ignore[attr-defined]
    value = await db.fetchval(sql)
    assert isinstance(value, str) and value.lstrip("-") in ("NaN", "Infinity"), f"{sql} read as {value!r}"


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
async def test_postgres_refuses_nan_or_infinity_in_json_text(client: AsyncClient, token: str) -> None:
    import asyncpg

    db = client.app.state.db  # type: ignore[attr-defined]
    with pytest.raises(asyncpg.exceptions.InvalidTextRepresentationError):
        await db.fetchval("SELECT $1::text::jsonb", f'{{"n": {token}}}')
