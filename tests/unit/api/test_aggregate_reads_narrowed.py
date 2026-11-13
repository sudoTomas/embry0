"""The totals a key with a repository allowlist reads, beyond what the walk compares, on a real database.

:mod:`tests.unit.api.test_aggregate_reads_inventory` shows that no total moves
for such a key when another repository is given work, and that each reads to
it as a deployment of its repositories alone does. These tests hold what that
walk does not:

- ``GET /stats`` counts the credentials the key reads, as ``GET /credentials``
  lists them, and names none of the environment's keys, which a key without
  a list still reads;
- ``GET /stats`` lists no issue of another repository among the recent and
  the costliest, though the key's own job worked on it before it moved;
- ``GET /traces?job_id=`` another repository's job answers byte for byte as a
  job that does not exist, and the list, paged one run at a time, holds
  exactly the key's runs with the same ``total`` on every page;
- the MCP ``stats`` tool reads the route with the caller's key, so it counts
  the key's repositories alone;
- a key without a list and the master key read every repository's totals.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0Client
from embry0.storage.database import DatabasePool
from tests.fixtures.aggregate_reads import NO_REPOSITORY, aggregate_reads_app, issue_id, job_id
from tests.fixtures.issue_reads import same_answer
from tests.fixtures.repository_writes import COVERED, MASTER, UNCOVERED, call
from tests.unit.api.test_aggregate_reads_inventory import TOTALS, answer, differing, urls

pytestmark = pytest.mark.requires_postgres

#: A job id that names no job, as long as the uncovered repository's merged job's.
MISSING = "job-sum-zzzzz-merged"
assert len(MISSING) == len(job_id(UNCOVERED, "merged")), "the two answers compare byte for byte"


async def _json(app: Any, url: str, key: str) -> Any:
    response = await call(app, "GET", url, key)
    assert response.status_code == 200, (url, response.status_code, response.text[:300])
    return response.json()


async def test_the_stats_count_the_credentials_a_key_reads_and_no_environment_key(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        await deployment.add_uncovered()
        app, keys = deployment.app, deployment.keys
        monkeypatch.setattr(app.state.credential_resolver, "env_fallback_keys", lambda: ["GITHUB_TOKEN__GLOBEX"])
        listed = await _json(app, "/api/v1/credentials", keys.limited)
        limited = (await _json(app, "/api/v1/stats", keys.limited))["credentials"]
        everyone = (await _json(app, "/api/v1/stats", keys.open))["credentials"]
        registry = await _json(app, "/api/v1/credentials", keys.open)
    assert listed and len(listed) < len(registry), f"the key reads its repository's credentials alone: {listed}"
    assert limited == {"count": len(listed), "quarantined": 0, "disabled": 0, "env_fallback_keys": []}, (
        f"the limited key counts the credentials it reads, {len(listed)}, and no environment key: {limited}"
    )
    assert everyone == {
        "count": len(registry),
        "quarantined": 1,
        # Each owner's model account, which tests.fixtures.repository_reads binds disabled.
        "disabled": 2,
        "env_fallback_keys": ["GITHUB_TOKEN__GLOBEX"],
    }, f"a key without an allowlist counts the registry and the environment's keys: {everyone}"


async def test_the_stats_list_no_issue_of_another_repository_that_the_key_s_job_worked_on(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An issue that moved to another repository after the key's job ran on it: the job is the key's and is counted,
    the issue is not the key's and is not listed, however costly the job."""
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        await deployment.add_uncovered()
        await db_with_migrations.execute(
            "INSERT INTO jobs (job_id, repo, task, status, issue_id, created_at, started_at, total_cost_usd) "
            "VALUES ('job-sum-widget-on-gizmo', $1, 'on the moved issue', 'completed', $2, NOW(), NOW(), 500.0)",
            COVERED,
            issue_id(UNCOVERED),
        )
        app, keys = deployment.app, deployment.keys
        limited = await _json(app, "/api/v1/stats", keys.limited)
        everyone = await _json(app, "/api/v1/stats", keys.open)

    def issues(stats: dict[str, Any], key: str) -> set[str]:
        return {row["trace_id"] for row in stats[key]}

    moved = issue_id(UNCOVERED)
    assert moved in issues(everyone, "top_expensive_issues") and moved in issues(everyone, "recent_issues"), (
        f"a key without an allowlist lists the moved issue: {everyone['top_expensive_issues']}"
    )
    assert {r["repo"] for r in limited["top_expensive_issues"] + limited["recent_issues"]} == {COVERED}, (
        f"the limited key lists only its repository's issues: {limited['top_expensive_issues']} "
        f"{limited['recent_issues']}"
    )
    assert limited["total_cost_usd"] >= 500.0, "the key's own job on the moved issue is counted"


