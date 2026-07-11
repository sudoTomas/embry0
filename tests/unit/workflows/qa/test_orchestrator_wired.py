"""Tests confirming QAWorkflow.compile() produces the multi-app graph."""

from __future__ import annotations

from embry0.workflows.qa.graph import QAWorkflow


def test_compiled_graph_includes_three_top_level_nodes():
    wf = QAWorkflow().compile(config={})
    nodes = set(wf.get_graph().nodes)
    assert "init_orchestrator" in nodes
    assert "orchestrate_qa" in nodes
    assert "qa_report" in nodes


def test_legacy_top_level_nodes_removed():
    """The Phase-1 graph no longer has top-level boot_qa / qa / retry / init_qa
    nodes; those were single-app concepts. The sub-task subgraph still uses
    qa_node internally (via subtask_nodes.exploratory_qa_node)."""
    wf = QAWorkflow().compile(config={})
    nodes = set(wf.get_graph().nodes)
    assert "boot_qa" not in nodes
    assert "qa" not in nodes
    assert "retry" not in nodes
    assert "init_qa" not in nodes
