"""The account pre-check's half of provider fallback (spec 2026-10-25).

For each provider a job resolves, ask whether it is paused; for each paused
one, pick the first model of the fallback order whose provider is ready. The
agent runs apply the plan (``embry0.agents.fallback.substitute``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog

from embry0.agents.fallback import (
    ACCOUNT_PAUSE_REASONS,
    ModelFallback,
    Pause,
    candidates,
    fallback_order,
    model_provider,
    runnable_fallback_models,
)

logger = structlog.get_logger(__name__)

# How long a run's own account failure pauses its provider for the job: the
# quarantine window CredentialHealth gives the same code, read off the config.
_WINDOW_ATTRS: dict[str, str] = {
    "rate_limited": "credential_rate_limit_quarantine_seconds",
    "quota_exhausted": "credential_quota_quarantine_seconds",
}


class _PauseReader(Protocol):
    async def job_pinned(self, job_id: str, provider: str) -> bool: ...

    async def llm_pause(self, repo: str, provider: str) -> Pause | None: ...


def job_account_failures(traces: Iterable[Mapping[str, Any]], config: Any, now: datetime) -> dict[str, Pause]:
    """The providers whose latest run of the job ended on a spent or rate-limited account, still inside its window.

    ``traces`` are the job's trace rows, newest first. The account behind such
    a run is quarantined only when the job finishes, so a resume, a relaunch
    or a retry of the job would otherwise run on it again.
    """
    seen: set[str] = set()
    paused: dict[str, Pause] = {}
    for row in traces:
        model = row.get("model")
        if not isinstance(model, str) or not model:
            continue
        provider = model_provider(model)
        if provider in seen:
            continue
        seen.add(provider)
        reason = ACCOUNT_PAUSE_REASONS.get(str(row.get("error_code") or ""))
        at = row.get("created_at")
        if reason is None or not isinstance(at, datetime):
            continue
        seconds = int(getattr(config, _WINDOW_ATTRS[reason], 0) or 0)
        until = at + timedelta(seconds=seconds)
        if until > now:
            paused[provider] = Pause(provider, reason, until)
    return paused


async def plan_model_fallbacks(
    resolver: _PauseReader,
    repo: str,
    providers: Iterable[str],
    *,
    job_id: str,
    model_policy: Mapping[str, Any] | None,
    job_failures: Mapping[str, Pause] | None = None,
) -> dict[str, ModelFallback]:
    """``{paused provider: the model its agents run on instead}``; empty when the order is.

    A provider the job pinned with Run with is never paused: the person's key
    runs or the job fails, as without a fallback order. A paused provider
    whose order offers no ready model stays as it is.
    """
    order = runnable_fallback_models(fallback_order(model_policy), (model_policy or {}).get("disabled_models") or ())
    if not order:
        return {}
    failures = dict(job_failures or {})
    cache: dict[str, Pause | None] = {}

    async def pause_of(provider: str) -> Pause | None:
        if provider not in cache:
            if await resolver.job_pinned(job_id, provider):
                cache[provider] = None
            else:
                cache[provider] = failures.get(provider) or await resolver.llm_pause(repo, provider)
        return cache[provider]

    paused = {p: pause for p in providers if (pause := await pause_of(p)) is not None}
    plan: dict[str, ModelFallback] = {}
    for provider, pause in paused.items():
        for model, other in candidates(provider, order, skip=paused):
            if await pause_of(other) is None:
                plan[provider] = ModelFallback.for_pause(pause, model)
                break
        else:
            logger.warning("provider_fallback_none_ready", job_id=job_id, provider=provider, reason=pause.reason)
            continue
        logger.info(
            "provider_fallback_planned",
            job_id=job_id,
            paused_provider=provider,
            reason=pause.reason,
            until=plan[provider].until,
            fallback_model=plan[provider].model,
        )
    return plan
