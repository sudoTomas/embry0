"""GitHub sync service — outbound push and inbound webhook processing."""

from datetime import UTC, datetime
from typing import Any

import structlog

from embry0.execution.github_client import GitHubClient
from embry0.services.trigger_labels import matching_triggers, normalize_labels
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.schemas import IssueStatus
from embry0.trackers import (
    TrackerIssue,
    TrackerIssueRef,
    TriggerContext,
    create_tracked_issue,
    end_waits,
    extract_labels,
    github_number_of,
    start_triage,
)

logger = structlog.get_logger(__name__)

TRACKER = "github"

_STATUS_TO_GH_STATE = {
    "open": "open",
    "triaging": "open",
    "in_progress": "open",
    "closed": "closed",
    "cancelled": "closed",
}


def _parse_issue(payload: dict[str, Any]) -> TrackerIssue | None:
    """Normalize a GitHub ``issues`` payload into a :class:`TrackerIssue`.

    Returns None when the payload lacks the repo or issue number — nothing
    downstream can act without an address.
    """
    gh_issue = payload.get("issue", {})
    repo = (payload.get("repository") or {}).get("full_name", "")
    gh_number = gh_issue.get("number")
    if not repo or not gh_number:
        return None
    return TrackerIssue(
        tracker=TRACKER,
        key=str(gh_number),
        title=gh_issue.get("title", ""),
        body=gh_issue.get("body", "") or "",
        url=gh_issue.get("html_url", ""),
        labels=extract_labels(gh_issue.get("labels")),
        repo=repo,
        state=gh_issue.get("state", ""),
    )


