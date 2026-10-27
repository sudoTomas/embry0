"""Hand-offs (spec 2026-10-27): a decision sent to the teammate better placed to make it.

The recipient of an open hand-off may take every action the decision lists,
for that decision only. Whoever answers the decision (the recipient, the
sender, an owner) closes its open hand-off as ``decided`` with the outcome;
:func:`settle` is called from every answer path and never fails the answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def assignee(row: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """An open hand-off as a decision's ``assigned_to``: who has it, from whom, and the note."""
    email = str(row.get("to_email") or "").lower()
    person = people.get(email) or {}
    created = row.get("created_at")
    return {
        "handoff_id": row.get("id"),
        "email": email,
        "name": str(person.get("name") or "") or email,
        "github_login": person.get("github_login"),
        "from": row.get("from_actor"),
        "note": row.get("note") or "",
        "at": created.isoformat() if isinstance(created, datetime) else created,
    }


def view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(row).items()}


async def settle(state: Any, decision_id: str, outcome: str, actor: str) -> dict[str, Any] | None:
    """Close ``decision_id``'s open hand-off as decided with ``outcome``; tell its sender. Never raises."""
    repo = getattr(state, "handoffs_repo", None)
    if repo is None:
        return None
    try:
        closed: dict[str, Any] | None = await repo.close(
            decision_id=decision_id, state="decided", outcome=outcome, by=actor, now=datetime.now(UTC)
        )
    except Exception:  # noqa: BLE001 - an answer stands whatever happens to its hand-off
        logger.warning("handoff_settle_failed", decision_id=decision_id, exc_info=True)
        return None
    if closed is not None:
        notify(state, "handoff.closed", closed)
        logger.info("handoff_decided", decision_id=decision_id, outcome=outcome, by=actor)
    return closed


def notify(state: Any, event: str, row: Mapping[str, Any]) -> None:
    """``handoff.created`` or ``handoff.closed`` to the event webhook, for a chat to tell the person."""
    notifier = getattr(state, "webhook_notifier", None)
    if notifier is None:
        return
    try:
        notifier.emit_soon(event, view(row))
    except Exception:  # noqa: BLE001 - a missed notice never undoes the hand-off
        logger.warning("handoff_notify_failed", event=event, exc_info=True)
