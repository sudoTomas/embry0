"""Apply parsed answer directives to issue_inputs rows.

Translates "/answer N: foo" into the same answer/skip transitions the
dashboard's POST endpoint performs, and triggers workflow resume when
the last blocking input lands.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class InboundResult:
    applied: int = 0
    skipped: int = 0
    unmatched: int = 0


async def apply_inbound_directives(
    *,
    issue_id: str,
    directives: list[tuple[int, str, str]],
    inputs_repo: Any,
    on_all_answered: Callable[[], Awaitable[None]],
    answered_by: str,
) -> InboundResult:
    """For each (sequence, action, value) directive, look up the
    corresponding pending input by 1-based sequence and apply.

    on_all_answered is awaited once after the last directive if no
    blocking inputs remain pending for the issue.
    """
    pending = await inputs_repo.list_pending_for_issue(issue_id)
    result = InboundResult()

    for seq, action, value in directives:
        if seq < 1 or seq > len(pending):
            logger.warning(
                "inbound_directive_unmatched",
                issue_id=issue_id,
                sequence=seq,
                pending_count=len(pending),
            )
            result.unmatched += 1
            continue
        row = pending[seq - 1]
        input_id = row["id"]
        if action == "answer":
            await inputs_repo.answer(input_id, answer=value, answered_by=answered_by)
            result.applied += 1
        elif action == "skip":
            await inputs_repo.skip(input_id, skipped_by=answered_by)
            result.skipped += 1
        else:
            logger.warning("inbound_unknown_action", action=action, issue_id=issue_id)

    # Re-check after all directives — resume if no more blocking inputs
    pending_blocking = await inputs_repo.count_pending_blocking(issue_id)
    if pending_blocking == 0:
        await on_all_answered()

    return result
