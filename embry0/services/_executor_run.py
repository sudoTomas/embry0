"""Workflow-run internals of IssueExecutor (RAV-1235 split).

Mixin methods annotate ``self: IssueExecutor`` — they run only as part of
that class and use its repositories/config. Behavior unchanged from the
pre-split module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from embry0.services.issue_executor import IssueExecutor

from embry0.orchestration.checkpoint import checkpointer_context
from embry0.orchestration.restart import NOT_RESUMABLE_PIPELINES, ShutdownInterrupt
from embry0.safety.error_codes import ErrorCode, classify_failure, classify_qa_outcome
from embry0.services._cost import resolve_job_total_cost
from embry0.services.healing.lifecycle import Origin
from embry0.services.healing.recorder import record_case_event
from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.trackers import github_number_of, linear_identifier_of

logger = structlog.get_logger(__name__)


async def _process_stream(
    self: IssueExecutor,
    graph: Any,
    input_value: Any,
    graph_config: dict[str, Any],
    issue_id: str | None,
    job_id: str,
    state_sink: dict[str, Any] | None = None,
    *,
    stop_on_drain: bool = False,
) -> tuple[dict[str, Any] | None, bool, Any]:
    """Stream graph execution events, detect interrupts.

    Returns (final_state, interrupted, interrupt_value).

    ``state_sink`` receives the same merged state as it accumulates. The
    return value is lost when the graph raises mid-run — which is exactly
    what ``overrun_mode=hard`` does — and the caller still needs the last
    known state to destroy the sandbox and record the cost (#246).

    With ``stop_on_drain``, a drain (#307) stops the run at a node boundary:
    once a node's update has streamed, LangGraph has its writes, so the
    stream is closed there (cancelling whatever node started next) and
    ``ShutdownInterrupt`` raised. The next process resumes from that point.
    """
    final_state: dict[str, Any] | None = None
    last_persisted_stage: str | None = None

    def _draining() -> bool:
        return stop_on_drain and bool(getattr(self, "draining", False))

    if _draining():
        raise ShutdownInterrupt(job_id)
    stream = graph.astream(
        input_value,
        config=graph_config,
        stream_mode=["updates", "custom"],
    )
    try:
        async for event in stream:
            mode = event[0] if isinstance(event, tuple) else "updates"
            data = event[1] if isinstance(event, tuple) else event

            if mode == "custom":
                await self._broadcast_event(job_id, data)
            elif mode == "updates":
                if isinstance(data, dict):
                    for _node_name, node_output in data.items():
                        if isinstance(node_output, dict):
                            final_state = {**(final_state or {}), **node_output}
                            if state_sink is not None:
                                state_sink.update(node_output)
                            # Persist the workflow stage on the jobs row at each
                            # node transition so pollers (the Console board)
                            # see it without a WS or log fetch. This is the
                            # single write point: every workflow — issue-to-pr
                            # and QA, fresh run and resume — streams through
                            # here. Best-effort: a stage write must never fail
                            # the workflow (same stance as checkpoint purge).
                            stage = node_output.get("current_stage")
                            if stage and stage != last_persisted_stage:
                                last_persisted_stage = stage
                                try:
                                    await self._jobs.update(job_id, current_stage=stage)
                                except Exception:
                                    logger.warning(
                                        "current_stage_persist_failed",
                                        job_id=job_id,
                                        stage=stage,
                                        exc_info=True,
                                    )
                # A node finished (custom events stream from inside one).
                if _draining():
                    raise ShutdownInterrupt(job_id)
    finally:
        # An async generator left mid-iteration is only closed when it is
        # garbage-collected; close it now so the graph's running nodes stop
        # and its pending checkpoint writes are flushed before we go on.
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()

    # Check if graph was interrupted
    state_snapshot = await graph.aget_state(graph_config)
    if state_snapshot and state_snapshot.next:
        interrupt_value = None
        if hasattr(state_snapshot, "tasks") and state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupt_value = task.interrupts[0].value
                    break
        return final_state, True, interrupt_value

    return final_state, False, None


def _overrun_of(exc: BaseException, state: dict[str, Any] | None) -> float:
    """How far past its per-job cap a failing run got, in USD (#246).

    `hard` mode raises from inside the agent node, so the ``updates`` dict
    that carried ``budget_overrun_usd`` never reaches state — the exception
    is the only place the figures survive. Any other crash falls back to
    whatever a previous (soft) crossing recorded on state.
    """
    from embry0.orchestration.budget import BudgetOverrunError

    if isinstance(exc, BudgetOverrunError):
        return max(0.0, exc.current_cost - exc.max_budget)
    return float((state or {}).get("budget_overrun_usd") or 0.0)


async def _refuse_if_over_budget(
    self: IssueExecutor,
    job_id: str,
    *,
    issue_id: str | None = None,
    actor: str | None = None,
) -> bool:
    """Fleet-wide dispatch gate (#246). True when the job was refused.

    Every background runner starts here, so the daily/monthly caps and the
    per-author rate limit are enforced once per dispatched job regardless of
    which entry point created it. It runs while the job is still ``pending``
    (#348), so a refusal goes straight to ``failed`` — the one
    pending -> failed transition the jobs repository allows — and the row
    never gets a ``started_at``. A refusal is a terminal failure rather than
    a silently-dropped job: the row carries ``ERR_BUDGET_REFUSED`` (its own
    code since #331 — the job never ran, unlike an in-run ``ERR_BUDGET_OVERRUN``
    hard stop, and only a refusal is exempt from rate-limit counts), an audit
    event names the limit, and the caller's ``finally`` still posts the
    outcome to the originating tracker.

    ``actor`` is ``jobs.dispatched_by`` — the human the trigger named. None
    means nobody identifiable asked, and the rate limit is skipped for those
    jobs rather than pooling every orchestrator-initiated run in one bucket.
    """
    from datetime import UTC, datetime

    gate = self._get_budget_gate()
    if gate is None:
        return False
    decision = await gate.check(actor=actor)
    if decision.allowed:
        return False

    logger.warning(
        "dispatch_refused_over_budget",
        job_id=job_id,
        issue_id=issue_id,
        limit=decision.limit,
        details=decision.details,
    )
    try:
        await self._jobs.update(
            job_id,
            status="failed",
            # Figure-free on purpose: this column is echoed onto the
            # originating issue, which may be public. The spend, the cap and
            # the actor stay in the audit row and the log line above.
            error_message=decision.public_message,
            error_code=ErrorCode.BUDGET_REFUSED.value,
            finished_at=datetime.now(UTC),
        )
        await self._finish_credential_uses(job_id, error_code=ErrorCode.BUDGET_REFUSED.value)
        await self._announce_finished(job_id)
    except (StatusTransitionConflict, ValueError):
        # ValueError: the job left `pending` meanwhile (cancelled) — the
        # repository refuses the transition; the job is not ours to fail.
        logger.warning("budget_refusal_status_conflict", job_id=job_id, exc_info=True)
    await self._emit_audit(
        self._db,
        "job.budget_refused",
        actor="system",
        details={**decision.details, "job_id": job_id, "limit": decision.limit, "message": decision.message},
        audit_log_path=self._audit_log_path,
        issue_id=issue_id,
    )
    if issue_id:
        # Never strand the issue in `triaging` — only `open` dispatches, so a
        # stuck row would swallow every future label trigger.
        try:
            await self._issues.update(issue_id, status="open")
        except (ValueError, StatusTransitionConflict):
            logger.warning("budget_refusal_issue_revert_failed", issue_id=issue_id, exc_info=True)
    return True


async def _run_onboard_workflow(
    self: IssueExecutor,
    job_id: str,
    *,
    repo: str,
    branch: str,
    skip_smoke: bool,
    agent_models: dict[str, str] | None = None,
) -> None:
    """Background runner for onboard jobs — mirrors ``_run_qa_workflow``'s
    lifecycle (trace binding, status transitions) minus QA verdict logic:
    the onboard graph's own nodes decide success (config written to the
    store) vs failure (rounds exhausted / infra error)."""
    from datetime import UTC, datetime

    import structlog.contextvars as cv

    trace_id_bound = False
    try:
        job_row = await self._jobs.get(job_id)
        trace_id = (job_row or {}).get("trace_id")
        if trace_id:
            cv.bind_contextvars(trace_id=trace_id)
            trace_id_bound = True
    except Exception:
        logger.warning("trace_id_bind_failed", job_id=job_id, exc_info=True)

    final_state: dict[str, Any] | None = None
    try:
        # #348: ask the gate before the job is marked running — a refused
        # job never ran and must not carry a started_at.
        if await self._refuse_if_over_budget(job_id):
            return

        await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "repo": repo,
            "branch_name": branch,
            "task": f"Onboard {repo}@{branch}",
            "current_stage": "init",
            "agent_outputs": [],
            "errors": [],
            "retry_count": 0,
            "total_cost_usd": 0.0,
            "budget_overrun_usd": 0.0,
            # qa_active gates scope='qa' env vars into sandboxes — the
            # smoke phase boots apps exactly like a QA sub-task would.
            "qa_active": True,
            "skip_smoke": skip_smoke,
            "onboard": {},
        }
        if agent_models:
            initial_state["agent_models_override"] = dict(agent_models)

        env_vars = await self._load_merged_env_vars(repo, job_id)
        if env_vars:
            initial_state["user_env_vars"] = env_vars

        final_state, interrupted, _ = await self._execute_workflow_stream(
            initial_state,
            issue_id="",
            job_id=job_id,
            pipeline="onboard",
        )

        if interrupted:
            logger.warning("onboard_workflow_unexpected_interrupt", job_id=job_id)
            await self._jobs.update(
                job_id,
                status="failed",
                error_message="onboard workflow interrupted unexpectedly",
                error_code=ErrorCode.UNEXPECTED_INTERRUPT.value,
                finished_at=datetime.now(UTC),
            )
            await self._finish_credential_uses(job_id, error_code=ErrorCode.UNEXPECTED_INTERRUPT.value)
            await self._announce_finished(job_id)
            return

        ob = (final_state or {}).get("onboard") or {}
        if ob.get("final_status") == "completed":
            await self._jobs.update(
                job_id,
                status="completed",
                finished_at=datetime.now(UTC),
            )
            await self._finish_credential_uses(job_id, error_code=None)
            await self._announce_finished(job_id)
        else:
            await self._jobs.update(
                job_id,
                status="failed",
                error_message=(ob.get("failure_summary") or ob.get("last_error") or "onboard did not complete")[:4000],
                error_code=ErrorCode.ONBOARD_FAILED.value,
                finished_at=datetime.now(UTC),
            )
            await self._finish_credential_uses(job_id, error_code=ErrorCode.ONBOARD_FAILED.value)
            await self._announce_finished(job_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("onboard_workflow_crashed", job_id=job_id, error=str(exc), exc_info=True)
        crash_code = classify_failure(exc=exc, stage="onboard").value
        try:
            await self._jobs.update(
                job_id,
                status="failed",
                error_message=f"onboard workflow crashed: {exc}"[:4000],
                error_code=crash_code,
                finished_at=datetime.now(UTC),
            )
            await self._finish_credential_uses(job_id, error_code=crash_code)
            await self._announce_finished(job_id)
        except StatusTransitionConflict:
            pass
    finally:
        if trace_id_bound:
            cv.unbind_contextvars("trace_id")


async def _cleanup_sandbox(self: IssueExecutor, final_state: dict[str, Any] | None, job_id: str) -> None:
    """Destroy the sandbox container if present, then the job's volumes.

    Every terminal path of every workflow ends here, which is what makes it
    the one place a finished job's volumes are released (#274).
    """
    container_id = final_state.get("sandbox_container_id") if final_state else None
    if container_id and self._sandbox:
        try:
            await self._sandbox.destroy(container_id)
            logger.info("sandbox_destroyed", job_id=job_id)
        except Exception:
            logger.warning("sandbox_destroy_failed", job_id=job_id, exc_info=True)
    await self._cleanup_job_volumes(job_id)


async def _cleanup_job_volumes(self: IssueExecutor, job_id: str) -> None:
    """Remove the DinD volumes owned by a job that has gone terminal (#274).

    Keyed on ``job_id`` alone, never on workflow state: a graph that raised or
    was cancelled has no final state, and that is exactly when volumes leaked.
    Best-effort — the VolumeReaper sweep retries anything left behind.
    """
    # getattr: unit tests build the executor via __new__ and set only what
    # their path touches.
    volume_mgr = getattr(self, "_qa_shared_volume_manager", None)
    if volume_mgr is None:
        return
    try:
        await volume_mgr.destroy_for_job(job_id)
    except Exception:
        logger.warning("job_volume_cleanup_failed", job_id=job_id, exc_info=True)


async def _load_merged_env_vars(self: IssueExecutor, repo_name: str, job_id: str) -> list[dict[str, Any]] | None:
    """Return decrypted, merged (global + repo) env vars for sandbox
    injection, or None when there's nothing to inject / no env repo.

    Repo vars override globals of the same key. Rows that fail to
    decrypt (e.g. encrypted under a rotated ENVIRONMENT_SECRET_KEY)
    are dropped and logged. The shape is the list-of-dicts
    (key/value/scope) that init_node's _filter_user_env_for_sandbox
    expects.

    Called by BOTH the issue→PR path (_run_workflow) and the
    standalone QA path (_run_qa_workflow) — keep it that way so the
    two paths can't drift (the QA path silently lacked env injection
    until 2026-05-17, which is why this is a shared helper).
    """
    if not repo_name or self._env_repo is None:
        return None
    try:
        from embry0.storage.encryption import decrypt_vars

        provider = getattr(self, "_secrets_provider", None)
        if provider is None:
            # Fallback for test-style construction without a secrets_provider
            # (production wiring always passes the resolved provider). Build
            # one from the configured key; with no key there is nothing we
            # could decrypt, so skip env injection rather than fall back to
            # a well-known default.
            key = self._config.environment_secret_key if self._config else ""
            if not key:
                logger.warning("env_injection_skipped_no_secrets_provider", job_id=job_id)
                return None
            from embry0.storage.encryption import FernetSecretsProvider

            # getattr: test-style configs are SimpleNamespace stubs without the property
            old_keys = getattr(self._config, "environment_secret_keys_old_list", []) if self._config else []
            provider = FernetSecretsProvider(secret_key=key, old_secret_keys=old_keys)
        global_rows = await self._env_repo.get_global()
        repo_rows = await self._env_repo.get_repo(repo_name)
        merged: dict[str, dict[str, Any]] = {}
        for row in global_rows:
            merged[row["key"]] = row
        for row in repo_rows:
            merged[row["key"]] = row
        decrypted = await decrypt_vars(list(merged.values()), provider)
        failed_keys = [v["key"] for v in decrypted if v["value"] == "[DECRYPTION_FAILED]"]
        if failed_keys:
            logger.warning(
                "env_vars_decrypt_failed_skipped",
                repo=repo_name,
                job_id=job_id,
                keys=failed_keys,
                msg="Encrypted values couldn't be decrypted with the current secret key. Those vars will NOT be injected into the sandbox.",
            )
        env_vars = [
            {"key": v["key"], "value": v["value"], "scope": v.get("scope", "app")}
            for v in decrypted
            if v["value"] != "[DECRYPTION_FAILED]"
        ]
        return env_vars or None
    except Exception:
        logger.warning("env_merge_failed", repo=repo_name, exc_info=True)
        return None


async def _agent_definitions_for_precheck(self: IssueExecutor) -> list[dict[str, Any]]:
    """DB rows when the pool is up, otherwise the builtin seeds."""
    db = getattr(self, "_db", None)
    if db is not None:
        try:
            from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

            rows = await AgentDefinitionsRepository(db).list_all()
            if rows:
                return [dict(row) for row in rows]
        except Exception:
            logger.warning("llm_precheck_agent_definitions_failed", exc_info=True)
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    return [dict(row) for row in BUILTIN_SEED.values()]


async def _model_policy_for_precheck(self: IssueExecutor) -> dict[str, Any] | None:
    """Models the policy can put an agent on: the default model and the tiers.

    ``load_model_policy`` returns None while the row sets no default and
    disables nothing, which would hide a Grok tier fallback from the sandbox.
    Disabled ids are carried and ignored by ``providers_for_job``.
    """
    db = getattr(self, "_db", None)
    if db is None:
        return None
    try:
        from embry0.storage.repositories.provider_config import ProviderConfigRepository

        cfg = await ProviderConfigRepository(db).get()
    except Exception:
        logger.warning("llm_precheck_model_policy_failed", exc_info=True)
        return None
    if not cfg:
        return None
    from embry0.agents.model_policy import build_model_policy

    return build_model_policy(cfg)


async def _precheck_llm_accounts(
    self: IssueExecutor,
    job_id: str,
    input_value: Any,
) -> tuple[Any, tuple[str, ...], dict[str, str], dict[str, str]]:
    """Resolve the job's LLM accounts before a container can exist.

    Returns ``(input_value, providers, kinds, ids)``; ``ids`` is the credential
    id per provider, which a resume re-enrolls its sandbox with. A dict input gains
    ``llm_credential_ids`` (ids only). A resume ``Command`` is left as-is;
    the provider set still comes back so a later sandbox in the same job
    passes ``llm_providers``. No resolver means nothing to resolve — callers
    that have not been wired to the registry keep today's empty set.
    """
    resolver = getattr(self, "_credential_resolver", None)
    if resolver is None:
        return input_value, (), {}, {}

    state = input_value if isinstance(input_value, dict) else None
    if state is None:
        job: dict[str, Any] | None = None
        jobs = getattr(self, "_jobs", None)
        if jobs is not None:
            try:
                job = await jobs.get(job_id)
            except Exception:
                logger.warning("llm_precheck_job_fetch_failed", job_id=job_id, exc_info=True)
        overrides = (job or {}).get("pipeline_config") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        state = {
            "repo": str((job or {}).get("repo") or ""),
            "agent_models_override": overrides.get("agent_models_override") or {},
        }

    from embry0.services.credentials.providers import providers_for_job

    override = state.get("agent_models_override")
    providers = providers_for_job(
        await _agent_definitions_for_precheck(self),
        override if isinstance(override, dict) else None,
        await _model_policy_for_precheck(self),
    )
    resolved = await resolver.resolve_llm_for_job(str(state.get("repo") or ""), providers, job_id=job_id)
    ids = {provider: cred.id for provider, cred in resolved.items()}
    kinds = {provider: cred.kind for provider, cred in resolved.items()}
    if isinstance(input_value, dict):
        input_value = {**input_value, "llm_credential_ids": ids}
    return input_value, providers, kinds, ids


async def _execute_workflow_stream(
    self: IssueExecutor,
    input_value: Any,
    issue_id: str | None,
    job_id: str,
    pipeline: str = "issue-to-pr",
    state_sink: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
    """Run a workflow against ``input_value``.

    ``pipeline`` selects which workflow to compile via ``_select_workflow``
    (defaults to ``issue-to-pr`` for the legacy issue path). Shared body of
    ``_run_workflow`` (fresh initial state) and ``resume``
    (Command(resume=...), or None to go on from the last checkpoint after a
    restart, #307). Returns the tuple from ``_process_stream``; a resume's
    state also gets back the facts in ``_RESUME_CARRIED_KEYS`` wherever this
    run left them empty.
    """
    from langgraph.types import Command

    workflow = self._select_workflow(pipeline)

    # Resolve every declared LLM provider before graph config, and therefore
    # before any node can create a container. There is no ``_run_graph``;
    # this function is the only caller of ``_build_graph_config``.
    input_value, llm_providers, llm_kinds, llm_ids = await _precheck_llm_accounts(self, job_id, input_value)
    graph_config = self._build_graph_config(job_id, llm_providers=llm_providers, llm_credential_kinds=llm_kinds)

    carried: dict[str, Any] = {}
    async with checkpointer_context(self._database_url) as saver:
        graph = workflow.compile(config={"checkpointer": saver})
        if input_value is None or isinstance(input_value, Command):
            snapshot = await graph.aget_state(graph_config)
            paused = getattr(snapshot, "values", None) or {}
            carried = {key: paused[key] for key in _RESUME_CARRIED_KEYS if paused.get(key)}
            # Seeded before the stream: a resume that raises cleans up from
            # the sink, and it needs the sandbox id as much as a finished one.
            if state_sink is not None:
                state_sink.update(carried)
            if input_value is None:
                await _reclaim_sandbox_after_restart(
                    self,
                    paused,
                    job_id,
                    llm_ids=llm_ids,
                    llm_kinds=llm_kinds,
                    git_push=pipeline not in _READ_ONLY_PIPELINES,
                )
            elif _resume_runs_agents(input_value):
                await _reenroll_paused_sandbox(
                    self,
                    paused,
                    job_id,
                    llm_ids=llm_ids,
                    llm_kinds=llm_kinds,
                    git_push=pipeline not in _READ_ONLY_PIPELINES,
                )
        final_state, interrupted, interrupt_value = await self._process_stream(
            graph,
            input_value,
            graph_config,
            issue_id,
            job_id,
            state_sink,
            stop_on_drain=pipeline not in NOT_RESUMABLE_PIPELINES,
        )
    # A run that produced no update still returns None: nothing is invented.
    if final_state is not None:
        for key, value in carried.items():
            if not final_state.get(key):
                final_state[key] = value
    return final_state, interrupted, interrupt_value


# Pipelines whose sandbox only reads its repository (#521): their nodes create
# it without git_push. Every other pipeline runs IssueToprWorkflow, whose init
# node creates it with git_push for the developer's branch. Mirrors
# IssueExecutor._select_workflow.
_READ_ONLY_PIPELINES = frozenset({"qa", "onboard"})


# Facts a job sets before it can pause, which its terminal steps read (#482).
# A resume replays from the checkpoint, so _process_stream returns only the
# updates of the nodes that ran after it — for merge_as_is that is
# {"current_stage": "completed"} alone. branch_name (developer) gates the PR
# fallback, base_branch (init) is that PR's target, developer_summary
# (developer, #488) is that PR's body, and sandbox_container_id (init) is what
# _cleanup_sandbox destroys. Outcome fields (agent_outputs, errors,
# error_code) are deliberately not carried: a run is judged by what it did,
# and an output from before the pause must not fail a merge_as_is.
_RESUME_CARRIED_KEYS = ("branch_name", "base_branch", "developer_summary", "sandbox_container_id")


# Resume choices that end the job without running another agent (max_retries_node).
_NO_AGENT_RESUME_CHOICES = frozenset({"abandon", "merge_as_is"})


def _resume_runs_agents(input_value: Any) -> bool:
    """True for a resume that may run an agent: answers, or a continue_retrying choice."""
    from langgraph.types import Command

    if not isinstance(input_value, Command):
        return False
    value = input_value.resume
    return not (isinstance(value, dict) and value.get("choice") in _NO_AGENT_RESUME_CHOICES)


async def _reenroll_paused_sandbox(
    self: IssueExecutor,
    values: dict[str, Any],
    job_id: str,
    *,
    llm_ids: dict[str, str],
    llm_kinds: dict[str, str],
    git_push: bool = False,
) -> None:
    """Give the paused job's sandbox its proxy access back before the graph resumes (#476).

    ``values`` is the paused checkpoint's state. The proxies forget a sandbox
    after 24 h and on every orchestrator start. The xAI account is the one the
    precheck just resolved for this resume; the proxy injects an account's
    token only for an oauth account, as at create. ``git_push`` restores the
    push right the sandbox was created with (#521), for the one branch the
    checkpoint names (#559). Raises what
    ``SandboxManager.reenroll`` raises: ``SANDBOX_GONE`` fails the resume,
    ``SandboxReenrollError`` leaves the job paused (``IssueExecutor.resume``).
    """
    sandbox = getattr(self, "_sandbox", None)
    if sandbox is None or not hasattr(sandbox, "reenroll"):
        return
    container = values.get("sandbox_container_id")
    if not container:
        return
    repo = str(values.get("repo") or "")
    forge = await self._forge_for(repo) if repo else "github"
    xai_credential_id = llm_ids.get("xai") if llm_kinds.get("xai") == "llm_oauth" else None
    context_scope: bool | list[str] | None = None
    if values.get("watcher_log_tool"):
        context_scope = True
    elif values.get("case_log_containers"):
        context_scope = list(values["case_log_containers"])
    git_push_branches: list[str] = []
    if git_push:
        # #559: the branch the paused job may push, from its checkpoint: the
        # developer's chosen branch once it has one (init_node's rule).
        from embry0.orchestration.branch_names import job_push_branch

        git_push_branches = [
            job_push_branch(
                issue_id=values.get("issue_id"),
                task=values.get("task"),
                job_id=values.get("job_id") or job_id,
                context=values.get("context"),
                chosen=values.get("branch_name"),
            )
        ]
    await sandbox.reenroll(
        container,
        job_id=job_id,
        repo=repo or None,
        forge=forge,
        xai_credential_id=xai_credential_id,
        context_scope=context_scope,
        git_push=git_push,
        git_push_branches=git_push_branches,
    )


async def _reclaim_sandbox_after_restart(
    self: IssueExecutor,
    values: dict[str, Any],
    job_id: str,
    *,
    llm_ids: dict[str, str],
    llm_kinds: dict[str, str],
    git_push: bool,
) -> None:
    """Make a job's sandbox usable again before its graph goes on after a restart (#307).

    ``values`` is the checkpoint's state. With a sandbox recorded there, the
    agent the previous process left running inside is killed (it would
    otherwise share the workspace with the re-run node), then the sandbox is
    re-enrolled with the proxies as a paused resume does; a sandbox that is
    gone fails the job (``SANDBOX_GONE``). With none recorded, the job stopped
    inside its first node, which may have created the container already, so
    any ``sandbox-<job_id>`` is removed and the node creates it afresh.
    """
    sandbox = getattr(self, "_sandbox", None)
    if sandbox is None:
        return
    container = values.get("sandbox_container_id")
    if not container:
        await sandbox.destroy(f"sandbox-{job_id}")
        return
    try:
        await sandbox.stop_agent(container)
    except Exception:  # noqa: BLE001 — reenroll below says whether the sandbox is usable
        logger.warning("sandbox_agent_stop_failed", job_id=job_id, exc_info=True)
    await _reenroll_paused_sandbox(self, values, job_id, llm_ids=llm_ids, llm_kinds=llm_kinds, git_push=git_push)


async def _handle_interrupt(
    self: IssueExecutor,
    issue_id: str | None,
    job_id: str,
    interrupt_value: dict[str, Any] | None,
) -> None:
    """Transition job/issue state based on the interrupt value from
    ``_process_stream``."""
    if interrupt_value:
        reason = interrupt_value.get("reason", "")
        if reason == "max_retries":
            # Job pauses; issue goes back to `open` so the user can either
            # cancel from the dashboard or re-trigger triage. The Issue
            # state machine doesn't have a `paused` value (allowed:
            # awaiting_input | cancelled | closed | in_progress | open) —
            # this used to crash with InvalidIssueStatusTransition because
            # `paused` is a job-only status.
            await self._jobs.update(job_id, status="paused")
            await self._issues.update(issue_id, status="open")
            logger.info("job_paused", job_id=job_id, reason=reason)
            return
        await self._handle_needs_info(issue_id, job_id, interrupt_value)
        return
    await self._jobs.update(job_id, status="awaiting_input")
    await self._issues.update(issue_id, status="awaiting_input")


async def _run_workflow(
    self: IssueExecutor,
    issue_id: str | None,
    job_id: str,
    issue: dict[str, Any],
    additional_context: str = "",
) -> None:
    """Execute the issue-to-pr workflow via astream() with interrupt handling."""
    from datetime import UTC, datetime

    import structlog.contextvars as cv

    # Bind trace_id to structlog contextvars so every log + audit emitted
    # during this workflow is tagged. Lookup on the job row — the trace_id
    # was assigned in execute() at job creation time.
    trace_id_bound = False
    job_row: dict[str, Any] | None = None
    try:
        job_row = await self._jobs.get(job_id)
        trace_id = (job_row or {}).get("trace_id")
        if trace_id:
            cv.bind_contextvars(trace_id=trace_id)
            trace_id_bound = True
    except Exception:
        logger.warning("trace_id_bind_failed", job_id=job_id, exc_info=True)

    final_state: dict[str, Any] | None = None
    # Mirrors the graph's merged state as it streams. `final_state` is the
    # return value, and a mid-run raise (overrun_mode=hard) never produces
    # one — the failure path reads the sandbox handle and the cost from here
    # instead of destroying nothing and recording $0.00 (#246).
    streamed_state: dict[str, Any] = {}
    try:
        # The actor is the job row's dispatcher, NOT issues.created_by: that
        # column holds a provenance constant ("webhook-label", "triage"), so
        # bucketing on it made the hourly limit one fleet-wide throttle (#246).
        # #348: asked before the job is marked running — a refused job never
        # ran and must not carry a started_at.
        if await self._refuse_if_over_budget(job_id, issue_id=issue_id, actor=(job_row or {}).get("dispatched_by")):
            return

        await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

        initial_state = {
            "job_id": job_id,
            "issue_id": issue_id,
            "repo": issue.get("repo") or "",
            "task": issue["title"] + ("\n\n" + issue["body"] if issue.get("body") else ""),
            "issue_number": github_number_of(issue),
            # EMB-39: issue labels feed conditional-criteria `labels:`
            # predicates in the QA orchestrator.
            "issue_labels": list(issue.get("labels") or []),
            "current_stage": "init",
            "agent_outputs": [],
            "errors": [],
            "retry_count": 0,
            "total_cost_usd": 0.0,
            "budget_overrun_usd": 0.0,
        }

        initial_state["context"] = (job_row or {}).get("context") or {
            "type": "git",
            "repo": issue.get("repo") or "",
        }

        # Pass through per-job overrides supplied at job creation time
        # (JobCreateRequest fields, stored on the job row's pipeline_config
        # by the POST /jobs handler) and surfaced on state:
        # - agent_models_override: agent nodes prefer it over triage.
        # - max_budget_usd: folded over triage's pipeline_config.budget_usd
        #   (see triage_node); the user cap always wins.
        # - budget_overrun_mode: "hard" pins this job to a hard stop under a
        #   soft fleet mode (#275); run_agent_node ignores any other value.
        # - execution/auth mode overrides: read by run_agent_node.
        overrides = (job_row or {}).get("pipeline_config") or {}
        if isinstance(overrides, dict):
            agent_models_override = overrides.get("agent_models_override")
            if agent_models_override:
                initial_state["agent_models_override"] = agent_models_override
            if overrides.get("max_budget_usd") is not None:
                initial_state["max_budget_usd"] = overrides["max_budget_usd"]
            if overrides.get("budget_overrun_mode"):
                initial_state["budget_overrun_mode_override"] = overrides["budget_overrun_mode"]
            if overrides.get("execution_mode_override"):
                initial_state["execution_mode_override"] = overrides["execution_mode_override"]
            if overrides.get("auth_mode_override"):
                initial_state["auth_mode_override"] = overrides["auth_mode_override"]

        # RAV-1058: honour a job-create sandbox_profile and pipeline_template.
        # Both were persisted to the job row but never consumed on this path
        # (the profile never reached SandboxManager.create; plan_route only
        # honoured triage's template pick, then overwrote the column).
        # getattr for _repo_prefs: unit tests build the executor via __new__
        # and set only the attributes their path touches. Pref fetch is
        # fail-open: prefs live in the same DB as the job row, and a
        # transient read failure should degrade gracefully, not kill the job.
        repo_prefs = getattr(self, "_repo_prefs", None)
        pref: dict[str, Any] | None = None
        if repo_prefs is not None and issue.get("repo"):
            try:
                pref = await repo_prefs.get(issue["repo"])
            except Exception:
                logger.warning("repo_prefs_fetch_failed", job_id=job_id, repo=issue["repo"], exc_info=True)
        if (job_row or {}).get("sandbox_profile"):
            initial_state["sandbox_profile"] = job_row["sandbox_profile"]  # type: ignore[index]
        elif pref and pref.get("sandbox_profile"):
            # RAV-1246: without an explicit job-create override, honour the
            # per-repo sandbox_profile preference. Must happen HERE — the graph
            # is init→triage, so the sandbox already exists by the time any
            # triage-side override could run. init_node resolves the name via
            # profiles_repo and fails the job loudly on an unknown profile
            # (same semantics as the job-create path above).
            initial_state["sandbox_profile"] = pref["sandbox_profile"]
            logger.info(
                "sandbox_profile_from_repo_preferences",
                job_id=job_id,
                repo=issue["repo"],
                profile=pref["sandbox_profile"],
            )
        # RAV-1254: per-repo pre-PR self-checks (ex .e0/dev.yaml) ride state
        # into the developer prompt for EVERY dispatch path.
        if pref and pref.get("dev_checks"):
            initial_state["dev_checks"] = pref["dev_checks"]
        if (job_row or {}).get("pipeline_template"):
            initial_state["pipeline_template_request"] = job_row["pipeline_template"]  # type: ignore[index]
        # #306: a watcher-found fix runs unattended, so it always gets review
        # and verification — plan_route refuses triage's lighter pick for it.
        if issue_id and await _works_watcher_case(self, issue_id):
            initial_state["pin_full_pipeline"] = True

        # Fold auto-answer Q&A block into the triage prompt when retriage is
        # triggered after all questions were auto-answerable (B3 fix).
        if additional_context:
            initial_state["additional_context"] = additional_context

        # Fetch merged env (global + repo) decrypted — to be injected into
        # the sandbox. Repo vars override globals of the same key.
        repo_name = issue.get("repo") or ""
        env_vars = await self._load_merged_env_vars(repo_name, job_id)
        if env_vars:
            initial_state["user_env_vars"] = env_vars

        # RAV-1079: open-PR awareness + duplicate dedup, BEFORE the graph
        # (and therefore before any sandbox spins up). Jobs that already
        # target an existing PR (pr-followup: pr_url set at dispatch,
        # context.ref carries the branch) are exempt — they update that PR
        # deliberately. The developer/reviewer see a job's known PR via
        # state["pr_url"]; fresh jobs get the open-PR list on state for
        # prompt injection, and an exact ticket match ends the job here
        # linking the existing PR (skip+link policy). The listing fails
        # CLOSED (#305): OpenPRListError propagates to the failure path below
        # (ERR_PR_LISTING_FAILED) — "could not list" is not "none open", and
        # running on would risk a second PR for the same ticket.
        job_pr_url = (job_row or {}).get("pr_url")
        job_ref = ((job_row or {}).get("context") or {}).get("ref")
        if job_pr_url:
            initial_state["pr_url"] = job_pr_url
        # getattr for _github_token: unit tests build the executor via
        # __new__ and set only what their path touches (same contract as
        # _post_tracker_outcome). No resolvable token → skip the fetch
        # entirely — an anonymous listing would fail on private repos and
        # burn unauthenticated rate limit for nothing.
        from embry0.services.credentials.resolver import forge_token
        from embry0.services.github_prs import OpenPRService, find_duplicate_pr

        resolver = getattr(self, "_credential_resolver", None)
        try:
            dedup_token = await forge_token(resolver, repo_name, "github", getattr(self, "_github_token", None) or "")
        except Exception:
            # Dedup is fail-open: a pinned credential that is unavailable
            # skips the fetch (the job's own enroll step reports it).
            logger.warning("pr_dedup_token_lookup_failed", repo=repo_name, exc_info=True)
            dedup_token = ""
        if (
            repo_name
            and "/" in repo_name
            and dedup_token
            and not job_pr_url
            and not job_ref
            and getattr(self._config, "pr_dedup_enabled", True)
        ):
            open_prs = await OpenPRService(github_token=dedup_token, credential_resolver=resolver).list_open_prs(
                repo_name,
                limit=getattr(self._config, "pr_dedup_max_prs", 30),
            )
            if open_prs:
                initial_state["open_prs"] = open_prs
                duplicate = find_duplicate_pr(
                    open_prs,
                    linear_identifier=linear_identifier_of(issue),
                    github_number=github_number_of(issue),
                    issue_id=issue_id,
                )
                earlier = await _failed_job_owning(self, duplicate, issue_id) if duplicate is not None else None
                if earlier is not None and duplicate is not None:
                    # #349: the open PR holds work an earlier job of this
                    # issue pushed before it failed. Continue on it, as a
                    # follow-up does: same branch, same PR. Skipping would
                    # mark this job completed against unfinished work.
                    _continue_failed_pr(initial_state, duplicate, earlier)
                    await self._jobs.update(job_id, pr_url=duplicate["url"])
                    logger.info(
                        "job_continues_failed_pr",
                        job_id=job_id,
                        issue_id=issue_id,
                        pr_url=duplicate["url"],
                        earlier_job_id=earlier.get("job_id"),
                    )
                elif duplicate is not None:
                    await self._skip_as_duplicate(
                        issue_id,
                        job_id,
                        repo_name,
                        duplicate,
                        task=(job_row or {}).get("task"),
                    )
                    return

        final_state, interrupted, interrupt_value = await self._execute_workflow_stream(
            initial_state,
            issue_id,
            job_id,
            state_sink=streamed_state,
        )

        if interrupted:
            await self._handle_interrupt(issue_id, job_id, interrupt_value)
            return

        if final_state:
            await self._handle_workflow_result(issue_id, job_id, final_state)
        else:
            await self._jobs.update(job_id, status="completed")
            await self._finish_credential_uses(job_id, error_code=None)
            await self._announce_finished(job_id)

        # Don't destroy sandbox for paused jobs
        job_record = await self._jobs.get(job_id)
        if job_record and job_record.get("status") == "paused":
            logger.info("sandbox_preserved_for_paused_job", job_id=job_id)
        else:
            await self._cleanup_sandbox(final_state, job_id)

    except Exception as exc:
        logger.error(
            "workflow_execution_failed",
            issue_id=issue_id,
            job_id=job_id,
            error=str(exc),
        )
        code = classify_failure(exc=exc, stage=(final_state or streamed_state or {}).get("current_stage"))
        # A job killed mid-run still spent money — a hard budget stop most of
        # all. Traces are written per agent turn, so they carry the real bill
        # even though no terminal node ever ran.
        last_state = final_state or streamed_state or None
        # #349: a run that raised after pushing its branch (a hard budget stop,
        # a sandbox that is gone) still gets its PR, so the work is not stranded.
        pr_url, _ = await self._open_fallback_pr(issue_id, job_id, last_state or {}, error_code=code.value)
        await self._jobs.update(
            job_id,
            status="failed",
            error_message=str(exc),
            error_code=code.value,
            total_cost_usd=await resolve_job_total_cost(
                getattr(self, "_traces", None),
                job_id,
                float((last_state or {}).get("total_cost_usd") or 0.0),
            ),
            budget_overrun_usd=_overrun_of(exc, last_state),
            **({"pr_url": pr_url} if pr_url else {}),
        )
        await self._finish_credential_uses(job_id, error_code=code.value)
        await self._announce_finished(job_id)
        if pr_url:
            await self._record_pr_opened(issue_id, job_id, pr_url)
        await self._issues.update(issue_id, status="open")
        await self._emit_audit(
            self._db,
            "issue.status_changed",
            actor="system",
            details={
                "old_status": "triaging",
                "new_status": "open",
                "reason": f"Workflow failed: {exc}",
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        # Purge checkpoint state for failed jobs.
        from embry0.orchestration.checkpoint import purge_thread as _purge_thread_on_error

        try:
            await _purge_thread_on_error(self._database_url, job_id)
        except Exception:
            logger.warning(
                "checkpoint_purge_on_error_failed",
                job_id=job_id,
                exc_info=True,
            )

        # Cleanup sandbox on error. `final_state` is None whenever the graph
        # raised rather than returned, so the streamed copy is what holds the
        # container id — without it a hard budget stop leaked a container.
        await self._cleanup_sandbox(last_state, job_id)
    finally:
        await self._post_tracker_outcome(issue_id, job_id)
        if trace_id_bound:
            try:
                cv.unbind_contextvars("trace_id")
            except Exception:
                logger.warning("trace_id_unbind_failed", job_id=job_id, exc_info=True)


async def _works_watcher_case(self: IssueExecutor, issue_id: str) -> bool:
    """True when the issue belongs to a healing case the watcher opened.

    Fail-open to False like every ledger read in the job lifecycle: without
    a recorder (tests, a deployment without the ledger) or on a ledger error
    the job runs as triage routes it.
    """
    recorder = getattr(self, "_case_recorder", None)
    if recorder is None:
        return False
    try:
        origin = await recorder.case_origin(issue_id)
    except Exception:
        logger.warning("case_origin_lookup_failed", issue_id=issue_id, exc_info=True)
        return False
    return bool(origin == Origin.WATCHER)


async def _failed_job_owning(self: IssueExecutor, pr: dict[str, Any], issue_id: str | None) -> dict[str, Any] | None:
    """The failed job of this issue whose open PR ``pr`` is, or None (#349).

    The newest job carrying that URL decides: a failed, cancelled or expired
    job of the same issue left unfinished work there for a retry to continue.
    Anything else (a completed job's PR awaiting review, a person's PR, another
    issue's) is a real duplicate. Fail-closed to None: a lookup error keeps
    today's skip.
    """
    from embry0.trackers.redispatch import REDISPATCHABLE_JOB_STATUSES

    if not issue_id or not pr.get("url") or not pr.get("head_ref"):
        return None
    try:
        owners = await self._jobs.find_by_pr_url(str(pr["url"]))
    except Exception:
        logger.warning("failed_pr_owner_lookup_failed", pr_url=pr.get("url"), exc_info=True)
        return None
    newest = owners[0] if owners else None
    if newest and newest.get("issue_id") == issue_id and newest.get("status") in REDISPATCHABLE_JOB_STATUSES:
        return newest
    return None


def _continue_failed_pr(state: dict[str, Any], pr: dict[str, Any], earlier: dict[str, Any]) -> None:
    """Point a fresh job's initial state at the failed job's PR (#349).

    The PR's branch becomes ``context.ref`` (workspace init checks it out; the
    developer is told it exists and not to open another PR), the PR becomes
    ``pr_url``, and why the earlier job stopped goes to the prompt.
    """
    state["pr_url"] = pr["url"]
    state["context"] = {**(state.get("context") or {}), "ref": pr["head_ref"]}
    stopped = str(earlier.get("error_message") or "it did not finish")[:500]
    note = (
        f"An earlier embry0 run on this task (job {earlier.get('job_id')}) pushed its work to "
        f"{pr['head_ref']} and opened {pr['url']}, then stopped: {stopped}\n"
        "Continue from that work on this branch: check what it already does, fix what made it "
        "stop, and finish the task. Do not start over and do not open another pull request."
    )
    existing = state.get("additional_context")
    state["additional_context"] = f"{existing}\n\n{note}" if existing else note


async def _skip_as_duplicate(
    self: IssueExecutor,
    issue_id: str | None,
    job_id: str,
    repo: str,
    duplicate: dict[str, Any],
    task: str | None = None,
    *,
    summary: str | None = None,
    total_cost_usd: float = 0.0,
    kind: str | None = None,
) -> None:
    """End a job as ``skipped`` because an open PR already covers its ticket.

    Two callers: the executor's pre-graph ticket match (RAV-1079 — no
    sandbox, no agent has run) and triage's semantic ``duplicate_of_pr``
    flag (``current_stage == "skipped_duplicate"``, after triage ran and
    spent money — ``summary`` and ``total_cost_usd`` come from that run).

    #474: the job opened nothing, so it ends ``skipped``, never ``completed``,
    and the other PR is NOT written to its ``pr_url``: that column is how
    merge sync and the case ledger tell which job owns a PR, and a person's
    PR recorded there was credited to embry0 when it merged. The PR is
    linked to the issue's healing case with no owning job instead, so its
    merge resolves the case ``out_of_band``. The issue goes back to ``open``:
    nothing is merged yet, and redispatch refuses a ticket whose latest job
    was skipped, so the open issue does not dispatch a second job. The
    caller's ``finally`` posts the tracker outcome comment, which carries
    ``result_summary`` because ``pr_url`` is empty.
    """
    from datetime import UTC, datetime

    from embry0.workflows.issue_to_pr.plan_route import job_title_from_task

    matched_by = duplicate.get("matched_by")
    if summary is None:
        summary = (
            f"Skipped: a pull request for this issue already exists: #{duplicate['number']} "
            f"({duplicate['url']}"
            + (f", matched by {matched_by}" if matched_by else "")
            + "). No new pull request was opened; review or merge the existing one instead."
        )
    logger.info(
        "job_skipped_duplicate_pr",
        job_id=job_id,
        issue_id=issue_id,
        repo=repo,
        pr_number=duplicate["number"],
        matched_by=matched_by,
    )
    await self._jobs.update(
        job_id,
        status="skipped",
        result_summary=summary,
        total_cost_usd=total_cost_usd,
        current_stage="skipped_duplicate",
        finished_at=datetime.now(UTC),
    )
    await self._finish_credential_uses(job_id, error_code=None)
    await self._announce_finished(job_id)
    await record_case_event(
        self,
        "existing_pr_found",
        log_ids={"job_id": job_id, "issue_id": issue_id},
        issue_id=issue_id,
        job_id=job_id,
        pr_url=str(duplicate["url"]),
    )
    if issue_id:
        await self._issues.update(issue_id, status="open")
        await self._issues.update_parent_status(issue_id)
    await self._emit_audit(
        self._db,
        "job.skipped_duplicate_pr",
        actor="system",
        details={
            "job_id": job_id,
            "repo": repo,
            "pr_number": duplicate["number"],
            "pr_url": duplicate["url"],
            "matched_by": matched_by,
        },
        audit_log_path=self._audit_log_path,
        issue_id=issue_id,
    )
    # getattr: same __new__-construction contract as _post_tracker_outcome.
    notifier = getattr(self, "_webhook_notifier", None)
    if notifier is not None:
        notifier.emit_soon(
            "job.skipped",
            {
                "job_id": job_id,
                "issue_id": issue_id,
                "title": job_title_from_task(task),
                "kind": kind,
                "repo": repo,
                # The job's own PR — none. The existing one is in the summary
                # and under existing_pr_url, never posing as this job's.
                "pr_url": None,
                "existing_pr_url": duplicate["url"],
                "summary": summary,
                "result_summary": summary,
                "deliverable_count": 0,
                "total_cost_usd": total_cost_usd,
                "error_code": None,
                "error_message": None,
            },
        )


async def _run_qa_workflow(
    self: IssueExecutor,
    job_id: str,
    *,
    repo: str,
    branch: str,
    qa_overrides: dict[str, Any],
    agent_models: dict[str, str] | None = None,
) -> None:
    """Background runner for issue-less QA jobs (started via ``start_job``).

    Mirrors the lifecycle bits ``_run_workflow`` provides — trace_id binding,
    status transitions, sandbox cleanup, checkpoint purge — but skips all
    of the issue-coupled handling (triage/needs_info/split). The workflow's
    own report/retry nodes own QA-specific success/failure semantics.
    """
    from datetime import UTC, datetime

    import structlog.contextvars as cv

    trace_id_bound = False
    try:
        job_row = await self._jobs.get(job_id)
        trace_id = (job_row or {}).get("trace_id")
        if trace_id:
            cv.bind_contextvars(trace_id=trace_id)
            trace_id_bound = True
    except Exception:
        logger.warning("trace_id_bind_failed", job_id=job_id, exc_info=True)

    final_state: dict[str, Any] | None = None
    try:
        # #348: ask the gate before the job is marked running — a refused
        # job never ran and must not carry a started_at.
        if await self._refuse_if_over_budget(job_id):
            return

        await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

        qa_block: dict[str, Any] = {
            "needs_qa": True,
            "acceptance_criteria": list(qa_overrides.get("acceptance_criteria") or []),
            "attempts": [],
            "failure_rounds": 0,
            "final_status": "pending",
        }
        if qa_overrides.get("qa_timeout_seconds"):
            qa_block["budget_seconds"] = qa_overrides["qa_timeout_seconds"]
        # Phase-C: force_all_apps from QAJobOverrides — when True,
        # qa_orchestrator_node skips affected-set computation entirely
        # and runs every declared app.
        if qa_overrides.get("force_all_apps"):
            qa_block["force_all_apps"] = True
        # EMB-39: force named conditional-criteria groups ON for this run.
        # Standalone runs have an empty diff, so this knob (or a label on
        # the issue path) is the only way conditional groups fire here.
        if qa_overrides.get("force_conditional_groups"):
            qa_block["force_conditional_groups"] = list(qa_overrides["force_conditional_groups"])
        # #229: per-job browser-backend override — outranks every qa.yaml
        # layer; qa_orchestrator_node applies it after app-config resolution.
        if qa_overrides.get("browser_backend"):
            qa_block["browser_backend"] = qa_overrides["browser_backend"]
        # #537: per-job sandbox profile — outranks every qa.yaml layer, applied
        # by qa_orchestrator_node like the browser backend.
        if qa_overrides.get("sandbox_profile"):
            qa_block["sandbox_profile"] = qa_overrides["sandbox_profile"]

        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "repo": repo,
            "branch_name": branch,
            # Phase-C: base_branch from QAJobOverrides. None means the
            # orchestrator's init_orchestrator_node falls back to "main".
            "base_branch": qa_overrides.get("base_branch"),
            "task": f"QA run on {repo}@{branch}",
            "current_stage": "init",
            "agent_outputs": [],
            "errors": [],
            "retry_count": 0,
            "total_cost_usd": 0.0,
            "budget_overrun_usd": 0.0,
            "qa_active": True,
            "qa": qa_block,
        }

        # Per-agent model override — run_agent_node folds it into the
        # resolver's pipeline_config.agent_models, and the QA orchestrator
        # threads it into each sub-task's state (fresh dicts per app).
        if agent_models:
            initial_state["agent_models_override"] = dict(agent_models)

        # Inject per-repo env vars into the QA sandboxes. The standalone
        # QA path historically skipped this (only the issue→PR path did
        # the merge), so credentials seeded for a repo never reached the
        # sub-task sandboxes — one app reported its cloud credentials
        # missing, another kept hitting its identity provider although its
        # QA bypass variable was in the store. Shared helper keeps both
        # paths in lockstep.
        env_vars = await self._load_merged_env_vars(repo, job_id)
        if env_vars:
            initial_state["user_env_vars"] = env_vars

        final_state, interrupted, _ = await self._execute_workflow_stream(
            initial_state,
            issue_id="",  # QA jobs have no associated issue
            job_id=job_id,
            pipeline="qa",
        )

        # The QA workflow does not interrupt today (no node in
        # embry0/workflows/qa/graph.py raises an Interrupt). If a future
        # version adds one, surface it as a failed job rather than silently
        # leaving the row in 'running'.
        if interrupted:
            logger.warning("qa_workflow_unexpected_interrupt", job_id=job_id)
            await self._jobs.update(
                job_id,
                status="failed",
                error_message="QA workflow interrupted unexpectedly",
                error_code=ErrorCode.UNEXPECTED_INTERRUPT.value,
                finished_at=datetime.now(UTC),
            )
            await self._finish_credential_uses(job_id, error_code=ErrorCode.UNEXPECTED_INTERRUPT.value)
            await self._announce_finished(job_id)
            # Terminal like any other failure: release the sandbox and the
            # job's volumes (#274).
            await self._cleanup_sandbox(final_state, job_id)
            return

        qa_state = (final_state or {}).get("qa") or {}
        qa_final = qa_state.get("final_status", "pending")
        per_app = qa_state.get("per_app_results") or []

        # Bucket per-app outcomes. SKIPPED counts as a pass (nothing to
        # run); INCONCLUSIVE and the hard failures count as "did not
        # pass"; INFRA_FAILURE is embry0's own fault and forces FAILED.
        _hard_fail = {
            "qa_failure",
            "e2e_failure",
            "boot_failure",
            "ready_check_failed",
        }
        passed_apps = [a["app_name"] for a in per_app if a.get("status") in ("passed", "skipped")]
        failed_apps = [a["app_name"] for a in per_app if a.get("status") in _hard_fail]
        inconclusive_apps = [a["app_name"] for a in per_app if a.get("status") == "inconclusive"]
        infra_apps = [a["app_name"] for a in per_app if a.get("status") == "infra_failure"]

        # passed → completed. ≥1 pass and no infra → partial (a
        # mostly-passing multi-app run isn't a flat failure). Zero
        # passes or any infra_failure → failed.
        if qa_final == "passed":
            job_status = "completed"
        elif infra_apps or not passed_apps:
            job_status = "failed"
        else:
            job_status = "partial"

        error_code = None
        if job_status == "failed":
            # #281: these rows used to carry no code at all.
            error_code = classify_qa_outcome(
                qa_error_code=qa_state.get("error_code"),
                validation_errors=qa_state.get("validation_errors") or (),
                infra_apps=infra_apps,
                failed_apps=failed_apps,
                inconclusive_apps=inconclusive_apps,
            ).value
        error_message = None
        if job_status in ("failed", "partial"):
            if per_app:
                summary = f"QA: {len(passed_apps)}/{len(per_app)} apps passed"
                detail = []
                if failed_apps:
                    detail.append("failed: " + ",".join(sorted(failed_apps)))
                if inconclusive_apps:
                    detail.append("inconclusive: " + ",".join(sorted(inconclusive_apps)))
                if infra_apps:
                    detail.append("infra: " + ",".join(sorted(infra_apps)))
                error_message = summary + (" — " + "; ".join(detail) if detail else "")
            else:
                # No per-app breakdown (e.g. early QA failure) — fall
                # back to the structured reason or a generic note.
                error_message = qa_state.get("error_message") or f"QA final_status={qa_final}"

        total_cost_usd = await resolve_job_total_cost(
            self._traces,
            job_id,
            float((final_state or {}).get("total_cost_usd", 0.0) or 0.0),
        )

        # #537: the profile the run actually used, not a placeholder. Absent
        # (config never resolved, or apps on different profiles) leaves the
        # row as start_job wrote it: the caller's override, or nothing.
        profile_used = {}
        if qa_state.get("sandbox_profile_used"):
            profile_used["sandbox_profile"] = qa_state["sandbox_profile_used"]
        await self._jobs.update(
            job_id,
            status=job_status,
            error_message=error_message,
            error_code=error_code,
            total_cost_usd=total_cost_usd,
            budget_overrun_usd=float((final_state or {}).get("budget_overrun_usd") or 0.0),
            finished_at=datetime.now(UTC),
            **profile_used,
        )
        await self._finish_credential_uses(job_id, error_code=error_code)
        await self._announce_finished(job_id)

        # Cleanup: sandbox + checkpoint state.
        await self._cleanup_sandbox(final_state, job_id)
        from embry0.orchestration.checkpoint import purge_thread

        try:
            await purge_thread(self._database_url, job_id)
        except Exception:
            logger.warning(
                "checkpoint_purge_on_qa_complete_failed",
                job_id=job_id,
                status=job_status,
                exc_info=True,
            )
    except Exception as exc:
        logger.error(
            "qa_workflow_execution_failed",
            job_id=job_id,
            error=str(exc),
        )
        crash_code = classify_failure(exc=exc, stage="qa").value
        # #264: a hard budget stop lands here; the row still says what was
        # spent and by how much the cap was crossed.
        spent: dict[str, float] = {"budget_overrun_usd": _overrun_of(exc, final_state)}
        try:
            spent["total_cost_usd"] = await resolve_job_total_cost(getattr(self, "_traces", None), job_id, 0.0)
        except Exception:
            logger.warning("qa_crash_cost_resolve_failed", job_id=job_id, exc_info=True)
        await self._jobs.update(
            job_id,
            status="failed",
            error_message=str(exc),
            error_code=crash_code,
            finished_at=datetime.now(UTC),
            **spent,
        )
        await self._finish_credential_uses(job_id, error_code=crash_code)
        await self._announce_finished(job_id)
        await self._cleanup_sandbox(final_state, job_id)
        from embry0.orchestration.checkpoint import purge_thread as _purge

        try:
            await _purge(self._database_url, job_id)
        except Exception:
            logger.warning("checkpoint_purge_on_qa_error_failed", job_id=job_id, exc_info=True)
    finally:
        if trace_id_bound:
            try:
                cv.unbind_contextvars("trace_id")
            except Exception:
                logger.warning("trace_id_unbind_failed", job_id=job_id, exc_info=True)
