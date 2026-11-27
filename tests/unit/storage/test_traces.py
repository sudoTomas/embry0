from typing import Any

import pytest
import structlog.testing

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.traces import TracesRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repos(db_with_migrations: DatabasePool) -> tuple[JobsRepository, TracesRepository]:
    return JobsRepository(db_with_migrations), TracesRepository(db_with_migrations)


@pytest.mark.asyncio
async def test_create_and_get_trace(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")

    trace_id = await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="pass",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
        result_summary="Fixed the auth bug",
    )
    assert trace_id is not None
    assert trace_id.startswith("trc-"), f"Expected trc- prefix, got {trace_id!r}"

    trace = await traces_repo.get(trace_id)
    assert trace is not None
    assert trace["agent_type"] == "developer"
    assert trace["cost_usd"] == 0.42
    assert trace["result"] == "pass"


@pytest.mark.asyncio
async def test_list_traces_by_job(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")

    await traces_repo.create(job_id=job_id, agent_type="developer", model="sonnet", result="pass")
    await traces_repo.create(job_id=job_id, agent_type="validator", model="haiku", result="pass")

    rows, total = await traces_repo.list_all(job_id=job_id)
    assert total == 2
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_sum_by_agent_for_job_aggregates_costs(repos: tuple[JobsRepository, TracesRepository]):
    """sum_by_agent_for_job groups by (agent_type, model) with cost/duration sums,
    ordered by cost DESC."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="o/r", task="t")

    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="success",
        cost_usd=0.10,
        duration_ms=1000,
    )
    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="success",
        cost_usd=0.20,
        duration_ms=2000,
    )
    await traces_repo.create(
        job_id=job_id,
        agent_type="review",
        model="claude-opus-4-6",
        result="success",
        cost_usd=0.50,
        duration_ms=500,
    )

    breakdown = await traces_repo.sum_by_agent_for_job(job_id)
    assert len(breakdown) == 2
    # Ordered by cost_usd DESC: review (0.50) first, then developer (0.30)
    assert breakdown[0]["agent_type"] == "review"
    assert float(breakdown[0]["cost_usd"]) == pytest.approx(0.50)
    assert breakdown[0]["runs"] == 1
    assert breakdown[1]["agent_type"] == "developer"
    assert breakdown[1]["runs"] == 2
    assert float(breakdown[1]["cost_usd"]) == pytest.approx(0.30)
    assert breakdown[1]["duration_ms"] == 3000


@pytest.mark.asyncio
async def test_sum_by_agent_for_job_empty_returns_empty(repos: tuple[JobsRepository, TracesRepository]):
    """No traces for a job returns an empty breakdown list."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="o/r", task="t")
    breakdown = await traces_repo.sum_by_agent_for_job(job_id)
    assert breakdown == []


@pytest.mark.asyncio
async def test_create_persists_token_columns(repos: tuple[JobsRepository, TracesRepository]):
    """EMB-35: the four token columns round-trip through create/get."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="t")

    trace_id = await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="success",
        input_tokens=1200,
        output_tokens=340,
        cache_read_tokens=98000,
        cache_creation_tokens=4500,
    )
    trace = await traces_repo.get(trace_id)
    assert trace is not None
    assert trace["input_tokens"] == 1200
    assert trace["output_tokens"] == 340
    assert trace["cache_read_tokens"] == 98000
    assert trace["cache_creation_tokens"] == 4500


@pytest.mark.asyncio
async def test_create_token_columns_default_zero(repos: tuple[JobsRepository, TracesRepository]):
    """Callers that don't pass token counts write zeros, not NULLs."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="t")
    trace_id = await traces_repo.create(job_id=job_id, agent_type="triage", model="m", result="success")
    trace = await traces_repo.get(trace_id)
    assert trace is not None
    assert trace["input_tokens"] == 0
    assert trace["output_tokens"] == 0
    assert trace["cache_read_tokens"] == 0
    assert trace["cache_creation_tokens"] == 0


@pytest.mark.asyncio
async def test_sum_by_agent_includes_token_sums(repos: tuple[JobsRepository, TracesRepository]):
    """EMB-35: sum_by_agent_for_job rolls up the token columns per (agent, model)."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="o/r", task="t")

    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="m",
        result="success",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=1000,
        cache_creation_tokens=200,
    )
    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="m",
        result="success",
        input_tokens=300,
        output_tokens=150,
        cache_read_tokens=3000,
        cache_creation_tokens=400,
    )

    breakdown = await traces_repo.sum_by_agent_for_job(job_id)
    assert len(breakdown) == 1
    assert breakdown[0]["input_tokens"] == 400
    assert breakdown[0]["output_tokens"] == 200
    assert breakdown[0]["cache_read_tokens"] == 4000
    assert breakdown[0]["cache_creation_tokens"] == 600


@pytest.mark.asyncio
async def test_token_stats_groups_by_agent_type(repos: tuple[JobsRepository, TracesRepository]):
    """EMB-35: token_stats aggregates across jobs by agent_type."""
    jobs_repo, traces_repo = repos
    job_a = await jobs_repo.create(repo="o/r", task="t1")
    job_b = await jobs_repo.create(repo="o/r", task="t2")

    await traces_repo.create(
        job_id=job_a,
        agent_type="developer",
        model="m1",
        result="success",
        cost_usd=0.5,
        input_tokens=100,
        cache_read_tokens=1000,
    )
    await traces_repo.create(
        job_id=job_b,
        agent_type="developer",
        model="m2",
        result="success",
        cost_usd=0.2,
        input_tokens=50,
        cache_read_tokens=500,
    )

    stats = await traces_repo.token_stats()
    dev = next(r for r in stats if r["agent_type"] == "developer")
    assert dev["runs"] == 2
    assert dev["input_tokens"] == 150
    assert dev["cache_read_tokens"] == 1500
    assert float(dev["cost_usd"]) == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_failures_for_job_returns_errored_runs_newest_first(repos: tuple[JobsRepository, TracesRepository]):
    """#281: the per-run failure record GET /jobs/{id} serves."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="t")
    other = await jobs_repo.create(repo="a/b", task="t2")

    await traces_repo.create(job_id=job_id, agent_type="triage", model="m", result="success")
    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="m",
        result="error",
        result_summary="Command failed with exit code 1",
        error_code="ERR_AGENT_PROCESS_FAILED",
        stderr_tail="ENOSPC: no space left on device",
    )
    await traces_repo.create(
        job_id=job_id, agent_type="review", model="m", result="error", error_code="ERR_AGENT_MAX_TURNS"
    )
    await traces_repo.create(job_id=other, agent_type="qa", model="m", result="error", stderr_tail="not mine")

    failures = await traces_repo.failures_for_job(job_id)

    assert [f["agent_type"] for f in failures] == ["review", "developer"]
    assert failures[1]["error_code"] == "ERR_AGENT_PROCESS_FAILED"
    assert failures[1]["stderr_tail"] == "ENOSPC: no space left on device"
    assert failures[0]["stderr_tail"] is None
    assert len(await traces_repo.failures_for_job(job_id, limit=1)) == 1


