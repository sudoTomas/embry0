"""Reviewing a finished job: a person's verdict on its work, and what they do with it.

- ``PUT /jobs/{job_id}/evaluation``: a rating (good, needs work, bad) with a
  note, or that the person merged the pull request.
- ``POST /jobs/{job_id}/send-back``: a follow-up on the job's pull request
  branch with the person's notes.
- ``POST /jobs/{job_id}/run-again``: the job started again as a new job.
- ``POST /jobs/{job_id}/dismiss``: seen, nothing to do.
- ``POST /jobs/{job_id}/reject``: the work is not wanted; optionally closes
  its pull request.

Each answer is stored as the job's evaluation (``job_ratings``, one row per
job), recorded as a ``job.review.*`` audit row, and settles the job's
decision hand-off when it has one. Reviewing is core work: the guild (the
game layer, ``embry0/api/v1/world.py``) only reads the evaluation. The rows
are still read and written through ``WorldRepository`` (``quest`` and
``rate``). These routes were the world's, under ``/world/quests/{job_id}/…``;
those paths stay as deprecated aliases (:data:`deprecated_router`) until
:data:`ALIASES_REMOVED_IN`.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import ensure_admin, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.run_with import apply_run_with, models_with

router = APIRouter()

#: The release that removes the ``/world/quests/{job_id}/…`` aliases: the one after the release that added the
#: ``/jobs/{job_id}/…`` routes. Removing a route needs a minor release before 1.0 (docs/releasing.md).
ALIASES_REMOVED_IN = "0.2.0"

#: A GitHub pull request's URL: its repository and number.
GITHUB_PR = re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)")

#: Job statuses of work that has not finished: it cannot be evaluated, run again or dismissed yet.
_NOT_FINISHED = ("pending", "running", "awaiting_input", "paused")

#: A review answer as a decision's outcome (decisions, spec 2026-10-27); an evaluation's is its verdict or rating.
_OUTCOMES: dict[str, str] = {
    "job.review.sent_back": "send_back",
    "job.review.rejected": "reject",
    "job.review.restarted": "run_again",
    "job.review.dismissed": "dismiss",
}


def _state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return value


def _iso(at: Any) -> str | None:
    if not isinstance(at, datetime):
        return None
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).isoformat()


def _stats_changed(request: Request) -> None:
    """The guild's cached agent stats count reviews: read them again on the next request."""
    cache = getattr(request.app.state, "world_cache", None)
    if cache is not None:
        cache.drop("stats")


def _json_field(value: Any) -> Any:
    """A JSONB column as the row gave it: decoded already, or text to decode."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


async def _job_or_404(request: Request, job_id: str) -> dict[str, Any]:
    """The job with its evaluation so far; 404 for an unknown job, 403 outside the key's repositories."""
    job = await _state(request, "world_repo").quest(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    await enforce_repo_allowlist(request, job.get("repo"), action="review a job")
    return dict(job)


def _refuse_unfinished(job: dict[str, Any], what: str) -> None:
    if job.get("status") in _NOT_FINISHED:
        raise HTTPException(status_code=409, detail=f"the job has not finished: {what} it once it has")


async def _audit(request: Request, action: AuditAction, job_id: str, **fields: Any) -> dict[str, Any]:
    """Record a person's evaluation of ``job_id`` and its audit row; the evaluation as it now stands."""
    from embry0.services.handoffs import settle

    actor = request_actor(request)
    row = await _state(request, "world_repo").rate(job_id, rated_by=actor, **fields)
    outcome = _OUTCOMES.get(action) or (
        "merged" if fields.get("verdict") == "merged" else str(fields.get("rating") or "rated")
    )
    await settle(request.app.state, f"done:{job_id}", outcome, actor)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=actor,
        details={"job_id": job_id, **{k: v for k, v in fields.items() if k != "note"}},
        audit_log_path=getattr(getattr(request.app.state, "config", None), "audit_log_path", None),
    )
    return {
        "job_id": job_id,
        "rating": row.get("rating"),
        "verdict": row.get("verdict"),
        "note": row.get("note") or "",
        "followup_job_id": row.get("followup_job_id"),
        "by": row.get("rated_by"),
        "at": _iso(row.get("rated_at")),
    }


class EvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["good", "needs_work", "bad"] | None = None
    #: "merged" when the person merged the pull request (through its merge approval or the forge).
    verdict: Literal["merged"] | None = None
    note: str = Field(default="", max_length=4_000)


@router.put("/jobs/{job_id}/evaluation")
async def evaluate_job(job_id: str, body: EvaluationBody, request: Request) -> dict[str, Any]:
    """A person's rating of the work (good, needs work, bad, with a note), or that they merged it."""
    if body.rating is None and body.verdict is None and not body.note:
        raise HTTPException(status_code=422, detail="give a rating, a verdict or a note")
    _refuse_unfinished(await _job_or_404(request, job_id), "evaluate")
    return await _audit(
        request, "job.review.evaluated", job_id, rating=body.rating, verdict=body.verdict, note=body.note
    )


class SendBackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(min_length=1, max_length=8_000, description="What the work still needs, in the person's words")
    run_with: RunWithRequest | None = None


@router.post("/jobs/{job_id}/send-back", status_code=202)
async def send_back(job_id: str, body: SendBackBody, request: Request) -> dict[str, Any]:
    """Send the work back with notes: a follow-up job on the job's own pull request branch."""
    job = await _job_or_404(request, job_id)
    match = GITHUB_PR.match(str(job.get("pr_url") or ""))
    if match is None:
        raise HTTPException(status_code=409, detail="the job opened no GitHub pull request to send back")
    run_with = await checked_run_with(request, body.run_with, match["repo"])
    dispatch = _state(request, "comment_dispatch")
    actor = request_actor(request)
    result = await dispatch.start_followup(
        match["repo"],
        int(match["number"]),
        request=f"sent back from the dashboard by {actor} with these notes:\n\n{body.notes}",
        dispatched_by=actor,
        title=f"[send-back] {match['repo']}#{match['number']}",
        kind="send_back",
        asks="the notes",
        pipeline_config=apply_run_with(None, run_with),
    )
    if not result.get("dispatched"):
        raise HTTPException(
            status_code=409, detail=f"not sent back: {str(result.get('reason') or 'refused').replace('_', ' ')}"
        )
    evaluation = await _audit(
        request,
        "job.review.sent_back",
        job_id,
        verdict="sent_back",
        rating="needs_work",
        note=body.notes,
        followup_job_id=str(result["job_id"]),
    )
    _stats_changed(request)
    return {**evaluation, "followup_job_id": result["job_id"]}


class RunAgainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_with: RunWithRequest | None = None


@router.post("/jobs/{job_id}/run-again", status_code=202)
async def run_again(job_id: str, request: Request, body: RunAgainBody | None = None) -> dict[str, Any]:
    """Start the job again as a new job, from what started it (its launch record).

    The new job copies the repo, task, pipeline and its settings; a model or
    key chosen with Run with replaces the old choice. The job's evaluation
    records the new job as its follow-up, so it leaves the review inbox.
    """
    _refuse_unfinished(await _job_or_404(request, job_id), "run it again")
    jobs = _state(request, "jobs_repo")
    job = await jobs.get(job_id)
    launch = _json_field((job or {}).get("launch"))
    if not job or not isinstance(launch, dict) or not launch.get("runner"):
        raise HTTPException(
            status_code=409,
            detail="this job has no launch record to start it again from; start it from its lead or ticket",
        )
    repo = str(job.get("repo") or "")
    run_with = await checked_run_with(request, body.run_with if body else None, repo) if repo else None
    pipeline_config = apply_run_with(_json_field(job.get("pipeline_config")), run_with)
    if run_with is not None and run_with.model and launch.get("runner") in ("qa", "onboard"):
        launch = {**launch, "agent_models": models_with(launch.get("agent_models"), run_with)}
    actor = request_actor(request)
    new_id = await jobs.create(
        repo=job.get("repo"),
        task=job.get("task"),
        issue_number=job.get("issue_number"),
        issue_id=job.get("issue_id"),
        pipeline_template=job.get("pipeline_template"),
        pipeline_config=pipeline_config,
        sandbox_profile=job.get("sandbox_profile"),
        context=_json_field(job.get("context")),
        dispatched_by=actor,
    )
    carried = {k: job[k] for k in ("job_kind", "area") if job.get(k)}
    if carried:
        await jobs.update(new_id, **carried)
    _state(request, "issue_executor").run_again(
        new_id, launch, issue_id=job.get("issue_id"), message=f"Started again from {job_id} by {actor}."
    )
    evaluation = await _audit(request, "job.review.restarted", job_id, followup_job_id=new_id)
    _stats_changed(request)
    return {**evaluation, "followup_job_id": new_id}


