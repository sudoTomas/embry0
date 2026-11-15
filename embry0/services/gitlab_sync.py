"""GitLab issue trigger — inbound webhook to pipeline dispatch (#165).

GitLab's Issue Hook is not GitHub's `issues` event with different key names,
and the differences are exactly the ones a naive port gets wrong:

* The event kind is in the **body** (``object_kind: "issue"``), not only in a
  header.
* The issue lives under ``object_attributes``, not ``issue``.
* Labels are objects with ``title``, not ``name``.
* **There is no ``labeled`` action.** Applying a label arrives as
  ``action: "update"``, and the only way to know a label was *just* added is
  the delta in ``changes.labels``.
* ``project.path_with_namespace`` can nest arbitrarily deep
  (``acme/widgets/shop``) — hence #162.

The dispatch decision mirrors the GitHub path deliberately: fire on the
*transition* into the labelled state, adopt an untracked issue that arrives
already labelled, and only dispatch from ``open``. Divergence between forges
here is what #156 and #157 were spent removing.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.execution.gitlab_client import GitLabClient, resolve_target_repo
from embry0.services.trigger_labels import matching_triggers, normalize_labels
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.storage.schemas import IssueStatus
from embry0.trackers import (
    TrackerIssue,
    TrackerIssueRef,
    TriggerContext,
    create_tracked_issue,
    end_waits,
    extract_labels,
    start_triage,
)

logger = structlog.get_logger(__name__)

TRACKER = "gitlab"

# Actions that can carry a label change. `close` and `reopen` are state-only.
_DISPATCHABLE_ACTIONS = ("open", "reopen", "update")


def previous_label_titles(payload: dict[str, Any]) -> list[str] | None:
    """Labels as they were *before* this delivery, when GitLab reports them.

    ``changes.labels.previous`` is the only signal distinguishing "the trigger
    label was just applied" from "the label was already there and something
    else changed". Returns None when the payload carries no label delta, which
    means labels did not change in this event.
    """
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        return None
    labels_change = changes.get("labels")
    if not isinstance(labels_change, dict):
        return None
    return extract_labels(labels_change.get("previous"))


class GitLabSyncService:
    """Turns a labelled GitLab issue into an embry0 job."""

    def __init__(
        self,
        client: GitLabClient,
        repo_map: dict[str, str] | None = None,
        *,
        dashboard_base_url: str = "",
    ) -> None:
        self._client = client
        self._repo_map = dict(repo_map or {})
        self._dashboard_base_url = dashboard_base_url

    @property
    def name(self) -> str:
        return TRACKER

    def _parse_issue(self, payload: dict[str, Any]) -> TrackerIssue | None:
        """Normalize a GitLab Issue Hook payload into a :class:`TrackerIssue`.

        Returns None without a project path or iid — nothing downstream can
        act without an address.
        """
        attrs = payload.get("object_attributes")
        if not isinstance(attrs, dict):
            return None
        project_path = str((payload.get("project") or {}).get("path_with_namespace") or "")
        iid = attrs.get("iid")
        if not project_path or iid is None:
            return None
        return TrackerIssue(
            tracker=TRACKER,
            key=str(iid),
            title=str(attrs.get("title") or ""),
            body=str(attrs.get("description") or ""),
            url=str(attrs.get("url") or ""),
            labels=extract_labels(payload.get("labels")),
            repo=resolve_target_repo(project_path, self._repo_map),
            state=str(attrs.get("state") or ""),
        )

    async def handle_webhook_event(
        self,
        payload: dict[str, Any],
        ctx: TriggerContext,
    ) -> dict[str, Any]:
        if payload.get("object_kind") != "issue":
            return {"status": "ignored", "reason": "not an issue event"}

        attrs = payload.get("object_attributes")
        if not isinstance(attrs, dict):
            return {"status": "ignored", "reason": "no object_attributes"}

        issue = self._parse_issue(payload)
        if issue is None:
            return {"status": "ignored", "reason": "missing project path or issue iid"}

        action = str(attrs.get("action") or "")
        issues_repo = ctx.issues_repo
        triggers = ctx.trigger_labels or set()

        existing = await issues_repo.get_by_tracker_ref(TRACKER, issue.repo, issue.key)

        # Keep the stored label set current regardless of dispatch outcome.
        if existing is not None:
            await issues_repo.update(existing["id"], labels=issue.labels)

        if action == "close":
            ended: list[str] = []
            if existing is not None:
                await self._safe_status(issues_repo, existing["id"], "closed")
                # #518, #521: a closed issue's job that waits for answers, or is paused, never goes on; it ends here.
                ended = await end_waits(ctx.issue_executor, existing["id"])
            return {"status": "accepted", "action": "issue_closed", **({"cancelled_jobs": ended} if ended else {})}

        if action not in _DISPATCHABLE_ACTIONS:
            return {"status": "ignored", "reason": f"unhandled action: {action}"}

        matched = matching_triggers(issue.labels, triggers)
        if not matched:
            return {"status": "accepted", "action": f"issue_{action}", "dispatched": False}

        # GitLab has no `labeled` action, so "was it just applied?" comes from
        # the payload delta when present, and from our stored labels otherwise.
        previous = previous_label_titles(payload)
        if previous is not None:
            already_labelled = bool(matching_triggers(previous, triggers))
        elif existing is not None:
            already_labelled = bool(matching_triggers(existing.get("labels") or [], triggers))
        else:
            already_labelled = False

        if already_labelled and existing is not None:
            return {
                "status": "accepted",
                "action": f"issue_{action}",
                "issue_id": existing["id"],
                "dispatched": False,
                "reason": "trigger label already present",
            }

        if existing is None:
            issue_id = await create_tracked_issue(issues_repo, issue, created_by="gitlab-webhook")
            current_status = IssueStatus.OPEN
            logger.info("gitlab_issue_adopted", issue_id=issue_id, project=issue.repo, iid=issue.key)
        else:
            issue_id = existing["id"]
            current_status = existing.get("status", "")

        dispatched = await start_triage(
            issue_id,
            current_status,
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
            "repo": issue.repo,
            "dispatched": dispatched,
        }

    async def _safe_status(self, issues_repo: IssuesRepository, issue_id: str, status: str) -> None:
        try:
            await issues_repo.update(issue_id, status=status)
        except (ValueError, StatusTransitionConflict):
            logger.info("gitlab_status_transition_skipped", issue_id=issue_id, target=status)

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        """Best-effort note back on the GitLab issue. Never raises."""
        return await self.post_outcome(ref.repo, ref.key, body)

    async def post_outcome(self, repo: str, tracker_key: str, body: str) -> bool:
        """Best-effort note back on the GitLab issue. Never raises."""
        try:
            return await self._client.create_issue_note(repo, int(tracker_key), body)
        except Exception:
            logger.warning("gitlab_note_failed", repo=repo, iid=tracker_key, exc_info=True)
            return False


__all__ = [
    "TRACKER",
    "GitLabSyncService",
    "normalize_labels",
    "previous_label_titles",
]
