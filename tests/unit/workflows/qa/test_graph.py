"""Unit tests for QAWorkflow LangGraph compilation."""

from athanor.workflows.qa.graph import QAWorkflow


def test_graph_compiles_with_expected_nodes():
    wf = QAWorkflow()
    graph = wf.compile()
    nodes = graph.get_graph().nodes
    assert "init_qa" in nodes
    assert "boot_qa" in nodes
    assert "qa" in nodes
    assert "qa_report" in nodes
    assert "retry" in nodes


def test_graph_starts_at_init_qa():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert any(s == "__start__" and t == "init_qa" for s, t in edges)


def test_graph_init_qa_to_boot_qa_to_qa_report_to_retry():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    # Static edges: boot_qa Command-routes so no static edge from boot_qa.
    assert ("init_qa", "boot_qa") in edges
    assert ("qa", "qa_report") in edges
    assert ("qa_report", "retry") in edges
