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

    if event_type == "issues" and action == "labeled":
        issue = payload.get("issue", {})
        labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
        trigger_labels = set(config.trigger_labels_list)
        if trigger_labels.intersection(labels):
            logger.info("webhook_trigger_matched", issue_number=issue.get("number"), labels=labels)
            return {"status": "accepted", "action": "job_queued"}

    if event_type == "issue_comment" and action == "created":
        logger.info("webhook_comment", issue=payload.get("issue", {}).get("number"))
        return {"status": "accepted", "action": "comment_received"}

    return {"status": "ignored", "event": event_type, "action": action}
