"""What an agent run may still spend, as its declaration at the metering proxies carries it.

The per-job cap is checked after each agent turn, and the daily and monthly caps at dispatch, so a run's own calls
can take a job (or the day) well past them before either looks. A run whose calls pass a metering proxy is declared
there with the scopes it draws on: its job's cap, and the deployment's daily and monthly caps, each with what it had
left once every settled run was charged, and the run's rates. The run meter's ledger turns those into purses at the
proxies (``embry0.execution.run_meter``), which the runs drawing on one share as they go; the proxy refuses a call
that would take a purse past what it has left (``embry0.execution.proxy.metering``), in every overrun mode.

Pure: the caller gathers the caps and what was settled against each.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from embry0.agents.invocation import ProviderRecord

__all__ = ["Limit", "run_budget"]


@dataclass(frozen=True)
class Limit:
    """One cap and what settled runs spent against it, in USD. A cap of 0 or less is no cap."""

    name: str  # "job" | "daily" | "monthly"
    cap: float
    spent: float


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def run_budget(record: ProviderRecord | None, limits: list[Limit], job_key: str) -> dict[str, Any] | None:
    """The run's budget request: its rates, and a scope for each set cap (``job_key`` for the job's, ``daily`` and
    ``monthly`` for the deployment's) with what the cap has left, never below 0. None, so no call is checked, when no
    cap is set or the run is free: no record, an unpriced one, or rates of 0."""
    if record is None or record.rates is None or not any(rate > 0 for rate in record.rates):
        return None
    scopes = []
    for limit in limits:
        if not _finite(limit.cap) or limit.cap <= 0:
            continue
        # A spend that is not a number leaves nothing (max(0.0, nan) is 0.0); one that leaves no finite amount is no
        # figure to hold a run to, and a proxy refuses a declaration naming one.
        remaining = max(0.0, limit.cap - limit.spent)
        if math.isfinite(remaining):
            key = job_key if limit.name == "job" else limit.name
            scopes.append({"key": key, "limit": limit.name, "remaining_usd": remaining})
    if not scopes:
        return None
    rate_in, rate_out = record.rates
    return {
        "rates": {"in": rate_in, "out": rate_out},
        "cache": {"read": record.cache_read_factor, "write": record.cache_write_factor},
        "scopes": scopes,
    }
