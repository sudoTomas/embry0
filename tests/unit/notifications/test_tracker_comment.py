"""TrackerCommentChannel — tracker write-back formats + registry wiring (#163).

The one channel class serves every tracker: it owns the three message formats
(questions asked / answer recorded / job outcome) and delegates transport to
the tracker's ``post_issue_comment``. The fake tracker conforms to the
IssueTracker protocol so it registers in a REAL TrackerRegistry — the tests
pin the call-time registry resolution, not a mocked one.
"""

from __future__ import annotations

from typing import Any

from embry0.notifications.tracker_comment import TrackerCommentChannel
from embry0.trackers import TrackerIssueRef, TrackerRegistry


class _FakeTracker:
    """Protocol-conforming recording tracker."""

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


def _channel(name: str = "linear") -> tuple[TrackerCommentChannel, _FakeTracker]:
    registry = TrackerRegistry()
    tracker = _FakeTracker(name)
    registry.register(tracker)
    return TrackerCommentChannel(registry, name), tracker


def _issue(tracker: str = "linear", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "iss-1",
        "tracker": tracker,
        "tracker_key": "EMB-48",
        "tracker_native_id": "uuid-1",
        "repo": "o/r",
    }
    row.update(overrides)
    return row


def test_name_property():
    channel, _ = _channel("gitlab")
    assert channel.name == "gitlab"


# ---- post_outcome formatting (moved from LinearSyncService.post_job_outcome) --


async def test_post_outcome_formats_terminal_comment():
    channel, tracker = _channel("linear")

    await channel.post_outcome(
        _issue(),
        {"job_id": "job-9", "status": "completed", "pr_url": "https://github.com/o/r/pull/5", "total_cost_usd": 2.5},
    )

    assert len(tracker.posts) == 1
    ref, body = tracker.posts[0]
    assert ref.native_id == "uuid-1"
    assert ref.key == "EMB-48"
    assert "job-9" in body
    assert "completed" in body
    assert "Pull request: https://github.com/o/r/pull/5" in body
    # The ticket may be readable company-wide: spend stays on the dashboard.
    assert "$" not in body and "2.5" not in body

    # Issues without a matching tracker origin are a silent no-op.
    tracker.posts.clear()
    await channel.post_outcome({"id": "iss-x"}, {"job_id": "j", "status": "failed"})
    assert tracker.posts == []


async def test_post_outcome_gitlab_says_merge_request():
    channel, tracker = _channel("gitlab")

    await channel.post_outcome(
        _issue(tracker="gitlab"),
        {"job_id": "job-9", "status": "completed", "pr_url": "https://gitlab.com/o/r/-/merge_requests/5"},
    )

    _, body = tracker.posts[0]
    assert "Merge request: https://gitlab.com/o/r/-/merge_requests/5" in body
    assert "Pull request" not in body


async def test_post_outcome_falls_back_to_result_summary_without_pr():
    channel, tracker = _channel("linear")

    await channel.post_outcome(
        _issue(),
        {"job_id": "job-9", "status": "completed", "result_summary": "Report: 3 flaky tests quarantined."},
    )

    _, body = tracker.posts[0]
    assert "Result:" in body
    assert "Report: 3 flaky tests quarantined." in body
    assert "Pull request" not in body


async def test_post_outcome_states_a_safe_error_not_the_exception_text():
    """jobs.error_message is str(exc) on most failure paths — tokens, paths, figures."""
    channel, tracker = _channel("linear")

    await channel.post_outcome(
        _issue(),
        {
            "job_id": "job-9",
            "status": "failed",
            "error_code": "ERR_SANDBOX_INIT",
            "error_message": "docker run failed: /home/operator/repos/x token=ghp_abc123 cost $4.10",
        },
    )

    _, body = tracker.posts[0]
    assert "failed" in body
    assert "Error: `ERR_SANDBOX_INIT`" in body and "sandbox" in body.lower()
    for leaked in ("ghp_abc123", "/home/operator", "$4.10", "docker run failed"):
        assert leaked not in body
    assert "job-9" in body and "dashboard" in body


async def test_post_outcome_error_without_a_known_code_is_still_safe():
    channel, tracker = _channel("gitlab")

    await channel.post_outcome(
        _issue("gitlab", tracker_key="7", tracker_native_id=None),
        {"job_id": "job-9", "status": "failed", "error_code": "ERR_BRAND_NEW", "error_message": "Traceback: boom"},
    )
    await channel.post_outcome(
        _issue("gitlab", tracker_key="7", tracker_native_id=None),
        {"job_id": "job-9", "status": "failed", "error_message": "Traceback: boom"},
    )

    for _, body in tracker.posts:
        assert "Traceback" not in body and "boom" not in body
    assert "`ERR_BRAND_NEW`" in tracker.posts[0][1]
    assert "`ERR_UNKNOWN`" in tracker.posts[1][1]


