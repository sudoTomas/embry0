"""Builtin pipeline-template seeding.

Run at orchestrator startup to upsert the canonical Athanor starter
pipelines. The seeds always overwrite (per upsert_builtin) — users who
want a customized variant should clone the builtin under a different name.

Each template is a small graph in the same shape the frontend stores
under ``graph_definition``: ``{nodes: [...], edges: [...], metadata: {...}}``.
Nodes use canonical agent types (triage, developer, reviewer, qa, output)
which the existing ``agentTypeToOperation`` mapping already understands.
"""

from typing import Any

import structlog

from athanor.storage.repositories.pipeline_templates import PipelineTemplatesRepository

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


# Three canonical starter pipelines, each mapped to one alchemical operation
# arc the agentTypeToOperation function already knows about:
#   quick-fix      → calcinate → ferment           (break down, transform)
#   review-only    → calcinate → distill           (break down, refine)
#   full-magnum-opus → calcinate → ferment → distill → conjoin → coagulate
BUILTIN_PIPELINE_TEMPLATES: dict[str, dict[str, Any]] = {
    "quick-fix": {
        "description": "Triage the issue, then a developer makes the fix. Smallest viable pipeline.",
        "graph_definition": _graph(
            name="quick-fix",
            description="Triage → developer.",
            nodes=[
                _node("triage", "triage", 0, 0),
                _node("developer", "developer", 240, 0),
            ],
            edges=[_edge("e1", "triage", "developer")],
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
        "description": "Full Athanor: triage → developer → reviewer → qa → output. The complete arc.",
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
    },
}


async def seed_builtin_pipeline_templates(repo: PipelineTemplatesRepository) -> None:
    """Idempotently upsert all builtin pipeline templates."""
    for name, fields in BUILTIN_PIPELINE_TEMPLATES.items():
        await repo.upsert_builtin(name=name, **fields)
        logger.info("pipeline_template_seeded", name=name)
