"""Agent session retention (ADR 0017): saved sessions of ended jobs, deleted after N days.

A saved session (``agent_sessions``) is an agent's whole conversation: for a
Claude subscription the CLI's session file, with every tool input and result
uncapped, so it holds more of a repository's code than the job events do. It
is kept so a later turn of the same job, or a pull request comment follow-up
on it, resumes instead of starting cold.

With ``AGENT_SESSION_RETENTION_DAYS`` set, a sweep deletes the sessions of
ended jobs whose last turn is older than that, in batches. Sessions of live
jobs (paused, awaiting input, running) stay. What deleting one costs: a pull
request comment follow-up on that job starts a fresh developer session
instead of resuming the one that wrote the pull request; the resume path
falls back to that when no session exists. That cost is why this is its own
setting rather than part of ``JOB_LOG_RETENTION_DAYS``. Unset, nothing is
touched. ``run_once(dry_run=True)`` counts the same sessions and deletes
nothing, which is what the admin preview reports.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from embry0.storage.repositories.agent_sessions import AgentSessionsRepository

logger = structlog.get_logger(__name__)

_DAY_SECONDS = 86400


@dataclass(slots=True)
class AgentSessionRetentionResult:
    retention_days: int | None
    sessions: int = 0
    bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"retention_days": self.retention_days, "sessions": self.sessions, "bytes": self.bytes}


class AgentSessionRetentionSweep:
    """Deletes ended jobs' sessions last used more than ``retention_days`` ago; off when that is None."""

    def __init__(
        self,
        repo: AgentSessionsRepository,
        *,
        retention_days: int | None,
        interval_seconds: int = _DAY_SECONDS,
        batch: int = 50,
    ) -> None:
        self._repo = repo
        self._days = retention_days
        self._interval = interval_seconds
        self._batch = batch

    @property
    def enabled(self) -> bool:
        return self._days is not None

    @property
    def retention_days(self) -> int | None:
        return self._days

    async def run_once(
        self,
        *,
        dry_run: bool = False,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> AgentSessionRetentionResult:
        """One pass at ``retention_days`` (default: the configured value). Nothing to do without one."""
        days = retention_days if retention_days is not None else self._days
        result = AgentSessionRetentionResult(retention_days=days)
        if days is None:
            return result
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        if dry_run:
            result.sessions, result.bytes = await self._repo.expired(cutoff=cutoff)
        else:
            result.sessions, result.bytes = await self._repo.delete_expired(cutoff=cutoff, limit=self._batch)
        logger.info("agent_session_retention_swept", dry_run=dry_run, cutoff=cutoff.isoformat(), **result.as_dict())
        return result

    async def start(self) -> None:  # pragma: no cover - the loop is exercised by the app
        """One sweep now, then one per interval, until the task is cancelled."""
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.warning("agent_session_retention_sweep_failed", exc_info=True)
            await asyncio.sleep(self._interval)