@router.post("/jobs/{job_id}/dismiss")
async def dismiss(job_id: str, request: Request) -> dict[str, Any]:
    """Seen, nothing to do: the job leaves the review inbox without a rating or a verdict."""
    _refuse_unfinished(await _job_or_404(request, job_id), "dismiss")
    return await _audit(request, "job.review.dismissed", job_id)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=4_000)
    #: Close the job's open pull request, with the note as its last comment.
    close_pr: bool = False


@router.post("/jobs/{job_id}/reject")
async def reject(job_id: str, body: RejectBody, request: Request) -> dict[str, Any]:
    """The work is not wanted: record it, and close its pull request when asked."""
    job = await _job_or_404(request, job_id)
    if body.close_pr:
        # Closing a pull request is a forge action as weighty as merging one,
        # and a merge already needs admin (merge approvals).
        await ensure_admin(request)
    closed = False
    match = GITHUB_PR.match(str(job.get("pr_url") or ""))
    if body.close_pr and match is not None and job.get("delivery_state") != "merged":
        organizations = _state(request, "organizations_repo")
        org = await organizations.get(match["repo"].split("/", 1)[0])
        if org is None:
            raise HTTPException(
                status_code=409, detail=f"no registered organization holds {match['repo']}'s credential"
            )
        from embry0.api.v1.organizations import org_forge_client

        client = await org_forge_client(request, org)
        repo, number = match["repo"], int(match["number"])
        if body.note:
            await client.post(f"/repos/{repo}/issues/{number}/comments", repo=repo, json={"body": body.note})
        resp = await client.patch(f"/repos/{repo}/pulls/{number}", repo=repo, json={"state": "closed"})
        if resp.status_code >= 300:
            raise HTTPException(
                status_code=502, detail=f"the forge did not close the pull request ({resp.status_code})"
            )
        closed = True
    evaluation = await _audit(request, "job.review.rejected", job_id, verdict="rejected", rating="bad", note=body.note)
    _stats_changed(request)
    return {**evaluation, "closed_pr": closed}


#: The routes that had a path under the world before reviewing moved out of the game layer, as (method, path).
#: Only these get a deprecated alias: a route added here later never had an old path.
_WORLD_PATHS: tuple[tuple[str, str], ...] = (
    ("PUT", "/jobs/{job_id}/evaluation"),
    ("POST", "/jobs/{job_id}/send-back"),
    ("POST", "/jobs/{job_id}/run-again"),
    ("POST", "/jobs/{job_id}/dismiss"),
    ("POST", "/jobs/{job_id}/reject"),
)


def _deprecated_aliases() -> APIRouter:
    """Each route of :data:`_WORLD_PATHS` at its old path under the world, served by the same handler, deprecated.

    Raises at import when one of them is not on :data:`router`, so the alias cannot quietly disappear.
    """
    served = {
        (method, r.path): r for r in router.routes if isinstance(r, APIRoute) for method in sorted(r.methods or ())
    }
    missing = [f"{method} {path}" for method, path in _WORLD_PATHS if (method, path) not in served]
    if missing:
        raise RuntimeError(f"job review routes with a world alias are not on the router: {missing}")
    aliases = APIRouter()
    for method, path in _WORLD_PATHS:
        route = served[(method, path)]
        aliases.add_api_route(
            path.replace("/jobs/{job_id}/", "/world/quests/{job_id}/", 1),
            route.endpoint,
            methods=[method],
            status_code=route.status_code,
            deprecated=True,
            name=f"{route.name}_world_alias",
            summary=f"{route.summary or route.name.replace('_', ' ').capitalize()} (deprecated)",
            description=(
                f"Deprecated: use `{method} /api/v1{path}`, which this path calls. "
                f"It is removed in {ALIASES_REMOVED_IN}.\n\n{inspect.cleandoc(route.endpoint.__doc__ or '')}"
            ),
        )
    return aliases


#: ``/world/quests/{job_id}/…``, where these routes were before reviewing moved out of the game layer.
deprecated_router = _deprecated_aliases()
