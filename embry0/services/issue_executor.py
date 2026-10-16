"""Issue executor — creates jobs from issues and runs the workflow pipeline."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from embry0.execution import events

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from embry0.api.events.bus import EventBus
    from embry0.metrics import LoopMetrics
    from embry0.orchestration.budget_gate import DispatchDecision
    from embry0.storage.repositories.environment import EnvironmentRepository
    from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

import embry0.services._executor_outcomes as _executor_outcomes
import embry0.services._executor_run as _executor_run
from embry0.audit.helpers import emit_audit
from embry0.orchestration import dispatch_actors
from embry0.orchestration.restart import ExecutorDraining, ShutdownInterrupt
from embry0.orchestration.run_slots import RunSlot, RunSlots
from embry0.safety.error_codes import ErrorCode
from embry0.services._cost import resolve_job_total_cost
from embry0.services._executor_outcomes import _fold_auto_answers_into_context
from embry0.services.healing.recorder import record_case_event
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository, StatusTransitionConflict
from embry0.storage.repositories.repos import UnregisteredRepoError
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.trackers import github_number_of
from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
from embry0.workflows.onboard.graph import OnboardWorkflow
from embry0.workflows.qa.graph import QAWorkflow
from embry0.workflows.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)

__all__ = ["IssueExecutor", "_fold_auto_answers_into_context"]


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
        # #167: forge lookup (repos.forge) so clone/enroll/MR routing
        # can tell GitHub from GitLab. None in tests → github.
        repos_repo: Any = None,
        secrets_provider: Any = None,  # FernetSecretsProvider; injected at startup
        qa_minio: Any = None,  # QAMinioClient (internal endpoint) — boot screenshots, deliverables
        qa_minio_sandbox: Any = None,  # QAMinioClient (sandbox-facing) — used by acquire_sandbox_node
        qa_token_registry: Any = None,  # SandboxTokenRegistry — used by acquire_sandbox + release_sandbox
        profiles_repo: SandboxProfilesRepository | None = None,  # SandboxProfilesRepository — QA + onboarding sandboxes
        agent_sessions_repo: Any = None,  # AgentSessionsRepository — Plan C resume
        # Phase-A wiring: cache layers + dashboard reporting
        qa_app_results_repo: Any = None,
        qa_image_tags_repo: Any = None,
        qa_base_digest_resolver: Any = None,
        qa_volume_state_repo: Any = None,
        qa_shared_volume_manager: Any = None,
        # Phase 5C: in-process QA event bus for SSE liveness. None in tests
        # / legacy callers; emit_result_node + qa_orchestrator_node no-op
        # when absent.
        qa_event_bus: Any = None,
        # Phase 5D: affected-set / dep-graph snapshot persistence. None in
        # tests / legacy callers; qa_orchestrator_node no-ops when absent.
        qa_run_metadata_repo: Any = None,
        github_token: str | None = None,
        github_comment_channel: Any = None,
        # Native Telegram question channel (reply-to-answer). None when the
        # bot token/chat are unconfigured, in tests, and in legacy callers.
        telegram_channel: Any = None,
        # Slack incoming-webhook question channel (outbound only). The URL is
        # read per send; None in tests and legacy callers.
        slack_channel: Any = None,
        # #163 (née EMB-47): TrackerCommentChannel per tracker name, for
        # write-back (questions trail / terminal outcome) on the issue's
        # originating tracker. None/empty in tests / legacy callers.
        tracker_channels: dict[str, Any] | None = None,
        # RAV-1052: outbound WebhookNotifier for job.completed/job.failed and
        # agent.question events. None in tests / legacy callers.
        webhook_notifier: Any = None,
        # #246: pre-dispatch daily/monthly cap + per-author rate limit.
        # Lazily built from the shared pool when absent; injected in tests.
        budget_gate: Any = None,
        # #278: CaseRecorder — reports job links and PRs to the healing-case
        # ledger, fail-open. None in tests / legacy callers.
        case_recorder: Any = None,
        # Credential registry (CredentialResolver). None in tests / legacy
        # callers: every forge token read then falls back to the env lookup.
        credential_resolver: Any = None,
        # CredentialsRepository: stamps the job's credential_uses rows when it
        # ends. None in tests / legacy callers: the ledger is left open.
        credentials_repo: Any = None,
        # CredentialHealth: quarantines the LLM account behind a provider
        # failure. None in tests / legacy callers: the job still finishes.
        credential_health: Any = None,
        # LoopMetrics: counts job events that could not be persisted. None in
        # tests / legacy callers: the failure is still logged.
        metrics: LoopMetrics | None = None,
    ) -> None:
        self._metrics = metrics
        self._case_recorder = case_recorder
        self._credential_resolver = credential_resolver
        self._credentials_repo = credentials_repo
        self._credential_health = credential_health
        self._tracker_channels = tracker_channels or {}
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
        self._repos = repos_repo
        self._secrets_provider = secrets_provider
        self._qa_minio = qa_minio
        self._qa_minio_sandbox = qa_minio_sandbox
        self._qa_token_registry = qa_token_registry
        self._profiles_repo = profiles_repo
        self._agent_sessions_repo = agent_sessions_repo
        # Phase-A wiring: cache layers + dashboard reporting
        self._qa_app_results_repo = qa_app_results_repo
        self._qa_image_tags_repo = qa_image_tags_repo
        # Set post-construction in api/app.py once docker exists, like
        # _qa_shared_volume_manager. None -> selection skips the check.
        self._qa_base_digest_resolver = qa_base_digest_resolver
        self._qa_volume_state_repo = qa_volume_state_repo
        self._qa_shared_volume_manager = qa_shared_volume_manager
        # Phase 5C: SSE liveness pub/sub. May be None in tests or when the
        # FastAPI lifespan didn't run (e.g. unit tests that build IssueExecutor
        # directly). The QA workflow nodes treat absence as no-op.
        self._qa_event_bus = qa_event_bus
        # Phase 5D: affected-set persistence repo. May be None in tests; the
        # QA orchestrator node treats absence as no-op.
        self._qa_run_metadata_repo = qa_run_metadata_repo
        self._github_token = github_token
        self._github_comment_channel = github_comment_channel
        self._telegram_channel = telegram_channel
        self._slack_channel = slack_channel
        self._webhook_notifier = webhook_notifier
        self._budget_gate = budget_gate
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._tasks_by_job: dict[str, asyncio.Task[Any]] = {}
        # #583: MAX_RUNNING_JOBS. A test double for config has no int here,
        # and reads as no cap.
        limit = getattr(config, "max_running_jobs", 0)
        self._run_slots = RunSlots(limit if isinstance(limit, int) and not isinstance(limit, bool) else 0)
        # #307: set by drain() at shutdown. No job starts once it is set, and
        # a running job stops at its next node boundary.
        self._draining = False
        # Jobs whose runner is past its run slot and doing work; the others
        # tracked in _tasks_by_job are waiting to start.
        self._started_jobs: set[str] = set()

    @property
    def draining(self) -> bool:
        return getattr(self, "_draining", False)

    def _get_budget_gate(self) -> Any:
        """The dispatch-time BudgetGate, built from the shared pool on first
        use. None when there is no DB (unit tests, legacy callers) — the
        dispatch guard then treats every job as within budget."""
        gate = getattr(self, "_budget_gate", None)
        if gate is not None:
            return gate
        db = getattr(self, "_db", None)
        if db is None:
            return None
        from embry0.orchestration.budget_gate import BudgetGate
        from embry0.storage.repositories.budget_config import BudgetConfigRepository
        from embry0.storage.repositories.budget_spend import BudgetSpendRepository

        self._budget_gate = BudgetGate(BudgetConfigRepository(db), BudgetSpendRepository(db))
        return self._budget_gate

    async def dispatch_refusal(self, *, actor: str | None = None) -> DispatchDecision | None:
        """The gate's refusal for a job dispatched right now, or None.

        The authoritative check still runs in the background runner
        (``_refuse_if_over_budget``), where a refusal becomes a failed job.
        Label triggers ask first (#284): a ticket that cannot run today should
        be told so once, not collect a failed job on every attempt.
        """
        gate = self._get_budget_gate()
        if gate is None:
            return None
        decision: DispatchDecision = await gate.check(actor=actor)
        return None if decision.allowed else decision

    async def _emit_audit(self, *args: Any, **kwargs: Any) -> None:
        """Late-bound ``emit_audit`` — mixin methods call through here so test
        patches on ``embry0.services.issue_executor.emit_audit`` reach them."""
        await emit_audit(*args, **kwargs)

    def _select_workflow(self, pipeline: str) -> Any:
        """Pick the workflow class based on the pipeline name.

        Unknown pipeline names fall back to the default issue-to-pr workflow
        for backwards compatibility with callers that pass arbitrary template
        ids in the ``pipeline_template`` column.
        """
        if pipeline == "qa":
            return QAWorkflow()
        if pipeline == "onboard":
            return OnboardWorkflow()
        return IssueToprWorkflow()

    def _build_graph_config(
        self,
        job_id: str,
        *,
        llm_providers: tuple[str, ...] = (),
        llm_credential_kinds: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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

        # RAV-601: plan_route needs templates (route resolution) and the jobs
        # repo (job_kind mirror); lazy-constructed like profiles_repo.
        templates_repo: Any = None
        if self._db is not None:
            from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository

            templates_repo = PipelineTemplatesRepository(self._db)

        # RAV-1251: DB-backed QA config — read by init_orchestrator_node
        # (resolution + legacy-source import) and write_config_node (onboard).
        qa_configs_repo: Any = None
        if self._db is not None:
            from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository

            qa_configs_repo = RepoQAConfigsRepository(self._db)

        # #298/#299: the verify node stores its result here.
        job_verifications_repo: Any = None
        if self._db is not None:
            from embry0.storage.repositories.job_verifications import JobVerificationsRepository

            job_verifications_repo = JobVerificationsRepository(self._db)

        return {
            # The stop for a graph that loops without reaching END
            # (ERR_GRAPH_RECURSION). LangGraph's default was 25 up to 1.0.5,
            # then 10000, and is 10007 since 1.1.4: without this key a
            # looping graph would run on until its budget ran out. Every run
            # and every resume takes its config from here, and a subgraph
            # invoked inside a node inherits the limit. 25 is the value
            # every job ran under before LangGraph 1.x (#498).
            "recursion_limit": 25,
            "configurable": {
                "thread_id": job_id,
                "job_id": job_id,
                "agent_runner": self._agent_runner,
                "sandbox_manager": self._sandbox,
                "proxy_manager": self._proxy,
                # #268: the container runtime the sandboxes run on, and its
                # CLI client for the in-sandbox commands not behind it yet.
                "runtime": self._sandbox.runtime if self._sandbox else None,
                "docker": self._sandbox.runtime.client if self._sandbox else None,
                "issues_repo": self._issues,
                "inputs_repo": self._inputs,
                # getattr: several unit tests build the executor via __new__
                # and set only the attributes their path touches.
                "jobs_repo": getattr(self, "_jobs", None),
                "pipeline_templates_repo": templates_repo,
                "qa_configs_repo": qa_configs_repo,
                "job_verifications_repo": job_verifications_repo,
                "db": self._db,
                "repo_preferences_repo": self._repo_prefs,
                # getattr: unit tests build the executor via __new__ and set
                # only what their path touches (same reason as _github_token).
                "repos_repo": getattr(self, "_repos", None),
                "credential_resolver": getattr(self, "_credential_resolver", None),
                "traces_repo": self._traces,
                "credentials": credentials,
                # Provider names and kinds only. Secrets stay in the resolver.
                "llm_providers": tuple(llm_providers),
                "llm_credential_kinds": dict(llm_credential_kinds or {}),
                # Workspace-init strategies read allowlist/bounds off the app
                # config (RAV-600).
                "embry0_config": self._config,
                # QA-specific deps. Read by the QA orchestrator and sub-task
                # nodes (embry0.workflows.qa.orchestrator, .subtask_nodes);
                # harmless for non-QA workflows because they never look these
                # keys up.
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
                # #334: shared so both prebaked-image lookups in a job reuse
                # one cache instead of re-pulling the base image.
                "qa_base_digest_resolver": getattr(self, "_qa_base_digest_resolver", None),
                "qa_volume_state_repo": self._qa_volume_state_repo,
                "qa_shared_volume_manager": self._qa_shared_volume_manager,
                # Phase 5C: SSE liveness — read by emit_result_node and
                # qa_orchestrator_node. Absent in unit tests and legacy paths.
                "qa_event_bus": self._qa_event_bus,
                # Phase 5D: affected-set persistence — read by qa_orchestrator_node
                # to upsert qa_run_metadata after the apps decision is final.
                "qa_run_metadata_repo": self._qa_run_metadata_repo,
                "github_token": self._github_token,
            },
        }

    async def unregistered_repo_refusal(self, repo: str) -> str | None:
        """Why ``repo`` must not be dispatched to, or None when it may be (#360).

        A repo with no ``repos`` row — under any spelling ``REPO_RENAMES``
        gives it — has no QA config, no env vars and no secrets in embry0, so
        a job against it looks healthy for minutes and then fails at QA, or
        runs without the environment it needs. That holds whatever
        ``DEFAULT_FORGE`` says: the default forge only answers *where to
        clone*, never *whether embry0 knows the repo*.

        None as well when there is nothing to judge — no registry wired (unit
        graphs), no repo on the issue — or the registry cannot answer (logged;
        a database that is down fails the job's own writes anyway).
        """
        repos = getattr(self, "_repos", None)  # executors built bare in unit tests carry none
        if repos is None or not repo:
            return None
        try:
            if await repos.registered_as(repo) is not None:
                return None
        except Exception:
            logger.warning("repo_registration_lookup_failed", repo=repo, exc_info=True)
            return None
        return (
            f"{ErrorCode.REPO_UNREGISTERED}: repo {repo!r} is not registered in embry0, so it has no QA config, "
            "env vars or secrets here. Register it (POST /api/v1/repos or the MCP repo_register tool), or fix "
            "the tracker's repo mapping if it names the wrong or an old repo."
        )

    async def execute(
        self,
        issue_id: str,
        additional_context: str = "",
        agent_models: dict[str, str] | None = None,
        dispatched_by: str | None = None,
    ) -> str:
        """Create a job for the issue and execute the workflow in the background.

        Returns the job_id. The workflow runs asynchronously.
        ``additional_context`` is threaded into ``initial_state`` so the triage
        agent can see prior Q&A answers (e.g. from an auto-answerable retriage).
        ``agent_models`` is a per-agent model override (EMB-47 label-driven
        dispatch), persisted as ``pipeline_config.agent_models_override`` — the
        same key POST /jobs stores and ``_run_workflow`` surfaces on state.
        ``dispatched_by`` is the actor that asked (webhook sender / API
        principal); it lands on the job row and is what the dispatch rate
        limit buckets on. None for orchestrator-initiated runs such as
        triage's sub-issues, which are then exempt from that limit (#246).
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

        # #360: every dispatch ends here, so this is the one refusal for all of
        # them. The callers return the issue to `open`; the Linear path asks
        # unregistered_repo_refusal() itself first, to tell the ticket why.
        refusal = await self.unregistered_repo_refusal(str(issue.get("repo") or ""))
        if refusal is not None:
            logger.warning("issue_dispatch_refused_unregistered_repo", issue_id=issue_id, repo=issue.get("repo"))
            raise UnregisteredRepoError(refusal)

        task_text = issue["title"]
        if issue.get("body"):
            task_text += "\n\n" + issue["body"]

        trace_id = f"trc-{uuid.uuid4().hex[:12]}"

        job_id = await self._jobs.create(
            repo=issue.get("repo") or "unknown/unknown",
            task=task_text,
            issue_number=github_number_of(issue),
            issue_id=issue_id,
            trace_id=trace_id,
            pipeline_config={"agent_models_override": dict(agent_models)} if agent_models else None,
            dispatched_by=dispatched_by,
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

        # #278: every dispatch ends here — tracker webhooks, the Linear
        # reconciler, a dashboard retry, an auto-answered re-triage — so this
        # is where a job is linked to its healing case (which accepts it).
        # CaseRecorder handles its own failures; the try is for the boundary
        # itself, which is an injected, untyped collaborator. A dispatch is
        # never lost to its own bookkeeping.
        await record_case_event(self, "job_dispatched", issue, job_id, log_ids={"issue_id": issue_id, "job_id": job_id})

        self._track_task(
            self._run_workflow(issue_id, job_id, issue, additional_context=additional_context),
            kind="workflow_execute",
            job_id=job_id,
            issue_id=issue_id,
            launch={"runner": "workflow", "issue_id": issue_id, "additional_context": additional_context},
        )

        return job_id

    def submit_job(
        self,
        job_id: str,
        *,
        repo: str | None,
        title: str,
        body: str = "",
        github_number: int | None = None,
        additional_context: str = "",
        kind: str = "workflow_execute",
    ) -> asyncio.Task[Any]:
        """Dispatch the issue-to-pr workflow for an existing issue-less job row.

        Public entry point for callers that create their own job row (direct
        POST /jobs, the log watcher, PR-comment dispatch) — builds the
        synthetic issue the workflow expects and runs it as a tracked
        background task. ``kind`` tags the task for failure logging.
        """
        synthetic_issue = {
            "repo": repo,
            "title": title,
            "body": body,
            # Tracker-shaped so github_number_of() reads it like a real row.
            "tracker": "github" if github_number is not None else None,
            "tracker_key": str(github_number) if github_number is not None else None,
        }
        return self._track_task(
            self._run_workflow(None, job_id, synthetic_issue, additional_context=additional_context),
            kind=kind,
            job_id=job_id,
            launch={
                "runner": "workflow",
                "issue_id": None,
                "issue": synthetic_issue,
                "additional_context": additional_context,
                "kind": kind,
            },
        )

    def schedule_resume(
        self,
        issue_id: str | None,
        job_id: str,
        answers: Any,
        *,
        kind: str,
    ) -> asyncio.Task[Any]:
        """Dispatch ``resume(...)`` as a tracked background task.

        Public wrapper so API routes never touch ``_track_task`` directly.
        ``issue_id`` is None for issue-less jobs (resume tolerates it).

        Raises ExecutorDraining while the orchestrator shuts down (#307): the
        answers would be lost with the process, so the caller must say so.
        """
        if self.draining:
            raise ExecutorDraining(f"embry0 is shutting down; resume job {job_id} once it is back")
        return self._track_task(
            self.resume(issue_id, job_id, answers),
            kind=kind,
            job_id=job_id,
            issue_id=issue_id,
            # resume() takes its slot itself, once the job reads `running`.
            run_slot=False,
        )

    async def resume_or_execute(
        self, issue_id: str, additional_context: str = "", dispatched_by: str | None = None
    ) -> None:
        """Resume the issue's awaiting_input job with ``additional_context``,
        or start a fresh pipeline run when no job is awaiting.

        Used by the answer flow once all blocking inputs are in. The fresh
        path flips the issue to ``triaging`` first and rolls back to ``open``
        if dispatch fails; ``additional_context`` (the accumulated Q&A) is
        threaded into the fresh run so triage sees prior answers.
        """
        jobs_list, _ = await self._jobs.list_all(issue_id=issue_id, limit=10, offset=0)
        awaiting_jobs = [j for j in jobs_list if j["status"] == "awaiting_input"]

        if awaiting_jobs:
            # list_all orders by created_at DESC — [0] is the latest.
            job_id = awaiting_jobs[0]["job_id"]
            self.schedule_resume(issue_id, job_id, additional_context, kind="resume_via_answer")
            logger.info("pipeline_resumed", issue_id=issue_id, job_id=job_id)
            return

        await self._issues.update(issue_id, status="triaging")
        try:
            # #281: the person who gave the last answer asked for this run;
            # a channel that names nobody falls back to the service actor.
            job_id = await self.execute(
                issue_id,
                additional_context=additional_context,
                dispatched_by=dispatched_by or dispatch_actors.ANSWER_RESUME,
            )
            logger.info("pipeline_resumed_fresh", issue_id=issue_id, job_id=job_id)
        except Exception:
            logger.warning("pipeline_resume_failed", issue_id=issue_id, exc_info=True)
            await self._issues.update(issue_id, status="open")

    async def start_job(
        self,
        repo: str,
        branch: str,
        pipeline: str,
        qa_overrides: dict[str, Any] | None = None,
        agent_models: dict[str, str] | None = None,
        dispatched_by: str | None = None,
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
        # #537: the caller's override, or nothing until the run resolves the
        # profile it uses — never a placeholder that reads like a fact.
        sandbox_profile = qa_overrides.get("sandbox_profile") or None
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
            dispatched_by=dispatched_by,
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
            launch={
                "runner": "qa",
                "repo": repo,
                "branch": branch,
                "qa_overrides": qa_overrides,
                "agent_models": agent_models,
            },
        )

        return job_id

    async def start_onboard_job(
        self,
        repo: str,
        branch: str,
        skip_smoke: bool = False,
        agent_models: dict[str, str] | None = None,
        dispatched_by: str | None = None,
    ) -> str:
        """EMB-50: issue-less onboard job — analyze ``repo`` and generate its
        qa.yaml v2 into the external config store. Returns the new job id."""
        import uuid

        trace_id = f"trc-{uuid.uuid4().hex[:12]}"
        pipeline_config: dict[str, Any] = {
            "pipeline": "onboard",
            "branch": branch,
            "skip_smoke": skip_smoke,
        }
        if agent_models:
            pipeline_config["agent_models_override"] = dict(agent_models)

        job_id = await self._jobs.create(
            repo=repo,
            task=f"Onboard {repo}@{branch} — generate qa.yaml into the config store",
            pipeline_template="onboard",
            pipeline_config=pipeline_config,
            sandbox_profile="slim",
            trace_id=trace_id,
            dispatched_by=dispatched_by,
        )

        await emit_audit(
            self._db,
            "onboard.job_created",
            actor="system",
            details={"job_id": job_id, "repo": repo, "branch": branch},
            audit_log_path=self._audit_log_path,
        )
        logger.info("onboard_job_created", job_id=job_id, repo=repo, branch=branch)

        self._track_task(
            self._run_onboard_workflow(
                job_id,
                repo=repo,
                branch=branch,
                skip_smoke=skip_smoke,
                agent_models=agent_models,
            ),
            kind="onboard_workflow_execute",
            job_id=job_id,
            launch={
                "runner": "onboard",
                "repo": repo,
                "branch": branch,
                "skip_smoke": skip_smoke,
                "agent_models": agent_models,
            },
        )
        return job_id

    def _track_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str,
        job_id: str,
        issue_id: str | None = None,
        run_slot: bool = True,
        launch: dict[str, Any] | None = None,
    ) -> asyncio.Task[Any]:
        """Create an asyncio.Task from ``coro``, register it, and attach a
        done-callback that logs failures and publishes a ``job_failed`` event.

        With ``run_slot`` the coroutine starts only once it holds a run slot
        (MAX_RUNNING_JOBS, #583) and gives it back when it returns, and never
        while the executor drains (#307). ``launch`` is recorded on the job
        row first, so a restart can start the job again (``relaunch``).

        Centralizes the former 3x duplication of create_task + set.add +
        add_done_callback(set.discard). The callback:

        - Removes the task from ``_background_tasks`` (idempotent).
        - On CancelledError: silent (normal shutdown path).
        - On any other exception: logs ``background_task_failed`` with the
          kind/job_id/issue_id context, and broadcasts a ``job_failed`` event
          (persisted, then published) so WS clients see the failure even if
          the coro died outside its own try/except.
        """
        workflow = coro
        if run_slot:
            coro = self._run_in_slot(workflow, job_id, launch)
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        if run_slot:
            # A task cancelled while queued, or before it ever ran, never
            # starts the workflow; close it so it is not left un-awaited.
            task.add_done_callback(lambda _t: workflow.close())
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
            if isinstance(exc, ShutdownInterrupt):
                # #307: stopped at a node boundary by a drain, not failed.
                logger.info("job_stopped_for_shutdown", kind=kind, job_id=job_id)
                return
            logger.error(
                "background_task_failed",
                kind=kind,
                job_id=job_id,
                issue_id=issue_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # spec 3.7: job_failed is persisted like every other event (it used
            # to bypass job_logs and reach only live clients). The callback is
            # synchronous, so the broadcast runs as a tracked task that a
            # clean shutdown awaits.
            pub_task = asyncio.create_task(
                self._broadcast_event(job_id, events.job_failed(kind=kind, message=str(exc)))
            )
            self._background_tasks.add(pub_task)
            pub_task.add_done_callback(self._background_tasks.discard)

        task.add_done_callback(_on_done)
        return task

    async def _acquire_run_slot(self, job_id: str, *, priority: bool = False) -> RunSlot | None:
        """Wait for a run slot (#583); None when the executor has no gate.

        A job that has to wait says so on its timeline. Cancelling the wait
        gives the claim back before the CancelledError propagates.
        """
        slots: RunSlots | None = getattr(self, "_run_slots", None)
        if slots is None:
            return None
        slot = slots.claim(job_id, priority=priority)
        if slot.granted:
            return slot
        try:
            position = slots.position(slot)
            logger.info(
                "job_queued_for_run_slot", job_id=job_id, position=position, running=slots.running, limit=slots.limit
            )
            try:
                await self._broadcast_event(
                    job_id,
                    events.system(
                        message=(
                            f"Queued: all {slots.limit} run slots (MAX_RUNNING_JOBS) are in use; "
                            f"position {position}. The job starts when a slot frees."
                        ),
                    ),
                )
            except Exception:
                logger.warning("job_queued_event_failed", job_id=job_id, exc_info=True)
            await slot.wait()
        except BaseException:
            slot.release()
            raise
        logger.info("job_run_slot_granted", job_id=job_id)
        return slot

    async def _run_in_slot(
        self, coro: Coroutine[Any, Any, Any], job_id: str, launch: dict[str, Any] | None = None
    ) -> Any:
        """Run ``coro`` holding a run slot, and give the slot back however it ends.

        ``launch`` is recorded before anything else, so a job that never gets
        to run still carries what starting it takes. While the executor
        drains (#307) the job is not started: it stays ``pending`` and the
        next boot launches it.
        """
        if launch is not None:
            try:
                await self._jobs.record_launch(job_id, launch)
            except Exception:
                logger.warning("job_launch_record_failed", job_id=job_id, exc_info=True)
        if self.draining:
            await self._defer_for_shutdown(job_id)
            return None
        slot = await self._acquire_run_slot(job_id)
        try:
            started = getattr(self, "_started_jobs", None)
            if started is not None:
                started.add(job_id)
            return await coro
        finally:
            started = getattr(self, "_started_jobs", None)
            if started is not None:
                started.discard(job_id)
            if slot is not None:
                slot.release()

    async def _defer_for_shutdown(self, job_id: str) -> None:
        """Leave a job that has not started ``pending`` for the next boot (#307)."""
        logger.info("job_start_deferred_for_shutdown", job_id=job_id)
        try:
            await self._jobs.mark_interrupted([job_id])
        except Exception:
            logger.warning("job_interrupt_mark_failed", job_id=job_id, exc_info=True)

    async def drain(self, timeout: float) -> list[str]:
        """Stop taking work and let the running jobs reach a checkpoint (#307).

        Called once, at shutdown, before the background tasks are cancelled.
        From here on no job starts: a job dispatched now, or waiting for a run
        slot, stays ``pending`` with its launch record. A running job stops at
        its next node boundary (``ShutdownInterrupt``), where LangGraph has
        saved its progress. One still inside a node after ``timeout`` seconds
        is cancelled; it resumes from its last saved node.

        Every job stopped or deferred gets ``interrupted_at``, and the agent
        a stopped job left running in its sandbox is killed. Returns the ids
        stamped.
        """
        self._draining = True
        tasks = dict(getattr(self, "_tasks_by_job", {}))
        started: set[str] = getattr(self, "_started_jobs", set())
        for job_id, task in tasks.items():
            if job_id not in started and not task.done():
                task.cancel()
        alive = [t for t in tasks.values() if not t.done()]
        logger.info("executor_draining", jobs=len(alive), timeout_seconds=timeout)
        if alive:
            _, still = await asyncio.wait(alive, timeout=timeout)
            if still:
                logger.warning(
                    "executor_drain_timeout",
                    job_ids=[job_id for job_id, t in tasks.items() if t in still],
                    msg="Jobs still inside a node at the drain deadline are cancelled and resume from their last checkpoint.",
                )
                for task in still:
                    task.cancel()
                await asyncio.wait(still, timeout=10)
        if not tasks:
            return []
        try:
            stamped = await self._jobs.mark_interrupted(list(tasks))
        except Exception:
            logger.warning("job_interrupt_mark_failed", job_ids=list(tasks), exc_info=True)
            return []

        async def settle(job_id: str) -> None:
            try:
                await self._broadcast_event(
                    job_id,
                    events.system(
                        message="Interrupted: embry0 is shutting down. The job resumes from its last checkpoint when it is back.",
                    ),
                )
            except Exception:
                logger.warning("job_interrupt_event_failed", job_id=job_id, exc_info=True)
            if self._sandbox is not None:
                try:
                    await self._sandbox.stop_agent(f"sandbox-{job_id}")
                except Exception:
                    logger.warning("sandbox_agent_stop_failed", job_id=job_id, exc_info=True)

        # Together: each stop_agent may take its 10 s timeout, and the grace
        # period is shared.
        await asyncio.gather(*(settle(job_id) for job_id in stamped))
        logger.info("executor_drained", interrupted=stamped)
        return stamped

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
            # Nor does it reach QA's own teardown: sub-task sandboxes, QA
            # networks and agent-started containers are released here (#328).
            runtime = getattr(self._sandbox, "runtime", None)
            if runtime is not None:
                from embry0.workflows.qa.cleanup import cleanup_cancelled_qa_job

                await cleanup_cancelled_qa_job(runtime, self._sandbox.destroy, job_id=job_id)
        # A cancelled graph never reaches its own cleanup, so the job's
        # volumes are released here (#274) — after the sandboxes, which mount them.
        await self._cleanup_job_volumes(job_id)

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
            await self._finish_credential_uses(job_id, error_code="cancelled")
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

    async def resume(self, issue_id: str | None, job_id: str, answers: Any, *, after_restart: bool = False) -> None:
        """Resume a paused pipeline with user answers using Command(resume=).

        ``issue_id`` is None for issue-less API-submitted jobs; issue-status
        updates no-op in that case (IssuesRepository.update skips missing rows).

        ``after_restart`` (#307) continues a job a previous orchestrator left
        ``running`` from its last checkpoint instead: there is no answer, the
        job and its issue already read as running, and a sandbox the proxies
        will not take back fails the job rather than pausing it.

        RAV-1406: like ``_run_workflow``, the terminal Linear outcome comment
        posts in the ``finally`` — resumed jobs used to finish silently
        because only the fresh path had the hook (its gate no-ops when the
        job merely paused again).
        """
        from langgraph.types import Command

        final_state: dict[str, Any] | None = None
        # See _run_workflow: a mid-run raise (overrun_mode=hard) never yields
        # a final_state, and the failure path needs the sandbox handle and the
        # cost from somewhere (#246).
        streamed_state: dict[str, Any] = {}
        from embry0.execution.sandbox_manager import SandboxReenrollError

        prior_job_status: str | None = None
        prior_issue_status: str | None = None
        slot: RunSlot | None = None
        try:
            if not after_restart:
                # Read before anything changes: a resume whose sandbox the proxies will
                # not take back puts the job and issue back exactly as they were (#476).
                # Best-effort: only that rare path uses it, so it must not change any other.
                try:
                    prior_job_status = ((await self._jobs.get(job_id)) or {}).get("status")
                    if issue_id:
                        prior_issue_status = ((await self._issues.get(issue_id)) or {}).get("status")
                except Exception:
                    logger.warning("resume_prior_status_read_failed", job_id=job_id, exc_info=True)
                await self._jobs.update(job_id, status="running")
                await self._issues.update(issue_id, status="triaging")
            # #583: a paused job held no slot. Its resume waits for one after
            # the job reads `running`, so a restart in between fails it as
            # orphaned rather than leaving an answered job awaiting input.
            try:
                slot = await self._acquire_run_slot(job_id, priority=True)
            except asyncio.CancelledError:
                if self.draining and not after_restart:
                    # #307: a drain cancelled the wait. Nothing ran, so the job
                    # goes back to waiting for its answer rather than resuming,
                    # without that answer, after the restart.
                    await self._jobs.update(job_id, status=prior_job_status or "awaiting_input")
                    await self._issues.update(issue_id, status=prior_issue_status or "awaiting_input")
                    logger.warning("resume_dropped_for_shutdown", job_id=job_id, msg="Send the answer again.")
                raise
            started = getattr(self, "_started_jobs", None)
            if started is not None:
                started.add(job_id)

            final_state, interrupted, interrupt_value = await self._execute_workflow_stream(
                None if after_restart else Command(resume=answers),
                issue_id,
                job_id,
                state_sink=streamed_state,
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

        except SandboxReenrollError as exc:
            if after_restart:
                # #307: there is no pause to go back to; fail like any other error.
                await self._fail_resume(issue_id, job_id, exc, final_state, streamed_state)
                return
            # The sandbox exists but could not be re-enrolled (a proxy restarting).
            # Nothing ran and the checkpoint still holds the pause, so the job keeps
            # its sandbox and can be resumed again.
            logger.warning("resume_reenroll_failed_job_left_paused", issue_id=issue_id, job_id=job_id, error=str(exc))
            await self._jobs.update(job_id, status=prior_job_status or "paused")
            await self._issues.update(issue_id, status=prior_issue_status or "open")
        except Exception as exc:
            await self._fail_resume(issue_id, job_id, exc, final_state, streamed_state)
        finally:
            started = getattr(self, "_started_jobs", None)
            if started is not None:
                started.discard(job_id)
            if slot is not None:
                slot.release()
            await self._post_tracker_outcome(issue_id, job_id)

    async def _fail_resume(
        self,
        issue_id: str | None,
        job_id: str,
        exc: Exception,
        final_state: dict[str, Any] | None,
        streamed_state: dict[str, Any],
    ) -> None:
        """A resume raised: fail the job, open its fallback PR, free its sandbox."""
        logger.error("pipeline_resume_failed", issue_id=issue_id, job_id=job_id, error=str(exc))
        from embry0.safety.error_codes import classify_failure

        code = classify_failure(exc=exc, stage=(final_state or streamed_state or {}).get("current_stage"))
        last_state = final_state or streamed_state or None
        # #349: a resumed run that raised after pushing its branch still
        # gets its PR; the carried facts (#482) name the branch.
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
            budget_overrun_usd=_executor_run._overrun_of(exc, last_state),
            **({"pr_url": pr_url} if pr_url else {}),
        )
        await self._finish_credential_uses(job_id, error_code=code.value)
        if pr_url:
            await self._record_pr_opened(issue_id, job_id, pr_url)
        await self._issues.update(issue_id, status="open")
        # A resumed job owns a sandbox too, and this path never destroyed
        # it — invisible until `hard` mode started raising mid-run.
        await self._cleanup_sandbox(last_state, job_id)

    def resume_after_restart(self, job: Mapping[str, Any]) -> asyncio.Task[Any]:
        """Continue a job the previous orchestrator left ``running`` from its
        last checkpoint (#307). ``job`` is a row from boot recovery."""
        job_id = str(job["job_id"])
        issue_id = job.get("issue_id")

        async def run() -> None:
            await self._announce_recovery(
                job_id, "Resumed: embry0 restarted during this job, which goes on from its last checkpoint."
            )
            await self.resume(issue_id, job_id, None, after_restart=True)

        return self._track_task(run(), kind="restart_resume", job_id=job_id, issue_id=issue_id, run_slot=False)

    def relaunch(self, job: Mapping[str, Any]) -> asyncio.Task[Any]:
        """Start again, from the beginning, a job the previous orchestrator left
        ``pending`` or ``running`` before its graph began (#307), from the
        launch record on its row. ``job`` is a row from boot recovery."""
        job_id = str(job["job_id"])
        return self._track_task(
            self._relaunch(job_id, dict(job.get("launch") or {})),
            kind="restart_relaunch",
            job_id=job_id,
            issue_id=job.get("issue_id"),
        )

    async def _relaunch(self, job_id: str, launch: dict[str, Any]) -> None:
        await self._announce_recovery(job_id, "Restarted: embry0 restarted before this job got going; it starts now.")
        runner = launch.get("runner")
        if runner == "qa":
            await self._run_qa_workflow(
                job_id,
                repo=str(launch.get("repo") or ""),
                branch=str(launch.get("branch") or ""),
                qa_overrides=dict(launch.get("qa_overrides") or {}),
                agent_models=launch.get("agent_models"),
            )
            return
        if runner == "onboard":
            await self._run_onboard_workflow(
                job_id,
                repo=str(launch.get("repo") or ""),
                branch=str(launch.get("branch") or ""),
                skip_smoke=bool(launch.get("skip_smoke")),
                agent_models=launch.get("agent_models"),
            )
            return
        issue_id = launch.get("issue_id")
        issue = (await self._issues.get(issue_id)) if issue_id else launch.get("issue")
        if not issue:
            from embry0.safety.error_codes import ErrorCode

            await self._jobs.fail_jobs(
                [job_id],
                ErrorCode.ORPHANED.value,
                "Orchestrator restarted and the job's issue no longer exists; nothing to start.",
            )
            return
        await self._run_workflow(
            issue_id, job_id, issue, additional_context=str(launch.get("additional_context") or "")
        )

    async def _announce_recovery(self, job_id: str, message: str) -> None:
        logger.info("job_recovered_after_restart", job_id=job_id)
        try:
            await self._broadcast_event(job_id, events.system(message=message))
        except Exception:
            logger.warning("job_recovery_event_failed", job_id=job_id, exc_info=True)

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
        self.schedule_resume(issue_id, job_id, None, kind="resume_via_inbound")
        logger.info("resume_for_issue_dispatched", issue_id=issue_id, job_id=job_id)

    # ------------------------------------------------------------------
    # RAV-1235: the workflow-run and outcome internals live in sibling
    # modules (functions taking ``self: IssueExecutor``); binding them in
    # the class body makes them ordinary methods — call sites, subclassing,
    # and ``patch.object(IssueExecutor, ...)`` all behave exactly as before.
    # ------------------------------------------------------------------
    _process_stream = _executor_run._process_stream
    _refuse_if_over_budget = _executor_run._refuse_if_over_budget
    _run_onboard_workflow = _executor_run._run_onboard_workflow
    _cleanup_sandbox = _executor_run._cleanup_sandbox
    _cleanup_job_volumes = _executor_run._cleanup_job_volumes
    _load_merged_env_vars = _executor_run._load_merged_env_vars
    _execute_workflow_stream = _executor_run._execute_workflow_stream
    _forge_for = _executor_outcomes._forge_for
    _handle_interrupt = _executor_run._handle_interrupt
    _run_workflow = _executor_run._run_workflow
    _skip_as_duplicate = _executor_run._skip_as_duplicate
    _run_qa_workflow = _executor_run._run_qa_workflow
    _post_tracker_outcome = _executor_outcomes._post_tracker_outcome
    _post_tracker_questions = _executor_outcomes._post_tracker_questions
    _persist_deliverables = _executor_outcomes._persist_deliverables
    _handle_workflow_result = _executor_outcomes._handle_workflow_result
    _open_fallback_pr = _executor_outcomes._open_fallback_pr
    _record_pr_opened = _executor_outcomes._record_pr_opened
    _handle_split = _executor_outcomes._handle_split
    _broadcast_event = _executor_outcomes._broadcast_event
    _announce_finished = _executor_outcomes._announce_finished
    _handle_needs_info = _executor_outcomes._handle_needs_info
    _finish_credential_uses = _executor_outcomes._finish_credential_uses
