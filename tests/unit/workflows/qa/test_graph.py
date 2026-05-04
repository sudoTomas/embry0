"""Unit tests for QAWorkflow LangGraph compilation."""

from athanor.workflows.qa.graph import QAWorkflow


def test_graph_compiles_with_expected_nodes():
    wf = QAWorkflow()
    graph = wf.compile()
    nodes = graph.get_graph().nodes
    assert "init_orchestrator" in nodes
    assert "orchestrate_qa" in nodes
    assert "qa_report" in nodes


def test_graph_starts_at_init_orchestrator():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert any(s == "__start__" and t == "init_orchestrator" for s, t in edges)


def test_graph_edges_init_orchestrator_to_orchestrate_qa_to_qa_report():
    wf = QAWorkflow()
    graph = wf.compile()
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("init_orchestrator", "orchestrate_qa") in edges
    assert ("orchestrate_qa", "qa_report") in edges
