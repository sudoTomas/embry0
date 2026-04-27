"""GitHub sync service — outbound push and inbound webhook processing."""

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from athanor.storage.repositories.issues import IssuesRepository

logger = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"

_STATUS_TO_GH_STATE = {
    "open": "open",
    "triaging": "open",
    "in_progress": "open",
    "closed": "closed",
    "cancelled": "closed",
}


def _extract_label_names(raw_labels: object) -> list[str]:
    """Extract label name strings from a GitHub issue's ``labels`` field.

    Defensive: silently skips entries that aren't dicts with a non-empty string
    ``name`` key. Handles the case where ``raw_labels`` itself is missing/None.
    """
    if not isinstance(raw_labels, list):
        return []
    names: list[str] = []
    for lbl in raw_labels:
        if isinstance(lbl, dict):
            name = lbl.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


class GitHubSyncService:
    """Handles two-way sync between Legion issues and GitHub issues."""

    def __init__(self, github_token: str | None = None) -> None:
        self._token = github_token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def push_create(self, issue_id: str, issues_repo: IssuesRepository) -> dict[str, Any] | None:
        """Create a GitHub issue from a Legion issue. Returns GitHub response data."""
        issue = await issues_repo.get(issue_id)
        if not issue or not issue.get("repo"):
            return None
        repo = issue["repo"]
        payload = {"title": issue["title"], "body": issue.get("body", ""), "labels": issue.get("labels", [])}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GITHUB_API}/repos/{repo}/issues", json=payload, headers=self._headers(), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
        await issues_repo.update(
            issue_id, github_number=data["number"], github_url=data["html_url"], github_synced_at=datetime.now(UTC)
        )
        logger.info("github_issue_created", issue_id=issue_id, github_number=data["number"])
        return data

    async def push_update(self, issue_id: str, issues_repo: IssuesRepository) -> dict[str, Any] | None:
        """Push Legion issue changes to GitHub."""
        issue = await issues_repo.get(issue_id)
        if not issue or not issue.get("repo") or not issue.get("github_number"):
            return None
        repo = issue["repo"]
        number = issue["github_number"]
        gh_state = _STATUS_TO_GH_STATE.get(issue["status"], "open")
        payload = {
            "title": issue["title"],
            "body": issue.get("body", ""),
            "labels": issue.get("labels", []),
            "state": gh_state,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{_GITHUB_API}/repos/{repo}/issues/{number}", json=payload, headers=self._headers(), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
        await issues_repo.update(issue_id, github_synced_at=datetime.now(UTC))
        logger.info("github_issue_updated", issue_id=issue_id, github_number=number)
        return data

    async def handle_webhook_event(
        self,
        event_type: str,
        action: str,
        payload: dict[str, Any],
        issues_repo: IssuesRepository,
        trigger_labels: set[str] | None = None,
        issue_executor: Any | None = None,
    ) -> dict[str, str]:
        """Process an inbound GitHub webhook event."""
        if event_type != "issues":
            return {"status": "ignored", "reason": "not an issue event"}
        gh_issue = payload.get("issue", {})
        repo_data = payload.get("repository", {})
        repo = repo_data.get("full_name", "")
        gh_number = gh_issue.get("number")
        if not repo or not gh_number:
            return {"status": "ignored", "reason": "missing repo or issue number"}
        existing = await issues_repo.get_by_github(repo=repo, github_number=gh_number)

        if action == "opened":
            if existing:
                return {"status": "ignored", "reason": "issue already exists"}
            labels = _extract_label_names(gh_issue.get("labels"))
            issue_id = await issues_repo.create(
                title=gh_issue.get("title", ""),
                body=gh_issue.get("body", "") or "",
                labels=labels,
                repo=repo,
                github_sync_enabled=True,
                created_by="webhook",
            )
            await issues_repo.update(issue_id, github_number=gh_number, github_url=gh_issue.get("html_url", ""))
            should_triage = bool(trigger_labels and trigger_labels.intersection(set(labels)))
            if should_triage:
                await issues_repo.update(issue_id, status="triaging")
                if issue_executor:
                    try:
                        job_id = await issue_executor.execute(issue_id)
                        logger.info("webhook_auto_triage_started", issue_id=issue_id, job_id=job_id)
                    except Exception:
                        logger.warning("webhook_auto_triage_failed", issue_id=issue_id, exc_info=True)
            logger.info("webhook_issue_created", issue_id=issue_id, github_number=gh_number)
            return {"status": "accepted", "action": "issue_created", "issue_id": issue_id}

        if not existing:
            return {"status": "ignored", "reason": "issue not tracked"}
        issue_id = existing["id"]

        if action == "edited":
            await issues_repo.update(
                issue_id,
                title=gh_issue.get("title", existing["title"]),
                body=gh_issue.get("body", "") or existing.get("body", ""),
            )
            return {"status": "accepted", "action": "issue_updated"}
        if action == "closed":
            await issues_repo.update(issue_id, status="closed")
            return {"status": "accepted", "action": "issue_closed"}
        if action == "reopened":
            await issues_repo.update(issue_id, status="open")
            return {"status": "accepted", "action": "issue_reopened"}
        if action in ("labeled", "unlabeled"):
            labels = _extract_label_names(gh_issue.get("labels"))
            await issues_repo.update(issue_id, labels=labels)
            return {"status": "accepted", "action": f"issue_{action}"}
        return {"status": "ignored", "reason": f"unhandled action: {action}"}
