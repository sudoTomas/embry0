"""Sub-task result types for the QA orchestrator.

A SubTaskResult is what each parallel sub-task subgraph emits. The
orchestrator collects all results, computes the overall_status for the run,
and persists each SubTaskResult to qa_app_results (Task 21).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SubTaskStatus(StrEnum):
    PASSED = "passed"
    QA_FAILURE = "qa_failure"
    E2E_FAILURE = "e2e_failure"
    BOOT_FAILURE = "boot_failure"
    READY_CHECK_FAILED = "ready_check_failed"
    INFRA_FAILURE = "infra_failure"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"


FAILED_STATUSES: frozenset[SubTaskStatus] = frozenset(
    {
        SubTaskStatus.QA_FAILURE,
        SubTaskStatus.E2E_FAILURE,
        SubTaskStatus.BOOT_FAILURE,
        SubTaskStatus.READY_CHECK_FAILED,
        SubTaskStatus.INCONCLUSIVE,
    }
)
"""Statuses that count as 'this app did not pass QA' for the run-level
reducer + reporting layers. Excludes SKIPPED (skipped is a pass) and
INFRA_FAILURE (athanor's fault, escalates to overall_status='infra_error')."""


def is_terminal_status(s: SubTaskStatus) -> bool:
    """Every status defined in the enum is a terminal state.

    Phase 1 has no in-flight intermediate states (those will be added when
    Phase 4's dashboard needs progress streaming).
    """
    return isinstance(s, SubTaskStatus)


@dataclass(slots=True)
class CacheHits:
    """Per-sub-task cache-layer outcomes. Phase 1 always emits all-False
    because no cache layers exist yet (Phase 2 wires them in)."""

    prebaked_image: bool = False
    shared_volume: bool = False
    turbo_remote: bool = False


@dataclass(slots=True)
class SubTaskResult:
    app_name: str
    status: SubTaskStatus
    duration_ms: int
    cache_hits: CacheHits = field(default_factory=CacheHits)
    trace_url: str | None = None
    failure_summary: str | None = None
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # StrEnum auto-coerces in JSON, but be explicit here
        d["status"] = str(self.status)
        return d


def overall_status(results: list[SubTaskResult]) -> str:
    """Compute the run-level status from per-app sub-task results.

    Rules (from spec §6.5):
      - 'infra_error' if ANY sub-task is INFRA_FAILURE
      - 'failed' if ANY sub-task is in _FAILED_STATUSES
      - 'passed' otherwise (including empty list and all-SKIPPED)
    """
    has_infra = any(r.status == SubTaskStatus.INFRA_FAILURE for r in results)
    if has_infra:
        return "infra_error"
    has_failed = any(r.status in FAILED_STATUSES for r in results)
    if has_failed:
        return "failed"
    return "passed"
