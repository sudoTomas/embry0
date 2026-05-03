"""Standalone QA pipeline — LangGraph StateGraph.

Graph: init_qa -> qa -> report -> retry -> {init_qa | END}

retry_node returns a Command that either routes back to init_qa
(auto-retry boot/seed/infra timeouts) or to END.
"""

from typing import Any

from langgraph.graph import START, StateGraph

from athanor.orchestration.state import JobState
from athanor.workflows.qa.nodes import (
    init_qa_node,
    qa_node,
    report_node,
    retry_node,
)


class QAWorkflow:
    name = "qa"
    description = "Standalone QA pipeline: boot target app + validate via Playwright MCP"

    def compile(self, config: Any = None) -> Any:
        builder = StateGraph(JobState)
        builder.add_node("init_qa", init_qa_node)  # type: ignore[type-var]
        builder.add_node("qa", qa_node)  # type: ignore[type-var]
        builder.add_node("report", report_node)  # type: ignore[type-var]
        builder.add_node("retry", retry_node)  # type: ignore[type-var]

        builder.add_edge(START, "init_qa")
        builder.add_edge("init_qa", "qa")
        builder.add_edge("qa", "report")
        builder.add_edge("report", "retry")
        # retry returns Command(goto=...) — no static edges from retry.

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
