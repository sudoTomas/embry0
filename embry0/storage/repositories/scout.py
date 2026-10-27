"""The scout loop's runs (spec 2026-10-27): which unit each scouting job was, and whether its leads were taken."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


class ScoutRunsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def record(self, *, job_id: str, repo: str, area: str, template: str, now: datetime) -> None:
        await self._db.execute(
            """
            INSERT INTO scout_runs (job_id, repo, area, template, dispatched_at)
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT (job_id) DO NOTHING
            """,
            job_id,
            repo,
            area,
            template,
            now,
        )

    async def last_dispatched(self) -> dict[tuple[str, str, str], datetime]:
        """When each unit (repository lowercased, area, template) was last scouted."""
        rows = await self._db.fetch(
            "SELECT lower(repo) AS repo, area, template, max(dispatched_at) AS at FROM scout_runs GROUP BY 1, 2, 3"
        )
        return {(str(r["repo"]), str(r["area"]), str(r["template"])): r["at"] for r in rows}

    async def unharvested(self) -> list[dict[str, Any]]:
        """The runs whose leads were not taken yet: the jobs still out, and the finished ones not read."""
        rows = await self._db.fetch(
            "SELECT job_id, repo, area, template, dispatched_at FROM scout_runs WHERE harvested_at IS NULL "
            "ORDER BY dispatched_at"
        )
        return [dict(r) for r in rows]

    async def harvested(self, job_id: str, *, leads: int, now: datetime) -> None:
        await self._db.execute(
            "UPDATE scout_runs SET harvested_at = $2, leads = $3 WHERE job_id = $1", job_id, now, leads
        )
