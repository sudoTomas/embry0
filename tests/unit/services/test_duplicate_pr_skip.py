"""A duplicate-PR skip ends the job skipped and claims no PR (#474).

When an open pull request already covers a ticket, embry0 used to end the job
``completed`` with that PR as its own ``pr_url`` and close the issue: the
ticket was told the job completed with a pull request, the healing case
credited a person's PR to embry0 when it merged, and the issue closed on an
unmerged fix. Both skip paths are covered — the executor's pre-graph ticket
match and triage's semantic ``duplicate_of_pr`` flag.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor

PR = {"number": 366, "url": "https://github.com/acme/widgets/pull/366", "head_ref": "fix/by-hand"}


def _executor() -> tuple[IssueExecutor, MagicMock]:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock(update=AsyncMock(), get=AsyncMock(return_value={"repo": "acme/widgets", "task": "t"}))
    ex._issues = MagicMock(update=AsyncMock(), update_parent_status=AsyncMock())
    # #521: a job that ends reads its issue before putting it back to open.
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": "in_progress"})
    ex._traces = None
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    recorder = MagicMock(existing_pr_found=AsyncMock(return_value="hc-1"), pr_opened=AsyncMock())
    ex._case_recorder = recorder
    return ex, recorder


def _status_writes(ex: IssueExecutor) -> list[dict]:
    return [c.kwargs for c in ex._jobs.update.call_args_list if "status" in c.kwargs]


def _assert_skipped_without_claiming(ex: IssueExecutor, recorder: MagicMock) -> dict:
    writes = _status_writes(ex)
    assert [w["status"] for w in writes] == ["skipped"], f"the job ended {[w['status'] for w in writes]}"
    final = writes[0]
    assert not any(c.kwargs.get("pr_url") for c in ex._jobs.update.call_args_list), (
        "the skipped job recorded someone else's PR as its own pr_url"
    )
    assert "already exists" in final["result_summary"] and PR["url"] in final["result_summary"]
    assert final["finished_at"] is not None
    ex._issues.update.assert_any_call("iss-1", status="open")
    assert not [c for c in ex._issues.update.call_args_list if c.kwargs.get("status") == "closed"], (
        "a skip closed the issue although the existing PR is not merged"
    )
    recorder.existing_pr_found.assert_awaited_once_with(issue_id="iss-1", job_id="job-1", pr_url=PR["url"])
    recorder.pr_opened.assert_not_awaited()
    return final


@pytest.mark.asyncio
async def test_the_pre_graph_ticket_match_ends_the_job_skipped():
    ex, recorder = _executor()
    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await ex._skip_as_duplicate("iss-1", "job-1", "acme/widgets", {**PR, "matched_by": "ticket ENG-1"})
    final = _assert_skipped_without_claiming(ex, recorder)
    assert final["total_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_a_triage_duplicate_flag_ends_the_job_skipped_with_what_triage_spent():
    """The 2026-10-02 shape: triage returned duplicate_of_pr naming a person's open PR."""
    ex, recorder = _executor()
    result = {
        "current_stage": "skipped_duplicate",
        "repo": "acme/widgets",
        "triage_decision": {"action": "proceed", "duplicate_of_pr": {"pr_number": 366, "reason": "same fix"}},
        "open_prs": [dict(PR)],
        "result_summary": f"Skipped: a pull request for this issue already exists: #366 ({PR['url']}).",
        "total_cost_usd": 0.41,
        "agent_outputs": [{"agent_type": "triage", "is_error": False}],
    }
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await ex._handle_workflow_result("iss-1", "job-1", result)

    final = _assert_skipped_without_claiming(ex, recorder)
    assert final["total_cost_usd"] == pytest.approx(0.41)


@pytest.mark.asyncio
async def test_the_ticket_comment_says_skipped_and_names_the_existing_pr():
    """The outcome comment is built from the job row the skip wrote."""
    from embry0.notifications.tracker_comment import TrackerCommentChannel
    from embry0.trackers import TrackerIssueRef, TrackerRegistry

    ex, _ = _executor()
    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await ex._skip_as_duplicate("iss-1", "job-1", "acme/widgets", {**PR, "matched_by": "ticket ENG-1"})
    row = {"job_id": "job-1", **_status_writes(ex)[0]}

    class _Tracker:
        name = "github"

        def __init__(self) -> None:
            self.posts: list[str] = []

        async def handle_webhook_event(self, payload, ctx):
            return {}

        async def list_backlog(self, repos, *, client_for=None):
            from embry0.trackers import BacklogRead

            return BacklogRead()

        async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
            self.posts.append(body)
            return True

    tracker = _Tracker()
    registry = TrackerRegistry()
    registry.register(tracker)
    await TrackerCommentChannel(registry, "github").post_outcome(
        {"id": "iss-1", "tracker": "github", "tracker_key": "7", "repo": "acme/widgets"}, row
    )

    body = tracker.posts[0]
    assert "finished: skipped" in body, body
    assert "already exists" in body and PR["url"] in body
    assert "No new pull request was opened" in body
    assert "Pull request:" not in body, "the comment presented the existing PR as the job's own"