@pytest.mark.asyncio
async def test_create_with_a_given_id_then_finish(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")
    trace_id = await traces_repo.create(
        job_id=job_id, agent_type="developer", model="m", result="running", trace_id="run-abc"
    )
    assert trace_id == "run-abc"
    assert (await traces_repo.get("run-abc"))["result"] == "running"
    await traces_repo.finish(
        "run-abc",
        result="error",
        cost_usd=0.5,
        duration_ms=10,
        tools_called={"Bash": 1},
        result_summary="boom",
        input_tokens=1,
        output_tokens=2,
        cache_read_tokens=3,
        cache_creation_tokens=4,
        error_code="ERR_X",
        stderr_tail="tail",
    )
    row = await traces_repo.get("run-abc")
    assert row["result"] == "error" and row["cost_usd"] == 0.5 and row["error_code"] == "ERR_X"
    assert row["tools_called"] == {"Bash": 1} and row["stderr_tail"] == "tail"
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_creation_tokens"]) == (
        1,
        2,
        3,
        4,
    )


@pytest.mark.asyncio
async def test_spend_is_counted_once_when_the_running_row_is_finished(db_with_migrations: DatabasePool):
    """The running row carries no cost; finish() counts the spend, once, with the row's model."""
    spent: list[tuple[str, float]] = []

    class _Metrics:
        def agent_spend(self, *, pipeline_template: str | None, model: str, cost_usd: float) -> None:
            spent.append((model, cost_usd))

    jobs_repo = JobsRepository(db_with_migrations)
    traces_repo = TracesRepository(db_with_migrations, metrics=_Metrics())  # type: ignore[arg-type]
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")
    await traces_repo.create(
        job_id=job_id, agent_type="developer", model="claude-opus-5", result="running", trace_id="run-1"
    )
    assert spent == [], "a running row has spent nothing yet"
    await traces_repo.finish(
        "run-1",
        result="success",
        cost_usd=1.25,
        duration_ms=10,
        tools_called=None,
        result_summary="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        error_code=None,
        stderr_tail=None,
    )
    assert spent == [("claude-opus-5", 1.25)], f"finish must count the spend exactly once, got {spent}"