def test_every_error_code_has_a_public_sentence_free_of_figures():
    from embry0.notifications.public_errors import public_error_message
    from embry0.safety.error_codes import ErrorCode

    for code in ErrorCode:
        sentence = public_error_message(code.value)
        assert sentence and "$" not in sentence and "/" not in sentence, code


def test_a_gate_refusal_says_the_job_never_started():
    """#331: refused-at-the-gate has its own code; the ticket must not read as
    if work was done and then cut off."""
    from embry0.notifications.public_errors import public_error_message

    refused = public_error_message("ERR_BUDGET_REFUSED")
    assert refused == "a spend or dispatch limit had already been reached, so the job was not started"
    assert refused != public_error_message("ERR_BUDGET_OVERRUN")


# ---- _ref gating -------------------------------------------------------------


async def test_no_post_for_wrong_tracker_issue():
    channel, tracker = _channel("linear")
    await channel.post_outcome(_issue(tracker="github"), {"job_id": "j", "status": "completed"})
    assert tracker.posts == []


async def test_no_post_without_key_or_native_id():
    channel, tracker = _channel("linear")
    await channel.post_outcome(
        _issue(tracker_key=None, tracker_native_id=None),
        {"job_id": "j", "status": "completed"},
    )
    assert tracker.posts == []


async def test_no_post_for_none_issue():
    channel, tracker = _channel("linear")
    await channel.post_outcome(None, {"job_id": "j", "status": "completed"})
    await channel.post_answer(None, {"id": "inp-1"}, "yes", answered_by="u", channel="api")
    assert tracker.posts == []


async def test_unregistered_tracker_is_a_silent_no_op():
    channel = TrackerCommentChannel(TrackerRegistry(), "linear")
    # Nothing registered under the name — must not raise.
    await channel.post_outcome(_issue(), {"job_id": "j", "status": "completed"})


# ---- call-time registry resolution -------------------------------------------


async def test_tracker_resolves_through_registry_at_call_time():
    """Re-registration (fresh credentials per webhook delivery) must be
    visible to an already-constructed channel — resolution happens per post,
    never at construction."""
    registry = TrackerRegistry()
    first = _FakeTracker("linear")
    registry.register(first)
    channel = TrackerCommentChannel(registry, "linear")

    await channel.post_outcome(_issue(), {"job_id": "j-1", "status": "completed"})
    assert len(first.posts) == 1

    replacement = _FakeTracker("linear")
    registry.register(replacement)

    await channel.post_outcome(_issue(), {"job_id": "j-2", "status": "completed"})
    assert len(first.posts) == 1  # the stale instance saw nothing new
    assert len(replacement.posts) == 1
    assert "j-2" in replacement.posts[0][1]


# ---- dispatch (blocking-questions comment) ------------------------------------


async def test_dispatch_posts_questions_comment():
    channel, tracker = _channel("linear")

    await channel.dispatch(
        _issue(),
        [
            {"input_id": "inp-1", "question": "Bump the major version?", "job_id": "job-7", "asking_node": "developer"},
            {"input_id": "inp-2", "question": "Drop py38?", "job_id": "job-7", "asking_node": "developer"},
        ],
    )

    assert len(tracker.posts) == 1
    _, body = tracker.posts[0]
    assert "embry0 needs input" in body
    assert "job-7" in body
    assert "developer" in body
    assert "> Bump the major version?" in body
    assert "> Drop py38?" in body
    # The how-to-answer instructions line.
    assert "answers are recorded here" in body


async def test_dispatch_no_questions_is_a_no_op():
    channel, tracker = _channel("linear")
    await channel.dispatch(_issue(), [])
    assert tracker.posts == []


# ---- post_answer ---------------------------------------------------------------


async def test_post_answer_quotes_question_and_names_actor_and_channel():
    channel, tracker = _channel("linear")

    await channel.post_answer(
        _issue(),
        {"id": "inp-1", "job_id": "job-1", "question": "Bump the major version?", "asking_node": "developer"},
        "yes, bump it",
        answered_by="key:hermes",
        channel="api",
    )

    assert len(tracker.posts) == 1
    _, body = tracker.posts[0]
    assert "> Bump the major version?" in body
    assert "**Answer** (by key:hermes via api):" in body
    assert "yes, bump it" in body
    assert "job-1" in body
    assert "developer" in body
