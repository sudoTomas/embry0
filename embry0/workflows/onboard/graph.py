"""Onboard pipeline — LangGraph StateGraph (EMB-50).

init_onboard → analyze → validate ─┬→ smoke ─┬→ write_config → cleanup → END
                       ↑           │         │
                       └───────────┴─────────┘  (retry with error feedback,
                                                 up to nodes.MAX_ROUNDS)

Failure at any point routes to cleanup (sandbox destroy) — the external
config store is only written after schema validation AND the boot/ready
smoke pass, so "present in store" always means "validated".
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from embry0.orchestration.state import JobState


class OnboardWorkflow:
    name = "onboard"
    description = "Analyze an existing repo and generate its qa.yaml v2 into the external config store"

    def compile(self, config: Any = None) -> Any:
        from embry0.workflows.onboard.nodes import (
            analyze_node,
            cleanup_node,
            init_onboard_node,
            route_after_smoke,
            route_after_validate,
            smoke_node,
            validate_node,
            write_config_node,
        )

        builder = StateGraph(JobState)
        builder.add_node("init_onboard", init_onboard_node)  # type: ignore[type-var]
        builder.add_node("analyze", analyze_node)  # type: ignore[type-var]
        builder.add_node("validate", validate_node)  # type: ignore[type-var]
        builder.add_node("smoke", smoke_node)  # type: ignore[type-var]
        builder.add_node("write_config", write_config_node)  # type: ignore[type-var]
        builder.add_node("cleanup", cleanup_node)  # type: ignore[type-var]

        builder.add_edge(START, "init_onboard")
        builder.add_edge("init_onboard", "analyze")
        builder.add_edge("analyze", "validate")
        builder.add_conditional_edges(
            "validate",
            route_after_validate,
            {"smoke": "smoke", "analyze": "analyze", "cleanup": "cleanup"},
        )
        builder.add_conditional_edges(
            "smoke",
            route_after_smoke,
            {"write_config": "write_config", "analyze": "analyze", "cleanup": "cleanup"},
        )
        builder.add_edge("write_config", "cleanup")
        builder.add_edge("cleanup", END)

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
