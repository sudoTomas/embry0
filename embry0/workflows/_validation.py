"""Shared pipeline validation helpers.

Extracted from WorkflowRegistry so the same checks can run at both workflow
registration time and pipeline-template create/update time (DB-stored templates
must not be able to smuggle deferred tools past the registry gate).
"""

from __future__ import annotations

from typing import Any

_DEFERRED_TOOLS = frozenset({"CreateIssue", "RequestInput", "UpdateStatus"})

# The template-node vocabulary the route-plan interpreter can execute
# (embry0/workflows/issue_to_pr/route_plan.py AGENT_TYPE_TO_NODE + triage).
# research/analysis/ops (RAV-604) all execute on the generic agent node.
_EXECUTABLE_AGENT_TYPES = frozenset({"triage", "developer", "reviewer", "qa", "output", "research", "analysis", "ops"})


def validate_graph_definition(name: str, graph: dict[str, Any]) -> list[str]:
    """Validate that a template's graph_definition is executable (RAV-601).

    Templates became runtime control-flow when the route-plan interpreter
    landed; the frontend editor can draw anything, but only a **single
    linear flow chain** of known agent types may save (v1 constraint —
    branching/parallel templates need interpreter support first).
    Returns a list of human-readable problems; empty = valid.
    """
    errors: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    # Deferred-tools gate first — it must be reported even for structurally
    # empty graphs (templates must not smuggle deferred tools).
    try:
        validate_pipeline_tools(name, graph)
    except ValueError as exc:
        errors.append(str(exc))

    if not nodes:
        return [*errors, f"template {name!r}: graph has no nodes"]

    ids = [n.get("node_id") for n in nodes]
    if len(ids) != len(set(ids)):
        errors.append(f"template {name!r}: duplicate node_id values")
    id_set = set(ids)

    for n in nodes:
        at = n.get("agent_type")
        if at not in _EXECUTABLE_AGENT_TYPES:
            errors.append(f"template {name!r}: node {n.get('node_id')!r} has unknown agent_type {at!r}")

    flow_edges = [e for e in edges if e.get("edge_type", "flow") == "flow"]
    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    for e in flow_edges:
        src, dst = e.get("source"), e.get("target")
        if src not in id_set or dst not in id_set:
            errors.append(f"template {name!r}: edge {e.get('edge_id')!r} references unknown node")
            continue
        out_count[src] = out_count.get(src, 0) + 1
        in_count[dst] = in_count.get(dst, 0) + 1

    if any(c > 1 for c in out_count.values()) or any(c > 1 for c in in_count.values()):
        errors.append(f"template {name!r}: branching graphs are not executable yet — one linear flow chain only")

    roots = [nid for nid in id_set if nid not in in_count]
    if len(roots) != 1:
        errors.append(f"template {name!r}: expected exactly one root node, found {len(roots)}")
    elif not errors:
        # Walk the chain: must reach every node (connected, acyclic).
        seen: list[Any] = []
        cur: Any = roots[0]
        while cur is not None and cur not in seen:
            seen.append(cur)
            cur = next((e["target"] for e in flow_edges if e.get("source") == cur), None)
        if cur is not None:
            errors.append(f"template {name!r}: cycle detected at {cur!r}")
        elif len(seen) != len(id_set):
            errors.append(f"template {name!r}: disconnected nodes — one linear flow chain only")

    by_type = {n.get("node_id"): n.get("agent_type") for n in nodes}
    if len(roots) == 1:
        for nid, at in by_type.items():
            if at == "triage" and nid != roots[0]:
                errors.append(f"template {name!r}: a triage node must be the root")
    if all(n.get("agent_type") == "triage" for n in nodes):
        errors.append(f"template {name!r}: needs at least one non-triage node")
    for e in flow_edges:
        if by_type.get(e.get("source")) == "output":
            errors.append(f"template {name!r}: 'output' must be terminal")

    return errors


def validate_pipeline_tools(name: str, pipeline_config: dict[str, Any]) -> None:
    """Reject pipelines that declare tools served by the deferred embry0 API proxy.

    ``pipeline_config`` is any dict that may contain an ``agent_tools`` key
    mapping agent names to lists of tool names.  For workflow registration
    callers this is ``workflow.pipeline_config``; for pipeline-template callers
    this is the ``graph_definition`` dict stored in the DB.

    Raises:
        ValueError: if any agent declares one of the reserved deferred tools.
    """
    agent_tools = pipeline_config.get("agent_tools", {}) if isinstance(pipeline_config, dict) else {}
    for agent, tools in (agent_tools or {}).items():
        bad = _DEFERRED_TOOLS.intersection(tools or [])
        if bad:
            raise ValueError(
                f"Pipeline {name!r} declares deferred tool(s) {sorted(bad)} "
                f"for agent {agent!r}. The embry0 API proxy that serves these is "
                "not yet implemented; see docs/architecture.md § embry0 API Proxy."
            )
