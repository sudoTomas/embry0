"""Static pipeline graphs for the jobs whose graph is not planned by triage (spec 3.8 / #40)."""

from __future__ import annotations

from embry0.workflows.pipeline_graphs import onboard_pipeline_graph, qa_pipeline_graph


def _compiled_node_names(workflow) -> set[str]:
    return {n for n in workflow.compile().get_graph().nodes if not n.startswith("__")}


def test_onboard_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.onboard.graph import OnboardWorkflow

    graph = onboard_pipeline_graph("job-1")
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(OnboardWorkflow()), (
        "the static graph must list the LangGraph nodes, no more, no less"
    )
    assert all(n["physical_node"] == n["node_id"] for n in graph["nodes"]), (
        "onboard nodes emit node_started under their own id"
    )
    assert graph["graph_id"] == "onboard-job-1"


def test_qa_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.qa.graph import QAWorkflow

    graph = qa_pipeline_graph("job-2")
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(QAWorkflow())
    assert [n["node_id"] for n in graph["nodes"]] == ["init_orchestrator", "orchestrate_qa", "qa_report"]
    assert graph["graph_id"] == "qa-job-2"


def test_graphs_have_the_frontend_shape() -> None:
    for graph in (onboard_pipeline_graph("j"), qa_pipeline_graph("j")):
        assert set(graph) >= {"graph_id", "name", "description", "nodes", "edges", "metadata"}
        for node in graph["nodes"]:
            assert set(node) >= {"node_id", "agent_type", "label", "position", "physical_node"}
            assert set(node["position"]) == {"x", "y"}
        ids = {n["node_id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert set(edge) == {"edge_id", "source", "target", "edge_type"}
            assert edge["source"] in ids and edge["target"] in ids, f"dangling edge {edge}"


def test_onboard_graph_carries_the_retry_edges() -> None:
    edges = {(e["source"], e["target"]) for e in onboard_pipeline_graph("j")["edges"]}
    assert {("validate", "analyze"), ("smoke", "analyze")} <= edges, "the retry loops are part of the real graph"
    assert ("init_onboard", "analyze") in edges and ("write_config", "cleanup") in edges
