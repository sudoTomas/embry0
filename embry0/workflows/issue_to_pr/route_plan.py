"""Template route planning + dispatch (RAV-601).

Pipeline templates are *interpreted, not compiled*: LangGraph fixes graph
topology at compile time, but triage — which selects the template — runs
inside the graph. So ``IssueToprWorkflow.compile()`` declares the full
superset of physical nodes, ``plan_route_node`` linearizes the selected
template's ``graph_definition`` into a ``route_plan`` snapshot on state,
and the pure ``next_route`` dispatcher returns the next physical node at
every forward-progression seam. Internal loops (developer retry, review
feedback, ask-user) never touch the cursor — they are not template edges.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Template agent_type → physical LangGraph node. ``qa`` maps to the head of
# the whole QA chain (init_orchestrator → orchestrate_qa → qa_report →
# qa_failure_bookkeeping) — one template step, internal edges untouched.
AGENT_TYPE_TO_NODE: dict[str, str] = {
    "developer": "developer",
    "reviewer": "review",
    "qa": "init_orchestrator",
    "output": "finalize_output",
}

# The dispatcher's return vocabulary; graph.py maps these to nodes.
ROUTE_TARGETS: dict[str, str] = {
    "developer": "developer",
    "review": "review",
    "qa": "init_orchestrator",
    "output": "finalize_output",
}

# The legacy chain, byte-for-byte today's behavior — used when no template
# resolves (pre-RAV-601 jobs, triage's legacy "standard"/"routine" values).
LEGACY_ROUTE_PLAN: list[dict[str, Any]] = [
    {"agent_type": "developer", "node_id": "developer", "config": {}},
    {"agent_type": "reviewer", "node_id": "reviewer", "config": {}},
    {"agent_type": "qa", "node_id": "qa", "config": {}},
]


def build_route_plan(graph_definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Linearize a validated graph_definition into an ordered step list.

    Assumes validate_graph_definition passed (single linear flow path);
    walks flow edges from the root, dropping the ``triage`` node (triage
    always runs first, as the selector — it is not a routed step).
    """
    nodes = {n["node_id"]: n for n in graph_definition.get("nodes", [])}
    flow_edges = [e for e in graph_definition.get("edges", []) if e.get("edge_type", "flow") == "flow"]
    successors = {e["source"]: e["target"] for e in flow_edges}
    targets = {e["target"] for e in flow_edges}
    roots = [nid for nid in nodes if nid not in targets]
    if len(roots) != 1:
        raise ValueError(f"graph_definition must have exactly one root node, found {roots}")

    plan: list[dict[str, Any]] = []
    node_id: str | None = roots[0]
    seen: set[str] = set()
    while node_id is not None:
        if node_id in seen:
            raise ValueError(f"graph_definition contains a cycle at {node_id!r}")
        seen.add(node_id)
        node = nodes[node_id]
        agent_type = node.get("agent_type", "")
        if agent_type != "triage":  # triage is the selector, not a step
            plan.append({"agent_type": agent_type, "node_id": node_id, "config": node.get("config") or {}})
        node_id = successors.get(node_id)
    return plan


def _step_target(step: dict[str, Any]) -> str:
    """Dispatcher key for a step ('developer' | 'review' | 'qa' | 'output')."""
    mapping = {"developer": "developer", "reviewer": "review", "qa": "qa", "output": "output"}
    return mapping[step["agent_type"]]


def next_route(state: dict[str, Any]) -> str:
    """Pure dispatcher: the next route key from route_plan/route_cursor.

    Skips a ``qa`` step when triage decided QA isn't needed — the template
    declares the superset, triage's set_qa_decision prunes within it.
    Returns ``"end"`` past the last step or when no plan exists.
    """
    plan = state.get("route_plan") or []
    cursor = int(state.get("route_cursor", 0) or 0)
    qa_block = state.get("qa") or {}
    needs_qa = bool(qa_block.get("needs_qa"))

    while cursor < len(plan):
        step = plan[cursor]
        target = _step_target(step)
        if target == "qa" and not needs_qa:
            cursor += 1
            continue
        return target
    return "end"


def cursor_at(state: dict[str, Any], agent_type: str) -> int:
    """Index of the first route step of ``agent_type`` (0 when absent).

    Used by re-entry paths (QA-failure retry_developer / rerun_qa) that jump
    the graph to a specific node: the cursor must point back AT that step so
    forward progression resumes from there instead of continuing past the
    stale position.
    """
    for i, step in enumerate(state.get("route_plan") or []):
        if step.get("agent_type") == agent_type:
            return i
    return 0


def advance(state: dict[str, Any]) -> dict[str, Any]:
    """State fragment that moves the cursor past the current step.

    Accounts for qa steps next_route silently skipped: the cursor lands one
    past the step that just executed (or past the trailing skipped steps).
    """
    plan = state.get("route_plan") or []
    cursor = int(state.get("route_cursor", 0) or 0)
    qa_block = state.get("qa") or {}
    needs_qa = bool(qa_block.get("needs_qa"))
    while cursor < len(plan) and _step_target(plan[cursor]) == "qa" and not needs_qa:
        cursor += 1
    return {"route_cursor": cursor + 1}
