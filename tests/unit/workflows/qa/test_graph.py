"""Unit tests for QAWorkflow LangGraph compilation."""

from athanor.workflows.qa.graph import QAWorkflow


def test_graph_compiles_with_expected_nodes():
    wf = QAWorkflow()
    graph = wf.compile()
    nodes = graph.get_graph().nodes
    assert "init_qa" in nodes
    assert "qa" in nodes
    assert "report" in nodes
    assert "retry" in nodes


def test_graph_starts_at_init_qa():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert any(s == "__start__" and t == "init_qa" for s, t in edges)


def test_graph_init_qa_to_qa_to_report_to_retry():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("init_qa", "qa") in edges
    assert ("qa", "report") in edges
    assert ("report", "retry") in edges
