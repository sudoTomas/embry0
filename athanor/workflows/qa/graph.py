"""Standalone QA pipeline — LangGraph StateGraph.

Phase 1 (multi-app): init_orchestrator -> orchestrate_qa -> qa_report -> END.

orchestrate_qa fans out one parallel sub-task per affected app via the
sub-task subgraph (see subtask_graph.py). qa_report writes the aggregate
GitHub check and sticky PR comment. Retry semantics are now PER-SUB-TASK
(handled inside the sub-task subgraph), not at the pipeline level.

Single-app callers get multi-app QA via a qa.yaml v2 with one apps: entry
(use `athanor migrate-qa-config --write` to convert legacy v1 files).
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from athanor.orchestration.state import JobState


class QAWorkflow:
    name = "qa"
    description = "Standalone QA pipeline: boot target app + validate via Playwright MCP"

    def compile(self, config: Any = None) -> Any:
        from athanor.workflows.qa.orchestrator import (
            init_orchestrator_node,
            qa_orchestrator_node,
        )
        from athanor.workflows.qa.orchestrator_report import qa_report_node

        builder = StateGraph(JobState)
        builder.add_node("init_orchestrator", init_orchestrator_node)
        builder.add_node("orchestrate_qa", qa_orchestrator_node)
        builder.add_node("qa_report", qa_report_node)

        builder.add_edge(START, "init_orchestrator")
        builder.add_edge("init_orchestrator", "orchestrate_qa")
        builder.add_edge("orchestrate_qa", "qa_report")
        builder.add_edge("qa_report", END)

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
