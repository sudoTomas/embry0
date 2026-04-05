"""GitHub webhook handler."""

import json as json_module

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from legion.api.auth import verify_webhook_signature

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

    if config.github_webhook_secret:
        verify_webhook_signature(body=body, signature=x_hub_signature_256, secret=config.github_webhook_secret)

    try:
        payload = json_module.loads(body)
    except (json_module.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

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
                event_type=event_type, action=action, payload=payload,
                issues_repo=issues_repo, trigger_labels=trigger_labels,
            )
            return result

    if event_type == "issue_comment" and action == "created":
        logger.info("webhook_comment", issue=payload.get("issue", {}).get("number"))
        return {"status": "accepted", "action": "comment_received"}

    return {"status": "ignored", "event": event_type, "action": action}
