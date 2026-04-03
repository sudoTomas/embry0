"""Budget tracking — checks and enforces spending limits."""

from dataclasses import dataclass


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
