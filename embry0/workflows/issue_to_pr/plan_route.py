"""plan_route + finalize_output nodes (RAV-601/603).

``plan_route_node`` runs right after triage: it resolves the pipeline
template, validates it, linearizes it into the ``route_plan`` snapshot on
state, and mirrors ``job_kind``/``pipeline_template`` onto the job row.
``finalize_output_node`` is the terminal ``output`` template step and the
deliverable collection point (RAV-603): the last agent output becomes a
``report`` deliverable (mirrored into ``result_summary`` for list views and
completion comments), and files the agent left in /workspace/deliverables/
are uploaded to MinIO as ``artifact`` deliverables. The executor persists
``state["deliverables"]`` as rows at job completion.
"""

from __future__ import annotations

import mimetypes
import posixpath
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

# RAV-603 artifact collection. Files the agent placed under this sandbox dir
# become artifact deliverables. Separate bucket from qa-artifacts: that one
# carries a lifecycle expiry and an attempt-numbered key layout; deliverables
# are the job's product and keep indefinitely under {job_id}/{relpath}.
DELIVERABLES_DIR = "/workspace/deliverables"
DELIVERABLES_BUCKET = "job-deliverables"
MAX_ARTIFACT_COUNT = 20
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # per file


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


async def _collect_artifacts(state: dict[str, Any], config: RunnableConfig) -> list[dict[str, Any]]:
    """Upload /workspace/deliverables/* files to MinIO as artifact deliverables.

    Best-effort by design: no docker/minio/container (tests, minimal
    deploys, sandbox already gone) or any per-file failure returns/skips
    with a WARNING — artifact collection must never fail the job. Caps:
    :data:`MAX_ARTIFACT_COUNT` files, :data:`MAX_ARTIFACT_BYTES` each;
    files beyond the caps are logged and skipped, not silently dropped.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    minio = configurable.get("qa_minio")
    container = state.get("sandbox_container_id")
    job_id = str(state.get("job_id") or "")
    if docker is None or minio is None or not container or not job_id:
        return []

    list_cmd = [
        "bash",
        "-c",
        f"test -d {DELIVERABLES_DIR} && find {DELIVERABLES_DIR} -type f -printf '%s\\t%p\\n' || true",
    ]
    try:
        raw = await docker.run_cmd(docker.build_exec_cmd(container, list_cmd), timeout=30)
    except Exception:
        logger.warning("deliverables_list_failed", job_id=job_id, exc_info=True)
        return []

    entries: list[tuple[int, str]] = []
    for line in (raw or "").splitlines():
        size_str, _, path = line.partition("\t")
        if not path:
            continue
        try:
            entries.append((int(size_str), path))
        except ValueError:
            continue
    if not entries:
        return []

    try:
        await minio.ensure_bucket(DELIVERABLES_BUCKET)
    except Exception:
        logger.warning("deliverables_bucket_failed", job_id=job_id, exc_info=True)
        return []

    if len(entries) > MAX_ARTIFACT_COUNT:
        logger.warning(
            "deliverables_count_capped",
            job_id=job_id,
            found=len(entries),
            kept=MAX_ARTIFACT_COUNT,
        )
    artifacts: list[dict[str, Any]] = []
    for size, path in sorted(entries, key=lambda e: e[1])[:MAX_ARTIFACT_COUNT]:
        rel = posixpath.relpath(path, DELIVERABLES_DIR)
        if size > MAX_ARTIFACT_BYTES:
            logger.warning("deliverable_too_large", job_id=job_id, path=rel, size=size)
            continue
        try:
            data = await docker.copy_bytes_from(container, path)
            media_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
            key = f"{job_id}/{rel}"
            await minio.put_object(DELIVERABLES_BUCKET, key, data, content_type=media_type)
        except Exception:
            logger.warning("deliverable_upload_failed", job_id=job_id, path=rel, exc_info=True)
            continue
        artifacts.append(
            {
                "type": "artifact",
                "title": rel,
                "storage_bucket": DELIVERABLES_BUCKET,
                "storage_key": key,
                "media_type": media_type,
                "size_bytes": len(data),
            }
        )
    return artifacts


async def finalize_output_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Terminal ``output`` step: collect the job's deliverables (RAV-603).

    - The last non-error agent output becomes a ``report`` deliverable and
      is mirrored (truncated) into ``result_summary`` — list views and the
      Linear/GitHub completion comments keep reading the denormalized copy.
    - Files under /workspace/deliverables/ become ``artifact`` deliverables
      in MinIO (best-effort; see :func:`_collect_artifacts`).

    The ``pr`` deliverable is NOT built here: code-kind jobs usually end
    without an ``output`` step, so the executor synthesizes it from
    ``pr_url`` at completion for every job uniformly.
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "finalize_output"})

    summary = ""
    for entry in reversed(state.get("agent_outputs") or []):
        if not entry.get("is_error") and (entry.get("output") or "").strip():
            summary = str(entry["output"]).strip()[:RESULT_SUMMARY_MAX_CHARS]
            break

    deliverables: list[dict[str, Any]] = []
    if summary:
        deliverables.append(
            {
                "type": "report",
                "title": f"{state.get('job_kind') or 'job'} report",
                "content": summary,
            }
        )
    deliverables.extend(await _collect_artifacts(state, config))

    writer({"type": "node_completed", "node": "finalize_output", "deliverables": len(deliverables)})
    return {
        "result_summary": summary,
        "deliverables": deliverables,
        "current_stage": "output_finalized",
    }
