from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow


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


def test_generic_agent_node_wired():
    """RAV-604: the generic node exists and every dispatch seam can reach it."""
    wf = IssueToprWorkflow()
    graph = wf.compile()
    g = graph.get_graph()
    assert "generic_agent" in set(g.nodes)
    edges = {(e.source, e.target) for e in g.edges}
    # Reachable from plan_route, from its own chaining seam, and onward to
    # finalize_output (the research-default shape: triage→research→output).
    assert ("plan_route", "generic_agent") in edges
    assert ("generic_agent", "generic_agent") in edges
    assert ("generic_agent", "finalize_output") in edges
    assert ("review", "generic_agent") in edges
    assert ("qa_failure_bookkeeping", "generic_agent") in edges


def test_issue_to_pr_graph_uses_multi_app_qa():
    wf = IssueToprWorkflow()
    graph = wf.compile()
    nodes = set(graph.get_graph().nodes)
    assert "init_orchestrator" in nodes
    assert "orchestrate_qa" in nodes
    assert "qa_report" in nodes
    assert "init_qa" not in nodes
    assert "boot_qa" not in nodes
    assert "qa" not in nodes
    assert "qa_failure_bookkeeping" in nodes
    assert "qa_exhausted" in nodes
