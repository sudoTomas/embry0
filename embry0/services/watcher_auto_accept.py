"""Auto-accept of watcher drafts (spec 2026-10-25): a stack's bar for starting a fix with no person's yes.

A watcher draft waits at stage ``tracked`` until a person accepts it. On a
stack whose ``auto_accept`` setting is on, the watcher's tick accepts the
drafts that clear the stack's bar, through the path ``POST /cases/{id}/accept``
takes, recorded under :data:`~embry0.orchestration.dispatch_actors.AUTO_ACCEPT`:

- it has waited ``auto_accept_after_hours`` since it was filed, so a person
  can cancel it first (the tick reconciles cancellations before it asks);
- its severity is at least ``auto_accept_min_severity``;
- its signature was seen at least ``auto_accept_min_occurrences`` times;
- it is not a retry after a fix that did not heal (a person decides those);
- no owner declined or snoozed it as a proposal;
- fewer than ``auto_accept_max_per_day`` drafts were auto-accepted in the last
  24 hours.

The spend caps and the per-author hourly limit still apply to the jobs it
starts: the actor is not exempt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Severity order; a draft without one is below every bar.
SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class AutoAcceptBar:
    after_hours: float = 24.0
    min_severity: str = "high"
    min_occurrences: int = 10
    max_per_day: int = 2

    @classmethod
    def from_config(cls, config: Any) -> AutoAcceptBar:
        """The bar a stack's config (its overrides over the deployment's) sets."""
        default = cls()
        return cls(
            after_hours=float(getattr(config, "watcher_auto_accept_after_hours", default.after_hours)),
            min_severity=str(getattr(config, "watcher_auto_accept_min_severity", default.min_severity)).strip().lower(),
            min_occurrences=int(getattr(config, "watcher_auto_accept_min_occurrences", default.min_occurrences)),
            max_per_day=int(getattr(config, "watcher_auto_accept_max_per_day", default.max_per_day)),
        )


def why_not(case: Mapping[str, Any], bar: AutoAcceptBar, now: datetime) -> str | None:
    """Why ``case`` does not clear ``bar``, or None when it does."""
    if case.get("origin") != "watcher" or str(case.get("stage")) != "tracked":
        return "not a tracked watcher draft"
    if case.get("issue_id"):
        return "a retry after a fix that did not heal waits for a person"
    filed = case.get("tracked_at") or case.get("detected_at")
    if filed is None or now - filed < timedelta(hours=bar.after_hours):
        return "still inside the grace period"
    severity = SEVERITY_RANK.get(str(case.get("severity") or "").strip().lower(), -1)
    if severity < SEVERITY_RANK.get(bar.min_severity, SEVERITY_RANK["high"]):
        return f"severity {case.get('severity') or 'unset'} is below {bar.min_severity}"
    if int(case.get("occurrence_count") or 0) < bar.min_occurrences:
        return f"seen {int(case.get('occurrence_count') or 0)} times, fewer than {bar.min_occurrences}"
    return None


async def auto_accept(
    *,
    candidates: Sequence[Mapping[str, Any]],
    bar: AutoAcceptBar,
    accepted_today: int,
    answered: Callable[[str], bool],
    accept: Callable[[str], Awaitable[str]],
    now: datetime,
) -> list[dict[str, str]]:
    """Accept the candidates that clear ``bar``, oldest first, while the day's cap has room.

    ``answered`` says whether an owner already answered the case's proposal
    (``case:<id>``); ``accept`` starts the case's job and returns its id. One
    acceptance that fails is logged and the rest are still tried.
    """
    room = bar.max_per_day - accepted_today
    done: list[dict[str, str]] = []
    oldest_first = sorted(candidates, key=lambda c: c.get("tracked_at") or c.get("detected_at") or now)
    for case in oldest_first:
        if room <= 0:
            break
        if why_not(case, bar, now) is not None or answered(f"case:{case['id']}"):
            continue
        try:
            job_id = await accept(str(case["id"]))
        except Exception as exc:  # noqa: BLE001 - one draft's refusal is its own
            logger.warning(
                "watcher_auto_accept_refused", case_id=case["id"], error=str(getattr(exc, "detail", exc))[:300]
            )
            continue
        done.append({"case_id": str(case["id"]), "job_id": job_id})
        room -= 1
    return done
