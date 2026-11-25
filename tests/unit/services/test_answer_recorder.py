"""answer_recorder.record_answer — the RAV-1076 shared answer choke point.

Persists the answer, mirrors it to the Telegram question message when the
answer came from another surface, and posts the Q&A pair on the linked
tracker issue via the issue's TrackerCommentChannel (#163). Every side
effect degrades to a no-op when its dependency is absent, and tracker
failures never propagate.

The channel here is the REAL TrackerCommentChannel over a fake tracker in a
real registry, so the comment formatting stays covered end to end.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.notifications.tracker_comment import TrackerCommentChannel
from embry0.services.answer_recorder import record_answer
from embry0.trackers import TrackerIssueRef, TrackerRegistry

pytestmark = pytest.mark.asyncio

_INP = {
    "id": "inp-1",
    "issue_id": "iss-1",
    "job_id": "job-1",
    "question": "Bump the major version?",
    "asking_node": "developer",
    "telegram_message_id": 42,
    "status": "pending",
}


def _config(bot_token="t", chat_id="c"):
    cfg = MagicMock()
    cfg.telegram_bot_token = bot_token
    cfg.telegram_chat_id = chat_id
    return cfg


class _FakeTracker:
    """IssueTracker-protocol-conforming recording tracker."""

    def __init__(self, name: str = "linear") -> None:
        self._name = name
        self.posts: list[tuple[TrackerIssueRef, str]] = []

    @property
    def name(self) -> str:
        return self._name

    async def handle_webhook_event(self, payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return {"status": "ignored"}

    async def list_backlog(self, repos, *, client_for=None):
        from embry0.trackers import BacklogRead

        return BacklogRead()

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        self.posts.append((ref, body))
        return True


def _tracker_channels(name: str = "linear") -> tuple[dict[str, Any], _FakeTracker]:
    registry = TrackerRegistry()
    tracker = _FakeTracker(name)
    registry.register(tracker)
    return {name: TrackerCommentChannel(registry, name)}, tracker


async def test_persists_answer_with_actor():
    inputs_repo = AsyncMock()
    await record_answer(
        inp=_INP,
        answer="yes",
        answered_by="ops@example.com",
        channel="api",
        inputs_repo=inputs_repo,
    )
    inputs_repo.answer.assert_awaited_once_with("inp-1", answer="yes", answered_by="ops@example.com")


async def test_posts_tracker_comment_with_question_answer_and_actor():
    inputs_repo = AsyncMock()
    issues_repo = AsyncMock()
    issues_repo.get.return_value = {
        "id": "iss-1",
        "tracker": "linear",
        "tracker_key": "ENG-1",
        "tracker_native_id": "uuid-1",
    }
    channels, tracker = _tracker_channels("linear")

    await record_answer(
        inp=_INP,
        answer="yes, bump it",
        answered_by="key:hermes",
        channel="api",
        inputs_repo=inputs_repo,
        issues_repo=issues_repo,
        tracker_channels=channels,
    )

    assert len(tracker.posts) == 1
    ref, body = tracker.posts[0]
    assert ref.native_id == "uuid-1"
    assert ref.key == "ENG-1"
    assert "> Bump the major version?" in body
    assert "yes, bump it" in body
    assert "key:hermes" in body
    assert "via api" in body
    assert "job-1" in body
    assert "developer" in body


@pytest.mark.parametrize(
    "issue_row",
    [None, {"id": "iss-1", "tracker": "github", "tracker_key": "1", "tracker_native_id": None}],
    ids=["issue_gone", "not_from_this_tracker"],
)
async def test_no_tracker_comment_without_linkage(issue_row):
    issues_repo = AsyncMock()
    issues_repo.get.return_value = issue_row
    channels, tracker = _tracker_channels("linear")
    await record_answer(
        inp=_INP,
        answer="yes",
        answered_by="u",
        channel="api",
        inputs_repo=AsyncMock(),
        issues_repo=issues_repo,
        tracker_channels=channels,
    )
    assert tracker.posts == []


async def test_no_tracker_comment_for_issueless_input():
    channels, tracker = _tracker_channels("linear")
    issues_repo = AsyncMock()
    await record_answer(
        inp={**_INP, "issue_id": None},
        answer="yes",
        answered_by="u",
        channel="api",
        inputs_repo=AsyncMock(),
        issues_repo=issues_repo,
        tracker_channels=channels,
    )
    issues_repo.get.assert_not_awaited()
    assert tracker.posts == []


async def test_tracker_failure_is_swallowed():
    issues_repo = AsyncMock()
    issues_repo.get.side_effect = RuntimeError("db down")
    inputs_repo = AsyncMock()
    channels, _tracker = _tracker_channels("linear")
    await record_answer(
        inp=_INP,
        answer="yes",
        answered_by="u",
        channel="api",
        inputs_repo=inputs_repo,
        issues_repo=issues_repo,
        tracker_channels=channels,
    )  # must not raise
    inputs_repo.answer.assert_awaited_once()


async def test_cross_channel_edit_for_non_telegram_answer():
    with patch("embry0.notifications.dispatcher.edit_message_answered", new=AsyncMock()) as edit:
        await record_answer(
            inp=_INP,
            answer="yes",
            answered_by="ops@example.com",
            channel="api",
            inputs_repo=AsyncMock(),
            config=_config(),
        )
    edit.assert_awaited_once()
    assert edit.await_args.kwargs["message_id"] == 42


async def test_no_cross_channel_edit_for_telegram_answer():
    with patch("embry0.notifications.dispatcher.edit_message_answered", new=AsyncMock()) as edit:
        await record_answer(
            inp=_INP,
            answer="yes",
            answered_by="telegram:user42",
            channel="telegram",
            inputs_repo=AsyncMock(),
            config=_config(),
        )
    edit.assert_not_awaited()


async def test_cross_channel_edit_failure_is_swallowed():
    with patch(
        "embry0.notifications.dispatcher.edit_message_answered",
        new=AsyncMock(side_effect=RuntimeError("telegram down")),
    ):
        await record_answer(
            inp=_INP,
            answer="yes",
            answered_by="u",
            channel="api",
            inputs_repo=AsyncMock(),
            config=_config(),
        )  # must not raise
