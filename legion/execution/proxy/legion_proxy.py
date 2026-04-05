"""Legion API proxy — gives sandbox agents controlled access to Legion's API."""

from __future__ import annotations
from typing import Any
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

async def _create_issue_handler(request: web.Request) -> web.Response:
    data = await request.json()
    issues_repo = request.app["issues_repo"]
    parent_issue_id = request.app["issue_id"]
    child_id = await issues_repo.create(
        title=data.get("title", "Untitled"), body=data.get("body", ""),
        priority=data.get("priority", "medium"), labels=data.get("labels", []),
        repo=request.app.get("repo"), parent_issue_id=parent_issue_id,
        created_by=data.get("created_by", "triage_agent"),
    )
    logger.info("legion_proxy_issue_created", child_id=child_id, parent=parent_issue_id)
    return web.json_response({"status": "created", "issue_id": child_id})

async def _request_input_handler(request: web.Request) -> web.Response:
    data = await request.json()
    inputs_repo = request.app["inputs_repo"]
    input_id = await inputs_repo.create(
        issue_id=request.app["issue_id"], job_id=request.app["job_id"],
        question=data.get("question", ""), asking_node=data.get("asking_node", "agent"),
        importance=data.get("importance", "blocking"), auto_answer=data.get("suggested_answer"),
    )
    logger.info("legion_proxy_input_created", input_id=input_id)
    return web.json_response({"status": "input_requested", "input_id": input_id})

async def _update_status_handler(request: web.Request) -> web.Response:
    data = await request.json()
    db = request.app.get("db")
    if db:
        from legion.audit.helpers import emit_audit
        await emit_audit(db, "issue.agent_update", actor=data.get("agent", "agent"),
            details={"message": data.get("message", "")}, issue_id=request.app["issue_id"])
    return web.json_response({"status": "ok"})

async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})

def create_legion_proxy_app(issues_repo: Any, inputs_repo: Any, issue_id: str, job_id: str, repo: str | None = None, db: Any = None) -> web.Application:
    app = web.Application()
    app["issues_repo"] = issues_repo
    app["inputs_repo"] = inputs_repo
    app["issue_id"] = issue_id
    app["job_id"] = job_id
    app["repo"] = repo
    app["db"] = db
    app.router.add_post("/create-issue", _create_issue_handler)
    app.router.add_post("/request-input", _request_input_handler)
    app.router.add_post("/update-status", _update_status_handler)
    app.router.add_get("/health", _health)
    return app
