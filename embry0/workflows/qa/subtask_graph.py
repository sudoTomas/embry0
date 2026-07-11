"""Sub-task LangGraph subgraph — one per affected app.

Builds the DAG that wires together the per-step nodes in subtask_nodes.py.
The orchestrator (Task 22) invokes one instance of this subgraph per
affected app via LangGraph's parallel-fan-out pattern.

Linear flow Phase 1 — every node short-circuits via state.status when a
prior node failed, so we don't need conditional edges yet. release_sandbox
runs unconditionally before emit_result so cleanup always happens.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from embry0.workflows.qa.subtask_nodes import (
    acquire_sandbox_node,
    boot_app_node,
    collect_artifacts_node,
    e2e_node,
    emit_result_node,
    exploratory_qa_node,
    release_sandbox_node,
    seed_node,
)
from embry0.workflows.qa.subtask_result_schema import SubTaskResult
from embry0.workflows.qa.subtask_state import SubTaskState, initial_state_for_app


def build_subtask_graph() -> Any:
    """Compile the sub-task subgraph.

    All 8 nodes wired in linear order. Each node short-circuits internally
    when a previous step set state["status"] (so release_sandbox can still
    clean up after a failed boot, and emit_result still emits a result).
    """
    builder = StateGraph(SubTaskState)
    builder.add_node("acquire_sandbox", acquire_sandbox_node)
    builder.add_node("boot_app", boot_app_node)
    builder.add_node("seed", seed_node)
    builder.add_node("e2e", e2e_node)
    builder.add_node("exploratory_qa", exploratory_qa_node)
    builder.add_node("collect_artifacts", collect_artifacts_node)
    builder.add_node("release_sandbox", release_sandbox_node)
    builder.add_node("emit_result", emit_result_node)

    builder.add_edge(START, "acquire_sandbox")
    builder.add_edge("acquire_sandbox", "boot_app")
    builder.add_edge("boot_app", "seed")
    builder.add_edge("seed", "e2e")
    builder.add_edge("e2e", "exploratory_qa")
    builder.add_edge("exploratory_qa", "collect_artifacts")
    builder.add_edge("collect_artifacts", "release_sandbox")
    builder.add_edge("release_sandbox", "emit_result")
    builder.add_edge("emit_result", END)

    return builder.compile()


# Process-wide cache: build_subtask_graph() is expensive (LangGraph compile),
# and the resulting graph is structurally invariant — same nodes/edges every
# time. We compile once per process and reuse. Tests that need a fresh graph
# instance call build_subtask_graph() directly rather than via _get_subgraph().
_SUBGRAPH_CACHE: Any | None = None


def _get_subgraph() -> Any:
    global _SUBGRAPH_CACHE
    if _SUBGRAPH_CACHE is None:
        _SUBGRAPH_CACHE = build_subtask_graph()
    return _SUBGRAPH_CACHE


async def run_subtask(
    resolved: ResolvedAppConfig,
    *,
    parent_run_id: str,
    repo: str,
    branch_name: str | None = None,
    user_env_vars: Any = None,
    prebaked_image_tag: str | None = None,
    shared_volume_name: str | None = None,
    turbo_remote_enabled: bool = False,
    config: dict[str, Any],
) -> SubTaskResult:
    """Helper: run one sub-task and return its SubTaskResult.

    Used by the QA orchestrator (Task 22) inside fan_out_subtasks. Each call
    receives a fresh per-app state dict; the compiled subgraph is cached.

    ``shared_volume_name`` — Phase-2 C3.  When provided, the sub-task mounts
    this pre-warmed Docker volume at ``/workspace`` and skips git clone.

    ``turbo_remote_enabled`` — Phase-2 E1.  When True, TURBO_* env vars are
    injected into the sandbox env (sourced from os.environ).
    """
    graph = _get_subgraph()
    initial = initial_state_for_app(
        resolved=resolved,
        parent_run_id=parent_run_id,
        repo=repo,
        branch_name=branch_name,
        user_env_vars=user_env_vars,
        prebaked_image_tag=prebaked_image_tag,
        shared_volume_name=shared_volume_name,
        turbo_remote_enabled=turbo_remote_enabled,
    )
    final_state = await graph.ainvoke(initial, config=config)
    # SubTaskState.subtask_result is typed SubTaskResult; graph.ainvoke is
    # untyped (Any) so narrow here. Always a SubTaskResult at runtime.
    return cast(SubTaskResult, final_state["subtask_result"])
