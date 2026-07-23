"""plan_route + finalize_output nodes (RAV-601).

``plan_route_node`` runs right after triage: it resolves the pipeline
template, validates it, linearizes it into the ``route_plan`` snapshot on
state, and mirrors ``job_kind``/``pipeline_template`` onto the job row.
``finalize_output_node`` is the terminal ``output`` template step: it
surfaces the last agent output as ``result_summary`` (the interim non-code
deliverable until RAV-603 lands a real deliverable model).
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.route_plan import LEGACY_ROUTE_PLAN, build_route_plan

logger = structlog.get_logger(__name__)

# plan_route treats these pipeline_template values as "no explicit template":
# "standard"/"routine" are triage's pre-RAV-601 free-form vocabulary.
_LEGACY_TEMPLATE_VALUES = {"", "standard", "routine"}

RESULT_SUMMARY_MAX_CHARS = 10_000


class TemplateInvalidError(RuntimeError):
    """An explicitly requested template failed validation and no fallback exists."""


async def _find_template(
    templates_repo: Any, *, name: str | None = None, kind: str | None = None
) -> dict[str, Any] | None:
    """Match by name or default_for_kind, then fetch the FULL row.

    list_all() is a summary projection without graph_definition — the
    follow-up get() pulls the graph the linearizer needs.
    """
    rows = await templates_repo.list_all()
    for row in rows:
        if (name is not None and row.get("name") == name) or (kind is not None and row.get("default_for_kind") == kind):
            full: dict[str, Any] | None = await templates_repo.get(row["id"])
            return full or dict(row)
    return None


async def plan_route_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Resolve template → route_plan snapshot. Resolution order:

    1. triage's explicit ``pipeline_template`` name (legacy values skipped);
    2. the template whose ``default_for_kind`` matches the job_kind;
    3. the legacy synthesized plan (bit-for-bit today's dev→review→qa chain).

    An invalid template falls through to the next tier — except an
    *explicitly requested* invalid template with no kind default, which
    fails loudly (silently-wrong routing would be worse).
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "plan_route"})

    decision = state.get("triage_decision") or {}
    job_kind = str(decision.get("job_kind") or "code")
    requested = str(decision.get("pipeline_template") or "")
    explicit = requested not in _LEGACY_TEMPLATE_VALUES

    templates_repo = config["configurable"].get("pipeline_templates_repo")
    plan: list[dict[str, Any]] | None = None
    resolved_name: str | None = None
    explicit_invalid = False

    if templates_repo is not None:
        candidates: list[tuple[str, dict[str, Any] | None]] = []
        if explicit:
            candidates.append(("explicit", await _find_template(templates_repo, name=requested)))
        candidates.append(("kind_default", await _find_template(templates_repo, kind=job_kind)))

        for tier, row in candidates:
            if row is None:
                if tier == "explicit":
                    logger.warning("plan_route_template_not_found", requested=requested, job_kind=job_kind)
                continue
            graph = row.get("graph_definition") or {}
            problems = validate_graph_definition(row["name"], graph)
            if problems:
                logger.warning("plan_route_template_invalid", template=row["name"], problems=problems)
                if tier == "explicit":
                    explicit_invalid = True
                continue
            plan = build_route_plan(graph)
            resolved_name = row["name"]
            break

    if plan is None:
        if explicit_invalid:
            raise TemplateInvalidError(
                f"Requested pipeline template {requested!r} is invalid and no default "
                f"template exists for job_kind {job_kind!r}"
            )
        # Legacy fallback — today's behavior, also covers pre-RAV-601 jobs.
        plan = [dict(step) for step in LEGACY_ROUTE_PLAN]
        resolved_name = None

    # Mirror onto the job row for dashboard filtering — best-effort.
    jobs_repo = config["configurable"].get("jobs_repo")
    if jobs_repo is not None:
        try:
            await jobs_repo.update(
                state.get("job_id", ""),
                job_kind=job_kind,
                **({"pipeline_template": resolved_name} if resolved_name else {}),
            )
        except Exception:
            logger.warning("plan_route_job_row_update_failed", job_id=state.get("job_id"), exc_info=True)

    logger.info(
        "route_planned",
        job_id=state.get("job_id"),
        job_kind=job_kind,
        template=resolved_name or "(legacy fallback)",
        steps=[s["agent_type"] for s in plan],
    )
    writer(
        {
            "type": "node_completed",
            "node": "plan_route",
            "job_kind": job_kind,
            "template": resolved_name or "legacy",
        }
    )
    return {
        "job_kind": job_kind,
        "route_plan": plan,
        "route_cursor": 0,
        "route_template_name": resolved_name,
    }


async def finalize_output_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Terminal ``output`` step: surface the last agent output as the result.

    Interim placement pre-RAV-603: no new tables — the truncated text lands
    in ``result_summary`` (already declared on JobState) and flows to the
    completion comment when the job has no PR.
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "finalize_output"})

    summary = ""
    for entry in reversed(state.get("agent_outputs") or []):
        if not entry.get("is_error") and (entry.get("output") or "").strip():
            summary = str(entry["output"]).strip()[:RESULT_SUMMARY_MAX_CHARS]
            break

    writer({"type": "node_completed", "node": "finalize_output"})
    return {"result_summary": summary, "current_stage": "output_finalized"}
