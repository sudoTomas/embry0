"""Issue executor — creates jobs from issues and runs the workflow pipeline."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from legion.api.events.bus import EventBus

from legion.audit.helpers import emit_audit
from legion.orchestration.checkpoint import checkpointer_context
from legion.storage.database import DatabasePool
from legion.storage.repositories.issues import IssuesRepository
from legion.storage.repositories.jobs import JobsRepository
from legion.storage.repositories.traces import TracesRepository
from legion.workflows.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)


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
    ) -> None:
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
        self._background_tasks: set[asyncio.Task] = set()
        self._tasks_by_job: dict[str, asyncio.Task] = {}

    def _build_graph_config(self, job_id: str) -> dict[str, Any]:
        """Build the graph config dict for LangGraph execution."""
        return {
            "configurable": {
                "thread_id": job_id,
                "agent_runner": self._agent_runner,
                "sandbox_manager": self._sandbox,
                "proxy_manager": self._proxy,
                "docker": getattr(self._sandbox, "_docker", None) if self._sandbox else None,
                "issues_repo": self._issues,
                "inputs_repo": self._inputs,
                "db": self._db,
            }
        }

    async def _process_stream(
        self,
        graph: Any,
        input_value: Any,
        graph_config: dict[str, Any],
        issue_id: str,
        job_id: str,
    ) -> tuple[dict[str, Any] | None, bool, Any]:
        """Stream graph execution events, detect interrupts.

        Returns (final_state, interrupted, interrupt_value).
        """
        final_state: dict[str, Any] | None = None

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

    async def execute(self, issue_id: str) -> str:
        """Create a job for the issue and execute the workflow in the background.

        Returns the job_id. The workflow runs asynchronously.
        """
        import uuid

        issue = await self._issues.get(issue_id)
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")

        # Concurrency guard: reject if a job is already active for this issue
        active_jobs, _ = await self._jobs.list(issue_id=issue_id, limit=10, offset=0)
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
            self._run_workflow(issue_id, job_id, issue),
            kind="workflow_execute",
            job_id=job_id,
            issue_id=issue_id,
        )

        return job_id

    def _track_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str,
        job_id: str,
        issue_id: str | None = None,
    ) -> asyncio.Task:
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
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        self._tasks_by_job[job_id] = task

        def _on_done(t: asyncio.Task) -> None:
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
        from legion.orchestration.checkpoint import purge_thread

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

    async def _run_workflow(self, issue_id: str, job_id: str, issue: dict[str, Any]) -> None:
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

            workflow = self._registry.get("issue-to-pr")
            if not workflow:
                raise RuntimeError("Workflow 'issue-to-pr' not registered")

            initial_state = {
                "job_id": job_id,
                "issue_id": issue_id,
                "repo": issue.get("repo") or "",
                "task": issue["title"] + ("\n\n" + issue["body"] if issue.get("body") else ""),
                "issue_number": issue.get("github_number"),
                "current_stage": "init",
                "agent_outputs": [],
                "errors": [],
                "retry_count": 0,
                "total_cost_usd": 0.0,
                "budget_overrun_usd": 0.0,
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

            graph_config = self._build_graph_config(job_id)

            async with checkpointer_context(self._database_url) as saver:
                graph = workflow.compile(config={"checkpointer": saver})

                final_state, interrupted, interrupt_value = await self._process_stream(
                    graph,
                    initial_state,
                    graph_config,
                    issue_id,
                    job_id,
                )

                if interrupted:
                    if interrupt_value:
                        reason = interrupt_value.get("reason", "")
                        if reason == "max_retries":
                            await self._jobs.update(job_id, status="paused")
                            await self._issues.update(issue_id, status="paused")
                            logger.info("job_paused", job_id=job_id, reason=reason)
                        else:
                            await self._handle_needs_info(issue_id, job_id, interrupt_value)
                    else:
                        await self._jobs.update(job_id, status="awaiting_input")
                        await self._issues.update(issue_id, status="awaiting_input")
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
            from legion.orchestration.state import TriageParseError
            from legion.safety.error_codes import ErrorCode

            if isinstance(exc, TriageParseError):
                code = ErrorCode.TRIAGE_MALFORMED
            elif isinstance(exc, RuntimeError) and "not registered" in str(exc):
                code = ErrorCode.WORKFLOW_UNKNOWN
            else:
                code = ErrorCode.UNKNOWN
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

            # Cleanup sandbox on error
            await self._cleanup_sandbox(final_state, job_id)
        finally:
            if trace_id_bound:
                try:
                    cv.unbind_contextvars("trace_id")
                except Exception:
                    logger.warning("trace_id_unbind_failed", job_id=job_id, exc_info=True)

    async def _handle_workflow_result(self, issue_id: str, job_id: str, result: dict[str, Any]) -> None:
        """Process the workflow result and update issue/job status."""
        triage_decision = result.get("pipeline_config", {})
        action = triage_decision.get("action", "proceed")
        current_stage = result.get("current_stage", "")

        logger.info(
            "workflow_result",
            issue_id=issue_id,
            job_id=job_id,
            action=action,
            stage=current_stage,
        )

        if current_stage == "awaiting_input":
            triage_decision = result.get("pipeline_config", {})
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
            failed_stages = {"abandoned", "triage_failed", "developer_retry"}
            has_errors = bool(result.get("errors"))
            final_status = "failed" if current_stage in failed_stages or has_errors else "completed"
            # Classify the failure: graph nodes may have set a specific error_code
            # in state (e.g. ERR_TRIAGE_MALFORMED); otherwise bucket as UNKNOWN.
            error_code: str | None = None
            if final_status == "failed":
                from legion.safety.error_codes import ErrorCode

                error_code = result.get("error_code") or ErrorCode.UNKNOWN.value
            await self._jobs.update(
                job_id,
                status=final_status,
                pr_url=result.get("pr_url"),
                error_message=result.get("result_summary") if final_status == "failed" else None,
                error_code=error_code,
                total_cost_usd=result.get("total_cost_usd", 0.0),
                finished_at=datetime.now(UTC),
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

    async def _handle_split(self, issue_id: str, job_id: str, decision: dict[str, Any]) -> None:
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

    async def _broadcast_event(self, job_id: str, event: dict) -> None:
        """Forward event to WebSocket subscribers and persist to job_logs."""
        # Add timestamp if missing (events from StreamWriter don't include one)
        if "timestamp" not in event:
            from datetime import UTC, datetime

            event = {**event, "timestamp": datetime.now(UTC).isoformat()}

        # Persist to database and capture the assigned sequence id.
        # Stamp it onto the event dict so WS subscribers can use it as a replay
        # cursor (see legion/api/ws/streaming.py).
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

    async def _handle_needs_info(self, issue_id: str, job_id: str, decision: dict[str, Any]) -> None:
        """Create input records, dispatch notifications, and pause for blocking questions.

        Atomicity: the INSERTs for all input rows and the status UPDATEs for the
        job + issue are wrapped in a single DB transaction. Either all persist
        or none do — no orphan input rows if the orchestrator crashes mid-flow.

        Audit events and notification dispatch happen AFTER the commit, because:
        - Audit is additive (can emit a few extra records; no correctness impact).
        - Notifications are best-effort (a missed Telegram ping is not a data issue).
        """
        import uuid

        from legion.notifications.dispatcher import dispatch_questions

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
                blocking_pairs = [
                    (eq["input_id"], eq["question"]) for eq in enriched_questions if eq["importance"] == "blocking"
                ]
                await dispatch_questions(
                    inputs_repo=self._inputs,
                    issue=issue,
                    job_id=job_id,
                    questions=blocking_pairs,
                    asking_node=asking_node,
                    config=self._config,
                )
        else:
            # All auto-answerable — re-run triage with answers.
            await self._jobs.update(job_id, status="completed")
            await self._issues.update(issue_id, status="triaging")
            try:
                new_job_id = await self.execute(issue_id)
                logger.info("auto_answer_retriage", issue_id=issue_id, new_job_id=new_job_id)
            except Exception:
                logger.warning("auto_answer_retriage_failed", issue_id=issue_id, exc_info=True)
                await self._issues.update(issue_id, status="open")

        logger.info("needs_info_handled", issue_id=issue_id, blocking=has_blocking)

    async def resume(self, issue_id: str, job_id: str, answers: Any) -> None:
        """Resume a paused pipeline with user answers using Command(resume=)."""
        from langgraph.types import Command

        try:
            await self._jobs.update(job_id, status="running")
            await self._issues.update(issue_id, status="triaging")

            workflow = self._registry.get("issue-to-pr")
            if not workflow:
                raise RuntimeError("Workflow 'issue-to-pr' not registered")

            graph_config = self._build_graph_config(job_id)

            async with checkpointer_context(self._database_url) as saver:
                graph = workflow.compile(config={"checkpointer": saver})

                final_state, interrupted, interrupt_value = await self._process_stream(
                    graph,
                    Command(resume=answers),
                    graph_config,
                    issue_id,
                    job_id,
                )

                if interrupted:
                    if interrupt_value:
                        reason = interrupt_value.get("reason", "")
                        if reason == "max_retries":
                            await self._jobs.update(job_id, status="paused")
                            await self._issues.update(issue_id, status="paused")
                            logger.info("job_paused", job_id=job_id, reason=reason)
                        else:
                            await self._handle_needs_info(issue_id, job_id, interrupt_value)
                    else:
                        await self._jobs.update(job_id, status="awaiting_input")
                        await self._issues.update(issue_id, status="awaiting_input")
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
            from legion.orchestration.state import TriageParseError
            from legion.safety.error_codes import ErrorCode

            if isinstance(exc, TriageParseError):
                code = ErrorCode.TRIAGE_MALFORMED
            elif isinstance(exc, RuntimeError) and "not registered" in str(exc):
                code = ErrorCode.WORKFLOW_UNKNOWN
            else:
                code = ErrorCode.UNKNOWN
            await self._jobs.update(
                job_id,
                status="failed",
                error_message=str(exc),
                error_code=code.value,
            )
            await self._issues.update(issue_id, status="open")
