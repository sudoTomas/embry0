"""Leads API (spec 2026-10-25): what embry0's finders found, a pass by hand, and starting one on an owner's yes.

``GET /leads`` lists the stored leads (open by default), each with what a
person or a game-like view needs: kind, repository, area, severity, effort,
when it was first and last seen. ``POST /leads/run`` runs one pass of the
finders now. Owners answer leads through the owner proposals routes, which
call :func:`start_lead` on a yes.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.services.leads import LEAD_KINDS, as_utc_iso
from embry0.services.leads.tasks import lead_task
from embry0.services.run_with import RunWith, apply_run_with

router = APIRouter()


def _repo(request: Request) -> Any:
    leads = getattr(request.app.state, "leads_repo", None)
    if leads is None:
        raise HTTPException(status_code=503, detail="leads are not initialized")
    return leads


def lead_view(row: dict[str, Any]) -> dict[str, Any]:
    """A stored lead as the API shows it."""
    data = dict(row.get("data") or {})
    return {
        "id": row["id"],
        "kind": row["kind"],
        "repo": row["repo"],
        "area": row.get("area"),
        "title": row["title"],
        "summary": row.get("summary") or "",
        "details": row.get("details") or "",
        "url": row.get("url"),
        "severity": row.get("severity"),
        "effort": row.get("effort"),
        "action": row.get("action") or "",
        "state": row.get("state"),
        "first_seen": as_utc_iso(row.get("first_seen")),
        "last_seen": as_utc_iso(row.get("last_seen")),
        "gone_at": as_utc_iso(row.get("gone_at")),
        "paths": list(data.get("paths") or []),
        "labels": list(data.get("labels") or []),
        "source": {k: v for k, v in data.items() if k not in ("paths", "labels")},
    }


@router.get("/leads")
async def list_leads(
    request: Request,
    repo: str | None = Query(default=None),
    kind: str | None = Query(default=None, description=", ".join(LEAD_KINDS)),
    state: str = Query(default="open", pattern="^(open|gone|all)$"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """The leads the finders stored, most recently seen first."""
    if kind is not None and kind not in LEAD_KINDS:
        raise HTTPException(status_code=422, detail=f"kind is one of {', '.join(LEAD_KINDS)}")
    if repo:
        await enforce_repo_allowlist(request, repo, action="read a repository's leads")
    rows = await _repo(request).list(repo=repo, kind=kind, state=None if state == "all" else state, limit=limit)
    return {"leads": [lead_view(r) for r in rows], "count": len(rows)}


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str | None = Field(default=None, description="one repository; absent reads every repository the loop reads")
    backlog: bool | None = Field(
        default=None, description="run the backlog intake whatever its interval says (true), or not (false)"
    )


@router.post("/leads/run")
async def run_leads(request: Request, body: RunBody | None = None) -> dict[str, Any]:
    """Run one pass of the finders now and say what each found, stored and closed."""
    body = body or RunBody()
    await enforce_repo_allowlist(request, body.repo, action="run the finders")
    loop = getattr(request.app.state, "leads_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="the leads loop is not initialized")
    repos = [body.repo] if body.repo else None
    return {"repos": await loop.tick(repos, backlog=body.backlog)}


# ---- an owner's yes ----


async def _dispatch_issue(
    request: Request, issue_id: str, status: str, *, tracker: str, by: str, run_with: RunWith | None
) -> str:
    from embry0.trackers.dispatch import dispatch_open_issue

    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    job_id = await dispatch_open_issue(
        issue_id,
        status,
        request.app.state.issues_repo,
        executor,
        tracker=tracker,
        reason="lead_accepted",
        actor=by,
        run_with=run_with,
    )
    if job_id is None:
        raise HTTPException(status_code=502, detail="the dispatch failed; the issue was returned to open, accept again")
    return job_id


async def _start_ticket(request: Request, row: dict[str, Any], by: str, run_with: RunWith | None) -> str:
    """A backlog ticket runs through its tracker's own path, as if its trigger label had gone on."""
    from embry0.storage.schemas import IssueStatus
    from embry0.trackers.dispatch import create_tracked_issue
    from embry0.trackers.models import TrackerIssue

    data = dict(row.get("data") or {})
    repo, tracker, key = str(row["repo"]), str(data.get("tracker") or ""), str(data.get("key") or "")
    if tracker == "linear":
        from embry0.api.trigger_context import current_linear_sync, linear_trigger_context
        from embry0.api.v1.cases import _linear_refusal

        linear = await current_linear_sync(request)
        if linear is None:
            raise HTTPException(status_code=503, detail="the Linear integration is not configured")
        ctx = await linear_trigger_context(request, actor=by)
        ctx.run_with = run_with
        result = await linear.label_and_dispatch(key, ctx, created_by="lead-accept", expected_repo=repo)
        job_id = str(result.get("job_id") or "")
        if result.get("action") != "dispatched" or not job_id:
            raise _linear_refusal(key, result)
        return job_id
    if tracker != "github":
        raise HTTPException(status_code=409, detail=f"a {tracker or 'trackerless'} ticket cannot be started here")
    from embry0.api.v1.cases import _github_issues

    fetched = await _github_issues(request).get_issue(repo, int(key))
    if fetched is None:
        raise HTTPException(status_code=502, detail=f"GitHub could not serve {repo}#{key}")
    if str(fetched.get("state") or "open") != "open":
        raise HTTPException(status_code=409, detail=f"{repo}#{key} is {fetched.get('state')} on GitHub")
    issues = request.app.state.issues_repo
    existing = await issues.get_by_tracker_ref("github", repo, key)
    if existing is not None:
        issue_id, status = str(existing["id"]), str(existing.get("status") or "")
    else:
        issue_id = await create_tracked_issue(
            issues,
            TrackerIssue(
                tracker="github",
                key=key,
                title=str(fetched.get("title") or row.get("title") or ""),
                body=str(fetched.get("body") or ""),
                url=str(fetched.get("html_url") or row.get("url") or ""),
                labels=[str(lbl.get("name")) for lbl in fetched.get("labels") or [] if isinstance(lbl, dict)],
                repo=repo,
                state="open",
            ),
            created_by="lead-accept",
        )
        status = str(IssueStatus.OPEN)
    if status != IssueStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"{repo}#{key} has a job already, or is closed in embry0")
    return await _dispatch_issue(request, issue_id, status, tracker="github", by=by, run_with=run_with)


