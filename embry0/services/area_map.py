"""Map areas (spec 2026-10-25, 2026-11-10): an agent job that writes a repository's areas document.

The job runs the ``map-areas`` template: the area-mapper reads the
repository and reports the document, and the output step reads it into the
job's state (``embry0.workflows.issue_to_pr.areas_report``); the executor
stores it on the map's proposal for a person to apply. Nothing is written to
the repository. Started from the dashboard with the model and key the person
chose (run with).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from embry0.audit.helpers import emit_audit
from embry0.safety.error_codes import error_code_for_exception
from embry0.services.run_with import RunWith, apply_run_with
from embry0.workflows.issue_to_pr.areas_report import TEMPLATE


def map_task(repo: str, *, existing: str | None, logql: str | None) -> str:
    """The job's task. Every map of a repository starts with the same line, so its branch name is stable."""
    lines = [f"Map the areas of {repo}: the parts its work is about, for embry0's areas document.", ""]
    if existing and existing.strip():
        lines += ["embry0 keeps this areas document for the repository. Start from it:", "", "```yaml"]
        lines += [existing.strip(), "```"]
    else:
        lines.append("embry0 keeps no areas document for this repository yet.")
    if logql and logql.strip():
        # The selector names the containers its logs come from: what an area's `logs` strings match.
        lines += [
            "",
            "embry0's watcher reads this repository's production logs with this LogQL:",
            "",
            "```",
            logql.strip(),
            "```",
        ]
    return "\n".join(lines)


async def start_map_areas_job(
    repo: str,
    *,
    jobs: Any,
    executor: Any,
    templates: Any,
    task: str,
    budget_usd: float,
    dispatched_by: str,
    proposal_id: str,
    attach: Callable[[str], Awaitable[None]],
    run_with: RunWith | None = None,
    db: Any = None,
    audit_log_path: Any = None,
) -> str:
    """Start one map-areas job delivering to ``proposal_id`` and return its id; :class:`LookupError` while no
    template is named :data:`TEMPLATE`.

    ``attach`` records the new job on the proposal before the job is
    submitted: the proposal is finished only by its own job, so a job that
    ends at once still finds itself named there. A job that cannot be
    submitted is marked failed before the error is raised again, so it holds
    nothing.
    """
    rows = await templates.list_all() if templates is not None else []
    if not any(row.get("name") == TEMPLATE for row in rows):
        raise LookupError(f"no pipeline template named {TEMPLATE!r}")
    if executor is None:
        raise RuntimeError("executor not initialized")
    job_id: str = await jobs.create(
        repo=repo,
        task=task,
        context={"type": "git", "repo": repo},
        pipeline_template=TEMPLATE,
        pipeline_config=apply_run_with(
            # A person asked, but nobody watches the run: its cap is a hard stop. Its document waits on the
            # proposal for a person to apply (spec 2026-11-10); no pull request is opened.
            {"max_budget_usd": budget_usd, "budget_overrun_mode": "hard", "areas_proposal_id": proposal_id},
            run_with,
        ),
        trace_id=f"trc-{uuid.uuid4().hex[:12]}",
        dispatched_by=dispatched_by,
    )
    await emit_audit(
        db,
        "job_created",
        actor=dispatched_by,
        details={"job_id": job_id, "repo": repo, "pipeline_template": TEMPLATE},
        audit_log_path=audit_log_path,
    )
    try:
        await attach(job_id)
        executor.submit_job(job_id, repo=repo, title=task.splitlines()[0], body=task, kind="map_areas")
    except Exception as exc:
        # A job that never started must not stay queued: it would hold the repository against the next map.
        await jobs.fail_jobs(
            [job_id], error_code_for_exception(exc).value, f"the map's job could not start: {exc}"[:500]
        )
        raise
    return job_id
