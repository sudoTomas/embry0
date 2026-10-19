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
import re
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.execution import events
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.route_plan import (
    GENERIC_AGENT_TYPES,
    LEGACY_ROUTE_PLAN,
    build_route_plan,
    specialist_job_kind,
    triage_template_config,
)
from embry0.workflows.issue_to_pr.specialist_agents import SPECIALIST_TEMPLATE_NAMES

logger = structlog.get_logger(__name__)

# plan_route treats these pipeline_template values as "no explicit template":
# "standard"/"routine" are triage's pre-RAV-601 free-form vocabulary.
_LEGACY_TEMPLATE_VALUES = {"", "standard", "routine"}

RESULT_SUMMARY_MAX_CHARS = 10_000

# RAV-1074: cap for the clean, deliverable-derived one-liner that rides the
# job.completed/job.failed webhook payload (chat consumers render it as a
# single paragraph). Separate from RESULT_SUMMARY_MAX_CHARS, which bounds the
# raw denormalized copy that parse_proposal and the completion comments read.
NOTIFICATION_SUMMARY_MAX_CHARS = 280


def job_title_from_task(task: str | None, *, limit: int = 140) -> str:
    """First meaningful line of a job's task text — the title a human wrote.

    Jobs are created with ``task = issue.title + "\\n\\n" + issue.body``
    (``_executor_run``), so the first non-empty line is the title. Webhook
    consumers previously had only ``job_id`` to identify a job with, which
    makes every notification read the same and forces the reader into the
    dashboard to find out what happened.

    Markdown heading markers are stripped and the line is truncated — the rest
    of the issue body is not the caller's business. Only ``#`` runs followed
    by whitespace are markers: a title like ``#123 fix the thing`` keeps its
    issue ref.
    """
    for line in (task or "").splitlines():
        stripped = re.sub(r"^#+(\s+|$)", "", line.strip()).strip()
        if stripped:
            return stripped[:limit] + ("\u2026" if len(stripped) > limit else "")
    return ""


def build_notification_summary(result: dict[str, Any]) -> str:
    """Derive a clean one-line summary for job-completion notifications.

    RAV-1074: the webhook forwarded ``result_summary`` raw, so Telegram quoted
    the developer's stream-of-consciousness. Prefer a deliverable-derived line;
    ``result_summary`` itself is left untouched (the watcher's ``parse_proposal``
    and the Linear/GitHub completion comments still read the full raw copy).

    Order: PR link (code jobs) → watcher-proposal ``TITLE:`` line → first
    non-empty paragraph of the report/agent output → empty.
    """
    pr_url = str(result.get("pr_url") or "").strip()
    if pr_url:
        return f"PR opened: {pr_url}"

    summary = str(result.get("result_summary") or "").strip()
    if not summary:
        return ""

    # Watcher proposals carry the plain-text TITLE:/SEVERITY:/FINGERPRINT:/BODY:
    # contract — surface just the TITLE line.
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TITLE:"):
            title = stripped[len("TITLE:") :].strip()
            if title:
                return title[:NOTIFICATION_SUMMARY_MAX_CHARS]
            break

    # Otherwise the first non-empty paragraph (up to the first blank line).
    paragraph: list[str] = []
    for line in summary.splitlines():
        if line.strip():
            paragraph.append(line.strip())
        elif paragraph:
            break
    lead = " ".join(paragraph).strip() or summary
    return lead[:NOTIFICATION_SUMMARY_MAX_CHARS]


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


# RAV-1285: template agent_type → the physical LangGraph node whose
# node_started/node_completed stream events represent it. The frontend uses
# this (via each graph node's ``physical_node``) to light nodes up live.
# ``qa`` is special — the QA chain streams qa_* events, not node_started.
_PHYSICAL_NODE: dict[str, str] = {
    "triage": "triage",
    "developer": "developer",
    "reviewer": "review",
    "review": "review",
    "qa": "qa",
    "output": "finalize_output",
    **dict.fromkeys(GENERIC_AGENT_TYPES, "generic_agent"),
}

_NODE_SPACING_X = 230
_NODE_BASE_X = 40
_NODE_BASE_Y = 120


