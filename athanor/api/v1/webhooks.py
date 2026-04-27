"""GitHub webhook handler."""

import json as json_module

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from athanor.api.auth import verify_webhook_signature

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict:
    config = request.app.state.config
    body = await request.body()

    verify_webhook_signature(
        body=body,
        signature=x_hub_signature_256,
        secret=config.github_webhook_secret,
        dev_mode=config.dev_mode,
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
            return result

    # NOTE: To receive issue_comment events, add "Issue comments" to the GitHub
    # webhook event subscription at:
    # https://github.com/<org>/<repo>/settings/hooks
    # Handle issue comment events — answers to input questions
    if event_type == "issue_comment" and action == "created":
        issues_repo = request.app.state.issues_repo
        inputs_repo = getattr(request.app.state, "inputs_repo", None)
        if issues_repo and inputs_repo:
            comment_body = payload.get("comment", {}).get("body", "")
            gh_issue = payload.get("issue", {})
            repo_name = payload.get("repository", {}).get("full_name", "")
            gh_number = gh_issue.get("number")

            if repo_name and gh_number and comment_body:
                issue = await issues_repo.get_by_github(repo=repo_name, github_number=gh_number)
                if issue and issue["status"] == "awaiting_input":
                    pending_inputs = await inputs_repo.list_by_issue(issue["id"])
                    answered_any = False
                    for inp in pending_inputs:
                        if inp["importance"] == "blocking" and inp["status"] == "pending":
                            await inputs_repo.answer(inp["id"], comment_body, answered_by="github")
                            answered_any = True
                            from athanor.notifications.dispatcher import notify_answer_cross_channel

                            await notify_answer_cross_channel(inputs_repo, inp, comment_body, "github", config)
                            break  # Only answer the first pending blocking input per comment

                    if answered_any:
                        pending = await inputs_repo.count_pending_blocking(issue["id"])
                        if pending == 0:
                            current_issue = await issues_repo.get(issue["id"])
                            if current_issue and current_issue["status"] == "awaiting_input":
                                executor = request.app.state.issue_executor
                                from athanor.api.v1.issues import _resume_pipeline

                                await _resume_pipeline(issue["id"], issues_repo, inputs_repo, executor)

                    return {"status": "accepted", "action": "comment_processed"}

        return {"status": "accepted", "action": "comment_received"}

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
                    issues_list, _ = await issues_repo.list(limit=100, offset=0)
                    for issue in issues_list:
                        if issue["id"].startswith(f"iss-{issue_id_prefix}"):
                            jobs_list, _ = await jobs_repo.list(issue_id=issue["id"], limit=10, offset=0)
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

        return {"status": "ignored", "event": "pull_request", "action": action}

    return {"status": "ignored", "event": event_type, "action": action}
