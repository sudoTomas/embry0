"""The MCP job tools read a job only within the caller's repository allowlist, and a dispatch bot's flow works.

The MCP server reaches embry0 through its REST routes with the caller's own
key (``embry0.mcp_server.client``), so its tools hold to the routes' rule:
here each tool runs against the real app over a real database
(:mod:`tests.fixtures.job_reads`). Through a key whose list covers one
repository, a tool reading the other repository's job gives exactly what it
gives for a job that does not exist, and the key's own jobs read as before.
A dispatch bot, an operate key with a list, creates jobs on its repository,
by ``repo`` and by a git ``context``, waits on them and reads their events,
deliverables and list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.api.deps import bust_api_key_cache
from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.job_reads import JOB, MARK, MISSING, audit_job, job_reads_app
from tests.fixtures.repository_writes import COVERED, UNCOVERED

pytestmark = pytest.mark.requires_postgres


def _use(monkeypatch: pytest.MonkeyPatch, app: FastAPI, key: str) -> Embry0Client:
    """The MCP server's client, calling ``app`` with ``key``."""
    client = Embry0Client("http://test", api_key=key, transport=ASGITransport(app=app, raise_app_exceptions=False))
    monkeypatch.setattr(server, "_client", client)
    return client


async def _outcome(call: Awaitable[Any]) -> Any:
    try:
        return "ok", await call
    except Embry0APIError as exc:
        return "error", exc.status_code, exc.detail


#: Each MCP tool that reads one job, called on a job id.
TOOLS: dict[str, Callable[[str], Awaitable[Any]]] = {
    "job_get": lambda job: server.job_get(job),
    "job_wait": lambda job: server.job_wait(job, timeout=0.1, poll_interval=0.05),
    "job_logs": lambda job: server.job_logs(job, tail=0),
    "job_inputs": lambda job: server.job_inputs(job),
    "job_deliverables": lambda job: server.job_deliverables(job),
}


@pytest.mark.parametrize("tool", list(TOOLS))
async def test_a_job_tool_gives_another_repository_s_job_as_a_job_that_does_not_exist(
    tool: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = _use(monkeypatch, app, keys.limited)
        uncovered = await _outcome(TOOLS[tool](JOB[UNCOVERED]))
        missing = await _outcome(TOOLS[tool](MISSING))
        own = await _outcome(TOOLS[tool](JOB[COVERED]))
        await limited.aclose()
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await _outcome(TOOLS[tool](JOB[UNCOVERED]))
        await everyone_client.aclose()
    assert repr(uncovered).replace(JOB[UNCOVERED], "<job>") == repr(missing).replace(MISSING, "<job>"), (
        f"{tool}: the limited key gets {UNCOVERED}'s job as a missing one: {uncovered} against {missing}"
    )
    assert JOB[UNCOVERED] in repr(everyone) or MARK[UNCOVERED] in repr(everyone), f"{tool}: {everyone}"
    assert JOB[COVERED] in repr(own) or MARK[COVERED] in repr(own), f"{tool}: the key reads its own job: {own}"


async def test_job_list_gives_a_key_its_own_repository_s_jobs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        client = _use(monkeypatch, app, keys.limited)
        every = await server.job_list()
        other = await server.job_list(repo=UNCOVERED)
        nobody = await server.job_list(repo="nobody/nobody")
        await client.aclose()
    assert {j["job_id"] for j in every["jobs"]} == {JOB[COVERED], audit_job(COVERED)} and every["total"] == 2, (
        f"job_list gives the limited key its own repository's jobs and counts them alone: {every}"
    )
    assert other == nobody and other["total"] == 0, f"another repository's list is a repository's with none: {other}"


async def test_the_dispatch_bot_starts_waits_on_and_reads_its_own_jobs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, _keys, _values):
        _row, bot = await ApiKeysRepository(db).create(
            name="dispatch-bot", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        submitted = MagicMock()
        monkeypatch.setattr(app.state.issue_executor, "submit_job", submitted)
        client = _use(monkeypatch, app, bot)
        started = [
            (await server.job_create(task="Fix the header", repo=COVERED))["job_id"],
            (await server.job_create(task="Fix the footer", context={"type": "git", "repo": COVERED}))["job_id"],
        ]
        for job in started:
            await app.state.jobs_repo.append_log_event(job, {"type": "progress", "message": f"working on {job}"})
            await db.execute(
                "INSERT INTO deliverables (id, job_id, type, title, content) VALUES ($1, $2, 'report', 'Report', $3)",
                f"rpt-{job}",
                job,
                f"done {job}",
            )
            await db.execute("UPDATE jobs SET status = 'completed', finished_at = now() WHERE job_id = $1", job)
        waited = [await server.job_wait(job, timeout=5, poll_interval=0.01) for job in started]
        events = [await server.job_logs(job, tail=0) for job in started]
        delivered = [await server.job_deliverables(job) for job in started]
        listed = await server.job_list()
        await client.aclose()
    assert submitted.call_count == 2, "both jobs were dispatched"
    assert [(w["job_id"], w["status"], w.get("wait_timed_out")) for w in waited] == [
        (job, "completed", None) for job in started
    ], waited
    for job, log, rows in zip(started, events, delivered, strict=True):
        assert f"working on {job}" in str(log["events"]), f"the bot reads its job's events: {log}"
        assert [r["content"] for r in rows] == [f"done {job}"], f"the bot reads its job's deliverables: {rows}"
    assert {j["job_id"] for j in listed["jobs"]} == {*started, JOB[COVERED], audit_job(COVERED)}, (
        f"the bot lists its repository's jobs, those it started included, and no other: {listed}"
    )
