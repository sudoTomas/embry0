"""Apply parsed answer directives to issue_inputs rows.

Translates "/answer <ref>: foo" into the same answer/skip transitions the
dashboard's POST endpoint performs, and triggers workflow resume when
the last blocking input lands.

``<ref>`` is resolved to exactly one row two ways (#253):

* a stable ``issue_inputs`` id — the canonical form, immune to the list
  shifting under the author;
* a positive integer, which means "the Nth question in the comment I am
  replying to" and is resolved through the caller-supplied
  ``resolve_sequence`` render-time mapping. It is **never** resolved against
  the live pending list: answers arriving from the dashboard, Telegram or
  MCP remove entries from that list, so position N in it can be a different
  question than the one the author read. ``resolve_sequence`` is also
  expected to refuse N outright when the issue has questions pending in more
  than one comment, since "the comment I am replying to" is then a guess.

With no ``resolve_sequence`` a positive integer is unresolvable, and the
directive is rejected rather than guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_INTEGER_RE = re.compile(r"^\d+$")


@dataclass
class InboundResult:
    applied: int = 0
    skipped: int = 0
    unmatched: int = 0
    # Positional refs the render-time mapping could not resolve. Tracked
    # separately because it is the one unmatched case the author can fix by
    # re-sending the directive with the question's id.
    unresolved_sequence: int = 0


async def _resolve(
    ref: str,
    *,
    issue_id: str,
    inputs_repo: Any,
    resolve_sequence: Callable[[int], Awaitable[dict[str, Any] | None]] | None,
) -> tuple[dict[str, Any] | None, str]:
    """(row, reason). row is None unless the ref names one pending input
    belonging to ``issue_id``."""
    if _INTEGER_RE.fullmatch(ref):
        if resolve_sequence is None:
            return None, "no_render_mapping"
        row = await resolve_sequence(int(ref))
        return (row, "ok") if row is not None else (None, "sequence_not_rendered")

    row = await inputs_repo.get(ref)
    if row is None:
        return None, "unknown_input_id"
    if row.get("issue_id") != issue_id:
        return None, "input_belongs_to_another_issue"
    if row.get("status") != "pending":
        return None, f"input_status_{row.get('status')}"
    return row, "ok"


async def apply_inbound_directives(
    *,
    issue_id: str,
    directives: list[tuple[str, str, str]],
    inputs_repo: Any,
    on_all_answered: Callable[[], Awaitable[None]],
    answered_by: str,
    record: Callable[[dict[str, Any], str], Awaitable[None]] | None = None,
    resolve_sequence: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None,
) -> InboundResult:
    """For each (reference, action, value) directive, look up the
    corresponding pending input and apply.

    on_all_answered is awaited once after the last directive if no
    blocking inputs remain pending for the issue.

    record: optional ``(input_row, answer)`` coroutine used for "answer"
    directives instead of the bare repo write — the caller binds the shared
    ``answer_recorder.record_answer`` path (RAV-1076) with its channel and
    side-effect dependencies. Falls back to ``inputs_repo.answer`` when
    absent so library users keep the minimal behavior.

    resolve_sequence: optional ``(n) -> row | None`` coroutine mapping a
    1-based position back to the input it had **at render time** on the
    channel the directive arrived from. Absent, positional refs are rejected.
    """
    result = InboundResult()

    for ref, action, value in directives:
        row, reason = await _resolve(
            ref,
            issue_id=issue_id,
            inputs_repo=inputs_repo,
            resolve_sequence=resolve_sequence,
        )
        if row is None:
            logger.warning(
                "inbound_directive_unmatched",
                issue_id=issue_id,
                reference=ref,
                reason=reason,
            )
            result.unmatched += 1
            if reason in ("no_render_mapping", "sequence_not_rendered"):
                result.unresolved_sequence += 1
            continue
        input_id = row["id"]
        if action == "answer":
            if record is not None:
                await record(row, value)
            else:
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
