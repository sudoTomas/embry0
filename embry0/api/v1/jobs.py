"""Jobs API — create, list, get, cancel jobs."""

import json
import os
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from embry0.api.deps import get_jobs_repo, request_actor, require_operator
from embry0.api.repo_allowlist import enforce_repo_allowlist, refusal_detail
from embry0.api.schemas import JobCreateRequest, JobListResponse, JobResponse, QAJobOverrides
from embry0.api.schemas.jobs import JobResumeRequest
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.helpers import emit_audit
from embry0.safety.redaction import redact_event, secret_env_values
from embry0.services.answer_recorder import record_answer
from embry0.services.run_with import RunWith, apply_run_with
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.repos import UnregisteredRepoError

logger = structlog.get_logger(__name__)

router = APIRouter()


class AnswerJobInputRequest(BaseModel):
    answer: str


CREATE_A_JOB = "create a job"


async def check_agent_models(request: Request, agent_models: dict[str, str] | None) -> None:
    # RAV-1297: explicit per-agent model overrides must name enabled models —
    # a clear 422 at submit beats the runtime tier-fallback substituting one.
    # Fail-open when the repo isn't wired (minimal test apps).
    if agent_models:
        from embry0.services.model_enablement import UnpricedModelError, ensure_models_priced

        try:
            ensure_models_priced(agent_models.values())
        except UnpricedModelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider_repo = getattr(request.app.state, "provider_repo", None)
    if agent_models and provider_repo is not None:
        from embry0.services.model_enablement import DisabledModelError, ensure_models_enabled

        try:
            await ensure_models_enabled(agent_models.values(), provider_repo)
        except DisabledModelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def onboard_refusal(request: Request, repo: str) -> str | None:
    """What :func:`start_onboard_job` would refuse the caller with on ``repo`` (no agent
    model overrides), or None; no log and no audit row. A dry run answers with this."""
    detail = refusal_detail(request, repo, action=CREATE_A_JOB)
    if detail is not None:
        return detail
    if getattr(request.app.state, "issue_executor", None) is None:
        return "executor not initialized"
    return None


