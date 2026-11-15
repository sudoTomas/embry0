"""GitHub webhook ingestion of /answer directives in issue_comment events.

These tests drive the FastAPI handler in ``embry0/api/v1/webhooks.py``
through ``httpx.AsyncClient`` against an in-memory ASGI app whose
``app.state`` repos are mocks. ``webhook_dev_mode=True`` plus an empty
secret bypasses HMAC verification (smee.io flow), so the tests focus on
the routing logic — payload → authorship gate → reference resolution →
dispatcher → resume — not on signature handling (covered separately in
``test_webhooks.py``).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

# Any association the default allowlist accepts. Every pre-#253 test payload
# implicitly came from an owner; the gate now makes that explicit.
TRUSTED = "OWNER"


def _make_app(*, issue: dict | None, rendered: list[dict] | None = None):
    """Build a test app with mocked repos.

    ``issue`` is what ``issues_repo.get_by_tracker_ref`` returns (or None to
    simulate an untracked repo/issue). ``rendered`` seeds issue_inputs rows
    carrying their #253 render mapping (``github_comment_id`` /
    ``github_comment_seq``), which is what ``/answer N:`` resolves against.
    """
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        github_webhook_secret="",  # dev_mode + empty secret = signature skipped
    )
    app = create_app(config)

    issues_repo = MagicMock()
    issues_repo.get_by_tracker_ref = AsyncMock(return_value=issue)
    issues_repo.get = AsyncMock(return_value=issue)

    rows: list[dict[str, Any]] = list(rendered or [])

    async def _get(input_id: str) -> dict[str, Any] | None:
        return next((r for r in rows if r["id"] == input_id), None)

    async def _get_by_sequence(issue_id: str, sequence: int) -> dict[str, Any] | None:
        # Mirrors IssueInputsRepository.get_by_github_comment_sequence: the
        # positional form resolves only while exactly one comment still has
        # questions waiting, and nothing blocking is left unmapped (#253).
        pending = [r for r in rows if r.get("issue_id") == issue_id and r.get("status") == "pending"]
        mapped = {r["github_comment_id"] for r in pending if r.get("github_comment_id")}
        if len(mapped) != 1:
            return None
        if any(not r.get("github_comment_id") and r.get("importance", "blocking") == "blocking" for r in pending):
            return None
        return next((r for r in pending if r.get("github_comment_seq") == sequence), None)

    inputs_repo = MagicMock()
    inputs_repo.get = AsyncMock(side_effect=_get)
    inputs_repo.get_by_github_comment_sequence = AsyncMock(side_effect=_get_by_sequence)
    inputs_repo.answer = AsyncMock()
    inputs_repo.skip = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)

    executor = MagicMock()
    executor.resume_for_issue = AsyncMock()

    github_sync = MagicMock()
    github_sync.handle_webhook_event = AsyncMock(return_value={"status": "ignored"})

    comment_channel = MagicMock()
    comment_channel.post_comment = AsyncMock(return_value={"id": 1})

    app.state.issues_repo = issues_repo
    app.state.inputs_repo = inputs_repo
    app.state.issue_executor = executor
    app.state.github_sync = github_sync
    app.state.github_comment_channel = comment_channel
    app.state.jobs_repo = MagicMock()
    return app, issues_repo, inputs_repo, executor, comment_channel


async def _post(
    app,
    body: str,
    *,
    association: str | None = TRUSTED,
    login: str = "octocat",
    number: int = 42,
    user_type: str = "User",
):
    comment: dict[str, Any] = {"body": body, "user": {"login": login, "type": user_type}}
    if association is not None:
        comment["author_association"] = association
    payload = {
        "action": "created",
        "comment": comment,
        "issue": {"number": number},
        "repository": {"full_name": "owner/repo"},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/v1/webhook",
            content=json.dumps(payload).encode(),
            headers={"X-GitHub-Event": "issue_comment", "Content-Type": "application/json"},
        )


def _pending(input_id: str, seq: int, *, comment_id: int = 100, status: str = "pending") -> dict[str, Any]:
    return {
        "id": input_id,
        "issue_id": "iss-test-gh-inbound",
        "question": f"Q{seq}?",
        "status": status,
        "github_comment_id": comment_id,
        "github_comment_seq": seq,
    }


ISSUE = {"id": "iss-test-gh-inbound", "status": "awaiting_input"}


@pytest.mark.asyncio
async def test_issue_comment_with_answer_directive_applies_to_issue():
    """GitHub posts an issue_comment with /answer 1: yes — the question that
    was rendered first in the newest question comment gets answered and the
    workflow is resumed because no blocking inputs remain."""
    app, issues_repo, inputs_repo, executor, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer 1: yes from github")

    assert r.status_code == 200, r.text
    assert r.json().get("applied") == 1
    issues_repo.get_by_tracker_ref.assert_awaited_once_with("github", "owner/repo", "42")
    inputs_repo.answer.assert_awaited_once_with("inp-gh1", answer="yes from github", answered_by="github:octocat")
    executor.resume_for_issue.assert_awaited_once_with("iss-test-gh-inbound")


@pytest.mark.asyncio
async def test_issue_comment_answer_by_input_id():
    """#253 canonical form: the id is immune to the list shifting."""
    app, _, inputs_repo, executor, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1), _pending("inp-gh2", 2)])

    r = await _post(app, "/answer inp-gh2: sqlite please")

    assert r.status_code == 200, r.text
    assert r.json().get("applied") == 1
    inputs_repo.answer.assert_awaited_once_with("inp-gh2", answer="sqlite please", answered_by="github:octocat")
    executor.resume_for_issue.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", None])
