"""Static pipeline graphs for the jobs whose graph triage does not plan.

issue-to-pr jobs get their graph from plan_route (built from the route plan);
onboarding and standalone QA jobs run a fixed LangGraph, so their graph is
written down here in the frontend PipelineGraph shape and emitted by the
first node. tests/unit/workflows/test_pipeline_graphs.py pins each node list
to the compiled graph's nodes, so the two cannot drift.

``physical_node`` is the LangGraph node whose node_started/node_completed
events light the graph node; every node here emits under its own id.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_SPACING_X = 230
_BASE_X = 40
_BASE_Y = 120

#: (node_id, agent_type, label). The ids are the LangGraph node names.
ONBOARD_NODE_IDS: tuple[tuple[str, str, str], ...] = (
    ("init_onboard", "onboarding", "Init"),
    ("analyze", "onboarding", "Analyze"),
    ("validate", "onboarding", "Validate"),
    ("smoke", "onboarding", "Smoke"),
    ("write_config", "onboarding", "Write config"),
    ("cleanup", "onboarding", "Cleanup"),
)
#: (source, target, edge_type). "flow" is the forward path, "feedback" a retry.
_ONBOARD_EDGES: tuple[tuple[str, str, str], ...] = (
    ("init_onboard", "analyze", "flow"),
    ("analyze", "validate", "flow"),
    ("validate", "smoke", "flow"),
    ("smoke", "write_config", "flow"),
    ("write_config", "cleanup", "flow"),
    ("validate", "analyze", "feedback"),
    ("smoke", "analyze", "feedback"),
    ("validate", "cleanup", "flow"),
    ("smoke", "cleanup", "flow"),
)

QA_NODE_IDS: tuple[tuple[str, str, str], ...] = (
    ("init_orchestrator", "qa", "Init"),
    ("orchestrate_qa", "qa", "QA"),
    ("qa_report", "qa", "Report"),
)
_QA_EDGES: tuple[tuple[str, str, str], ...] = (
    ("init_orchestrator", "orchestrate_qa", "flow"),
    ("orchestrate_qa", "qa_report", "flow"),
)


def _static_graph(
    *,
    graph_id: str,
    name: str,
    description: str,
    nodes: Sequence[tuple[str, str, str]],
    edges: Sequence[tuple[str, str, str]],
) -> dict[str, Any]:
    return {
        "graph_id": graph_id,
        "name": name,
        "description": description,
        "nodes": [
            {
                "node_id": node_id,
                "agent_type": agent_type,
                "label": label,
                "position": {"x": _BASE_X + index * _SPACING_X, "y": _BASE_Y},
                "physical_node": node_id,
            }
            for index, (node_id, agent_type, label) in enumerate(nodes)
        ],
        "edges": [
            {"edge_id": f"{source}->{target}", "source": source, "target": target, "edge_type": edge_type}
            for source, target, edge_type in edges
        ],
        # PipelineMetadata (frontend/src/lib/types/pipelines.ts): a fixed graph has
        # no budget or loop cap of its own and was not planned by triage.
        "metadata": {"max_total_budget_usd": 0, "max_total_loops": 0, "created_by": "auto", "template_id": None},
    }


def onboard_pipeline_graph(job_id: str) -> dict[str, Any]:
    return _static_graph(
        graph_id=f"onboard-{job_id}",
        name="onboarding",
        description="Analyze the repository and write its QA config",
        nodes=ONBOARD_NODE_IDS,
        edges=_ONBOARD_EDGES,
    )


def qa_pipeline_graph(job_id: str) -> dict[str, Any]:
    return _static_graph(
        graph_id=f"qa-{job_id}",
        name="standalone QA",
        description="Boot the apps and run the browser QA agent per app",
        nodes=QA_NODE_IDS,
        edges=_QA_EDGES,
    )
