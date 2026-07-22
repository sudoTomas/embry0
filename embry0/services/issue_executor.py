"""Issue executor — creates jobs from issues and runs the workflow pipeline."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from embry0.api.events.bus import EventBus
    from embry0.storage.repositories.environment import EnvironmentRepository
    from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

from embry0.audit.helpers import emit_audit
from embry0.orchestration.checkpoint import checkpointer_context
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository, StatusTransitionConflict
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
from embry0.workflows.qa.graph import QAWorkflow
from embry0.workflows.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)


def _fold_auto_answers_into_context(enriched_questions: list[dict[str, Any]]) -> str:
    """Build a Q&A context block from auto-answered triage questions.

    ``enriched_questions`` is the list of dicts produced by ``_handle_needs_info``
    after DB insertion — each dict has ``question``, ``importance``, and
    ``auto_answer`` keys.

    Returns a formatted block suitable for prepending to ``additional_context``
    in the next triage call, so the triage agent sees the answers and can
    proceed without re-asking.
    """
    lines: list[str] = ["Triage auto-answers (questions the system can answer automatically):"]
    for q in enriched_questions:
        if q.get("auto_answer") is not None:
            lines.append(f"\nQ: {q['question']}\nA: {q['auto_answer']}")
    if len(lines) == 1:
        # No auto-answers found — return empty so caller doesn't set context
        return ""
    return "\n".join(lines)


class IssueExecutor:
    """Orchestrates triage and pipeline execution for issues."""

    def __init__(
        self,
        issues_repo: IssuesRepository,
        jobs_repo: JobsRepository,
        traces_repo: TracesRepository,
        workflow_registry: WorkflowRegistry,
        database_url: str,
        audit_log_path: Any = None,
        db: DatabasePool | None = None,
        inputs_repo: Any = None,
        config: Any = None,
        sandbox_manager: Any = None,
        agent_runner: Any = None,
        proxy_manager: Any = None,
        event_bus: EventBus | None = None,
        env_repo: EnvironmentRepository | None = None,
        repo_preferences_repo: RepoPreferencesRepository | None = None,
        secrets_provider: Any = None,  # FernetSecretsProvider; injected at startup
        qa_minio: Any = None,  # QAMinioClient (internal endpoint) — used by report_node
        qa_minio_sandbox: Any = None,  # QAMinioClient (sandbox-facing) — used by init_qa_node
        qa_token_registry: Any = None,  # SandboxTokenRegistry — used by init_qa + report
        profiles_repo: SandboxProfilesRepository | None = None,  # SandboxProfilesRepository — init_qa
        agent_sessions_repo: Any = None,  # AgentSessionsRepository — Plan C resume
        # Phase-A wiring: cache layers + dashboard reporting
        qa_app_results_repo: Any = None,
        qa_image_tags_repo: Any = None,
        qa_volume_state_repo: Any = None,
        qa_shared_volume_manager: Any = None,
        # Phase 5C: in-process QA event bus for SSE liveness. None in tests
        # / legacy callers; emit_result_node + qa_orchestrator_node no-op
        # when absent.
        qa_event_bus: Any = None,
        # Phase 5D: affected-set / dep-graph snapshot persistence. None in
        # tests / legacy callers; qa_orchestrator_node no-ops when absent.
        qa_run_metadata_repo: Any = None,
        # Phase 5G: per-repo workspace_provider overrides set via the admin
        # UI. When None, the orchestrator uses the .embry0/qa.yaml config
        # unmodified. None in tests / legacy callers.
        qa_workspace_provider_overrides_repo: Any = None,
        github_token: str | None = None,
        github_comment_channel: Any = None,
        # EMB-47: LinearSyncService for terminal-outcome write-back on issues
        # dispatched from Linear. None in tests / legacy callers.
        linear_sync: Any = None,
    ) -> None:
        self._linear_sync = linear_sync
        self._issues = issues_repo
        self._jobs = jobs_repo
        self._traces = traces_repo
        self._registry = workflow_registry
        self._database_url = database_url
        self._audit_log_path = audit_log_path
        self._db = db
        self._inputs = inputs_repo
        self._config = config
        self._sandbox = sandbox_manager
        self._agent_runner = agent_runner
        self._proxy = proxy_manager
        self._event_bus = event_bus
        self._env_repo = env_repo
        self._repo_prefs = repo_preferences_repo
        self._secrets_provider = secrets_provider
        self._qa_minio = qa_minio
        self._qa_minio_sandbox = qa_minio_sandbox
        self._qa_token_registry = qa_token_registry
        self._profiles_repo = profiles_repo
        self._agent_sessions_repo = agent_sessions_repo
        # Phase-A wiring: cache layers + dashboard reporting
        self._qa_app_results_repo = qa_app_results_repo
        self._qa_image_tags_repo = qa_image_tags_repo
        self._qa_volume_state_repo = qa_volume_state_repo
        self._qa_shared_volume_manager = qa_shared_volume_manager
        # Phase 5C: SSE liveness pub/sub. May be None in tests or when the
        # FastAPI lifespan didn't run (e.g. unit tests that build IssueExecutor
        # directly). The QA workflow nodes treat absence as no-op.
        self._qa_event_bus = qa_event_bus
        # Phase 5D: affected-set persistence repo. May be None in tests; the
        # QA orchestrator node treats absence as no-op.
        self._qa_run_metadata_repo = qa_run_metadata_repo
        # Phase 5G: workspace_provider overrides repo. May be None in tests;
        # the QA orchestrator node treats absence as no-op (uses qa.yaml).
        self._qa_workspace_provider_overrides_repo = qa_workspace_provider_overrides_repo
        self._github_token = github_token
        self._github_comment_channel = github_comment_channel
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._tasks_by_job: dict[str, asyncio.Task[Any]] = {}

    def _select_workflow(self, pipeline: str) -> Any:
        """Pick the workflow class based on the pipeline name.

        Unknown pipeline names fall back to the default issue-to-pr workflow
        for backwards compatibility with callers that pass arbitrary template
        ids in the ``pipeline_template`` column.
        """
        if pipeline == "qa":
            return QAWorkflow()
        return IssueToprWorkflow()

    def _build_graph_config(self, job_id: str) -> dict[str, Any]:
        """Build the graph config dict for LangGraph execution."""
        # Read OAuth token so the resolver can validate credentials before
        # dispatching to the sandbox. The sandbox also injects it independently
        # via SandboxManager.create(), but the resolver checks it up-front.
        oauth_token = ""
        api_key = ""
        if self._config:
            api_key = getattr(self._config, "anthropic_api_key", "") or ""
        if self._sandbox and hasattr(self._sandbox, "_read_oauth_token"):
            oauth_token = self._sandbox._read_oauth_token() or ""
        credentials = {"api_key": api_key, "oauth_token": oauth_token}

        # Lazy-construct profiles_repo from the shared DB pool when one wasn't
        # injected (covers older test fixtures that don't pass it explicitly).
        profiles_repo: Any = self._profiles_repo
        if profiles_repo is None and self._db is not None:
            profiles_repo = SandboxProfilesRepository(self._db)

        return {
            "configurable": {
                "thread_id": job_id,
                "job_id": job_id,
                "agent_runner": self._agent_runner,
                "sandbox_manager": self._sandbox,
                "proxy_manager": self._proxy,
                "docker": getattr(self._sandbox, "_docker", None) if self._sandbox else None,
                "issues_repo": self._issues,
                "inputs_repo": self._inputs,
                "db": self._db,
                "repo_preferences_repo": self._repo_prefs,
                "traces_repo": self._traces,
                "credentials": credentials,
                # QA-specific deps. Read by embry0.workflows.qa.nodes.{init_qa,
                # qa, report, retry}; harmless for non-QA workflows because they
                # never look these keys up.
                "qa_minio": self._qa_minio,
                "qa_minio_sandbox": self._qa_minio_sandbox,
                "qa_token_registry": self._qa_token_registry,
                "profiles_repo": profiles_repo,
                "agent_sessions_repo": self._agent_sessions_repo,
                # Phase-A wiring: cache layers + dashboard reporting.
                # Read by:
                #   init_orchestrator_node (qa_shared_volume_manager,
                #     qa_volume_state_repo)
                #   qa_orchestrator_node (qa_image_tags_repo,
                #     qa_app_results_repo)
                #   qa_report_node (github_token)
                "qa_app_results_repo": self._qa_app_results_repo,
                "qa_image_tags_repo": self._qa_image_tags_repo,
                "qa_volume_state_repo": self._qa_volume_state_repo,
                "qa_shared_volume_manager": self._qa_shared_volume_manager,
                # Phase 5C: SSE liveness — read by emit_result_node and
                # qa_orchestrator_node. Absent in unit tests and legacy paths.
                "qa_event_bus": self._qa_event_bus,
                # Phase 5D: affected-set persistence — read by qa_orchestrator_node
                # to upsert qa_run_metadata after the apps decision is final.
                "qa_run_metadata_repo": self._qa_run_metadata_repo,
                # Phase 5G: workspace_provider overrides — read by
                # qa_orchestrator_node to layer DB-set provider config on top
                # of the .embry0/qa.yaml workspace_provider section.
                "qa_workspace_provider_overrides_repo": (self._qa_workspace_provider_overrides_repo),
                "github_token": self._github_token,
            }
        }

    async def _process_stream(
        self,
        graph: Any,
        input_value: Any,
        graph_config: dict[str, Any],
        issue_id: str | None,
        job_id: str,
    ) -> tuple[dict[str, Any] | None, bool, Any]:
        """Stream graph execution events, detect interrupts.

        Returns (final_state, interrupted, interrupt_value).
        """
        final_state: dict[str, Any] | None = None
        last_persisted_stage: str | None = None

        async for event in graph.astream(
            input_value,
            config=graph_config,
            stream_mode=["updates", "custom"],
        ):
            mode = event[0] if isinstance(event, tuple) else "updates"
            data = event[1] if isinstance(event, tuple) else event

            if mode == "custom":
                await self._broadcast_event(job_id, data)
            elif mode == "updates":
                if isinstance(data, dict):
                    for _node_name, node_output in data.items():
                        if isinstance(node_output, dict):
                            final_state = {**(final_state or {}), **node_output}
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

    async def execute(
        self,
        issue_id: str,
        additional_context: str = "",
        agent_models: dict[str, str] | None = None,
    ) -> str:
        """Create a job for the issue and execute the workflow in the background.

        Returns the job_id. The workflow runs asynchronously.
        ``additional_context`` is threaded into ``initial_state`` so the triage
        agent can see prior Q&A answers (e.g. from an auto-answerable retriage).
        ``agent_models`` is a per-agent model override (EMB-47 label-driven
        dispatch), persisted as ``pipeline_config.agent_models_override`` — the
        same key POST /jobs stores and ``_run_workflow`` surfaces on state.
        """
        import uuid

        issue = await self._issues.get(issue_id)
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")

        # Concurrency guard: reject if a job is already active for this issue
        active_jobs, _ = await self._jobs.list_all(issue_id=issue_id, limit=10, offset=0)
        active_statuses = {"pending", "running", "awaiting_input", "paused"}
        for existing_job in active_jobs:
            if existing_job["status"] in active_statuses:
                raise ValueError(
                    f"Issue {issue_id} already has an active job: "
                    f"{existing_job['job_id']} (status={existing_job['status']})"
                )

        task_text = issue["title"]
        if issue.get("body"):
            task_text += "\n\n" + issue["body"]

        trace_id = f"trc-{uuid.uuid4().hex[:12]}"

        job_id = await self._jobs.create(
            repo=issue.get("repo") or "unknown/unknown",
            task=task_text,
            issue_number=issue.get("github_number"),
            issue_id=issue_id,
            trace_id=trace_id,
            pipeline_config={"agent_models_override": dict(agent_models)} if agent_models else None,
        )

        await emit_audit(
            self._db,
            "issue.job_created",
            actor="system",
            details={"job_id": job_id, "issue_id": issue_id},
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        logger.info("issue_job_created", issue_id=issue_id, job_id=job_id)

        self._track_task(
            self._run_workflow(issue_id, job_id, issue, additional_context=additional_context),
            kind="workflow_execute",
            job_id=job_id,
            issue_id=issue_id,
        )

        return job_id

    async def start_job(
        self,
        repo: str,
        branch: str,
        pipeline: str,
        qa_overrides: dict[str, Any] | None = None,
        agent_models: dict[str, str] | None = None,
    ) -> str:
        """Issue-less job entry point — used by ``POST /api/v1/jobs`` for
        standalone pipelines (currently ``pipeline='qa'``).

        Creates a job row, seeds the workflow's initial state, and dispatches
        the configured workflow in the background. Returns the new job id.
        """
        import uuid

        if pipeline != "qa":
            # Today only QA uses this path. Other pipelines still go through
            # JobsRepository.create directly from the API handler.
            raise ValueError(f"start_job does not support pipeline={pipeline!r}")

        qa_overrides = qa_overrides or {}
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"

        # Persist a thin job row. The full QA config lives in pipeline_config
        # so the dashboard / list endpoints surface it.
        pipeline_config: dict[str, Any] = {
            "pipeline": "qa",
            "branch": branch,
            "qa": {k: v for k, v in qa_overrides.items() if v is not None},
        }
        # Per-agent model override (JobCreateRequest.agent_models). Same key the
        # issue path stores, so the job row surfaces it identically.
        if agent_models:
            pipeline_config["agent_models_override"] = dict(agent_models)
        sandbox_profile = qa_overrides.get("sandbox_profile") or "qa-jvm"
        # `task` is required on the jobs row; synthesize a human-readable
        # placeholder so the dashboard has something to display.
        task_text = f"QA run on {repo}@{branch}"

        job_id = await self._jobs.create(
            repo=repo,
            task=task_text,
            pipeline_template="qa",
            pipeline_config=pipeline_config,
            sandbox_profile=sandbox_profile,
            trace_id=trace_id,
        )

        await emit_audit(
            self._db,
            "qa.job_created",
            actor="system",
            details={"job_id": job_id, "repo": repo, "branch": branch},
            audit_log_path=self._audit_log_path,
        )

        logger.info("qa_job_created", job_id=job_id, repo=repo, branch=branch)

        self._track_task(
            self._run_qa_workflow(
                job_id,
                repo=repo,
                branch=branch,
                qa_overrides=qa_overrides,
                agent_models=agent_models,
            ),
            kind="qa_workflow_execute",
            job_id=job_id,
        )

        return job_id

    def _track_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str,
        job_id: str,
        issue_id: str | None = None,
    ) -> asyncio.Task[Any]:
        """Create an asyncio.Task from ``coro``, register it, and attach a
        done-callback that logs failures and publishes a ``job_failed`` event.

        Centralizes the former 3x duplication of create_task + set.add +
        add_done_callback(set.discard). The callback:

        - Removes the task from ``_background_tasks`` (idempotent).
        - On CancelledError: silent (normal shutdown path).
        - On any other exception: logs ``background_task_failed`` with the
          kind/job_id/issue_id context, and publishes a ``job_failed`` event
          via the event bus so WS clients see the failure even if the coro
          died outside its own try/except.
        """
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._background_tasks.add(task)
        self._tasks_by_job[job_id] = task

        def _on_done(t: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(t)
            # Only remove from _tasks_by_job if this task is still the one
            # registered for that job_id (it may have been overwritten by a
            # later task tracked under the same job_id).
            if self._tasks_by_job.get(job_id) is t:
                self._tasks_by_job.pop(job_id, None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            logger.error(
                "background_task_failed",
                kind=kind,
                job_id=job_id,
                issue_id=issue_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Best-effort WS notification. Track the publish task on
            # _background_tasks so a clean shutdown awaits it (otherwise it
            # could be orphaned if the loop is tearing down).
            if self._event_bus is not None:
                pub_task = asyncio.create_task(
                    self._event_bus.publish(
                        job_id,
                        {
                            "type": "job_failed",
                            "job_id": job_id,
                            "kind": kind,
                            "error": str(exc),
                        },
                    )
                )
                self._background_tasks.add(pub_task)
                pub_task.add_done_callback(self._background_tasks.discard)

        task.add_done_callback(_on_done)
        return task

    async def cancel_job(self, job_id: str, *, actor: str = "system") -> None:
        """Centralised cancellation: cancels the active task, purges the
        sandbox, purges the LangGraph checkpoint, transitions DB status,
        resets the associated issue. Idempotent.
        """
        from embry0.orchestration.checkpoint import purge_thread

        # 1. Cancel the live task (best effort — may already be done).
        # shield: if the HTTP request handler is cancelled while we wait,
        # don't double-cancel the already-cancelling background task.
        task = self._tasks_by_job.get(job_id)
        task_still_alive_after_timeout = False
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except TimeoutError:
                task_still_alive_after_timeout = True
                logger.warning(
                    "cancel_timeout_task_still_alive",
                    job_id=job_id,
                    msg="Background task did not honour cancel within 5s; proceeding with status update anyway.",
                )
            except asyncio.CancelledError:
                # Task finished cancelling — expected path.
                pass

        # 2. Destroy sandbox (idempotent)
        container_name = f"sandbox-{job_id}"
        if self._sandbox is not None:
            try:
                await self._sandbox.destroy(container_name)
            except Exception:
                logger.warning("sandbox_destroy_on_cancel_failed", job_id=job_id, exc_info=True)

        # 3. Update DB: job → cancelled, issue → open.
        # Re-check status if the task didn't cancel in time — avoid clobbering
        # a 'completed'/'failed' status the task just wrote.
        if task_still_alive_after_timeout:
            try:
                current = await self._jobs.get(job_id)
                if current and current.get("status") in ("completed", "failed"):
                    logger.info(
                        "cancel_superseded_by_terminal_status",
                        job_id=job_id,
                        actual_status=current.get("status"),
                    )
                    return
            except Exception:
                logger.warning("cancel_status_recheck_failed", job_id=job_id, exc_info=True)

        try:
            await self._jobs.update(job_id, status="cancelled")
        except StatusTransitionConflict:
            logger.warning(
                "job_status_race_on_cancel",
                job_id=job_id,
                msg="Status changed by concurrent writer before cancel could apply; treating as already resolved.",
            )
        except Exception:
            logger.warning("job_status_cancel_update_failed", job_id=job_id, exc_info=True)
        job = None
        try:
            job = await self._jobs.get(job_id)
        except Exception:
            logger.warning("job_fetch_during_cancel_failed", job_id=job_id, exc_info=True)
        if job and job.get("issue_id"):
            try:
                await self._issues.update(job["issue_id"], status="open")
            except Exception:
                logger.warning("issue_reset_on_cancel_failed", issue_id=job["issue_id"], exc_info=True)

        # 4. Purge LangGraph checkpoints for this thread
        try:
            await purge_thread(self._database_url, job_id)
        except Exception:
            logger.warning("checkpoint_purge_failed", job_id=job_id, exc_info=True)

        # 5. Emit audit event (best effort)
        try:
            await emit_audit(
                self._db,
                "job.cancelled",
                actor=actor,
                details={"job_id": job_id},
                audit_log_path=self._audit_log_path,
                issue_id=job.get("issue_id") if job else None,
            )
        except Exception:
            logger.warning("cancel_audit_emit_failed", job_id=job_id, exc_info=True)

        logger.info("job_cancelled", job_id=job_id, actor=actor)

    async def _cleanup_sandbox(self, final_state: dict[str, Any] | None, job_id: str) -> None:
        """Destroy the sandbox container if present."""
        container_id = final_state.get("sandbox_container_id") if final_state else None
        if container_id and self._sandbox:
            try:
                await self._sandbox.destroy(container_id)
                logger.info("sandbox_destroyed", job_id=job_id)
            except Exception:
                logger.warning("sandbox_destroy_failed", job_id=job_id, exc_info=True)

    async def _load_merged_env_vars(self, repo_name: str, job_id: str) -> list[dict[str, Any]] | None:
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
            from embry0.api.v1.environment import _decrypt_vars

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

                provider = FernetSecretsProvider(secret_key=key)
            global_rows = await self._env_repo.get_global()
            repo_rows = await self._env_repo.get_repo(repo_name)
            merged: dict[str, dict[str, Any]] = {}
            for row in global_rows:
                merged[row["key"]] = row
            for row in repo_rows:
                merged[row["key"]] = row
            decrypted = await _decrypt_vars(list(merged.values()), provider)
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

    async def _execute_workflow_stream(
        self,
        input_value: Any,
        issue_id: str | None,
        job_id: str,
        pipeline: str = "issue-to-pr",
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        """Run a workflow against ``input_value``.

        ``pipeline`` selects which workflow to compile via ``_select_workflow``
        (defaults to ``issue-to-pr`` for the legacy issue path). Shared body of
        ``_run_workflow`` (fresh initial state) and ``resume``
        (Command(resume=...)). Returns the tuple from ``_process_stream``.
        """
        workflow = self._select_workflow(pipeline)

        graph_config = self._build_graph_config(job_id)

        async with checkpointer_context(self._database_url) as saver:
            graph = workflow.compile(config={"checkpointer": saver})
            return await self._process_stream(
                graph,
                input_value,
                graph_config,
                issue_id,
                job_id,
            )

    async def _handle_interrupt(
        self,
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
        self,
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
        try:
            await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

            initial_state = {
                "job_id": job_id,
                "issue_id": issue_id,
                "repo": issue.get("repo") or "",
                "task": issue["title"] + ("\n\n" + issue["body"] if issue.get("body") else ""),
                "issue_number": issue.get("github_number"),
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

            # Pass through any per-agent model override supplied at job creation
            # time (JobCreateRequest.agent_models). Stored on the job row under
            # pipeline_config.agent_models_override; read here and surfaced on
            # state so agent nodes can prefer it over the triage decision.
            overrides = (job_row or {}).get("pipeline_config") or {}
            if isinstance(overrides, dict):
                agent_models_override = overrides.get("agent_models_override")
                if agent_models_override:
                    initial_state["agent_models_override"] = agent_models_override

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

            final_state, interrupted, interrupt_value = await self._execute_workflow_stream(
                initial_state,
                issue_id,
                job_id,
            )

            if interrupted:
                await self._handle_interrupt(issue_id, job_id, interrupt_value)
                return

            if final_state:
                await self._handle_workflow_result(issue_id, job_id, final_state)
            else:
                await self._jobs.update(job_id, status="completed")

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
            from embry0.safety.error_codes import error_code_for_exception

            code = error_code_for_exception(exc)
            await self._jobs.update(
                job_id,
                status="failed",
                error_message=str(exc),
                error_code=code.value,
            )
            await self._issues.update(issue_id, status="open")
            await emit_audit(
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

            # Cleanup sandbox on error
            await self._cleanup_sandbox(final_state, job_id)
        finally:
            await self._post_linear_outcome(issue_id, job_id)
            if trace_id_bound:
                try:
                    cv.unbind_contextvars("trace_id")
                except Exception:
                    logger.warning("trace_id_unbind_failed", job_id=job_id, exc_info=True)

    async def _post_linear_outcome(self, issue_id: str | None, job_id: str) -> None:
        """EMB-47 write-back: comment the terminal job outcome on the source
        Linear issue. No-op unless the integration is wired AND the issue was
        dispatched from Linear AND the job reached a terminal status (paused /
        awaiting-input runs get their comment when they eventually finish).

        getattr: several unit tests build the executor via ``__new__`` and set
        only the attributes their path touches — this hook must not add a new
        construction requirement to that pattern."""
        if getattr(self, "_linear_sync", None) is None or not issue_id:
            return
        try:
            issue = await self._issues.get(issue_id)
            if not issue or not issue.get("linear_issue_id"):
                return
            job = await self._jobs.get(job_id)
            if not job or job.get("status") in ("running", "pending", "paused", "awaiting_input"):
                return
            await self._linear_sync.post_job_outcome(issue, job)
        except Exception:
            logger.warning("linear_outcome_post_failed", job_id=job_id, exc_info=True)

    async def _run_qa_workflow(
        self,
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
            await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

            qa_block: dict[str, Any] = {
                "needs_qa": True,
                "acceptance_criteria": list(qa_overrides.get("acceptance_criteria") or []),
                "sandbox_profile_name": qa_overrides.get("sandbox_profile") or "qa-jvm",
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
            # the merge), so credentials seeded for command-center never
            # reached the sub-task sandboxes — directory reported "Missing
            # AZURE_*", credit kept hitting Auth0 despite RC_QA_BYPASS being
            # in the store, etc. Shared helper keeps both paths in lockstep.
            env_vars = await self._load_merged_env_vars(repo, job_id)
            if env_vars:
                initial_state["user_env_vars"] = env_vars

            final_state, interrupted, _ = await self._execute_workflow_stream(
                initial_state,
                issue_id="",  # QA jobs have no associated issue
                job_id=job_id,
                pipeline="qa",
            )

            # QA workflow does not interrupt today (retry_node returns
            # Command(goto=END|init_qa) — never an Interrupt). If a future
            # version adds one, surface it as a failed job rather than silently
            # leaving the row in 'running'.
            if interrupted:
                logger.warning("qa_workflow_unexpected_interrupt", job_id=job_id)
                await self._jobs.update(
                    job_id,
                    status="failed",
                    error_message="QA workflow interrupted unexpectedly",
                    finished_at=datetime.now(UTC),
                )
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

            error_code = qa_state.get("error_code") if job_status == "failed" else None
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

            # The QA fan-out subgraph never propagates per-agent
            # total_cost_usd up to the parent state (only the issue→PR path
            # accumulates it via run_agent_node), so the state value is
            # always 0.0 for QA — every QA job row showed $0.00. Trace rows
            # are written per agent under this job_id regardless of path and
            # are the same source GET /jobs cost_breakdown derives from;
            # take the total from there so the row and the breakdown agree.
            # Fall back to the state value if there are no trace rows, so a
            # future path that does accumulate isn't zeroed out.
            traces_cost = await self._traces.total_cost_for_job(job_id)
            state_cost = float((final_state or {}).get("total_cost_usd", 0.0) or 0.0)
            total_cost_usd = traces_cost if traces_cost > 0.0 else state_cost

            await self._jobs.update(
                job_id,
                status=job_status,
                error_message=error_message,
                error_code=error_code,
                total_cost_usd=total_cost_usd,
                finished_at=datetime.now(UTC),
            )

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
            from embry0.safety.error_codes import ErrorCode

            await self._jobs.update(
                job_id,
                status="failed",
                error_message=str(exc),
                error_code=ErrorCode.UNKNOWN.value,
                finished_at=datetime.now(UTC),
            )
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

    async def _handle_workflow_result(self, issue_id: str | None, job_id: str, result: dict[str, Any]) -> None:
        """Process the workflow result and update issue/job status."""
        # action lives in triage_decision (D4: state["pipeline_config"] is now the
        # flat PipelineConfig dict; it has no "action" key).
        triage_decision = result.get("triage_decision", {})
        action = triage_decision.get("action", "proceed") if isinstance(triage_decision, dict) else "proceed"
        current_stage = result.get("current_stage", "")

        logger.info(
            "workflow_result",
            issue_id=issue_id,
            job_id=job_id,
            action=action,
            stage=current_stage,
        )

        if current_stage == "awaiting_input":
            triage_decision = result.get("triage_decision", {})
            await self._handle_needs_info(issue_id, job_id, triage_decision)
            return

        if action == "split":
            await self._handle_split(issue_id, job_id, triage_decision)
        elif action == "needs_info":
            await self._handle_needs_info(issue_id, job_id, triage_decision)
        else:
            # action == "proceed" — workflow ran through the full pipeline
            from datetime import UTC, datetime

            # Pipeline ends at review_complete (approved) or abandoned (user gave up)
            failed_stages = {"abandoned", "failed", "developer_retry"}
            has_errors = bool(result.get("errors"))
            final_status = "failed" if current_stage in failed_stages or has_errors else "completed"
            # Classify the failure: graph nodes may have set a specific error_code
            # in state (e.g. ERR_TRIAGE_MALFORMED); otherwise bucket as UNKNOWN.
            error_code: str | None = None
            error_message: str | None = None
            if final_status == "failed":
                from embry0.safety.error_codes import ErrorCode

                error_code = result.get("error_code") or ErrorCode.UNKNOWN.value
                # Never leave a failed job opaque (GET /jobs/{id} must surface a
                # reason): prefer the workflow summary, else the collected errors,
                # else the stage it died at.
                error_message = result.get("result_summary")
                if not error_message:
                    errs = result.get("errors") or []
                    error_message = "; ".join(str(e) for e in errs) if errs else f"failed at stage {current_stage!r}"
            await self._jobs.update(
                job_id,
                status=final_status,
                pr_url=result.get("pr_url"),
                error_message=error_message,
                error_code=error_code,
                total_cost_usd=result.get("total_cost_usd", 0.0),
                finished_at=datetime.now(UTC),
            )
            # Purge LangGraph checkpoint state for completed/failed jobs.
            # Best-effort: job status is already written; failure here does not
            # affect the job outcome.
            from embry0.orchestration.checkpoint import purge_thread

            try:
                await purge_thread(self._database_url, job_id)
            except Exception:
                logger.warning(
                    "checkpoint_purge_on_complete_failed",
                    job_id=job_id,
                    status=final_status,
                    exc_info=True,
                )
            issue_status = "closed" if final_status == "completed" else "open"
            await self._issues.update(issue_id, status=issue_status)
            await self._issues.update_parent_status(issue_id)
            await emit_audit(
                self._db,
                "issue.status_changed",
                actor="system",
                details={
                    "old_status": "triaging",
                    "new_status": issue_status,
                    "job_status": final_status,
                },
                audit_log_path=self._audit_log_path,
                issue_id=issue_id,
            )

    async def _handle_split(self, issue_id: str | None, job_id: str, decision: dict[str, Any]) -> None:
        """Decompose issue into child issues based on triage split decision."""
        sub_tasks = decision.get("sub_tasks", [])
        if not sub_tasks:
            logger.warning("split_no_subtasks", issue_id=issue_id)
            await self._issues.update(issue_id, status="open")
            return

        issue = await self._issues.get(issue_id)
        child_ids: list[str] = []

        for sub in sub_tasks:
            child_id = await self._issues.create(
                title=sub.get("task", sub.get("title", "Untitled subtask")),
                body=sub.get("description", sub.get("body", "")),
                priority=issue.get("priority", "medium") if issue else "medium",
                labels=issue.get("labels", []) if issue else [],
                repo=issue.get("repo") if issue else None,
                parent_issue_id=issue_id,
                created_by="triage_agent",
            )
            child_ids.append(child_id)

        await self._jobs.update(job_id, status="completed")
        await self._issues.update(issue_id, status="open")
        await self._issues.update_parent_status(issue_id)

        await emit_audit(
            self._db,
            "issue.decomposed",
            actor="triage_agent",
            details={
                "child_issue_ids": child_ids,
                "count": len(child_ids),
                "reasoning": decision.get("reasoning", ""),
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        await emit_audit(
            self._db,
            "issue.triaged",
            actor="triage_agent",
            details={
                "action": "split",
                "confidence": decision.get("confidence"),
                "reasoning": decision.get("reasoning", ""),
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        logger.info("issue_decomposed", issue_id=issue_id, children=child_ids)

    async def _broadcast_event(self, job_id: str, event: dict[str, Any]) -> None:
        """Forward event to WebSocket subscribers and persist to job_logs."""
        # Add timestamp if missing (events from StreamWriter don't include one)
        if "timestamp" not in event:
            from datetime import UTC, datetime

            event = {**event, "timestamp": datetime.now(UTC).isoformat()}

        # Persist to database and capture the assigned sequence id.
        # Stamp it onto the event dict so WS subscribers can use it as a replay
        # cursor (see embry0/api/ws/streaming.py).
        seq: int | None = None
        try:
            seq = await self._jobs.append_log_event(job_id, event)
        except Exception:
            logger.warning("event_persist_failed", job_id=job_id, exc_info=True)
        if seq is not None:
            event = {**event, "event_seq": seq}

        # Persist cost incrementally on cost_update events.
        # Use atomic increment — each cost_update event carries the DELTA for the
        # most recent turn, not the cumulative total. Using update() here would
        # overwrite prior turns' cost.
        if event.get("type") == "cost_update" and event.get("cost_usd"):
            try:
                await self._jobs.increment_cost(job_id, event["cost_usd"])
            except Exception:
                logger.warning("cost_update_failed", job_id=job_id, exc_info=True)

        # Forward to WebSocket subscribers via the concurrency-safe event bus.
        # The bus logs individual subscriber failures; we skip if no bus was
        # injected (test shim).
        if self._event_bus is not None:
            await self._event_bus.publish(job_id, event)

    async def _handle_needs_info(self, issue_id: str | None, job_id: str, decision: dict[str, Any]) -> None:
        """Create input records, dispatch notifications, and pause for blocking questions.

        Atomicity: the INSERTs for all input rows and the status UPDATEs for the
        job + issue are wrapped in a single DB transaction. Either all persist
        or none do — no orphan input rows if the orchestrator crashes mid-flow.

        Audit events and notification dispatch happen AFTER the commit, because:
        - Audit is additive (can emit a few extra records; no correctness impact).
        - Notifications are best-effort (a missed Telegram ping is not a data issue).
        """
        import uuid

        from embry0.notifications.dispatcher import dispatch_questions

        questions = decision.get("questions", [])
        asking_node = decision.get("asking_node", "triage") if "asking_node" in decision else "triage"

        # Normalize questions to dicts and compute rollout details.
        normalized: list[dict[str, Any]] = []
        for q in questions:
            if isinstance(q, str):
                q = {"question": q, "importance": "blocking"}
            importance = q.get("importance", "blocking")
            auto_answer = q.get("suggested_answer") if importance == "auto_answerable" else None
            normalized.append(
                {
                    "question": q["question"],
                    "importance": importance,
                    "auto_answer": auto_answer,
                }
            )

        has_blocking = any(n["importance"] == "blocking" for n in normalized)

        # Transactional writes: inputs + status updates as one unit.
        if self._db is None:
            raise RuntimeError("IssueExecutor._db is None — cannot write inputs")
        enriched_questions: list[dict[str, Any]] = []
        async with self._db.transaction() as conn:
            for n in normalized:
                input_id = f"inp-{uuid.uuid4().hex[:12]}"
                status = (
                    "auto_answered"
                    if n["auto_answer"] is not None and n["importance"] == "auto_answerable"
                    else "pending"
                )
                await conn.execute(
                    """
                    INSERT INTO issue_inputs (
                        id, issue_id, job_id, asking_node, question,
                        importance, auto_answer, status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    input_id,
                    issue_id,
                    job_id,
                    asking_node,
                    n["question"],
                    n["importance"],
                    n["auto_answer"],
                    status,
                )
                enriched_questions.append(
                    {
                        "question": n["question"],
                        "importance": n["importance"],
                        "auto_answer": n["auto_answer"],
                        "input_id": input_id,
                    }
                )

            if has_blocking:
                await conn.execute(
                    "UPDATE jobs SET status = 'awaiting_input' WHERE job_id = $1",
                    job_id,
                )
                await conn.execute(
                    "UPDATE issues SET status = 'awaiting_input' WHERE id = $1",
                    issue_id,
                )

        # Audit (post-commit, additive)
        for eq in enriched_questions:
            await emit_audit(
                self._db,
                "issue.input_created",
                actor=asking_node,
                details={
                    "input_id": eq["input_id"],
                    "question": eq["question"],
                    "importance": eq["importance"],
                },
                audit_log_path=self._audit_log_path,
                issue_id=issue_id,
            )

        if has_blocking:
            await emit_audit(
                self._db,
                "issue.pipeline_paused",
                actor="system",
                details={
                    "job_id": job_id,
                    "pending_count": sum(1 for eq in enriched_questions if eq["importance"] == "blocking"),
                },
                audit_log_path=self._audit_log_path,
                issue_id=issue_id,
            )

            # Best-effort notification dispatch
            issue = await self._issues.get(issue_id)
            if issue and self._config:
                blocking_questions = [
                    {
                        "input_id": eq["input_id"],
                        "question": eq["question"],
                        "asking_node": asking_node,
                        "importance": eq["importance"],
                    }
                    for eq in enriched_questions
                    if eq["importance"] == "blocking"
                ]
                # The GitHub-comment channel is injected by the API lifespan
                # via app.state; the always-on dashboard no-op channel runs
                # by default.
                await dispatch_questions(
                    blocking_questions,
                    issue,
                    github_comment_channel=self._github_comment_channel,
                )
        else:
            # All auto-answerable — re-run triage with the answers folded into
            # additional_context. Previously this called execute(issue_id) with
            # no context, causing the new triage agent to re-ask the same
            # questions. Now the agent sees the Q&A block and can proceed.
            qa_block = _fold_auto_answers_into_context(enriched_questions)
            await self._jobs.update(job_id, status="completed")
            if issue_id is None:
                # Issue-less API jobs have no issue row to re-triage — the
                # auto-answer re-run path is issue-driven. Surfaced by the
                # str|None sweep; previously execute(None) raised and was
                # swallowed by the except below. Make the skip explicit.
                logger.warning("auto_answer_retriage_skipped_issueless", job_id=job_id)
                return
            await self._issues.update(issue_id, status="triaging")
            try:
                new_job_id = await self.execute(issue_id, additional_context=qa_block)
                logger.info(
                    "auto_answer_retriage",
                    issue_id=issue_id,
                    new_job_id=new_job_id,
                    qa_block_len=len(qa_block),
                )
            except Exception:
                logger.warning("auto_answer_retriage_failed", issue_id=issue_id, exc_info=True)
                await self._issues.update(issue_id, status="open")

        logger.info("needs_info_handled", issue_id=issue_id, blocking=has_blocking)

    async def resume(self, issue_id: str | None, job_id: str, answers: Any) -> None:
        """Resume a paused pipeline with user answers using Command(resume=).

        ``issue_id`` is None for issue-less API-submitted jobs; issue-status
        updates no-op in that case (IssuesRepository.update skips missing rows).
        """
        from langgraph.types import Command

        final_state: dict[str, Any] | None = None
        try:
            await self._jobs.update(job_id, status="running")
            await self._issues.update(issue_id, status="triaging")

            final_state, interrupted, interrupt_value = await self._execute_workflow_stream(
                Command(resume=answers),
                issue_id,
                job_id,
            )

            if interrupted:
                await self._handle_interrupt(issue_id, job_id, interrupt_value)
                return

            if final_state:
                await self._handle_workflow_result(issue_id, job_id, final_state)

            # Don't destroy sandbox for paused jobs
            job_record = await self._jobs.get(job_id)
            if job_record and job_record.get("status") == "paused":
                logger.info("sandbox_preserved_for_paused_job", job_id=job_id)
            else:
                await self._cleanup_sandbox(final_state, job_id)

        except Exception as exc:
            logger.error("pipeline_resume_failed", issue_id=issue_id, job_id=job_id, error=str(exc))
            from embry0.safety.error_codes import error_code_for_exception

            code = error_code_for_exception(exc)
            await self._jobs.update(
                job_id,
                status="failed",
                error_message=str(exc),
                error_code=code.value,
            )
            await self._issues.update(issue_id, status="open")

    async def resume_for_issue(self, issue_id: str) -> None:
        """Resume the latest awaiting_input job for ``issue_id``.

        Used by inbound channels (GitHub issue_comment, Telegram reply)
        once the dispatcher has persisted answers into ``issue_inputs``.
        Looks up the most recent awaiting_input job and dispatches
        ``resume(...)`` in the background — answers are read from
        ``state["user_answers"]`` by downstream nodes (which load them
        from the DB), so we pass ``answers=None`` here.

        No-op when:
        - the issue has no awaiting_input job (e.g. comment arrived after a
          concurrent answer already triggered a resume),
        - the issue itself is no longer ``awaiting_input``.

        This mirrors ``embry0.api.v1.issues._resume_pipeline``'s idempotency
        guard so cross-channel races are safe.
        """
        issue = await self._issues.get(issue_id)
        if not issue or issue.get("status") != "awaiting_input":
            logger.info(
                "resume_for_issue_skipped_status",
                issue_id=issue_id,
                status=(issue or {}).get("status"),
            )
            return

        jobs_list, _ = await self._jobs.list_all(issue_id=issue_id, limit=10, offset=0)
        awaiting_jobs = [j for j in jobs_list if j["status"] == "awaiting_input"]
        if not awaiting_jobs:
            logger.info("resume_for_issue_no_awaiting_job", issue_id=issue_id)
            return

        # `list_all` orders by created_at DESC, so awaiting_jobs[0] is the latest.
        job_id = awaiting_jobs[0]["job_id"]
        self._track_task(
            self.resume(issue_id, job_id, answers=None),
            kind="resume_via_inbound",
            job_id=job_id,
            issue_id=issue_id,
        )
        logger.info("resume_for_issue_dispatched", issue_id=issue_id, job_id=job_id)