async def test_answer_from_untrusted_author_records_nothing(association):
    """#253: answering unblocks someone else's pipeline. Anyone who can
    comment on a public repo must not be able to do that."""
    app, issues_repo, inputs_repo, executor, channel = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer 1: drop the database", association=association)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "applied": 0, "reason": "author not allowed"}
    inputs_repo.answer.assert_not_awaited()
    inputs_repo.skip.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()
    # Rejected before any lookup, and with no reply that would advertise the
    # pending question to a drive-by commenter.
    issues_repo.get_by_tracker_ref.assert_not_awaited()
    channel.post_comment.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("association", ["COLLABORATOR", "MEMBER", "OWNER", "owner"])
async def test_answer_from_trusted_author_records(association):
    app, _, inputs_repo, executor, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer 1: yes", association=association)

    assert r.status_code == 200, r.text
    assert r.json().get("applied") == 1
    inputs_repo.answer.assert_awaited_once_with("inp-gh1", answer="yes", answered_by="github:octocat")
    executor.resume_for_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_from_an_untrusted_author_records_nothing():
    """/skip is the same privilege as /answer — it lets the pipeline run on
    without the answer — and goes through the same gate."""
    app, _, inputs_repo, executor, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/skip 1", association="NONE")

    assert r.json() == {"ok": True, "applied": 0, "reason": "author not allowed"}
    inputs_repo.skip.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_from_a_bot_is_ignored():
    """A bot answering the question a bot asked is a loop, not an answer —
    even when the bot's association is in the allowlist."""
    app, _, inputs_repo, _, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer 1: yes", login="embry0[bot]")

    assert r.json() == {"ok": True, "applied": 0, "reason": "author not allowed"}
    inputs_repo.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_from_a_user_typed_bot_is_ignored():
    """The `[bot]` suffix is a convention; `user.type` is what GitHub sets."""
    app, _, inputs_repo, _, _ = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer 1: yes", login="embry0-ci", user_type="Bot")

    assert r.json() == {"ok": True, "applied": 0, "reason": "author not allowed"}
    inputs_repo.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_one_from_github_after_dashboard_answered_it_does_not_hit_question_two():
    """#253 acceptance: question 1 was answered from the dashboard, so it is
    no longer pending. "/answer 1:" must resolve to nothing — NOT to the row
    that is now first in the live pending list (question 2)."""
    app, _, inputs_repo, executor, channel = _make_app(
        issue=ISSUE,
        rendered=[
            _pending("inp-gh1", 1, status="answered"),  # answered from the dashboard
            _pending("inp-gh2", 2),
        ],
    )

    r = await _post(app, "/answer 1: postgres")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 0 and body["unmatched"] == 1
    inputs_repo.answer.assert_not_awaited()
    executor.resume_for_issue.assert_awaited_once()  # count_pending_blocking is mocked to 0
    # The author is told how to retry rather than left guessing, by the id the
    # question comment now prints under each question.
    channel.post_comment.assert_awaited_once()
    reply = channel.post_comment.await_args.args[2]
    assert "/answer inp-" in reply
    assert "dashboard" in reply


