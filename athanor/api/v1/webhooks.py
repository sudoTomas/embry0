"""GitHub webhook handler."""

import json as json_module
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from athanor.api.auth import verify_webhook_signature
from athanor.notifications.inbound_dispatch import apply_inbound_directives
from athanor.notifications.inbound_parser import parse_answer_directives

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, Any]:
    config = request.app.state.config
    body = await request.body()

    verify_webhook_signature(
        body=body,
        signature=x_hub_signature_256,
        secret=config.github_webhook_secret,
        webhook_dev_mode=config.webhook_dev_mode,
    )

    try:
        payload = json_module.loads(body)
    except (json_module.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Unwrap smee.io relay envelope — smee wraps the real payload inside a
    # "payload" key as a JSON string. When receiving webhooks directly from
    # GitHub this branch is a no-op.
    if "payload" in payload and isinstance(payload["payload"], str):
        try:
            payload = json_module.loads(payload["payload"])
        except (json_module.JSONDecodeError, ValueError):
            pass  # Not a smee envelope — use the payload as-is

    action = payload.get("action", "")
    event_type = x_github_event
    logger.info("webhook_received", event_type=event_type, action=action)

    # Handle issue events via sync service
    if event_type == "issues" and action in ("opened", "edited", "closed", "reopened", "labeled", "unlabeled"):
        issues_repo = request.app.state.issues_repo
        github_sync = request.app.state.github_sync
        if issues_repo and github_sync:
            trigger_labels = set(config.trigger_labels_list) if hasattr(config, "trigger_labels_list") else set()
            result = await github_sync.handle_webhook_event(
                event_type=event_type,
                action=action,
                payload=payload,
                issues_repo=issues_repo,
                trigger_labels=trigger_labels,
                issue_executor=request.app.state.issue_executor,
            )
            return dict(result)

    # NOTE: To receive issue_comment events, add "Issue comments" to the GitHub
    # webhook event subscription at:
    # https://github.com/<org>/<repo>/settings/hooks
    # Handle issue comment events — answers to input questions via /answer N:
    # directives parsed from the comment body. Routes through the shared
    # inbound dispatcher (athanor.notifications.inbound_dispatch) so the same
    # answer/skip transitions apply as the dashboard's POST /answer endpoint.
    if event_type == "issue_comment" and action == "created":
        comment = payload.get("comment") or {}
        comment_body = comment.get("body") or ""
        if not comment_body:
            return {"ok": True, "applied": 0, "reason": "empty body"}

        directives = parse_answer_directives(comment_body)
        if not directives:
            return {"ok": True, "applied": 0, "reason": "no directives"}

        repo_name = (payload.get("repository") or {}).get("full_name") or ""
        gh_number = (payload.get("issue") or {}).get("number")
        if not repo_name:
            logger.warning("issue_comment_missing_repo", payload_keys=list(payload.keys()))
            return {"ok": True, "applied": 0, "reason": "missing repo"}
        if gh_number is None:
            logger.warning("issue_comment_missing_issue_number", repo=repo_name)
            return {"ok": True, "applied": 0, "reason": "missing issue number"}

        issues_repo = request.app.state.issues_repo
        inputs_repo = getattr(request.app.state, "inputs_repo", None)
        executor = getattr(request.app.state, "issue_executor", None)
        if not (issues_repo and inputs_repo and executor):
            logger.warning(
                "issue_comment_missing_state",
                has_issues=bool(issues_repo),
                has_inputs=bool(inputs_repo),
                has_executor=bool(executor),
            )
            return {"ok": True, "applied": 0, "reason": "state not initialised"}

        issue = await issues_repo.find_by_repo_and_github_number(
            repo=repo_name, github_number=gh_number
        )
        if issue is None:
            return {"ok": True, "applied": 0, "reason": "issue not tracked"}

        issue_id = issue["id"]
        login = (comment.get("user") or {}).get("login") or "unknown"

        async def _resume() -> None:
            await executor.resume_for_issue(issue_id)

        result = await apply_inbound_directives(
            issue_id=issue_id,
            directives=directives,
            inputs_repo=inputs_repo,
            on_all_answered=_resume,
            answered_by=f"github:{login}",
        )
        return {
            "ok": True,
            "applied": result.applied,
            "skipped": result.skipped,
            "unmatched": result.unmatched,
        }

    # Handle pull request events — PR linking
    # NOTE: GitHub webhook must also subscribe to "Pull requests" events
    if event_type == "pull_request" and action in ("opened", "closed"):
        repo_name = payload.get("repository", {}).get("full_name", "")
        pr = payload.get("pull_request", {})
        pr_url = pr.get("html_url", "")
        branch = pr.get("head", {}).get("ref", "")
        merged = pr.get("merged", False)

        if repo_name and pr_url:
            jobs_repo = request.app.state.jobs_repo
            issues_repo = request.app.state.issues_repo

            # Match PR to job via branch name pattern: athanor/{id}-{slug}
            if branch.startswith("athanor/"):
                parts = branch.split("/", 1)[1].split("-", 1)
                if len(parts) >= 1:
                    issue_id_prefix = parts[0]
                    matched = await issues_repo.find_by_id_prefix(f"iss-{issue_id_prefix}")
                    if matched:
                        issue = matched[0]
                        jobs_list, _ = await jobs_repo.list_all(issue_id=issue["id"], limit=10, offset=0)
                        for job in jobs_list:
                            if not job.get("pr_url"):
                                await jobs_repo.update(job["job_id"], pr_url=pr_url)

                        if action == "closed" and merged:
                            await issues_repo.update(issue["id"], status="closed")
                            for job in jobs_list:
                                if job["status"] in ("completed", "running"):
                                    await jobs_repo.update(job["job_id"], status="pr_merged")
                        elif action == "closed" and not merged:
                            for job in jobs_list:
                                if job["status"] in ("completed", "running"):
                                    await jobs_repo.update(job["job_id"], status="pr_closed")

                        return {"status": "accepted", "action": f"pr_{action}"}
                    else:
                        logger.info("pr_link_no_matching_issue", branch=branch, prefix=issue_id_prefix)

        return {"status": "ignored", "event": "pull_request", "action": action}

    return {"status": "ignored", "event": event_type, "action": action}
