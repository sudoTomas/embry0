import pytest

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
        skill_actions={"shell:testing": 2},
        result_summary="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        error_code=None,
        stderr_tail=None,
    )
    assert (await traces_repo.get("run-sk"))["skill_actions"] == {"shell:testing": 2}
