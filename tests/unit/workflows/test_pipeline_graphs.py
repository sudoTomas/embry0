"""Static pipeline graphs for the jobs whose graph is not planned by triage (spec 3.8 / #40)."""

from __future__ import annotations

from embry0.workflows.pipeline_graphs import onboard_pipeline_graph, qa_pipeline_graph


def _pr_review_graph(job_id: str):
    import embry0.workflows.pipeline_graphs as graphs

    builder = getattr(graphs, "pr_review_pipeline_graph", None)
    assert builder is not None, "there is no static graph for pr-review jobs, so their page waits for one forever"
    return builder(job_id)


def _compiled_node_names(workflow) -> set[str]:
    return {n for n in workflow.compile().get_graph().nodes if not n.startswith("__")}


def test_onboard_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.onboard.graph import OnboardWorkflow

    graph = onboard_pipeline_graph("job-1")
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(OnboardWorkflow()), (
        "the static graph must list the LangGraph nodes, no more, no less"
    )
    assert all(n["physical_node"] == n["node_id"] for n in graph["nodes"]), (
        "each onboard graph node is lit by the LangGraph node of the same name"
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


def test_every_onboard_node_emits_its_own_node_events() -> None:
    """A node that never emits stays ``pending`` on the map after the job ends
    (the terminal override only flips ``running`` nodes), so pin emission per id."""
    from pathlib import Path

    import embry0.workflows.onboard.nodes as onboard_nodes
    from embry0.workflows.pipeline_graphs import ONBOARD_NODE_IDS

    source = Path(onboard_nodes.__file__).read_text()
    for node_id, _agent, _label in ONBOARD_NODE_IDS:
        assert f'node_started(node="{node_id}"' in source, f"onboard node {node_id!r} never emits node_started"
        assert f'node_completed(node="{node_id}"' in source, f"onboard node {node_id!r} never emits node_completed"


def test_every_qa_node_emits_its_own_node_events() -> None:
    from pathlib import Path

    import embry0.workflows.qa.orchestrator as qa_orchestrator
    import embry0.workflows.qa.orchestrator_report as qa_report
    from embry0.workflows.pipeline_graphs import QA_NODE_IDS

    source = Path(qa_orchestrator.__file__).read_text() + Path(qa_report.__file__).read_text()
    for node_id, _agent, _label in QA_NODE_IDS:
        assert f'node_started(node="{node_id}"' in source, f"QA node {node_id!r} never emits node_started"
        assert f'node_completed(node="{node_id}"' in source, f"QA node {node_id!r} never emits node_completed"


def test_onboard_graph_carries_the_retry_edges() -> None:
    edges = {(e["source"], e["target"]) for e in onboard_pipeline_graph("j")["edges"]}
    assert {("validate", "analyze"), ("smoke", "analyze")} <= edges, "the retry loops are part of the real graph"
    assert ("init_onboard", "analyze") in edges and ("write_config", "cleanup") in edges


def test_pr_review_graph_names_exactly_the_compiled_nodes() -> None:
    from embry0.workflows.pr_review.graph import PrReviewWorkflow

    graph = _pr_review_graph("job-3")
    assert [n["node_id"] for n in graph["nodes"]] == ["init_pr_review", "review", "publish", "cleanup"]
    assert {n["node_id"] for n in graph["nodes"]} == _compiled_node_names(PrReviewWorkflow())
    assert graph["graph_id"] == "pr-review-job-3"
    assert set(graph) >= {"graph_id", "name", "description", "nodes", "edges", "metadata"}
    ids = {n["node_id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])


def test_every_pr_review_node_emits_its_own_node_events() -> None:
    from pathlib import Path

    import embry0.workflows.pipeline_graphs as graphs
    import embry0.workflows.pr_review.nodes as pr_review_nodes

    source = Path(pr_review_nodes.__file__).read_text()
    for node_id, _agent, _label in graphs.PR_REVIEW_NODE_IDS:
        assert f'node_started(node="{node_id}"' in source, f"pr-review node {node_id!r} never emits node_started"
        assert f'node_completed(node="{node_id}"' in source, f"pr-review node {node_id!r} never emits node_completed"
