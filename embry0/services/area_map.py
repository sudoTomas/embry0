"""Map areas (spec 2026-10-25): an agent job that writes a repository's ``.embry0/areas.yml``.

The job runs the ``map-areas`` template: the area-mapper reads the
repository and reports the file, and the output step commits it on the
job's branch, so the executor opens the pull request a person reviews
(``embry0.workflows.issue_to_pr.areas_delivery``). Started from the
dashboard with the model and key the person chose (run with).
"""

from __future__ import annotations

import uuid
from typing import Any

from embry0.audit.helpers import emit_audit
from embry0.services.areas import AREAS_PATH
from embry0.services.run_with import RunWith, apply_run_with
from embry0.workflows.issue_to_pr.areas_delivery import TEMPLATE


def map_task(repo: str, *, existing: str | None, logql: str | None) -> str:
    """The job's task. Every map of a repository starts with the same line, so its branch name is stable."""
    lines = [f"Map the areas of {repo}: the parts its work is about, for embry0's {AREAS_PATH}.", ""]
    if existing and existing.strip():
        lines += [f"{AREAS_PATH} exists on the default branch. Start from it:", "", "```yaml", existing.strip(), "```"]
    else:
        lines.append(f"{AREAS_PATH} does not exist yet.")
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
    run_with: RunWith | None = None,
    db: Any = None,
    audit_log_path: Any = None,
) -> str:
    """Start one map-areas job and return its id; :class:`LookupError` while no template is named :data:`TEMPLATE`."""
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
            # A person asked, but nobody watches the run: its cap is a hard stop.
            {"max_budget_usd": budget_usd, "budget_overrun_mode": "hard", "open_pr": True},
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
    executor.submit_job(job_id, repo=repo, title=task.splitlines()[0], body=task, kind="map_areas")
    return job_id