@pytest.mark.asyncio
async def test_positional_answer_is_refused_when_two_comments_are_outstanding():
    """Comment 100's question is still pending when comment 200 asks another.
    GitHub gives issue comments no in_reply_to, so "/answer 1:" could mean
    either — it must be refused and replied to, never silently applied."""
    app, _, inputs_repo, _, channel = _make_app(
        issue=ISSUE,
        rendered=[
            _pending("inp-old", 1, comment_id=100),
            _pending("inp-new", 1, comment_id=200),
        ],
    )

    r = await _post(app, "/answer 1: postgres")

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 0 and r.json()["unmatched"] == 1
    inputs_repo.answer.assert_not_awaited()
    channel.post_comment.assert_awaited_once()

    # The id is the way out, and it still works.
    channel.post_comment.reset_mock()
    r = await _post(app, "/answer inp-new: postgres")
    assert r.json()["applied"] == 1
    inputs_repo.answer.assert_awaited_once_with("inp-new", answer="postgres", answered_by="github:octocat")
    channel.post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_positional_answer_against_an_unrendered_issue_is_rejected():
    """Pre-#253 question comments carry no render mapping; N is unresolvable
    and must not fall back to the live pending list."""
    app, _, inputs_repo, _, channel = _make_app(
        issue=ISSUE,
        rendered=[{"id": "inp-old", "issue_id": ISSUE["id"], "status": "pending"}],
    )

    r = await _post(app, "/answer 1: postgres")

    assert r.json()["applied"] == 0
    inputs_repo.answer.assert_not_awaited()
    channel.post_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_input_id_is_rejected():
    app, _, inputs_repo, _, channel = _make_app(issue=ISSUE, rendered=[_pending("inp-gh1", 1)])

    r = await _post(app, "/answer inp-doesnotexist: postgres")

    body = r.json()
    assert body["applied"] == 0 and body["unmatched"] == 1
    inputs_repo.answer.assert_not_awaited()
    # An unknown id is a typo, not a stale numbering — no retry advice needed.
    channel.post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_id_belonging_to_another_issue_is_rejected():
    app, _, inputs_repo, _, _ = _make_app(
        issue=ISSUE,
        rendered=[{"id": "inp-other", "issue_id": "iss-somewhere-else", "status": "pending"}],
    )

    r = await _post(app, "/answer inp-other: postgres")

    assert r.json()["applied"] == 0
    inputs_repo.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_comment_without_directive_is_ignored():
    """A comment that doesn't include /answer is ignored (no error, no change).
    The authorship gate never fires for ordinary chatter."""
    app, _, inputs_repo, executor, _ = _make_app(issue={"id": "iss-test-gh-noop", "status": "open"})

    r = await _post(app, "thanks for the heads up", association=None, number=43)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "applied": 0, "reason": "no directives"}
    inputs_repo.answer.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_comment_for_unknown_repo_is_ignored():
    """If the (repo, github_number) isn't tracked in embry0, the webhook is a no-op."""
    app, issues_repo, inputs_repo, executor, _ = _make_app(issue=None)

    r = await _post(app, "/answer 1: yes", number=1)

    assert r.status_code == 200
    assert r.json().get("applied", 0) == 0
    issues_repo.get_by_tracker_ref.assert_awaited_once_with("github", "owner/repo", "1")
    inputs_repo.answer.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_answer_to_a_question_of_a_job_that_ended_is_not_applied():
    """#521: the question's job was cancelled (its issue closed, then reopened); the reply records nothing."""
    row = {**_pending("inp-gh1", 1), "job_id": "job-1"}
    app, _, inputs_repo, executor, _ = _make_app(issue=ISSUE, rendered=[row])
    app.state.jobs_repo = MagicMock(get=AsyncMock(return_value={"job_id": "job-1", "status": "cancelled"}))

    r = await _post(app, "/answer inp-gh1: monotonic")

    assert r.status_code == 200, r.text
    assert (r.json().get("applied"), r.json().get("job_ended")) == (0, 1), f"an ended job's answer: {r.json()}"
    inputs_repo.answer.assert_not_awaited()
