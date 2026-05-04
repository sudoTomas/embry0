"""Inbound dispatcher: given parsed directives + issue_id, apply each to
the matching issue_inputs row via the existing answer flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_dispatch_two_answers_to_two_pending_inputs():
    from athanor.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = MagicMock()
    inputs_repo.list_pending_for_issue = AsyncMock(
        return_value=[
            {"id": "inp-1", "question": "Q1?", "status": "pending"},
            {"id": "inp-2", "question": "Q2?", "status": "pending"},
        ]
    )
    inputs_repo.answer = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)

    resume_called = AsyncMock()

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[(1, "answer", "PostgreSQL"), (2, "answer", "yes")],
        inputs_repo=inputs_repo,
        on_all_answered=resume_called,
        answered_by="github:webhook",
    )

    assert inputs_repo.answer.await_count == 2
    inputs_repo.answer.assert_any_await("inp-1", answer="PostgreSQL", answered_by="github:webhook")
    inputs_repo.answer.assert_any_await("inp-2", answer="yes", answered_by="github:webhook")
    resume_called.assert_awaited_once()
    assert result.applied == 2
    assert result.skipped == 0
    assert result.unmatched == 0


@pytest.mark.asyncio
async def test_dispatch_skip_marks_input_as_skipped():
    """A /skip N directive marks the input as 'skipped' status — the workflow
    can either continue past it (if non-blocking) or escalate."""
    from athanor.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = MagicMock()
    inputs_repo.list_pending_for_issue = AsyncMock(
        return_value=[
            {"id": "inp-1", "question": "Q1?", "status": "pending"},
        ]
    )
    inputs_repo.answer = AsyncMock()
    inputs_repo.skip = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[(1, "skip", "")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.skip.assert_awaited_once_with("inp-1", skipped_by="github:webhook")
    assert result.skipped == 1
    assert result.applied == 0


@pytest.mark.asyncio
async def test_dispatch_ignores_unknown_sequence():
    """A directive with a sequence > pending count is silently skipped (logged)."""
    from athanor.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = MagicMock()
    inputs_repo.list_pending_for_issue = AsyncMock(
        return_value=[
            {"id": "inp-1", "question": "Q1?", "status": "pending"},
        ]
    )
    inputs_repo.answer = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=1)

    result = await apply_inbound_directives(
        issue_id="iss-1",
        directives=[(99, "answer", "x")],
        inputs_repo=inputs_repo,
        on_all_answered=AsyncMock(),
        answered_by="github:webhook",
    )

    inputs_repo.answer.assert_not_awaited()
    assert result.applied == 0
    assert result.unmatched == 1


@pytest.mark.asyncio
async def test_dispatch_does_not_resume_when_blocking_questions_remain():
    """If after applying directives there are still pending blocking inputs,
    on_all_answered is NOT called."""
    from athanor.notifications.inbound_dispatch import apply_inbound_directives

    inputs_repo = MagicMock()
    inputs_repo.list_pending_for_issue = AsyncMock(
        return_value=[
            {"id": "inp-1", "question": "Q1?", "status": "pending"},
            {"id": "inp-2", "question": "Q2?", "status": "pending"},
        ]
    )
    inputs_repo.answer = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=1)  # one still pending

    resume_called = AsyncMock()
    await apply_inbound_directives(
        issue_id="iss-1",
        directives=[(1, "answer", "yes")],
        inputs_repo=inputs_repo,
        on_all_answered=resume_called,
        answered_by="telegram:user",
    )
    resume_called.assert_not_awaited()