async def start_onboard_job(
    request: Request,
    *,
    repo: str,
    branch: str | None,
    skip_smoke: bool = False,
    agent_models: dict[str, str] | None = None,
    proposal_id: str | None = None,
    run_with: RunWith | None = None,
) -> str:
    """EMB-50: start an onboard job after every check ``POST /jobs`` runs first.

    The calling key's repository allowlist, the agent model overrides and
    the executor, then the dispatch under the caller's actor, which the
    executor's ``onboard.job_created`` audit row names. ``POST /jobs {"pipeline": "onboard"}`` and the
    organization onboard action both start their jobs here, so the action
    cannot start a job the caller's key could not start through the route.
    ``proposal_id`` runs it in propose mode (ADR 0019): the draft is stored on
    that setup proposal, not as the test setup. Without a ``branch`` the
    executor resolves the repo's base branch (#29).
    Raises HTTPException as the route answers.
    """
    await enforce_repo_allowlist(request, repo, action=CREATE_A_JOB)
    await check_agent_models(request, agent_models)
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    try:
        job_id: str = await executor.start_onboard_job(
            repo=repo,
            branch=branch,
            skip_smoke=skip_smoke,
            agent_models=agent_models,
            dispatched_by=request_actor(request),
            proposal_id=proposal_id,
            run_with=run_with,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job_id


@router.post("/jobs", status_code=201)
async def create_job(
    req: JobCreateRequest, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)
) -> dict[str, Any]:
    # EMB-50: onboard pipeline — analyze the repo + generate its qa.yaml into
    # the external config store. Issue-less, like qa. Without a branch the
    # executor resolves the repo's base branch (#29). Its checks live in
    # start_onboard_job, which the organization onboard action shares.
    run_repo = req.repo or (req.context.repo if req.context else None)
    run_with = await checked_run_with(request, req.run_with, run_repo)
    if req.pipeline == "onboard":
        assert req.repo is not None  # noqa: S101 — schema-enforced for onboard
        job_id = await start_onboard_job(
            request,
            repo=req.repo,
            branch=req.branch or None,
            skip_smoke=req.skip_smoke,
            agent_models=req.agent_models,
            run_with=run_with,
        )
        job = await jobs.get(job_id)
        return job or {"job_id": job_id}

    # The schema has folded `repo` into `context` for git jobs; a non-git job
    # names no repository, which a repository-restricted key may not start.
    await enforce_repo_allowlist(request, run_repo, action=CREATE_A_JOB)
    await check_agent_models(request, req.agent_models)

    # QA pipeline takes a different code path: no triage, no issue, dispatched
    # via IssueExecutor.start_job which seeds the QA initial state.
    if req.pipeline == "qa":
        if not req.branch:
            raise HTTPException(status_code=400, detail="qa pipeline requires 'branch'")
        executor = getattr(request.app.state, "issue_executor", None)
        if executor is None:
            raise HTTPException(status_code=503, detail="executor not initialized")
        qa_overrides = (req.qa or QAJobOverrides()).model_dump()
        try:
            job_id = await executor.start_job(
                repo=req.repo,
                branch=req.branch,
                pipeline="qa",
                qa_overrides=qa_overrides,
                agent_models=req.agent_models,
                dispatched_by=request_actor(request),
                run_with=run_with,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = await jobs.get(job_id)
        return job or {"job_id": job_id}

    # #30: review one pull request now. Skips the repo's pr_review.enabled
    # setting, never its other gates; the dispatcher says why when it refuses.
    if req.pipeline == "pr-review":
        dispatcher = getattr(request.app.state, "pr_review_dispatcher", None)
        if dispatcher is None:
            raise HTTPException(status_code=503, detail="pull request review is not initialized")
        assert req.repo is not None and req.number is not None  # noqa: S101 — schema-enforced
        try:
            chosen = {"run_with": run_with} if run_with is not None else {}
            result = await dispatcher.dispatch_manual(
                repo=req.repo, number=req.number, actor=request_actor(request), **chosen
            )
        except UnregisteredRepoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.reason == "repo_not_registered":
            raise HTTPException(status_code=400, detail=f"review not started: {result.reason}")
        if not result.dispatched or not result.job_id:
            raise HTTPException(status_code=409, detail=f"review not started: {result.reason}")
        job = await jobs.get(result.job_id)
        return job or {"job_id": result.job_id}

    # Legacy issue-to-pr path. ``task`` non-empty is enforced at the schema
    # layer (JobCreateRequest._enforce_task_for_non_qa) — assertion below is a
    # safety net for type checkers.
    assert req.task is not None  # noqa: S101 — schema-enforced

    # Merge per-job overrides into pipeline_config so triage/dispatch can
    # honour them downstream (see IssueExecutor._run_workflow + agent node).
    # RAV-1058: max_budget_usd and the execution/auth mode overrides used to be
    # accepted by the schema and silently dropped here — linear_dispatch and
    # the dashboard both send them expecting effect.
    effective_config = dict(req.pipeline_config) if req.pipeline_config else None
    if req.agent_models:
        effective_config = {**(effective_config or {}), "agent_models_override": req.agent_models}
    if req.max_budget_usd is not None:
        effective_config = {**(effective_config or {}), "max_budget_usd": req.max_budget_usd}
    if req.execution_mode_override:
        effective_config = {**(effective_config or {}), "execution_mode_override": req.execution_mode_override}
    if req.auth_mode_override:
        effective_config = {**(effective_config or {}), "auth_mode_override": req.auth_mode_override}
    effective_config = apply_run_with(effective_config, run_with)

    trace_id = f"trc-{uuid.uuid4().hex[:12]}"
    job_id = await jobs.create(
        repo=req.repo,
        task=req.task,
        issue_number=req.issue_number,
        pipeline_template=req.pipeline_template,
        pipeline_config=effective_config,
        sandbox_profile=req.sandbox_profile,
        trace_id=trace_id,
        context=req.context.model_dump() if req.context else None,
        # #246: the API principal is this job's actor — the identity the
        # dispatch rate limit buckets on.
        dispatched_by=request_actor(request),
    )
    config = request.app.state.config
    db = getattr(request.app.state, "db", None)
    await emit_audit(
        db,
        "job_created",
        actor=request_actor(request),
        details={"job_id": job_id, "repo": req.repo},
        audit_log_path=config.audit_log_path,
    )

    # Dispatch the issue-to-pr workflow in the background. Without this the row
    # would sit in `pending` forever: the direct POST /jobs path created the job
    # but never ran it (only the issues path dispatched the workflow). The
    # workflow tolerates a null issue_id for an issue-less, API-submitted job.
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    executor.submit_job(
        job_id,
        repo=req.repo,
        title=req.task,
        github_number=req.issue_number,
        additional_context=req.additional_context or "",
    )

    job = await jobs.get(job_id)
    return job or {"job_id": job_id}


BRIEF_TASK_CHARS = 200


def _brief_task(task: str) -> tuple[str, bool]:
    """The first line of a task, cut to ``BRIEF_TASK_CHARS``.

    A job's task is its whole prompt. A watcher analysis job's prompt is the
    log briefing it analysed, tens of thousands of characters, and a list page
    of twenty such jobs carried close to a megabyte of text the table clipped
    to one cell. Lists ask for the brief form; the full text stays on
    ``GET /jobs/{job_id}``.
    """
    first_line = task.split("\n", 1)[0]
    cut = first_line[:BRIEF_TASK_CHARS]
    if cut == task:
        return task, False
    return cut.rstrip() + "…", True


@router.get("/jobs")
async def list_jobs(
    status: str | None = None,
    repo: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    brief: bool = Query(
        default=False,
        description=(
            "Cut each task to its first line, at most 200 characters, and set "
            "task_truncated. The full text is on GET /jobs/{job_id}."
        ),
    ),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> JobListResponse:
    rows, total = await jobs.list_all(status=status, repo=repo, limit=limit, offset=offset)
    items = [JobResponse(**r) for r in rows]
    if brief:
        for item in items:
            item.task, item.task_truncated = _brief_task(item.task)
    return JobListResponse(jobs=items, total=total, offset=offset, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("account_wait") and job.get("status") != "paused":
        # Spent account waits: only a paused job waits; a cancelled one keeps the stale record.
        job["account_wait"] = None
    # Per-agent cost breakdown (E7 lean variant — aggregates existing traces rows).
    traces_repo = getattr(request.app.state, "traces_repo", None)
    if traces_repo is not None:
        try:
            job["cost_breakdown"] = await traces_repo.sum_by_agent_for_job(job_id)
        except Exception:
            import structlog

            structlog.get_logger(__name__).warning("cost_breakdown_query_failed", job_id=job_id, exc_info=True)
            job["cost_breakdown"] = []
        # #281: why each errored run failed — diagnosis + redacted stderr tail.
        # Served here (dashboard, MCP job_get) and nowhere public.
        try:
            job["agent_failures"] = await traces_repo.failures_for_job(job_id)
        except Exception:
            import structlog

            structlog.get_logger(__name__).warning("agent_failures_query_failed", job_id=job_id, exc_info=True)
            job["agent_failures"] = []
    return job


@router.get("/jobs/{job_id}/inputs")
async def get_job_inputs(job_id: str, request: Request) -> list[Any]:
    """Get all inputs (questions) for a job."""
    inputs_repo = request.app.state.inputs_repo
    result: list[Any] = await inputs_repo.list_by_job(job_id)
    return result


LOG_EVENTS_PAGE_DEFAULT = 500
LOG_EVENTS_PAGE_MAX = 1000


@router.get("/jobs/{job_id}/logs/events")
async def get_job_log_events(
    job_id: str,
    since_seq: int = Query(0, ge=0, description="Return only events with a seq greater than this."),
    limit: int = Query(LOG_EVENTS_PAGE_DEFAULT, ge=1, le=LOG_EVENTS_PAGE_MAX),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> dict[str, Any]:
    """Get one page of persisted pipeline events for a job, oldest first.

    Each event carries its ``event_seq`` (the same cursor the WS replay
    uses). To read the whole log, call again with
    ``since_seq=next_since_seq`` while ``has_more`` is true (#203).
    """
    # One row past the page tells "exactly limit rows" from "more to come".
    rows = await jobs.get_log_events(job_id, since_seq=since_seq, limit=limit + 1)
    has_more = len(rows) > limit
    result = []
    next_since_seq = since_seq
    for e in rows[:limit]:
        row_id = e.get("id")
        if isinstance(row_id, int):
            next_since_seq = max(next_since_seq, row_id)
        payload = e.get("payload", {})
        if isinstance(payload, dict):
            if isinstance(row_id, int):
                payload = {**payload, "event_seq": row_id}
            result.append(payload)
    return {"events": result, "next_since_seq": next_since_seq, "has_more": has_more}


def _as_object(value: Any) -> Any:
    """A jsonb column as the dashboard expects it: asyncpg hands it back decoded
    when the pool has a codec and as text when it does not."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@router.get("/jobs/{job_id}/verification")
async def get_job_verification(job_id: str, request: Request) -> dict[str, Any]:
    """The job's verification result (docs/verification.md, "The result"): the
    ``job_verifications`` row the verify node wrote, 404 when the job has none."""
    repo = getattr(request.app.state, "job_verifications_repo", None)
    row = await repo.get(job_id) if repo is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="No verification for this job")
    row = dict(row)
    row["report"] = _as_object(row.get("report"))
    return row


async def _job_scrub_values(request: Request, job_id: str) -> list[str]:
    """The values a transcript of this job is scrubbed with: the registry and
    this process's secret-looking env vars, plus the job's own env vars as the
    sandbox had them. The sandbox scrubbed the event stream with
    ``secret_env_values()`` over an environment that held the job's decrypted
    env vars (embry0/sandbox/runner.py); the stored messages never passed
    through that, so the route rebuilds that environment with the
    orchestrator's own loader and adds every secret-typed value outright. A
    job or repo that cannot be resolved scrubs with what the process has."""
    executor = getattr(request.app.state, "issue_executor", None)
    jobs = getattr(request.app.state, "jobs_repo", None)
    rows: list[dict[str, Any]] | None = None
    if executor is not None and jobs is not None:
        try:
            job = await jobs.get(job_id)
            repo_name = job.get("repo") if job else None
            if repo_name:
                rows = await executor._load_merged_env_vars(repo_name, job_id)
            else:
                logger.debug("session_scrub_repo_unresolved", job_id=job_id)
        except Exception:
            logger.debug("session_scrub_env_vars_unavailable", job_id=job_id, exc_info=True)
    environ = dict(os.environ)
    secret_typed: list[str] = []
    for row in rows or []:
        environ[str(row["key"])] = str(row["value"])
        if row.get("var_type") == "secret":
            secret_typed.append(str(row["value"]))
    return secret_env_values(environ, extra=secret_typed)


@router.get("/jobs/{job_id}/agents/{agent_type}/session", dependencies=[require_operator])
async def get_agent_session(job_id: str, agent_type: str, request: Request) -> Response:
    """The stored conversation of one agent for this job (``agent_sessions``
    is one row per job and agent type, its ``messages`` merged across the
    agent's runs), every message passed through :func:`redact_event`. 204 when
    the row holds only a CLI ``session_blob`` (not exposed), 404 when there is
    no row. Operator tier: a transcript shows what the agent saw in the
    sandbox, scrubbed after the fact, where the event stream was scrubbed as
    it was written; a read key gets 403."""
    repo = getattr(request.app.state, "agent_sessions_repo", None)
    row = await repo.get(job_id, agent_type) if repo is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="No stored session for this agent")
    messages = _as_object(row.get("messages"))
    if not messages:
        return Response(status_code=204)
    known = await _job_scrub_values(request, job_id)
    last_turn_at = row.get("last_turn_at")
    return JSONResponse(
        {
            "job_id": row.get("job_id", job_id),
            "agent_type": row.get("agent_type", agent_type),
            "mode": row.get("mode"),
            "session_id": row.get("session_id"),
            "last_turn_at": last_turn_at.isoformat() if hasattr(last_turn_at, "isoformat") else last_turn_at,
            # Wrapped so a plain-string message is scrubbed like a dict's values.
            "messages": [redact_event({"m": m}, known)["m"] for m in messages],
        }
    )


@router.post("/jobs/{job_id}/events-token")
async def create_events_token(job_id: str, request: Request) -> dict[str, Any]:
    """Mint a short-lived, single-job WS token for browser event streams.

    Authenticated with the normal Bearer header (router-level deps); the
    returned token is the only credential the browser puts in the WS
    subprotocol — the raw API key never rides the handshake. The SSE
    events token works the same way.
    """
    # RAV-1070; the SSE events-token precedent is RAV-1060.
    from embry0.api.scoped_tokens import mint_scoped_token, signing_material

    token, expires_in = mint_scoped_token(signing_material(request.app.state.config), f"ws:{job_id}")
    return {"token": token, "expires_in": expires_in}


@router.post("/jobs/{job_id}/inputs/{input_id}/answer")
async def answer_job_input(
    job_id: str,
    input_id: str,
    req: AnswerJobInputRequest,
    request: Request,
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> dict[str, Any]:
    """Answer an agent question on a job — the issue-less-job path.

    Direct POST /jobs runs have no issue row, so the issue-scoped answer
    endpoint cannot serve them. Mirrors its contract: records the answer,
    and when no blocking questions remain pending for the job, resumes the
    workflow from its checkpoint with the accumulated Q&A as context.
    Issue-backed inputs should keep using the issues endpoint (its resume
    path also updates the issue row), but this one tolerates them.
    """
    # EMB-43.
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # An answer resumes the job once no blocking question is left.
    await enforce_repo_allowlist(request, job.get("repo"), action="answer a question on a job")

    inputs_repo = request.app.state.inputs_repo
    inp = await inputs_repo.get(input_id)
    if not inp or inp["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="Input not found")
    # RAV-1059: auto_answered rows stay answerable — a human answer overrides
    # the orchestrator's auto-answer post-hoc (the state.py contract). The
    # override lands in the accumulated Q&A context the agent sees on its
    # next resume; only a real human answer is final.
    if inp["status"] == "answered":
        raise HTTPException(status_code=409, detail="Input has already been answered")

    # RAV-1076: shared record path — persists the answer, mirrors it to the
    # Telegram question message, and posts the Q&A on the linked Linear issue.
    # answered_by carries the real principal (session email / key name), not
    # the historical hardcoded "user".
    await record_answer(
        inp=inp,
        answer=req.answer,
        answered_by=request_actor(request),
        channel="api",
        inputs_repo=inputs_repo,
        issues_repo=getattr(request.app.state, "issues_repo", None),
        tracker_channels=getattr(request.app.state, "tracker_channels", None),
        config=getattr(request.app.state, "config", None),
    )

    pending = await inputs_repo.count_pending_blocking_for_job(job_id)
    if pending == 0:
        fresh = await jobs.get(job_id)
        if fresh and fresh["status"] == "awaiting_input":
            answered = await inputs_repo.list_all_answered_for_job(job_id)
            context_lines = [
                f"Q: {a['question']}\nA: {a.get('answer') or a.get('auto_answer') or ''}" for a in answered
            ]
            executor = request.app.state.issue_executor
            executor.schedule_resume(
                inp.get("issue_id") or None,
                job_id,
                "\n\n".join(context_lines),
                kind="resume_via_job_answer",
            )

    updated = await inputs_repo.get(input_id)
    return dict(updated or {})


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    req: JobResumeRequest,
    request: Request,
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> dict[str, Any]:
    """Resume a paused job with optional guidance."""
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await enforce_repo_allowlist(request, job.get("repo"), action="resume a job")
    if job["status"] != "paused":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not paused")

    executor = request.app.state.issue_executor
    if job.get("account_wait"):
        # Spent account waits (spec 2026-10-25): a job parked for its account
        # stops waiting on "abandon" and otherwise goes on now ("Try now").
        # One whose account is still spent is parked again by the pre-check.
        if req.choice == "abandon":
            await executor.cancel_job(job_id, actor=request_actor(request))
            return {"job_id": job_id, "status": "cancelled", "choice": "abandon"}
        if executor.draining:
            raise HTTPException(status_code=503, detail="embry0 is shutting down; try again once it is back")
        if not await jobs.claim_account_wait(job_id):
            raise HTTPException(status_code=409, detail="Job is no longer waiting for an account")
        executor.go_on_after_account_wait(job)
        return {"job_id": job_id, "status": "resuming", "choice": "account_ready"}

    # issue_id is None for issue-less, API-submitted jobs (POST /jobs with a
    # task string). The workflow tolerates that on first run (submit_job
    # dispatches with a null issue_id from create_job) and
    # IssuesRepository.update() no-ops on a missing issue, so resume must
    # tolerate it too — otherwise every paused API job is unrecoverable (its
    # interrupt options can never be exercised; only /cancel works).
    issue_id = job.get("issue_id") or None

    executor.schedule_resume(
        issue_id,
        job_id,
        {"choice": req.choice, "guidance": req.guidance},
        kind="resume_via_api",
    )

    return {"job_id": job_id, "status": "resuming", "choice": req.choice}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await enforce_repo_allowlist(request, job.get("repo"), action="cancel a job")
    if job["status"] not in ("pending", "running", "awaiting_input", "paused"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel job in {job['status']} state")

    executor = request.app.state.issue_executor
    actor = request_actor(request)
    await executor.cancel_job(job_id, actor=actor)

    return {"job_id": job_id, "status": "cancelled"}
