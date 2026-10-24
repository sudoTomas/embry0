"""BudgetSpendRepository — the daily/monthly rollup the dispatch gate reads."""

from datetime import UTC, datetime, timedelta

import pytest

from embry0.storage.repositories.budget_spend import BudgetSpendRepository


class FakeDB:
    def __init__(self, row=None, value=None):
        self._row = row
        self._value = value
        self.queries: list[tuple] = []

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        return self._row

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return self._value


@pytest.mark.asyncio
async def test_rollup_returns_floats():
    db = FakeDB(row={"daily": 12, "monthly": 340})
    assert await BudgetSpendRepository(db).spend_rollup() == {"daily": 12.0, "monthly": 340.0}


@pytest.mark.asyncio
async def test_rollup_on_an_empty_fleet():
    assert await BudgetSpendRepository(FakeDB(row=None)).spend_rollup() == {"daily": 0.0, "monthly": 0.0}


@pytest.mark.asyncio
async def test_rollup_counts_in_flight_spend_from_traces():
    """jobs.total_cost_usd is only written at terminal status, so the query
    takes the greater of the job total and its trace rollup."""
    db = FakeDB(row={"daily": 0, "monthly": 0})
    await BudgetSpendRepository(db).spend_rollup()
    sql = db.queries[0][0]
    assert "GREATEST(j.total_cost_usd" in sql
    assert "FROM traces" in sql
    assert "date_trunc('day', NOW())" in sql
    assert "date_trunc('month', NOW())" in sql


@pytest.mark.asyncio
async def test_dispatch_count_passes_actor_and_window():
    since = datetime(2026, 9, 17, 11, 0, tzinfo=UTC)
    db = FakeDB(value=3)
    assert await BudgetSpendRepository(db).dispatch_count_since("alex", since) == 3
    assert db.queries[0][1] == ("alex", since, None)


@pytest.mark.asyncio
async def test_dispatch_count_of_none_is_zero():
    assert await BudgetSpendRepository(FakeDB(value=None)).dispatch_count_since("t", datetime.now(UTC)) == 0


@pytest.mark.asyncio
async def test_dispatch_count_buckets_on_the_job_dispatcher_not_the_issue():
    """issues.created_by is a provenance constant ("webhook-label", "triage"),
    so bucketing on it made the hourly limit one fleet-wide throttle (#246)."""
    db = FakeDB(value=0)
    await BudgetSpendRepository(db).dispatch_count_since("alex", datetime.now(UTC))
    sql = db.queries[0][0]
    assert "dispatched_by = $1" in sql
    assert "created_by" not in sql
    assert "JOIN issues" not in sql


@pytest.mark.asyncio
async def test_dispatch_count_excludes_jobs_the_gate_already_refused():
    """Counting refusals lets one lockout extend itself for another hour."""
    db = FakeDB(value=0)
    await BudgetSpendRepository(db).dispatch_count_since("alex", datetime.now(UTC))
    assert "ERR_BUDGET_REFUSED" in db.queries[0][0]


# ---------------------------------------------------------------------------
# The queries themselves, against the migrated schema (#246).
#
# String assertions above cannot catch what actually breaks these: a column
# renamed by a later migration, a typo in a FILTER clause, a join that silently
# multiplies rows. The gate fails loud on those now, which means a broken query
# fails every dispatch — so it has to be executed here, not just inspected.
# ---------------------------------------------------------------------------


async def _seed_job(db, job_id, *, started_at_sql="NOW()", cost=0.0, dispatched_by=None, error_code=None):
    await db.execute(
        f"""
        INSERT INTO jobs (job_id, repo, task, status, total_cost_usd, started_at, dispatched_by, error_code)
        VALUES ($1, 'o/r', 't', 'completed', $2, {started_at_sql}, $3, $4)
        """,
        job_id,
        cost,
        dispatched_by,
        error_code,
    )


async def _seed_trace(db, trace_id, job_id, cost):
    await db.execute(
        """
        INSERT INTO traces (trace_id, job_id, agent_type, model, cost_usd, result)
        VALUES ($1, $2, 'developer', 'm', $3, 'ok')
        """,
        trace_id,
        job_id,
        cost,
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_rollup_sql_against_the_migrated_schema(db_with_migrations):
    db = db_with_migrations
    repo = BudgetSpendRepository(db)

    # Today: a finished job, an in-flight job whose cost exists only as traces,
    # and one carrying both (GREATEST must pick the larger, never the sum).
    await _seed_job(db, "job-today-1", cost=5.0)
    await _seed_job(db, "job-today-2", cost=0.0)
    await _seed_trace(db, "trc-1", "job-today-2", 3.25)
    await _seed_job(db, "job-today-3", cost=2.0)
    await _seed_trace(db, "trc-2", "job-today-3", 0.5)

    # Before today, but inside this month — unless today is the 1st.
    await _seed_job(db, "job-earlier", started_at_sql="date_trunc('day', NOW()) - INTERVAL '1 hour'", cost=7.0)
    # Last month: outside both windows.
    await _seed_job(db, "job-old", started_at_sql="date_trunc('month', NOW()) - INTERVAL '1 day'", cost=100.0)

    rollup = await repo.spend_rollup()

    assert rollup["daily"] == pytest.approx(10.25)  # 5.00 + 3.25 + 2.00, not 2.50
    on_the_first = await db.fetchval("SELECT date_trunc('day', NOW()) = date_trunc('month', NOW())")
    assert rollup["monthly"] == pytest.approx(10.25 if on_the_first else 17.25)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_rollup_dates_an_unstarted_job_by_created_at(db_with_migrations):
    """COALESCE(started_at, created_at): a pending job has no started_at, and
    must still be attributed to the day it was created."""
    db = db_with_migrations
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, total_cost_usd) VALUES ('job-p', 'o/r', 't', 'pending', 4.0)"
    )
    assert (await BudgetSpendRepository(db).spend_rollup())["daily"] == pytest.approx(4.0)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_actor_count_sql_against_the_migrated_schema(db_with_migrations):
    db = db_with_migrations
    repo = BudgetSpendRepository(db)

    await _seed_job(db, "job-a", dispatched_by="alex")
    await _seed_job(db, "job-b", dispatched_by="alex")
    # Already refused by this very gate — counting it would let the lockout
    # extend itself for another hour.
    await _seed_job(db, "job-c", dispatched_by="alex", error_code="ERR_BUDGET_REFUSED")
    # A different actor, and an unattributable job: neither shares the bucket.
    await _seed_job(db, "job-d", dispatched_by="octocat")
    await _seed_job(db, "job-e", dispatched_by=None)

    since = datetime.now(UTC) - timedelta(hours=1)
    assert await repo.dispatch_count_since("alex", since) == 2
    assert await repo.dispatch_count_since("octocat", since) == 1
    assert await repo.dispatch_count_since("nobody", since) == 0
    # #116: the runner's own job is not one of the actor's other jobs.
    assert await repo.dispatch_count_since("alex", since, excluding="job-b") == 1
    assert await repo.dispatch_count_since("alex", since, excluding="job-missing") == 2


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_actor_count_sql_respects_the_window(db_with_migrations):
    db = db_with_migrations
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, dispatched_by, created_at)
        VALUES ('job-old', 'o/r', 't', 'completed', 'alex', NOW() - INTERVAL '2 hours')
        """
    )
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await BudgetSpendRepository(db).dispatch_count_since("alex", since) == 0
