"""Issue executor — creates jobs from issues and runs the workflow pipeline."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from legion.audit.logger import emit_audit_event
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
    ) -> None:
        self._issues = issues_repo
        self._jobs = jobs_repo
        self._traces = traces_repo
        self._registry = workflow_registry
        self._database_url = database_url
        self._audit_log_path = audit_log_path
        self._background_tasks: set[asyncio.Task] = set()

    async def execute(self, issue_id: str) -> str:
        """Create a job for the issue and execute the workflow in the background.

        Returns the job_id. The workflow runs asynchronously.
        """
        issue = await self._issues.get(issue_id)
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")

        task_text = issue["title"]
        if issue.get("body"):
            task_text += "\n\n" + issue["body"]

        job_id = await self._jobs.create(
            repo=issue.get("repo") or "unknown/unknown",
            task=task_text,
            issue_number=issue.get("github_number"),
            issue_id=issue_id,
        )

        emit_audit_event(
            "issue.job_created",
            actor="system",
            details={"job_id": job_id, "issue_id": issue_id},
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        logger.info("issue_job_created", issue_id=issue_id, job_id=job_id)

        task = asyncio.create_task(self._run_workflow(issue_id, job_id, issue))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return job_id

    async def _run_workflow(
        self, issue_id: str, job_id: str, issue: dict[str, Any]
    ) -> None:
        """Execute the issue-to-pr workflow and handle the outcome."""
        from datetime import UTC, datetime

        try:
            await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))

            workflow = self._registry.get("issue-to-pr")
            if not workflow:
                raise RuntimeError("Workflow 'issue-to-pr' not registered")

            initial_state = {
                "job_id": job_id,
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

            graph = workflow.compile()
            result = await graph.ainvoke(initial_state)

            await self._handle_workflow_result(issue_id, job_id, result)

        except Exception as exc:
            logger.error(
                "workflow_execution_failed",
                issue_id=issue_id,
                job_id=job_id,
                error=str(exc),
            )
            await self._jobs.update(job_id, status="failed", error_message=str(exc))
            await self._issues.update(issue_id, status="open")
            emit_audit_event(
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

    async def _handle_workflow_result(
        self, issue_id: str, job_id: str, result: dict[str, Any]
    ) -> None:
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

        if action == "split":
            await self._handle_split(issue_id, job_id, triage_decision)
        elif action == "needs_info":
            await self._handle_needs_info(issue_id, job_id, triage_decision)
        else:
            # action == "proceed" — workflow ran through the full pipeline
            from datetime import UTC, datetime

            final_status = "completed" if current_stage == "completed" else "failed"
            await self._jobs.update(
                job_id,
                status=final_status,
                pr_url=result.get("pr_url"),
                error_message=result.get("result_summary") if final_status == "failed" else None,
                total_cost_usd=result.get("total_cost_usd", 0.0),
                finished_at=datetime.now(UTC),
            )
            issue_status = "closed" if final_status == "completed" else "open"
            await self._issues.update(issue_id, status=issue_status)
            emit_audit_event(
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

    async def _handle_split(
        self, issue_id: str, job_id: str, decision: dict[str, Any]
    ) -> None:
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

        emit_audit_event(
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

        emit_audit_event(
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

    async def _handle_needs_info(
        self, issue_id: str, job_id: str, decision: dict[str, Any]
    ) -> None:
        """Handle needs_info — set issue back to open and log the questions."""
        await self._jobs.update(job_id, status="completed")
        await self._issues.update(issue_id, status="open")

        emit_audit_event(
            "issue.triaged",
            actor="triage_agent",
            details={
                "action": "needs_info",
                "confidence": decision.get("confidence"),
                "questions": decision.get("questions", []),
                "reasoning": decision.get("reasoning", ""),
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        logger.info(
            "issue_needs_info",
            issue_id=issue_id,
            questions=decision.get("questions", []),
        )
