"""What a route needs to hand a tracker a dispatch: labels, client, context.

The Linear webhook route built these inline. Accepting a Linear draft from
the API (#460) dispatches through the same service method as that webhook,
and it must do so with the same context — the same trigger labels, ledger,
jobs repository and dashboard URL — or the two would dedupe, count and
comment differently. So the construction lives here, once, and both routes
call it.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import Request

from embry0.trackers import TriggerContext

logger = structlog.get_logger(__name__)


async def resolve_trigger_labels(request: Request) -> list[str]:
    """Effective trigger labels for this request, shared by every forge.

    Prefers the DB-backed resolver so the dashboard's Settings → Integrations
    field actually takes effect; falls back to the env-derived config when the
    repository is not wired (older test apps construct a partial app.state).
    """
    repo = getattr(request.app.state, "integration_repo", None)
    if repo is not None:
        try:
            resolved: list[str] = await repo.get_trigger_labels()
            return resolved
        except Exception:
            logger.warning("trigger_labels_db_read_failed", exc_info=True)
    config = request.app.state.config
    fallback: list[str] = list(getattr(config, "trigger_labels_list", []))
    return fallback


def adopt_linear_sync(request: Request, resolved_sync: Any | None) -> Any | None:
    """The Linear service to use, given what the provider just resolved.

    A freshly resolved service replaces the shared instance: the outbound
    write-back paths (answer_recorder, _executor_outcomes, PATCH /issues)
    read ``app.state.linear_sync`` directly and would otherwise stay pinned
    to whatever startup built from env. The tracker registry gets the fresh
    instance too — registration replaces (#160). None when Linear is not
    configured on either surface.
    """
    if resolved_sync is not None:
        request.app.state.linear_sync = resolved_sync
        trackers = getattr(request.app.state, "trackers", None)
        if trackers is not None:
            trackers.register(resolved_sync)
    return resolved_sync or getattr(request.app.state, "linear_sync", None)


async def current_linear_sync(request: Request) -> Any | None:
    """The Linear service under the credentials current right now (DB row
    over env), for a route that has no webhook signature to verify."""
    provider = getattr(request.app.state, "linear_provider", None)
    resolved_sync = (await provider.resolve())[0] if provider is not None else None
    return adopt_linear_sync(request, resolved_sync)


async def linear_trigger_context(request: Request, *, actor: str) -> TriggerContext:
    """The dispatch context for a Linear ticket. ``actor`` is the human who
    asked — the webhook's sender, or whoever accepted the draft."""
    state = request.app.state
    return TriggerContext(
        issues_repo=state.issues_repo,
        issue_executor=state.issue_executor,
        jobs_repo=getattr(state, "jobs_repo", None),
        dispatch_ledger=getattr(state, "dispatch_ledger", None),
        trigger_labels=set(await resolve_trigger_labels(request)),
        # No localhost fallback: a loopback URL is a dead link on a ticket.
        dashboard_base_url=getattr(state.config, "dashboard_public_url", "") or "",
        auto_retry=bool(getattr(getattr(state, "linear_reconciler", None), "acts", False)),
        actor=actor,
    )


async def trigger_labels_to_name(request: Request) -> list[str]:
    """The trigger labels a message names (#521); none when they cannot be read, which only shortens the message."""
    try:
        return await resolve_trigger_labels(request)
    except Exception:
        logger.warning("trigger_labels_unreadable_for_a_message", exc_info=True)
        return []
