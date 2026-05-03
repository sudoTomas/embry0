"""Standalone QA pipeline — LangGraph StateGraph.

Graph: init_qa -> boot_qa -> {qa | qa_report} -> retry -> {init_qa | END}

boot_qa returns Command(goto="qa" | "qa_report") based on backend boot outcome.
retry_node returns a Command that either routes back to init_qa
(auto-retry boot/seed/infra timeouts) or to END.

Node name "qa_report" matches the issue_to_pr workflow's naming so a single
boot_qa_node implementation works for both graphs.
"""

from typing import Any

from langgraph.graph import START, StateGraph

from athanor.orchestration.state import JobState
from athanor.workflows.qa.nodes import (
    boot_qa_node,
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
        builder.add_node("boot_qa", boot_qa_node)  # type: ignore[type-var]
        builder.add_node("qa", qa_node)  # type: ignore[type-var]
        builder.add_node("qa_report", report_node)  # type: ignore[type-var]
        builder.add_node("retry", retry_node)  # type: ignore[type-var]

        builder.add_edge(START, "init_qa")
        builder.add_edge("init_qa", "boot_qa")
        # boot_qa returns Command(goto="qa" | "qa_report") — no static edge from boot_qa.
        builder.add_edge("qa", "qa_report")
        builder.add_edge("qa_report", "retry")
        # retry returns Command(goto=...) — no static edges from retry.

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