async def _start_review(request: Request, row: dict[str, Any], by: str, run_with: RunWith | None) -> str:
    """A follow-up job on the pull request's own branch, as a tagged PR comment starts one."""
    data = dict(row.get("data") or {})
    repo, head = str(row["repo"]), str(data.get("head_ref") or "")
    if not head:
        raise HTTPException(status_code=409, detail="the pull request's branch is not known; it cannot be followed up")
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    refusal = await executor.dispatch_refusal(actor=by)
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    task = lead_task(row)
    jobs = request.app.state.jobs_repo
    job_id: str = await jobs.create(
        repo=repo,
        task=task,
        trace_id=f"trc-{uuid.uuid4().hex[:12]}",
        context={"type": "git", "repo": repo, "ref": head},
        pipeline_template="pr-followup",
        pipeline_config=apply_run_with(None, run_with),
        dispatched_by=by,
    )
    if row.get("url"):
        await jobs.update(job_id, pr_url=row["url"])
    executor.submit_job(job_id, repo=repo, title=f"[review] {repo}#{data.get('number')}", body=task, kind="lead")
    return job_id


async def start_lead(request: Request, lead_id: str, *, by: str, run_with: RunWith | None) -> str:
    """Start the work an open lead offers and return the job's id; HTTPException as the routes answer."""
    row = await _repo(request).get(lead_id)
    if row is None or row.get("state") != "open":
        raise HTTPException(status_code=404, detail="no open lead with that id")
    kind = str(row["kind"])
    if kind == "ticket":
        return await _start_ticket(request, row, by, run_with)
    if kind == "review":
        return await _start_review(request, row, by, run_with)
    # ci, flaky, alert: work nobody wrote up becomes an issue in embry0 with the task, then runs as one.
    labels = ["embry0-lead", f"lead:{kind}"]
    if row.get("area"):
        from embry0.services.areas import Area

        labels.append(f"area:{Area(str(row['area'])).slug}")
    issue_id = await request.app.state.issues_repo.create(
        title=str(row["title"])[:300],
        body=lead_task(row),
        labels=labels,
        repo=str(row["repo"]),
        created_by="lead-accept",
    )
    return await _dispatch_issue(request, issue_id, "open", tracker="", by=by, run_with=run_with)
