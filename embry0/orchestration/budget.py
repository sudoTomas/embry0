"""Budget tracking — checks and enforces spending limits.

Layer 1 of the three cost-protection layers (``docs/architecture.md``): the
per-job cap. Layers 2 and 3 — the daily/monthly rollup caps and the
per-author hourly rate limit — live in ``embry0.orchestration.budget_gate``
and run once, at dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Fallback per-job cap for state that carries neither a job-create override
# nor a triage-resolved ``pipeline_config.budget_usd``. Mirrors
# ``PipelineConfig.budget_usd``'s own default.
DEFAULT_JOB_BUDGET_USD = 10.0


class BudgetOverrunError(RuntimeError):
    """A job crossed its per-job cap while ``overrun_mode`` is ``hard``.

    Raised from the agent node so the run aborts at the ceiling instead of
    finishing the graph; ``error_code_for_exception`` maps it to
    ``ErrorCode.BUDGET_OVERRUN``.
    """

    def __init__(self, current_cost: float, max_budget: float) -> None:
        super().__init__(
            f"Job budget exhausted: ${current_cost:.2f} spent against a ${max_budget:.2f} cap (overrun_mode=hard)"
        )
        self.current_cost = current_cost
        self.max_budget = max_budget


@dataclass
class BudgetCheckResult:
    allowed: bool
    remaining: float
    overrun: float


def check_budget(
    current_cost: float,
    max_budget: float,
    overrun_mode: str = "soft",
) -> BudgetCheckResult:
    overrun = max(0.0, current_cost - max_budget)
    remaining = max(0.0, max_budget - current_cost)
    if overrun > 0:
        allowed = overrun_mode == "soft"
    else:
        allowed = True
    return BudgetCheckResult(allowed=allowed, remaining=remaining, overrun=overrun)


def effective_overrun_mode(fleet_mode: str, job_mode: Any) -> str:
    """The overrun mode for one job: a job may tighten the fleet mode, never loosen it.

    Unattended jobs (the watcher, #275) pin themselves to ``hard`` so their cap
    stops the run even while the fleet runs ``soft``. Any other job-level value
    is ignored — a job-create field must not be a way around an operator's
    ``hard``.
    """
    return "hard" if job_mode == "hard" else fleet_mode


def resolve_job_cap(state: dict[str, Any], *, ceiling: float = 0.0, may_exceed_ceiling: bool = True) -> float:
    """The per-job cap in force for ``state``.

    Triage folds a job-create ``max_budget_usd`` over ``budget_usd`` when it
    writes ``pipeline_config``, so that key wins once it exists; before triage
    has run the raw override is the only signal. ``ceiling`` is the fleet-wide
    ``budget.max_budget_per_job_usd`` of the settings document — it bounds the
    result so a per-job choice can go under the operator's cap but never over
    it. 0 (the default) means no ceiling. A person's continue past the cap
    goes on top of the ceiling unless ``may_exceed_ceiling`` is false
    (``budget.continue_may_exceed_ceiling``): then the ceiling bounds it too.
    """
    pipeline = state.get("pipeline_config") or {}
    if isinstance(pipeline, dict) and pipeline.get("budget_usd") is not None:
        cap = float(pipeline["budget_usd"])
    elif state.get("max_budget_usd") is not None:
        cap = float(state["max_budget_usd"])
    else:
        cap = DEFAULT_JOB_BUDGET_USD
    bounded = min(cap, ceiling) if ceiling > 0 else cap
    # A person who continued the job past its cap let it spend more: an
    # operator's choice, so it goes on top of the ceiling, not under it,
    # unless the deployment made the ceiling a hard limit.
    total = bounded + budget_allowance(state)
    return min(total, ceiling) if ceiling > 0 and not may_exceed_ceiling else total


def budget_allowance(state: dict[str, Any]) -> float:
    """How much past its cap a person let this job spend (``continue_retrying`` on a budget pause)."""
    try:
        allowance = float(state.get("budget_allowance_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return allowance if allowance > 0 else 0.0


def continue_adds(
    state: dict[str, Any],
    *,
    ceiling: float = 0.0,
    extra: float | None = None,
    default_extra: float | None = None,
    may_exceed_ceiling: bool = True,
    extra_max: float | None = None,
) -> float:
    """How much more than it has spent a continue lets a job spend.

    ``extra`` is what the person named; without it ``default_extra``
    (``budget.continue_default_extra_usd``), and without that one more cap.
    Whichever it is, at most ``extra_max`` (``budget.continue_extra_max_usd``),
    so the default never adds more than a person could name. When the
    ceiling is a hard limit, at most what is left under it too: 0 for a job
    already at it, which a continue cannot help.
    """
    base = resolve_job_cap({**state, "budget_allowance_usd": 0.0}, ceiling=ceiling)
    if extra and extra > 0:
        more = float(extra)
    elif default_extra and default_extra > 0:
        more = float(default_extra)
    else:
        more = base
    if extra_max is not None and extra_max > 0:
        more = min(more, float(extra_max))
    if ceiling > 0 and not may_exceed_ceiling:
        spent = float(state.get("total_cost_usd") or 0.0)
        more = min(more, max(0.0, ceiling - spent))
    return more


def allowance_to_continue(
    state: dict[str, Any],
    *,
    ceiling: float = 0.0,
    extra: float | None = None,
    default_extra: float | None = None,
    may_exceed_ceiling: bool = True,
    extra_max: float | None = None,
) -> float:
    """The allowance that lets a job over its cap spend :func:`continue_adds` more than it has.

    The job may then spend up to what it has spent plus that; an allowance it
    already holds is never lowered.
    """
    base = resolve_job_cap({**state, "budget_allowance_usd": 0.0}, ceiling=ceiling)
    more = continue_adds(
        state,
        ceiling=ceiling,
        extra=extra,
        default_extra=default_extra,
        may_exceed_ceiling=may_exceed_ceiling,
        extra_max=extra_max,
    )
    spent = float(state.get("total_cost_usd") or 0.0)
    return max(budget_allowance(state), round(spent - base + more, 2))
