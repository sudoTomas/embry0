"""Builtin pipeline-template seeding.

Run at orchestrator startup to upsert the canonical embry0 starter
pipelines. The seeds always overwrite (per upsert_builtin) — users who
want a customized variant should clone the builtin under a different name.

Each template is a small graph in the same shape the frontend stores
under ``graph_definition``: ``{nodes: [...], edges: [...], metadata: {...}}``.
Nodes use canonical agent types (triage, developer, reviewer, qa, output)
which the existing ``agentTypeToOperation`` mapping already understands.
"""

from typing import Any

import structlog

from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository

logger = structlog.get_logger(__name__)


def _node(node_id: str, agent_type: str, x: int, y: int) -> dict[str, Any]:
    """Build a minimal node entry. Position is in pipeline-editor coordinates;
    the editor's auto-arrange will reposition on first open if the user prefers."""
    return {
        "node_id": node_id,
        "agent_type": agent_type,
        "label": agent_type,
        "position": {"x": x, "y": y},
    }


def _edge(edge_id: str, source: str, target: str) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "source": source,
        "target": target,
        "edge_type": "flow",
    }


def _graph(name: str, description: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "graph_id": name,
        "name": name,
        "description": description,
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "max_total_budget_usd": 10.0,
            "max_total_loops": 3,
            "created_by": "auto",
        },
    }


def _specialist_route(name: str, agent_type: str, description: str) -> dict[str, Any]:
    """A specialist route: triage, the specialist on the generic node, output.

    No ``default_for_kind``, and triage is not offered it: a job reaches it
    only by naming the template (``SPECIALIST_TEMPLATE_NAMES``), and
    plan_route records it as an analysis job (``specialist_job_kind``).
    """
    return {
        "description": description,
        "graph_definition": _graph(
            name=name,
            description=f"Triage → {agent_type} → output.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node(agent_type, agent_type, 240, 0),
                _node("output", "output", 480, 0),
            ],
            edges=[
                _edge("e1", "triage", agent_type),
                _edge("e2", agent_type, "output"),
            ],
        ),
        "sandbox_profile": "slim",
    }


