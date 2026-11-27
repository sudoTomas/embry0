"""What an agent run may still spend, as its declaration at the metering proxies carries it.

The per-job cap is checked after each agent turn, and the daily and monthly caps at dispatch, so a run's own calls
can take a job (or the day) well past them before either looks. A run whose calls pass a metering proxy is declared
there with its budget (``embry0.execution.run_meter``): the least that its job and the deployment's day and month
have left, and the run's rates. The proxy refuses a call whose ceiling, priced at those rates, would take what the
run has been charged past that (``embry0.execution.proxy.metering``), in every overrun mode.

Pure: the caller gathers the caps and what was spent against each.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from embry0.agents.invocation import ProviderRecord

__all__ = ["Limit", "run_budget"]


@dataclass(frozen=True)
class Limit:
    """One cap and what was spent against it, in USD. A cap of 0 or less is no cap."""

    name: str  # "job" | "daily" | "monthly", as a declaration names it
    cap: float
    spent: float


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def run_budget(record: ProviderRecord | None, limits: list[Limit]) -> dict[str, Any] | None:
    """The ``budget`` of the run's declaration: what it may still spend (the least any set cap has left, never below
    0), the limit that leaves it, and the rates its record prices it at. None, so no call is checked, when no cap is
    set or the run is free: no record, an unpriced one, or rates of 0."""
    if record is None or record.rates is None or not any(rate > 0 for rate in record.rates):
        return None
    # A spend that is not a number leaves nothing (max(0.0, nan) is 0.0); one that leaves no finite amount is no
    # figure to hold a run to, and a proxy refuses a declaration naming one.
    left = [(max(0.0, limit.cap - limit.spent), limit.name) for limit in limits if _finite(limit.cap) and limit.cap > 0]
    left = [(remaining, name) for remaining, name in left if math.isfinite(remaining)]
    if not left:
        return None
    remaining, name = min(left)
    rate_in, rate_out = record.rates
    return {
        "remaining_usd": remaining,
        "limit": name,
        "rates": {"in": rate_in, "out": rate_out},
        "cache": {"read": record.cache_read_factor, "write": record.cache_write_factor},
    }
