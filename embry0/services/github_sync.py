"""GitHub sync service — outbound push and inbound webhook processing."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
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


#: How long before the create an issue the lookup finds may have been made (#613): the local clock and GitHub's
#: may differ, and the create's own retries take time. As the question comment's lookup has it.
_LOOKUP_MARGIN = timedelta(minutes=5)
#: The pause before the lookup, so an issue GitHub made as the answer was lost shows in its list.
_LOOKUP_PAUSE_SECONDS = 1.5


def _when(value: Any) -> datetime | None:
    """A GitHub timestamp (``2026-01-01T00:00:00Z``) as an aware datetime; None for anything else."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_text(stored: Any, sent: Any) -> bool:
    """Whether GitHub's stored text is the text sent, line endings and trailing whitespace aside."""

    def norm(text: Any) -> str:
        return "\n".join(line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")).strip()

    return norm(stored) == norm(sent)


class GitHubSyncService:
    """Handles two-way sync between embry0 issues and GitHub issues."""

    def __init__(
        self,
        github_token: str | None = None,
        client: GitHubClient | None = None,
        credential_resolver: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._token = github_token
        self._sleep = sleep
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
        data = await self._create_issue(repo, payload)
        await issues_repo.update(
            issue_id,
            tracker=TRACKER,
            tracker_key=str(data["number"]),
            tracker_url=data["html_url"],
            github_synced_at=datetime.now(UTC),
        )
        logger.info("github_issue_created", issue_id=issue_id, github_number=data["number"])
        return data

    async def _create_issue(self, repo: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the GitHub issue; the issue GitHub made.

        The create is sent again only when GitHub did not act on it (#45). An unknown outcome (a 5xx, or an answer
        lost in transit) may hide an issue GitHub made: the newest issues are read once for it (#613), and only
        when it is not there is the create sent once more. When they cannot be read the outcome stays unknown, and
        the create is not sent again, since it could make a second issue: the failure is raised. Raises too when
        the create was refused, or when that last send fails.
        """
        started = datetime.now(UTC)
        unknown: httpx.TransportError | None = None
        resp: httpx.Response | None = None
        try:
            resp = await self._client.post(f"/repos/{repo}/issues", repo=repo, json=payload)
        except httpx.TransportError as exc:
            logger.warning("github_issue_create_outcome_unknown", repo=repo, exc_info=True)
            unknown = exc
        if resp is not None and resp.status_code < 500:
            resp.raise_for_status()
            created: dict[str, Any] = resp.json()
            return created
        await self._sleep(_LOOKUP_PAUSE_SECONDS)
        read, made = await self._issue_made_since(repo, payload, started)
        if made is not None:
            logger.info("github_issue_found_after_an_unknown_outcome", repo=repo, github_number=made.get("number"))
            return made
        if not read:
            logger.warning("github_issue_create_left_unknown", repo=repo)
            if unknown is not None:
                raise unknown
            assert resp is not None  # noqa: S101 - one of the two is set
            raise httpx.HTTPStatusError(f"GitHub answered {resp.status_code}", request=resp.request, response=resp)
        resp = await self._client.post(f"/repos/{repo}/issues", repo=repo, json=payload)
        resp.raise_for_status()
        again: dict[str, Any] = resp.json()
        return again

    async def _issue_made_since(
        self, repo: str, payload: dict[str, Any], started: datetime
    ) -> tuple[bool, dict[str, Any] | None]:
        """Whether the issues could be read, and the one this token created in ``repo`` with ``payload``'s title and
        body, at most ``_LOOKUP_MARGIN`` before ``started`` (None when there is none).

        As the question comment's lookup does (``embry0.notifications.github_comment``): only the token's own issue
        counts, since a person's, or another finding's from the same template, can carry the same words; and only
        one created in the window, since GitHub's ``since`` selects by update time, which an old issue's edit moves.
        The match assumes GitHub stores the title and body as sent, so both sides' line endings and trailing
        whitespace are normalised first. One page of 100 issues is read: a repository that took more in those
        minutes is not read past them. When the token's user or the issues cannot be read, the outcome stays
        unknown.
        """
        earliest = started - _LOOKUP_MARGIN
        try:
            who = await self._client.get("/user", repo=repo)
            login = str((who.json() or {}).get("login") or "") if who.status_code == 200 else ""
            resp = await self._client.get(
                f"/repos/{repo}/issues",
                repo=repo,
                params={
                    "state": "all",
                    "sort": "created",
                    "direction": "desc",
                    "since": earliest.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "per_page": 100,
                },
            )
            listed = resp.json() if resp.status_code == 200 else None
        except Exception:
            logger.warning("github_issue_lookup_failed", repo=repo, exc_info=True)
            return False, None
        if not login or not isinstance(listed, list):
            return False, None
        for item in listed:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            created = _when(item.get("created_at"))
            author = str((item.get("user") or {}).get("login") or "")
            if created is None or created < earliest or author.lower() != login.lower():
                continue
            if _same_text(item.get("title"), payload.get("title")) and _same_text(
                item.get("body"), payload.get("body")
            ):
                return True, item
        return True, None

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