async def test_a_trace_list_holds_the_key_s_runs_on_every_page_and_another_s_job_has_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        await deployment.add_uncovered()
        app, keys = deployment.app, deployment.keys
        everyone = await _json(app, "/api/v1/traces?limit=200", keys.open)
        covered_jobs = {
            str(r["job_id"])
            for r in await db_with_migrations.fetch(
                "SELECT job_id FROM jobs WHERE lower(COALESCE(repo, context->>'repo')) = $1", COVERED
            )
        }
        pages = [await _json(app, f"/api/v1/traces?limit=1&offset={n}", keys.limited) for n in range(14)]
        other = await call(app, "GET", f"/api/v1/traces?job_id={job_id(UNCOVERED, 'merged')}", keys.limited)
        missing = await call(app, "GET", f"/api/v1/traces?job_id={MISSING}", keys.limited)
        reached = await call(app, "GET", f"/api/v1/traces?job_id={job_id(UNCOVERED, 'merged')}", keys.open)
    own = [t["trace_id"] for t in everyone["traces"] if t["job_id"] in covered_jobs]
    assert own and len(own) < everyone["total"], f"both repositories' runs are stored: {everyone['total']}"
    walked = [t["trace_id"] for page in pages for t in page["traces"]]
    assert walked == own, f"the limited key's pages hold its own runs, each once, in order: {walked} != {own}"
    assert {page["total"] for page in pages} == {len(own)}, (
        f"every page counts the key's runs alone: {[page['total'] for page in pages]}"
    )
    names = {job_id(UNCOVERED, "merged"): "<job>"}
    assert same_answer(other, names, missing, {MISSING: "<job>"}) is None, (
        f"another repository's job reads as one with no runs, as a missing job does: {other.text} {missing.text}"
    )
    assert reached.json()["total"] > 0, f"a key without an allowlist reads that job's runs: {reached.text[:200]}"


async def test_the_mcp_stats_tool_counts_the_key_s_repositories_alone(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        app, keys = deployment.app, deployment.keys
        before = await _json(app, "/api/v1/stats", keys.open)
        await deployment.add_uncovered()
        client = Embry0Client("http://test", api_key=keys.limited, transport=ASGITransport(app=app))
        monkeypatch.setattr(server, "_client", client)
        limited = await server.stats()
        await client.aclose()
        client = Embry0Client("http://test", api_key=keys.open, transport=ASGITransport(app=app))
        monkeypatch.setattr(server, "_client", client)
        everyone = await server.stats()
        await client.aclose()
    keep = ("total_jobs", "total_issues", "total_cost_usd", "cost_by_repo", "tokens_by_agent", "recent_issues")
    assert {k: limited[k] for k in keep} == {k: before[k] for k in keep}, (
        f"the tool reads the limited key's totals as a deployment of {COVERED} alone: "
        f"{ {k: limited[k] for k in keep[:3]} } != { {k: before[k] for k in keep[:3]} }"
    )
    assert everyone["total_jobs"] > before["total_jobs"], "a key without an allowlist counts every repository's jobs"
    assert {r["repo"] for r in everyone["cost_by_repo"]} == {COVERED, UNCOVERED}, everyone["cost_by_repo"]


async def test_a_key_without_an_allowlist_and_the_master_key_read_every_repository_s_totals(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        await deployment.add_uncovered()
        app, keys = deployment.app, deployment.keys
        differ = {}
        for path in TOTALS:
            for url in urls(path, deployment.values):
                everyone = answer(await call(app, "GET", url, keys.open))
                master = answer(await call(app, "GET", url, MASTER))
                if moved := differing(everyone, master, ("body.computed_at", "body.generated_at", "body.rollup")):
                    differ[url] = sorted(moved)
        stats = await _json(app, "/api/v1/stats", MASTER)
        traces = await _json(app, f"/api/v1/traces?job_id={NO_REPOSITORY}", MASTER)
    assert differ == {}, f"the master key reads each total as a key without an allowlist does: {differ}"
    assert {r["repo"] for r in stats["cost_by_repo"]} == {COVERED, UNCOVERED}, stats["cost_by_repo"]
    assert traces["total"] == 1, f"the master key reads the runs of a job of no repository: {traces}"


@pytest.mark.parametrize("query", ["limit=-1", "limit=0", "limit=201", "limit=100000", "offset=-1"])
async def test_a_trace_page_out_of_bounds_is_refused(
    query: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page holds 1 to 200 runs from offset 0 on: anything else is the caller's mistake (422), never a server error
    nor every run at once."""
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        app, keys = deployment.app, deployment.keys
        answers = {
            who: (await call(app, "GET", f"/api/v1/traces?{query}", key)).status_code
            for who, key in (("limited", keys.limited), ("open", keys.open))
        }
        widest = await call(app, "GET", "/api/v1/traces?limit=200", keys.open)
    assert answers == {"limited": 422, "open": 422}, f"?{query} is refused for every caller: {answers}"
    assert widest.status_code == 200, widest.text[:200]
