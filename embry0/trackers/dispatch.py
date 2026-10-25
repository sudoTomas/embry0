"""Shared trigger-side dispatch steps (#160).

Every tracker's inbound flow ends the same way — create the embry0 row
mirroring the tracker's issue, then move it to ``triaging`` and hand it to
the executor. GitHub and GitLab each carried a private copy of both steps;
the divergence between them was incidental. This module is the single copy.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.storage.repositories.repos import UnregisteredRepoError
from embry0.storage.schemas import IssueStatus
from embry0.trackers.models import TrackerIssue

logger = structlog.get_logger(__name__)


async def create_tracked_issue(issues_repo: Any, issue: TrackerIssue, *, created_by: str) -> str:
    """Create the embry0 row mirroring a tracker's issue and return its id.

    One column family for every tracker (#161). The only per-tracker branch
    left is behavioral, not referential: GitHub-originated rows enable the
    outbound push feature so embry0-side edits flow back to the issue.
    """
    create_kwargs: dict[str, Any] = {}
    if issue.tracker == "github":
        create_kwargs["github_sync_enabled"] = True
    # The tracker ref goes in with the row, not after it: a concurrent writer
    # for the same ticket then fails on idx_issues_tracker_ref before any row
    # of its own exists (asyncpg.UniqueViolationError), instead of leaving an
    # orphan that carries no ref.
    issue_id: str = await issues_repo.create(
        title=issue.title,
        body=issue.body,
        labels=list(issue.labels),
        repo=issue.repo,
        created_by=created_by,
        tracker=issue.tracker,
        tracker_key=issue.key,
        tracker_url=issue.url,
        tracker_native_id=issue.native_id or None,
        **create_kwargs,
    )
    return issue_id


async def start_triage(
    issue_id: str,
    current_status: str,
    issues_repo: Any,
    issue_executor: Any | None,
    *,
    tracker: str,
    reason: str,
    actor: str = "",
) -> bool:
    """Move an issue to ``triaging`` and dispatch a job. True if dispatched.

    Only issues sitting in ``open`` are dispatchable. That restriction is
    deliberate rather than incidental (#156, the label-trigger fix):

    * ``triaging`` / ``in_progress`` / ``awaiting_input`` / ``paused`` all mean
      a job is already active. ``IssueExecutor.execute`` rejects those anyway,
      and ``VALID_ISSUE_TRANSITIONS`` has no ``in_progress -> triaging`` edge,
      so the status write itself would raise before we got that far.
    * ``closed`` / ``cancelled`` must not be resurrected by a label.

    Re-labelling after a run *does* dispatch again: every terminal path returns
    the issue to ``open`` (``_executor_run``, ``_executor_outcomes``).

    ``actor`` is the person the delivery names — the label's applier, not the
    issue's opener. It lands on ``jobs.dispatched_by`` so the dispatch rate
    limit buckets per human (#246).
    """
    job_id = await dispatch_open_issue(
        issue_id, current_status, issues_repo, issue_executor, tracker=tracker, reason=reason, actor=actor
    )
    return job_id is not None


async def dispatch_open_issue(
    issue_id: str,
    current_status: str,
    issues_repo: Any,
    issue_executor: Any | None,
    *,
    tracker: str,
    reason: str,
    actor: str = "",
    run_with: Any = None,
) -> str | None:
    """:func:`start_triage`, returning the job id it dispatched (None if not).

    The API's case-accept route needs the id to hand back; the webhook
    callers only need to know whether a job started.
    """
    if issue_executor is None:
        return None
    if current_status != IssueStatus.OPEN:
        logger.info(
            "webhook_trigger_skipped_status",
            issue_id=issue_id,
            status=current_status,
            tracker=tracker,
            reason=reason,
        )
        return None

    try:
        await issues_repo.update(issue_id, status="triaging")
    except (ValueError, StatusTransitionConflict):
        # Lost a race with a concurrent writer — someone else owns the issue.
        logger.warning("webhook_trigger_status_conflict", issue_id=issue_id, tracker=tracker, exc_info=True)
        return None

    try:
        # Only a person's choice rides along: a webhook's dispatch is the call it always was.
        chosen = {"run_with": run_with} if run_with is not None else {}
        job_id = await issue_executor.execute(issue_id, dispatched_by=actor or None, **chosen)
    except Exception as exc:
        if isinstance(exc, UnregisteredRepoError):
            # #360: refused, not broken — the repo has no `repos` row.
            logger.warning(
                "webhook_dispatch_refused_unregistered_repo", issue_id=issue_id, tracker=tracker, detail=str(exc)
            )
        else:
            logger.warning("webhook_auto_triage_failed", issue_id=issue_id, tracker=tracker, exc_info=True)
        # Never strand the issue in `triaging`: only `open` dispatches, so a
        # stuck row would silently swallow every future label trigger.
        try:
            await issues_repo.update(issue_id, status="open")
        except (ValueError, StatusTransitionConflict):
            logger.warning("webhook_trigger_revert_failed", issue_id=issue_id, tracker=tracker, exc_info=True)
        return None

    logger.info(
        "webhook_auto_triage_started",
        issue_id=issue_id,
        job_id=job_id,
        tracker=tracker,
        reason=reason,
    )
    return str(job_id)