def build_pipeline_graph(
    *,
    job_id: str,
    plan: list[dict[str, Any]],
    graph_definition: dict[str, Any] | None = None,
    template_name: str | None = None,
) -> dict[str, Any]:
    """The job's real pipeline graph, in the frontend PipelineGraph shape.

    RAV-1285: the job-logs page always listened for a ``pipeline_graph``
    stream event that nothing ever emitted. This builds one from the truth
    the router runs on — the resolved template's ``graph_definition`` when
    one won (positions/labels/edges preserved), or a synthesized linear
    chain from the route plan on the legacy path. A ``triage`` node is
    prepended when the template doesn't declare one: triage always runs as
    the selector, and a live view that hides the first minute of the job
    would look broken. Every node carries ``physical_node`` so the frontend
    can map node_started/node_completed events onto graph nodes.
    """

    def _mk_node(node_id: str, agent_type: str, index: int, source: dict[str, Any] | None = None) -> dict[str, Any]:
        position = (source or {}).get("position") or {}
        return {
            "node_id": node_id,
            "agent_type": agent_type,
            "label": (source or {}).get("label") or agent_type,
            "position": {
                "x": position.get("x", _NODE_BASE_X + index * _NODE_SPACING_X),
                "y": position.get("y", _NODE_BASE_Y),
            },
            "physical_node": _PHYSICAL_NODE.get(agent_type, agent_type),
        }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    if graph_definition and graph_definition.get("nodes"):
        src_nodes = list(graph_definition.get("nodes") or [])
        for i, n in enumerate(src_nodes):
            nodes.append(_mk_node(str(n.get("node_id") or f"node-{i}"), str(n.get("agent_type") or ""), i, n))
        for e in graph_definition.get("edges") or []:
            edges.append(
                {
                    "edge_id": str(e.get("edge_id") or f"{e.get('source')}->{e.get('target')}"),
                    "source": str(e.get("source") or ""),
                    "target": str(e.get("target") or ""),
                    "edge_type": e.get("edge_type", "flow"),
                }
            )
        if not any(n["agent_type"] == "triage" for n in nodes):
            # Root = the node with no incoming flow edge (validated single-linear).
            targets = {e["target"] for e in edges if e["edge_type"] == "flow"}
            root = next((n for n in nodes if n["node_id"] not in targets), None)
            triage = _mk_node("triage", "triage", 0)
            if root is not None:
                triage["position"] = {
                    "x": min(n["position"]["x"] for n in nodes) - _NODE_SPACING_X,
                    "y": root["position"]["y"],
                }
                edges.insert(
                    0, {"edge_id": "triage->root", "source": "triage", "target": root["node_id"], "edge_type": "flow"}
                )
            nodes.insert(0, triage)
    else:
        chain = [{"node_id": "triage", "agent_type": "triage"}] + [
            {"node_id": str(s.get("node_id") or s.get("agent_type")), "agent_type": str(s.get("agent_type") or "")}
            for s in plan
        ]
        for i, step in enumerate(chain):
            nodes.append(_mk_node(step["node_id"], step["agent_type"], i))
            if i > 0:
                prev = chain[i - 1]["node_id"]
                edges.append(
                    {
                        "edge_id": f"{prev}->{step['node_id']}",
                        "source": prev,
                        "target": step["node_id"],
                        "edge_type": "flow",
                    }
                )

    src_meta = (graph_definition or {}).get("metadata") or {}
    return {
        "graph_id": f"route-{job_id}",
        "name": template_name or "standard pipeline",
        "description": (graph_definition or {}).get("description") or "",
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "max_total_budget_usd": src_meta.get("max_total_budget_usd", 0.0),
            "max_total_loops": src_meta.get("max_total_loops", 0),
            "created_by": "triage",
            "template_id": template_name,
        },
    }


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


async def pinned_triage_template_config(state: dict[str, Any], templates_repo: Any) -> dict[str, Any] | None:
    """The triage node config of the template the job named up front (#468).

    Only a template named on the job (``pipeline_template_request``) is known
    before triage runs; one that triage picks, or a kind default, is resolved
    after it. Best-effort: a lookup failure leaves triage on its usual model.
    """
    requested = str(state.get("pipeline_template_request") or "")
    if templates_repo is None or requested in _LEGACY_TEMPLATE_VALUES:
        return None
    try:
        row = await _find_template(templates_repo, name=requested)
    except Exception:
        logger.warning("triage_template_lookup_failed", template=requested, exc_info=True)
        return None
    if row is None:
        return None
    return triage_template_config(row.get("graph_definition") or {})


