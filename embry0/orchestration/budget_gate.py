"""Pre-dispatch budget gate — daily/monthly caps and a per-actor rate limit.

Layers 2 and 3 of the three cost-protection layers (``docs/architecture.md``).
Both are fleet-wide policies read from ``budget_config``, so they belong at
dispatch rather than inside the graph: one check per job, before any agent
burns a token.

Fail-open, but only for a degraded database. The caps live in the same
Postgres as the jobs table, so a connection or timeout failure means the
orchestrator is already degraded and refusing every dispatch would turn a cost
control into an outage — those log a warning and allow. A syntax error, an
undefined column after a schema change or a plain coding mistake means *the
gate* is broken, not the DB: swallowing those would disable layers 2 and 3
behind one warning line and permit unlimited spend with green CI, so they
propagate and fail the job loudly. The refusal path is loud either way (audit
row + ``ERR_BUDGET_REFUSED``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import structlog

from embry0.orchestration.dispatch_actors import is_rate_limit_exempt

logger = structlog.get_logger(__name__)

# Errors that mean "Postgres is unreachable right now", as opposed to "this
# query is wrong". OSError covers every ConnectionError; asyncpg raises
# InterfaceError for a closed/released pool and PostgresConnectionError for a
# server-side drop. InsufficientResourcesError is the pool-exhausted family
# (TooManyConnectionsError) — load, not a defect.
_TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (
    OSError,  # includes ConnectionError and its subclasses
    TimeoutError,  # asyncio.TimeoutError is an alias since 3.11
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.InsufficientResourcesError,
)


@dataclass(frozen=True)
class DispatchDecision:
    """Outcome of the gate. ``limit`` names which layer refused."""

    allowed: bool
    limit: str = ""  # "daily" | "monthly" | "rate_limit"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def public_message(self) -> str:
        """The refusal as it may be published, with no figures or identities.

        ``message`` is the operator-facing detail and belongs in the audit row
        and the logs. What goes to ``jobs.error_message`` does not stay there:
        ``TrackerCommentChannel.post_outcome`` echoes that column verbatim onto
        the originating issue, which is frequently a public GitHub or GitLab
        ticket. Fleet spend, the configured caps and who dispatched what are
        nobody's business there (#246).
        """
        if self.limit == "rate_limit":
            return "dispatch refused: dispatch rate limit reached"
        if self.limit:
            return "dispatch refused: budget cap reached"
        return ""


class BudgetGate:
    """Decides whether a new job may be dispatched.

    Both collaborators are injected and the clock is a plain callable, so the
    whole policy is exercisable without Postgres.
    """

    def __init__(
        self,
        budget_config_repo: Any,
        spend_repo: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config_repo = budget_config_repo
        self._spend_repo = spend_repo
        self._clock = clock or (lambda: datetime.now(UTC))

    async def check(self, *, actor: str | None = None, excluding: str | None = None) -> DispatchDecision:
        """Decide whether a job may start.

        ``actor`` is the identity that asked for the job (``jobs.dispatched_by``
        — a webhook sender login or an API principal). None when nobody
        identifiable asked, which is the common case for triage-created
        sub-issues: those skip layer 3 entirely rather than sharing a bucket
        with the trigger that spawned their parent.

        ``excluding`` is the job being decided on when its row already exists
        (the runner's check). Left out of the rate count, it does not count
        against itself, so the runner and a caller that asks before creating
        the job (``IssueExecutor.dispatch_refusal``) count the same jobs and
        ``RATE_LIMIT_PER_AUTHOR_PER_HOUR`` jobs an hour run, not one fewer (#116).
        """
        try:
            config = await self._config_repo.get()
            rollup = await self._spend_repo.spend_rollup()
        except _TRANSIENT_DB_ERRORS:
            logger.warning("budget_gate_read_failed", exc_info=True)
            return DispatchDecision(allowed=True)

        daily_cap = _positive(config.get("daily_cap_usd"))
        monthly_cap = _positive(config.get("monthly_cap_usd"))
        daily = float(rollup.get("daily", 0.0) or 0.0)
        monthly = float(rollup.get("monthly", 0.0) or 0.0)

        # A cap of 0 (or absent) means "unlimited" — the settings UI uses the
        # same convention, and a literal zero cap would block every dispatch.
        if daily_cap and daily >= daily_cap:
            return DispatchDecision(
                allowed=False,
                limit="daily",
                message=f"Daily budget cap reached: ${daily:.2f} spent today against a ${daily_cap:.2f} cap",
                details={"spent_usd": daily, "cap_usd": daily_cap},
            )
        if monthly_cap and monthly >= monthly_cap:
            return DispatchDecision(
                allowed=False,
                limit="monthly",
                message=(
                    f"Monthly budget cap reached: ${monthly:.2f} spent this month against a ${monthly_cap:.2f} cap"
                ),
                details={"spent_usd": monthly, "cap_usd": monthly_cap},
            )

        rate_limit = int(config.get("rate_limit_per_author_per_hour") or 0)
        if actor and rate_limit > 0 and not is_rate_limit_exempt(actor):
            since = self._clock() - timedelta(hours=1)
            try:
                dispatched = await self._spend_repo.dispatch_count_since(actor, since, excluding=excluding)
            except _TRANSIENT_DB_ERRORS:
                logger.warning("budget_gate_rate_read_failed", actor=actor, exc_info=True)
                return DispatchDecision(allowed=True)
            if dispatched >= rate_limit:
                return DispatchDecision(
                    allowed=False,
                    limit="rate_limit",
                    message=(
                        f"Rate limit reached for {actor}: {dispatched} job(s) dispatched "
                        f"in the last hour against a limit of {rate_limit}"
                    ),
                    details={"actor": actor, "dispatched": dispatched, "limit_per_hour": rate_limit},
                )

        return DispatchDecision(allowed=True)


def _positive(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0
