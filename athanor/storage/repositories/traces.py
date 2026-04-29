"""Traces repository — PostgreSQL CRUD for agent execution telemetry."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


class TracesRepository:
    """CRUD operations for the traces table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        job_id: str,
        agent_type: str,
        model: str,
        result: str,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        tools_called: dict[str, int] | None = None,
        result_summary: str = "",
    ) -> str:
        """Create a trace record and return its ID."""
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO traces (
                trace_id, job_id, agent_type, model, result,
                cost_usd, duration_ms, tools_called, result_summary
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
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
        )
        logger.info("trace_created", trace_id=trace_id, job_id=job_id, agent_type=agent_type)
        return trace_id

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
                   COALESCE(SUM(duration_ms), 0) as duration_ms
            FROM traces
            WHERE job_id = $1
            GROUP BY agent_type, model
            ORDER BY cost_usd DESC
            """,
            job_id,
        )
        return [dict(r) for r in rows]