@pytest.mark.asyncio
async def test_finish_records_the_runs_actions_by_skill(repos: tuple[JobsRepository, TracesRepository]):
    """Progression spec 2026-10-24: the run's tool calls, by the skill they train, land on the row."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")
    await traces_repo.create(job_id=job_id, agent_type="developer", model="m", result="running", trace_id="run-sk")
    assert (await traces_repo.get("run-sk"))["skill_actions"] == {}, "a new row starts with none"
    await traces_repo.finish(
        "run-sk",
        result="success",
        cost_usd=0.0,
        duration_ms=1,
        tools_called={"Bash": 2},
        skill_actions={"shell:test-runner": 2},
        result_summary="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        error_code=None,
        stderr_tail=None,
    )
    assert (await traces_repo.get("run-sk"))["skill_actions"] == {"shell:test-runner": 2}


@pytest.mark.asyncio
async def test_running_traces_are_listed_and_closed_only_while_still_running(db_with_migrations: DatabasePool):
    """#694: close_running closes a run that never ended, counts its spend once, and never overwrites a trace its own
    finish (or an earlier close) reached first."""
    spent: list[tuple[str, float]] = []

    class _Metrics:
        def agent_spend(self, *, pipeline_template: str | None, model: str, cost_usd: float) -> None:
            spent.append((model, cost_usd))

    jobs_repo = JobsRepository(db_with_migrations)
    traces_repo = TracesRepository(db_with_migrations, metrics=_Metrics())  # type: ignore[arg-type]
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")
    pricing = {"provider": "xai", "model": "grok-4.7", "rates": {"in": 2.0, "out": 6.0}}
    for run_id in ("run-1", "run-2"):
        await traces_repo.create(
            job_id=job_id, agent_type="developer", model="grok-4.7", result="running", trace_id=run_id, pricing=pricing
        )
    await traces_repo.create(
        job_id=job_id, agent_type="developer", model="grok-4.7", result="success", trace_id="run-3"
    )
    running = await traces_repo.running()
    assert [r["trace_id"] for r in running] == ["run-1", "run-2"]
    assert running[0]["pricing"] == pricing and running[0]["job_id"] == job_id and running[0]["created_at"] is not None
    fields = {
        "result": "error",
        "cost_usd": 0.75,
        "duration_ms": 5,
        "result_summary": "closed",
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "error_code": "ERR_RUN_INTERRUPTED",
        "stderr_tail": None,
        "metering": {"closed": {"cause": "restart"}},
        "sandbox_cost_usd": 0.75,
    }
    assert await traces_repo.close_running("run-1", **fields) is True
    assert await traces_repo.close_running("run-1", **{**fields, "cost_usd": 9.0}) is False, (
        "a closed trace closed again"
    )
    assert await traces_repo.close_running("run-3", **fields) is False, "a finished trace was overwritten"
    closed = await traces_repo.get("run-1")
    assert closed is not None
    assert (closed["result"], closed["cost_usd"], closed["error_code"]) == ("error", 0.75, "ERR_RUN_INTERRUPTED")
    assert closed["metering"] == {"closed": {"cause": "restart"}}
    assert spent == [("grok-4.7", 0.75)], f"the close must count the spend exactly once, got {spent}"
    assert [r["trace_id"] for r in await traces_repo.running()] == ["run-2"]


@pytest.mark.asyncio
async def test_a_close_adds_its_charge_to_the_job_in_the_same_transaction(db_with_migrations: DatabasePool):
    """#694: the close, its cost_update and the job's total go together or not at all."""
    import contextlib
    from typing import Any

    jobs_repo = JobsRepository(db_with_migrations)
    job_id = await jobs_repo.create(repo="a/b", task="t")
    update = {"type": "cost_update", "cost_usd": 0.25, "run_id": "run-1", "node": "developer", "agent": "developer"}
    fields: dict[str, Any] = {
        "result": "error",
        "cost_usd": 0.25,
        "duration_ms": 1,
        "result_summary": "closed",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "error_code": "ERR_RUN_INTERRUPTED",
        "stderr_tail": None,
        "metering": None,
        "sandbox_cost_usd": 0.25,
    }

    class _Conn:
        def __init__(self, conn: Any) -> None:
            self._conn = conn

        async def fetchrow(self, *args: Any) -> Any:
            return await self._conn.fetchrow(*args)

        async def execute(self, sql: str, *args: Any) -> Any:
            if "INSERT INTO job_logs" in sql:
                raise RuntimeError("the log write failed")
            return await self._conn.execute(sql, *args)

    class _LogFails:
        def __getattr__(self, name: str) -> Any:
            return getattr(db_with_migrations, name)

        @contextlib.asynccontextmanager
        async def transaction(self) -> Any:
            async with db_with_migrations.transaction() as conn:
                yield _Conn(conn)

    traces_repo = TracesRepository(db_with_migrations)
    for run_id in ("run-1", "run-2"):
        await traces_repo.create(job_id=job_id, agent_type="developer", model="m", result="running", trace_id=run_id)
    with pytest.raises(RuntimeError):
        await TracesRepository(_LogFails()).close_running("run-1", **fields, cost_update=update)  # type: ignore[arg-type]
    still = await traces_repo.get("run-1")
    assert still is not None and still["result"] == "running", "the close stayed when its charge was not added"
    assert (await jobs_repo.get(job_id) or {})["total_cost_usd"] == 0.0

    assert await traces_repo.close_running("run-1", **fields, cost_update=update) is True
    assert await traces_repo.close_running("run-2", **fields, cost_update=None) is True
    assert (await jobs_repo.get(job_id) or {})["total_cost_usd"] == pytest.approx(0.25), "added once, for run-1"
    kept = await jobs_repo.run_events("run-1", ("cost_update",))
    assert [(e["cost_usd"], e["job_id"]) for e in kept] == [(0.25, job_id)]
    assert await jobs_repo.run_events("run-2", ("cost_update",)) == []


