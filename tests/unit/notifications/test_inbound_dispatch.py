"""Inbound dispatcher: given parsed directives + issue_id, apply each to
the matching issue_inputs row via the existing answer flow.

Since #253 a directive reference is either a stable input id (resolved
through ``inputs_repo.get``) or a position resolved through the caller's
``resolve_sequence`` render-time mapping — never an index into the live
pending list.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

_ROWS = {
    "inp-1": {"id": "inp-1", "issue_id": "iss-1", "question": "Q1?", "status": "pending"},
    "inp-2": {"id": "inp-2", "issue_id": "iss-1", "question": "Q2?", "status": "pending"},
}


def _repo(rows: dict[str, dict] | None = None, *, pending_blocking: int = 0) -> MagicMock:
    rows = _ROWS if rows is None else rows
    inputs_repo = MagicMock()
    inputs_repo.get = AsyncMock(side_effect=lambda input_id: rows.get(input_id))
    inputs_repo.answer = AsyncMock()
    inputs_repo.skip = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=pending_blocking)
    return inputs_repo


def _by_sequence(rows: dict[str, dict], order: list[str]):
    """A render-time mapping: position -> row, as one comment rendered it."""

    async def _resolve(n: int):
        if n < 1 or n > len(order):
            return None
        return rows.get(order[n - 1])

    return _resolve


@pytest.mark.asyncio
async def test_dispatch_two_answers_to_two_pending_inputs():
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo()
    resume_called = AsyncMock()

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("1", "answer", "PostgreSQL"), ("2", "answer", "yes")],
        inputs_repo=inputs_repo,
        on_all_answered=resume_called,
        answered_by="github:webhook",
        resolve_sequence=_by_sequence(_ROWS, ["inp-1", "inp-2"]),
    )

    assert inputs_repo.answer.await_count == 2
    inputs_repo.answer.assert_any_await("inp-1", answer="PostgreSQL", answered_by="github:webhook")
    inputs_repo.answer.assert_any_await("inp-2", answer="yes", answered_by="github:webhook")
    resume_called.assert_awaited_once()
    assert result.applied == 2
    assert result.skipped == 0
    assert result.unmatched == 0


@pytest.mark.asyncio
async def test_dispatch_answers_by_input_id():
    """#253: the canonical form needs no positional mapping at all."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo()

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("inp-2", "answer", "yes")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_awaited_once_with("inp-2", answer="yes", answered_by="github:webhook")
    assert result.applied == 1
    assert result.unmatched == 0


@pytest.mark.asyncio
async def test_dispatch_answer_uses_record_callback_when_provided():
    """RAV-1076: callers bind the shared record_answer path; the bare repo
    write is only the fallback. Skips still go straight to the repo."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo()
    record = AsyncMock()

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("1", "answer", "PostgreSQL"), ("2", "skip", "")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:someone",
        record=record,
        resolve_sequence=_by_sequence(_ROWS, ["inp-1", "inp-2"]),
    )

    record.assert_awaited_once_with(_ROWS["inp-1"], "PostgreSQL")
    inputs_repo.answer.assert_not_awaited()
    inputs_repo.skip.assert_awaited_once_with("inp-2", skipped_by="github:someone")
    assert result.applied == 1
    assert result.skipped == 1


@pytest.mark.asyncio
async def test_dispatch_skip_marks_input_as_skipped():
    """A /skip directive marks the input as 'skipped' status — the workflow
    can either continue past it (if non-blocking) or escalate."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo()

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("1", "skip", "")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
        resolve_sequence=_by_sequence(_ROWS, ["inp-1"]),
    )

    inputs_repo.skip.assert_awaited_once_with("inp-1", skipped_by="github:webhook")
    assert result.skipped == 1
    assert result.applied == 0


@pytest.mark.asyncio
async def test_dispatch_ignores_unknown_sequence():
    """A position the render-time mapping does not cover is skipped (logged)
    and counted as an unresolved sequence, not applied to a neighbour."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo(pending_blocking=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("99", "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
        resolve_sequence=_by_sequence(_ROWS, ["inp-1"]),
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.applied == 0
    assert result.unmatched == 1
    assert result.unresolved_sequence == 1


@pytest.mark.asyncio
async def test_positional_ref_is_rejected_without_a_render_mapping():
    """#253: no mapping means no answer. Falling back to the live pending
    list is exactly the race this fix removes."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo(pending_blocking=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("1", "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.applied == 0
    assert result.unresolved_sequence == 1


@pytest.mark.asyncio
async def test_unknown_input_id_is_rejected():
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo(pending_blocking=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("inp-nope", "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.unmatched == 1
    assert result.unresolved_sequence == 0


@pytest.mark.asyncio
async def test_input_id_from_another_issue_is_rejected():
    """The id is global; the comment is not. A directive on issue A must not
    reach issue B's question."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    rows = {"inp-9": {"id": "inp-9", "issue_id": "iss-OTHER", "status": "pending"}}
    inputs_repo = _repo(rows, pending_blocking=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("inp-9", "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.unmatched == 1


@pytest.mark.asyncio
async def test_already_answered_input_id_is_rejected():
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    rows = {"inp-1": {"id": "inp-1", "issue_id": "iss-1", "status": "answered"}}
    inputs_repo = _repo(rows, pending_blocking=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("inp-1", "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.unmatched == 1


@pytest.mark.asyncio
async def test_dispatch_does_not_resume_when_blocking_questions_remain():
    """If after applying directives there are still pending blocking inputs,
    on_all_answered is NOT called."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo(pending_blocking=1)

    resume_called = AsyncMock()
    await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("1", "answer", "yes")],
        inputs_repo=inputs_repo,
        on_all_answered=resume_called,
        answered_by="telegram:user",
        resolve_sequence=_by_sequence(_ROWS, ["inp-1", "inp-2"]),
    )
    resume_called.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("through", ["the record callback", "the bare repository write"])
async def test_an_answer_another_one_beat_to_its_question_is_not_counted_as_applied(through):
    """#499: the reply read the question pending, but another answer was written first: it was not applied."""
    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = _repo()
    inputs_repo.answer = AsyncMock(return_value=False)
    record = AsyncMock(return_value=False) if through == "the record callback" else None

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[("inp-1", "answer", "PostgreSQL")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:someone",
        record=record,
    )

    assert (result.applied, getattr(result, "already_answered", None)) == (0, 1), (
        f"an answer that was not taken counted as applied ({through}): {result}"
    )
