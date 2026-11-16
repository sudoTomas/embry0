"""A read of a row holding an integer longer than ``int`` reads answers, and the list keeps it (#523).

Postgres stores a JSONB number in full and hands it back as its digits; Python
refuses more than 4,300 of them, and the JSONB codec's ``json.loads`` raised for
the whole value, so one such job made ``GET /jobs`` answer 500.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

TOO_LONG = "1" + "0" * sys.get_int_max_str_digits()  # 1e4300


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The test app answering an error with its status, as a server does."""
    transport = ASGITransport(app=api_client.app, raise_app_exceptions=False)  # type: ignore[attr-defined]
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as c:
        c.app = api_client.app  # type: ignore[attr-defined]
        yield c


async def test_a_job_holding_such_an_integer_is_read_and_listed(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    for job_id in ("job-long", "job-other"):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, status) VALUES ($1, 't', 'acme/widgets', 'completed')", job_id
        )
    # Sent as text and cast by Postgres: the codec's json.dumps cannot write such an int either.
    await db.execute(
        "UPDATE jobs SET pipeline_config = $1::text::jsonb WHERE job_id = 'job-long'", f'{{"n": {TOO_LONG}, "m": 1}}'
    )
    assert len(await db.fetchval("SELECT pipeline_config->>'n' FROM jobs WHERE job_id = 'job-long'")) == len(TOO_LONG)

    one = await client.get("/api/v1/jobs/job-long")
    assert one.status_code == 200, f"GET /jobs/job-long answered {one.status_code}"
    assert one.json()["pipeline_config"] == {"n": TOO_LONG, "m": 1}, "the integer is not served as its digits"
    listed = await client.get("/api/v1/jobs")
    assert listed.status_code == 200, f"one such job takes GET /jobs down: {listed.status_code}"
    assert {"job-long", "job-other"} <= {job["job_id"] for job in listed.json()["jobs"]}


async def test_a_route_with_no_response_model_serves_it_too(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    before = await client.get("/api/v1/config/settings")
    assert before.status_code == 200
    await db.execute("UPDATE settings_documents SET data = $1::text::jsonb", f'{{"n": {TOO_LONG}}}')
    answer = await client.get("/api/v1/config/settings")
    assert answer.status_code == 200, f"GET /config/settings answered {answer.status_code}"
    assert TOO_LONG in answer.text
