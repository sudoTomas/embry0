"""Sandbox janitor + stuck-run detector.

Phase 1 ships minimal versions of both. They run as background tasks
scheduled by the existing athanor periodic-task runner (the wiring of which
is deferred to Task 27 alongside the orchestrator wiring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class JanitorReport:
    reaped_container_ids: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # container_id -> error


class _DockerLike(Protocol):
    async def list_containers_with_label(self, label_prefix: str = ...) -> list[dict[str, Any]]: ...
    async def kill(self, container_id: str) -> None: ...
    async def remove(self, container_id: str) -> None: ...


class _RunsRepoLike(Protocol):
    async def is_run_active(self, qa_run_id: str) -> bool: ...
    async def list_in_progress_runs_with_last_progress(
        self,
    ) -> list[dict[str, Any]]: ...


async def reap_orphan_sandboxes(
    docker: _DockerLike,
    runs_repo: _RunsRepoLike,
) -> JanitorReport:
    """Find athanor-labelled containers whose parent run is gone; reap them.

    Spec §9.4: runs every 60s in production. Phase 1 implementation is the
    primitive — the scheduling is wired in Task 27.
    """
    report = JanitorReport()
    containers = await docker.list_containers_with_label("athanor.qa_parent_run_id")

    for c in containers:
        run_id = (c.get("labels") or {}).get("athanor.qa_parent_run_id")
        if run_id is None:
            continue
        try:
            active = await runs_repo.is_run_active(run_id)
        except Exception as exc:  # noqa: BLE001
            report.failures[c["id"]] = f"is_run_active error: {exc}"
            continue
        if active:
            continue
        try:
            await docker.kill(c["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("janitor_kill_failed", container_id=c["id"], error=str(exc))
        try:
            await docker.remove(c["id"])
            report.reaped_container_ids.append(c["id"])
        except Exception as exc:  # noqa: BLE001
            report.failures[c["id"]] = f"remove error: {exc}"
    return report


async def detect_stuck_runs(
    runs_repo: _RunsRepoLike,
    threshold_minutes: int = 30,
) -> list[str]:
    """Return run ids with no sub-task progress for >= threshold_minutes.

    Caller force-cancels and emits status: infra_failure (spec §9.4).
    """
    rows = await runs_repo.list_in_progress_runs_with_last_progress()
    return [r["qa_run_id"] for r in rows if r.get("minutes_since_progress", 0) >= threshold_minutes]
