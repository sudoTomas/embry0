"""Traces repository — PostgreSQL CRUD for agent execution telemetry."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog

from embry0.storage.database import DatabasePool

if TYPE_CHECKING:
    from embry0.metrics import LoopMetrics

logger = structlog.get_logger(__name__)


class TracesRepository:
    """CRUD operations for the traces table."""

    def __init__(self, db: DatabasePool, *, metrics: LoopMetrics | None = None) -> None:
        self._db = db
        # #285: every agent run writes exactly one trace row, which makes this
        # the one place spend can be counted once. None = count nothing.
        self._metrics = metrics

    async def create(
        self,
        job_id: str,
        agent_type: str,
        model: str,
        result: str,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        tools_called: dict[str, int] | None = None,
        skill_actions: dict[str, int] | None = None,
        result_summary: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        error_code: str | None = None,
        stderr_tail: str | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Create a trace record and return its ID.

        ``trace_id`` is the run id run_agent_node mints before the sandbox
        exec (spec 3.4), so the row and the run's events share one id; a
        caller without one gets a fresh ``trc-`` id.

        ``error_code`` / ``stderr_tail`` (#281) describe an ERRORED run: the
        runner's diagnosis and the redacted, size-capped end of its stderr.
        Callers pass the tail already scrubbed — this layer stores verbatim.
        """
        trace_id = trace_id or f"trc-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO traces (
                trace_id, job_id, agent_type, model, result,
                cost_usd, duration_ms, tools_called, result_summary,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                error_code, stderr_tail, skill_actions
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            """,
            trace_id,
            job_id,
            agent_type,
            model,
            result,
            cost_usd,
            duration_ms,
            tools_called or {},
            result_summary,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            error_code,
            stderr_tail,
            skill_actions or {},
        )
        logger.info("trace_created", trace_id=trace_id, job_id=job_id, agent_type=agent_type)
        if self._metrics is not None and cost_usd > 0:
            await self._count_spend(job_id, model, cost_usd)
        return trace_id

    async def finish(
        self,
        trace_id: str,
        *,
        result: str,
        cost_usd: float,
        duration_ms: int,
        tools_called: dict[str, int] | None,
        result_summary: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        error_code: str | None,
        stderr_tail: str | None,
        skill_actions: dict[str, int] | None = None,
    ) -> None:
        """Finalize the row written at run start (result='running').

        The running row carries no cost, so spend is counted here, once.
        """
        await self._db.execute(
            """
            UPDATE traces SET result = $2, cost_usd = $3, duration_ms = $4, tools_called = $5, result_summary = $6,
                input_tokens = $7, output_tokens = $8, cache_read_tokens = $9, cache_creation_tokens = $10,
                error_code = $11, stderr_tail = $12, skill_actions = $13
            WHERE trace_id = $1
            """,
            trace_id,
            result,
            cost_usd,
            duration_ms,
            tools_called or {},
            result_summary,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            error_code,
            stderr_tail,
            skill_actions or {},
        )
        logger.info("trace_finished", trace_id=trace_id, result=result)
        if self._metrics is not None and cost_usd > 0:
            row = await self._db.fetchrow("SELECT job_id, model FROM traces WHERE trace_id = $1", trace_id)
            if row is not None:
                await self._count_spend(str(row["job_id"]), str(row["model"]), cost_usd)

    async def _count_spend(self, job_id: str, model: str, cost_usd: float) -> None:
        """Best-effort: the trace is written; a metric must not fail its caller.

        Two guards for two different failures: a lookup that fails costs the
        row its attribution but the dollars are still counted (under
        "unassigned"), while the metric call is guarded in its own right
        rather than relying on the facade swallowing its errors.
        """
        assert self._metrics is not None
        pipeline: str | None = None
        try:
            # NULL until triage has routed the job, so triage's own spend
            # reads pipeline_template="unassigned" unless the job was created
            # with a template (watcher analyses are).
            pipeline = await self._db.fetchval("SELECT pipeline_template FROM jobs WHERE job_id = $1", job_id)
        except Exception:
            logger.warning("trace_spend_pipeline_lookup_failed", job_id=job_id, exc_info=True)
        try:
            self._metrics.agent_spend(pipeline_template=pipeline, model=model, cost_usd=cost_usd)
        except Exception:
            logger.warning("trace_spend_metric_failed", job_id=job_id, exc_info=True)

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        """Fetch a single trace by ID."""
        row = await self._db.fetchrow("SELECT * FROM traces WHERE trace_id = $1", trace_id)
        if row is None:
            return None
        return dict(row)

    async def list_all(
        self,
        job_id: str | None = None,
        agent_type: str | None = None,
        result: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List traces with optional filters. Returns (rows, total_count)."""
        conditions: list[str] = []
        args: list[Any] = []
        idx = 1

        if job_id:
            conditions.append(f"job_id = ${idx}")
            args.append(job_id)
            idx += 1
        if agent_type:
            conditions.append(f"agent_type = ${idx}")
            args.append(agent_type)
            idx += 1
        if result:
            conditions.append(f"result = ${idx}")
            args.append(result)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self._db.fetchval(f"SELECT COUNT(*) FROM traces {where}", *args)

        args.extend([limit, offset])
        rows = await self._db.fetch(
            f"SELECT * FROM traces {where} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *args,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def failures_for_job(self, job_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """The job's errored agent runs, newest first (#281): which agent, the
        runner's diagnosis, and the redacted stderr tail. Bounded — a job that
        retried fifty times must not balloon GET /jobs/{id}."""
        rows = await self._db.fetch(
            """
            SELECT trace_id, agent_type, model, error_code, result_summary, stderr_tail, created_at
            FROM traces
            WHERE job_id = $1 AND result = 'error'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            job_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def total_cost_for_job(self, job_id: str) -> float:
        """Sum cost_usd across every trace row for a job.

        The standalone QA fan-out subgraph does not propagate per-agent
        total_cost_usd up to the parent workflow state (only the issue→PR
        path accumulates it via run_agent_node), so the QA path's state
        value is always 0.0. Trace rows, however, are written per agent
        under the parent job_id regardless of path — and they are the same
        source GET /jobs cost_breakdown is derived from, so using them as
        the authoritative job total keeps the row and the breakdown
        consistent.
        """
        val = await self._db.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM traces WHERE job_id = $1",
            job_id,
        )
        return float(val or 0.0)

    async def sum_by_agent_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Aggregate traces by (agent_type, model) with cost + duration totals.

        Used to surface per-agent cost breakdown on GET /api/v1/jobs/{id}.
        Returns rows ordered by cost_usd DESC so the most expensive agents
        show first.
        """
        rows = await self._db.fetch(
            """
            SELECT agent_type, model,
                   COUNT(*) as runs,
                   COALESCE(SUM(cost_usd), 0.0) as cost_usd,
                   COALESCE(SUM(duration_ms), 0) as duration_ms,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens
            FROM traces
            WHERE job_id = $1
            GROUP BY agent_type, model
            ORDER BY cost_usd DESC
            """,
            job_id,
        )
        return [dict(r) for r in rows]

    async def token_stats(self) -> list[dict[str, Any]]:
        """Aggregate token usage by agent_type across all traces.

        Powers the /stats token breakdown. cache_hit_rate is derived by the
        API layer (cache_read / (input + cache_read)) so the SQL stays a
        plain rollup.
        """
        rows = await self._db.fetch(
            """
            SELECT agent_type,
                   COUNT(*) as runs,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                   COALESCE(SUM(cost_usd), 0.0) as cost_usd
            FROM traces
            GROUP BY agent_type
            ORDER BY cost_usd DESC
            """
        )
        return [dict(r) for r in rows]