# Three canonical starter pipelines, each mapped to one alchemical operation
# arc the agentTypeToOperation function already knows about:
#   quick-fix      → calcinate → ferment → distill (break down, transform, refine)
#   review-only    → calcinate → distill           (break down, refine)
#   full-magnum-opus → calcinate → ferment → distill → conjoin → coagulate
BUILTIN_PIPELINE_TEMPLATES: dict[str, dict[str, Any]] = {
    "quick-fix": {
        "description": (
            "Triage the issue, a developer makes the fix, a reviewer checks it. "
            "Smallest pipeline that still gets a verdict (and so the verification gate) before merge."
        ),
        "graph_definition": _graph(
            name="quick-fix",
            description="Triage → developer → reviewer.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("developer", "developer", 240, 0),
                _node("reviewer", "reviewer", 480, 0),
            ],
            edges=[_edge("e1", "triage", "developer"), _edge("e2", "developer", "reviewer")],
        ),
        "sandbox_profile": "slim",
    },
    "review-only": {
        "description": "Triage and route to a reviewer. No code changes — distillation pass over an existing PR or branch.",
        "graph_definition": _graph(
            name="review-only",
            description="Triage → reviewer.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("reviewer", "reviewer", 240, 0),
            ],
            edges=[_edge("e1", "triage", "reviewer")],
        ),
        "sandbox_profile": "slim",
    },
    "full-magnum-opus": {
        "description": "Full embry0: triage → developer → reviewer → qa → output. The complete arc.",
        "graph_definition": _graph(
            name="full-magnum-opus",
            description="Triage → developer → reviewer → qa → output.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("developer", "developer", 240, 0),
                _node("reviewer", "reviewer", 480, 0),
                _node("qa", "qa", 720, 0),
                _node("output", "output", 960, 0),
            ],
            edges=[
                _edge("e1", "triage", "developer"),
                _edge("e2", "developer", "reviewer"),
                _edge("e3", "reviewer", "qa"),
                _edge("e4", "qa", "output"),
            ],
        ),
        "sandbox_profile": "slim",
        "default_for_kind": "code",
    },
    # RAV-604: one default route per non-code job kind. Each runs its
    # kind's agent on the generic agent node, then finalize_output captures
    # the agent's final message as the job deliverable.
    "research-default": {
        "description": (
            "Default route for job_kind=research: triage → research agent → output. "
            "Investigates the staged source material and delivers a cited findings report."
        ),
        "graph_definition": _graph(
            name="research-default",
            description="Triage → research → output.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("research", "research", 240, 0),
                _node("output", "output", 480, 0),
            ],
            edges=[
                _edge("e1", "triage", "research"),
                _edge("e2", "research", "output"),
            ],
        ),
        "sandbox_profile": "slim",
        "default_for_kind": "research",
    },
    "analysis-default": {
        "description": (
            "Default route for job_kind=analysis: triage → analysis agent → output. "
            "Delivers evidence-backed findings and recommendations over the workspace."
        ),
        "graph_definition": _graph(
            name="analysis-default",
            description="Triage → analysis → output.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("analysis", "analysis", 240, 0),
                _node("output", "output", 480, 0),
            ],
            edges=[
                _edge("e1", "triage", "analysis"),
                _edge("e2", "analysis", "output"),
            ],
        ),
        "sandbox_profile": "slim",
        "default_for_kind": "analysis",
    },
    "ops-default": {
        "description": (
            "Default route for job_kind=ops: triage → ops agent → output. "
            "Performs the operational task in the workspace and delivers a verified action report."
        ),
        "graph_definition": _graph(
            name="ops-default",
            description="Triage → ops → output.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("ops", "ops", 240, 0),
                _node("output", "output", 480, 0),
            ],
            edges=[
                _edge("e1", "triage", "ops"),
                _edge("e2", "ops", "output"),
            ],
        ),
        "sandbox_profile": "slim",
        "default_for_kind": "ops",
    },
    "pr-followup": {
        "description": (
            "PR follow-up route: triage → developer → reviewer → output. "
            "For jobs dispatched against an EXISTING PR branch (comment-driven "
            "dispatch) — the developer adopts the PR-creating session, works on "
            "the branch carried in context.ref, and never opens a new PR. No QA "
            "step: the original PR's pipeline owns QA."
        ),
        "graph_definition": _graph(
            name="pr-followup",
            description="Triage → developer → reviewer → output on an existing PR branch.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("developer", "developer", 240, 0),
                _node("review", "reviewer", 480, 0),
                _node("output", "output", 720, 0),
            ],
            edges=[
                _edge("e1", "triage", "developer"),
                _edge("e2", "developer", "review"),
                _edge("e3", "review", "output"),
            ],
        ),
        "sandbox_profile": "slim",
    },
    # One route per specialist agent (specialist_agents.py). Each is
    # read-only and delivers one report, and the descriptions say so on the
    # Pipelines page. Triage is not offered these (SPECIALIST_TEMPLATE_NAMES):
    # a job runs one only by naming it.
    "kintsugi-trace": _specialist_route(
        "kintsugi-trace",
        "kintsugi",
        "Read-only: triage → kintsugi → output. Runs the repository's own gates, reads code against "
        "its docs, and delivers a ledger of graded cracks with the seal for each.",
    ),
    "garden": _specialist_route(
        "garden",
        "gardener",
        "Read-only: triage → gardener → output. Delivers one provable weed with its proof, the exact "
        "diff that pulls it, and the check that shows nothing changed.",
    ),
    "interrogate": _specialist_route(
        "interrogate",
        "interrogator",
        "Read-only: triage → interrogator → output. Reviews a branch or pull request from four angles "
        "and delivers evidence-backed findings with a verdict.",
    ),
    "why": _specialist_route(
        "why",
        "why",
        "Read-only: triage → why → output. Answers why code is the way it is, citing history, "
        "decision records and docs, with each claim's confidence marked.",
    ),
    "how": _specialist_route(
        "how",
        "how",
        "Read-only: triage → how → output. Explains how a subsystem works: entry points, runtime "
        "flow, ownership and layering, and a file:line map.",
    ),
    "blast-radius": _specialist_route(
        "blast-radius",
        "blast-radius",
        "Read-only: triage → blast-radius → output. Finds what a change could break beyond its diff "
        "and proves the one fact it is safe because of, or marks it unproven.",
    ),
    "comment-pass": _specialist_route(
        "comment-pass",
        "comment-sicko",
        "Read-only: triage → comment-sicko → output. Delivers the comments a diff should delete and "
        "the checks that should replace the constraints they claim.",
    ),
    "design-audit": _specialist_route(
        "design-audit",
        "design-auditor",
        "Read-only: triage → design-auditor → output. Measures the frontend against the design "
        "standard in the job and delivers ranked findings with a reproducible 0 to 100 score.",
    ),
}


async def seed_builtin_pipeline_templates(repo: PipelineTemplatesRepository) -> None:
    """Idempotently upsert all builtin pipeline templates."""
    for name, fields in BUILTIN_PIPELINE_TEMPLATES.items():
        await repo.upsert_builtin(name=name, **fields)
        logger.info("pipeline_template_seeded", name=name)
