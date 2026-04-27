from athanor.workflows.issue_to_pr.graph import IssueToprWorkflow


def test_workflow_has_name():
    wf = IssueToprWorkflow()
    assert wf.name == "issue-to-pr"
    assert wf.description


def test_workflow_compiles():
    wf = IssueToprWorkflow()
    graph = wf.compile()
    assert graph is not None
    assert hasattr(graph, "ainvoke")


def test_workflow_graph_has_expected_nodes():
    wf = IssueToprWorkflow()
    graph = wf.compile()
    node_names = set(graph.nodes.keys())
    assert "init" in node_names
    assert "triage" in node_names
    assert "developer" in node_names
    assert "review" in node_names
    assert "retry" in node_names
    assert "max_retries" in node_names