class GitHubSyncService:
    """Handles two-way sync between embry0 issues and GitHub issues."""

    def __init__(
        self,
        github_token: str | None = None,
        client: GitHubClient | None = None,
        credential_resolver: Any | None = None,
    ) -> None:
        self._token = github_token
        # Spec 2026-09-29 §3.1: issue write-back resolves through the registry
        # when wired; None keeps the pre-registry env lookup.
        self._client = client or GitHubClient(default_token=github_token or "", credential_resolver=credential_resolver)

    @property
    def name(self) -> str:
        return TRACKER

    def _headers(self, repo: str | None = None) -> dict[str, str]:
        """Retained for callers/tests that assert on header construction."""
        return self._client.headers(repo)

    async def push_create(self, issue_id: str, issues_repo: IssuesRepository) -> dict[str, Any] | None:
        """Create a GitHub issue from an embry0 issue. Returns GitHub response data."""
        issue = await issues_repo.get(issue_id)
        if not issue or not issue.get("repo"):
            return None
        repo = issue["repo"]
        payload = {"title": issue["title"], "body": issue.get("body", ""), "labels": issue.get("labels", [])}
        resp = await self._client.post(f"/repos/{repo}/issues", repo=repo, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        await issues_repo.update(
            issue_id,
            tracker=TRACKER,
            tracker_key=str(data["number"]),
            tracker_url=data["html_url"],
            github_synced_at=datetime.now(UTC),
        )
        logger.info("github_issue_created", issue_id=issue_id, github_number=data["number"])
        return data

    async def push_update(self, issue_id: str, issues_repo: IssuesRepository) -> dict[str, Any] | None:
        """Push embry0 issue changes to GitHub."""
        issue = await issues_repo.get(issue_id)
        number = github_number_of(issue)
        if not issue or not issue.get("repo") or number is None:
            return None
        repo = issue["repo"]
        gh_state = _STATUS_TO_GH_STATE.get(issue["status"], "open")
        payload = {
            "title": issue["title"],
            "body": issue.get("body", ""),
            "labels": issue.get("labels", []),
            "state": gh_state,
        }
        # Safe to send again (#45): it sets the issue's fields, so a second send leaves them as the first did.
        resp = await self._client.patch(f"/repos/{repo}/issues/{number}", repo=repo, json=payload, retry_transient=True)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        await issues_repo.update(issue_id, github_synced_at=datetime.now(UTC))
        logger.info("github_issue_updated", issue_id=issue_id, github_number=number)
        return data

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        """Best-effort comment on a GitHub issue. Never raises."""
        try:
            resp = await self._client.post(
                f"/repos/{ref.repo}/issues/{ref.key}/comments",
                repo=ref.repo,
                json={"body": body},
            )
            resp.raise_for_status()
            return True
        except Exception:
            logger.warning("github_comment_failed", repo=ref.repo, number=ref.key, exc_info=True)
            return False

    async def handle_webhook_event(
        self,
        payload: dict[str, Any],
        ctx: TriggerContext,
    ) -> dict[str, Any]:
        """Process an inbound GitHub webhook event."""
        if ctx.event_type != "issues":
            return {"status": "ignored", "reason": "not an issue event"}
        issue = _parse_issue(payload)
        if issue is None:
            return {"status": "ignored", "reason": "missing repo or issue number"}
        action = ctx.action
        issues_repo = ctx.issues_repo
        existing = await issues_repo.get_by_tracker_ref(TRACKER, issue.repo, issue.key)
        triggers = ctx.trigger_labels or set()

        if action == "opened":
            if existing:
                return {"status": "ignored", "reason": "issue already exists"}
            issue_id = await create_tracked_issue(issues_repo, issue, created_by="webhook")
            dispatched = False
            if matching_triggers(issue.labels, triggers):
                # A row created moments ago is always `open`.
                dispatched = await start_triage(
                    issue_id,
                    IssueStatus.OPEN,
                    issues_repo,
                    ctx.issue_executor,
                    tracker=TRACKER,
                    reason="opened_with_label",
                    actor=ctx.actor,
                )
            logger.info("webhook_issue_created", issue_id=issue_id, github_number=issue.key)
            return {
                "status": "accepted",
                "action": "issue_created",
                "issue_id": issue_id,
                "dispatched": dispatched,
            }

        if not existing:
            # An untracked issue that gets the trigger label is adopted rather
            # than dropped — before this, the only way to start a job from a
            # label was to have the label present at creation time.
            if action == "labeled" and matching_triggers(issue.labels, triggers):
                issue_id = await create_tracked_issue(issues_repo, issue, created_by="webhook-label")
                dispatched = await start_triage(
                    issue_id,
                    IssueStatus.OPEN,
                    issues_repo,
                    ctx.issue_executor,
                    tracker=TRACKER,
                    reason="adopted_on_label",
                    actor=ctx.actor,
                )
                logger.info("webhook_issue_adopted", issue_id=issue_id, github_number=issue.key)
                return {
                    "status": "accepted",
                    "action": "issue_adopted",
                    "issue_id": issue_id,
                    "dispatched": dispatched,
                }
            return {"status": "ignored", "reason": "issue not tracked"}
        issue_id = existing["id"]

        if action == "edited":
            await issues_repo.update(
                issue_id,
                title=issue.title or existing["title"],
                body=issue.body or existing.get("body", ""),
            )
            return {"status": "accepted", "action": "issue_updated"}
        if action == "closed":
            await issues_repo.update(issue_id, status="closed")
            # #518, #521: a closed issue's job that waits for answers, or is paused, never goes on; it ends here.
            ended = await end_waits(ctx.issue_executor, issue_id)
            return {"status": "accepted", "action": "issue_closed", **({"cancelled_jobs": ended} if ended else {})}
        if action == "reopened":
            await issues_repo.update(issue_id, status="open")
            return {"status": "accepted", "action": "issue_reopened"}
        if action in ("labeled", "unlabeled"):
            # Snapshot the stored labels before overwriting them — the diff is
            # what makes this idempotent under webhook re-delivery and stops an
            # unrelated label edit from re-firing an already-labelled issue.
            previous = normalize_labels(existing.get("labels"))
            await issues_repo.update(issue_id, labels=issue.labels)
            dispatched = False
            if action == "labeled" and matching_triggers(issue.labels, triggers) - previous:
                dispatched = await start_triage(
                    issue_id,
                    existing.get("status", ""),
                    issues_repo,
                    ctx.issue_executor,
                    tracker=TRACKER,
                    reason="labeled",
                    actor=ctx.actor,
                )
            return {
                "status": "accepted",
                "action": f"issue_{action}",
                "issue_id": issue_id,
                "dispatched": dispatched,
            }
        return {"status": "ignored", "reason": f"unhandled action: {action}"}