#: Counts past the BIGINT token columns: ten calls each charged 1e18 tokens, and further out in both directions.
_PAST_THE_COLUMNS = {
    "input_tokens": 5,
    "output_tokens": 10 * 10**18,
    "cache_read_tokens": 2**70,
    "cache_creation_tokens": -(2**64),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("write", ["create", "finish", "close_running"])
async def test_a_count_past_its_column_is_held_to_it_and_the_trace_still_ends(
    repos: tuple[JobsRepository, TracesRepository], write: str
):
    """A count the column cannot hold would fail the write that ends the trace, and every close from the same figures,
    leaving it running with its charge unrecorded."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="t")
    ending: dict[str, Any] = {
        "result": "error",
        "cost_usd": 1.0,
        "duration_ms": 1,
        "result_summary": "",
        "error_code": None,
        "stderr_tail": None,
        "metering": None,
        "sandbox_cost_usd": 1.0,
        **_PAST_THE_COLUMNS,
    }
    if write != "create":
        await traces_repo.create(job_id=job_id, agent_type="developer", model="m", result="running", trace_id="run-1")
    with structlog.testing.capture_logs() as logs:
        try:
            if write == "create":
                await traces_repo.create(job_id=job_id, agent_type="developer", model="m", trace_id="run-1", **ending)
            elif write == "finish":
                await traces_repo.finish("run-1", tools_called=None, **ending)
            else:
                assert await traces_repo.close_running("run-1", **ending) is True
        except AssertionError:
            raise
        except Exception as exc:
            pytest.fail(f"{write}: a count past its column failed the write: {exc!r}")
    row = await traces_repo.get("run-1")
    assert row is not None and row["result"] == "error", f"{write}: the trace was left {row and row['result']}"
    written = (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_creation_tokens"])
    assert written == (5, 2**63 - 1, 2**63 - 1, -(2**63)), f"{write}: wrote {written}"
    held = sorted(e["column"] for e in logs if e["event"] == "trace_count_held_to_column")
    assert held == ["cache_creation_tokens", "cache_read_tokens", "output_tokens"], f"{write}: logged {held}"