async def plan_route_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Resolve template → route_plan snapshot. Resolution order:

    1. triage's explicit ``pipeline_template`` name (legacy values skipped,
       and a specialist template, which runs only when the job names it);
    2. the template whose ``default_for_kind`` matches the job_kind;
    3. the legacy synthesized plan (bit-for-bit today's dev→review→qa chain).

    An invalid template falls through to the next tier — except an
    *explicitly requested* invalid template with no kind default, which
    fails loudly (silently-wrong routing would be worse).

    #306: a job working a watcher-origin case (``pin_full_pipeline``) always
    runs the full code pipeline — the ``code`` kind default (review and QA),
    or the legacy plan. Triage's template pick and job kind are ignored for
    it, so triage cannot route an unattended self-healing fix to
    ``quick-fix`` or to a kind without review. An operator's explicit
    ``pipeline_template_request`` still wins: that is a person's call.
    """
    writer = get_stream_writer()
    writer(events.node_started(node="plan_route"))

    decision = state.get("triage_decision") or {}
    job_kind = str(decision.get("job_kind") or "code")
    requested = str(decision.get("pipeline_template") or "")
    if state.get("pin_full_pipeline") and (requested not in _LEGACY_TEMPLATE_VALUES or job_kind != "code"):
        logger.info(
            "plan_route_triage_pick_overridden",
            job_id=state.get("job_id"),
            reason="watcher-origin case runs the full pipeline",
            triage_template=requested or None,
            triage_kind=job_kind,
        )
        requested, job_kind = "", "code"
    explicit = requested not in _LEGACY_TEMPLATE_VALUES

    # RAV-1058: a template named on JobCreateRequest.pipeline_template is an
    # operator instruction — it outranks triage's pick. Surfaced on state by
    # IssueExecutor._run_workflow (the job-row column itself is overwritten
    # with the resolved name below, so the request rides its own state key).
    requested_user = str(state.get("pipeline_template_request") or "")
    explicit_user = requested_user not in _LEGACY_TEMPLATE_VALUES

    # A specialist template runs only when the job names it. Triage is not
    # offered one, so its pick of one is dropped and the kind default routes.
    if requested in SPECIALIST_TEMPLATE_NAMES and requested != requested_user:
        logger.info(
            "plan_route_triage_pick_overridden",
            job_id=state.get("job_id"),
            reason="a specialist template runs only when the job names it",
            triage_template=requested,
            triage_kind=job_kind,
        )
        requested, explicit = "", False

    templates_repo = config["configurable"].get("pipeline_templates_repo")
    plan: list[dict[str, Any]] | None = None
    resolved_name: str | None = None
    resolved_kind: str | None = None
    resolved_graph: dict[str, Any] | None = None
    explicit_invalid = False

    if templates_repo is not None:
        candidates: list[tuple[str, dict[str, Any] | None]] = []
        if explicit_user:
            candidates.append(("explicit", await _find_template(templates_repo, name=requested_user)))
        if explicit and requested != requested_user:
            candidates.append(("explicit", await _find_template(templates_repo, name=requested)))
        candidates.append(("kind_default", await _find_template(templates_repo, kind=job_kind)))

        for tier, row in candidates:
            if row is None:
                if tier == "explicit":
                    logger.warning(
                        "plan_route_template_not_found",
                        requested=requested_user or requested,
                        job_kind=job_kind,
                    )
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
            resolved_kind = row.get("default_for_kind") or specialist_job_kind(plan)
            resolved_graph = graph
            break

    if plan is None:
        if explicit_invalid:
            raise TemplateInvalidError(
                f"Requested pipeline template {(requested_user or requested)!r} is invalid and no default "
                f"template exists for job_kind {job_kind!r}"
            )
        # Legacy fallback — today's behavior, also covers pre-RAV-601 jobs.
        plan = [dict(step) for step in LEGACY_ROUTE_PLAN]
        resolved_name = None

    # RAV-1075: report the kind the resolved template actually runs, not
    # triage's opinion. When a template wins (user-requested or kind-default)
    # its ``default_for_kind`` is the honest job_kind — a watcher run pinned to
    # ``analysis-default`` is an analysis job even if triage guessed ``code``.
    # A specialist route declares no kind, and its agents make it analysis.
    # Otherwise fall back to triage's kind (the legacy path, a custom template).
    # Triage's raw opinion stays in state["triage_decision"] for audit.
    effective_kind = resolved_kind or job_kind

    # Mirror onto the job row for dashboard filtering — best-effort.
    jobs_repo = config["configurable"].get("jobs_repo")
    if jobs_repo is not None:
        try:
            await jobs_repo.update(
                state.get("job_id", ""),
                job_kind=effective_kind,
                **({"pipeline_template": resolved_name} if resolved_name else {}),
            )
        except Exception:
            logger.warning("plan_route_job_row_update_failed", job_id=state.get("job_id"), exc_info=True)

    logger.info(
        "route_planned",
        job_id=state.get("job_id"),
        job_kind=effective_kind,
        triage_kind=job_kind,
        template=resolved_name or "(legacy fallback)",
        steps=[s["agent_type"] for s in plan],
    )
    # RAV-1285: the job-logs pipeline panel listens for this event — emit the
    # real graph the router will run. It rides the custom stream into
    # _broadcast_event, so it is persisted to job_logs AND broadcast: live
    # viewers, WS-replay late joiners, and the REST fallback all receive it.
    writer(
        events.pipeline_graph(
            graph=build_pipeline_graph(
                job_id=str(state.get("job_id") or ""),
                plan=plan,
                graph_definition=resolved_graph,
                template_name=resolved_name,
            ),
            node="plan_route",
        )
    )

    writer(
        events.node_completed(
            node="plan_route",
            job_kind=effective_kind,
            template=resolved_name or "legacy",
        )
    )
    return {
        "job_kind": effective_kind,
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
    writer(events.node_started(node="finalize_output"))

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

    writer(events.node_completed(node="finalize_output", deliverables=len(deliverables)))
    return {
        "result_summary": summary,
        "deliverables": deliverables,
        "current_stage": "output_finalized",
    }
