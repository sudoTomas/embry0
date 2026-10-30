"""Per-job budget for watcher analysis jobs (#275).

Watcher ticks run unattended, so nobody is there to notice a soft-mode
warning: their cap is always a hard stop, whatever the fleet-wide
``budget.overrun_mode`` (the settings document) says. The cap itself is a watcher setting
(``WATCHER_JOB_BUDGET_USD``); ``repo_config`` is the seam for a per-repo
watcher config row (#297) to supply its own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# About twice the dearest ``analysis-default`` job seen in production
# (2026-09-20: completed max $1.07, p99 $0.90; failed max $1.14). The cap is
# checked between agent turns, so it has to sit close to real spend to mean
# anything: worst case is the cap plus one full turn.
DEFAULT_WATCHER_JOB_BUDGET_USD = 2.0


@dataclass(frozen=True)
class WatcherJobBudget:
    cap_usd: float

    def as_pipeline_config(self) -> dict[str, Any]:
        """The job-row ``pipeline_config`` keys ``_run_workflow`` surfaces on state."""
        return {"max_budget_usd": self.cap_usd, "budget_overrun_mode": "hard"}


def resolve_watcher_job_budget(config: Any, repo_config: Mapping[str, Any] | None = None) -> WatcherJobBudget:
    """Cap for one analysis job: the repo's own row, else the watcher setting.

    An absent, non-numeric, non-finite or non-positive value falls through to
    the next source and finally to the default. 0 means "unlimited" elsewhere
    in the budget config, and ``inf`` would amount to the same; an unattended
    job never gets that reading.
    """
    for candidate in (
        (repo_config or {}).get("job_budget_usd"),
        getattr(config, "watcher_job_budget_usd", None),
    ):
        cap = _positive(candidate)
        if cap:
            return WatcherJobBudget(cap_usd=cap)
    return WatcherJobBudget(cap_usd=DEFAULT_WATCHER_JOB_BUDGET_USD)


def _positive(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed > 0 else 0.0
