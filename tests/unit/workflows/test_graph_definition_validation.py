"""validate_graph_definition — executable-template constraints (RAV-601)."""

from embry0.workflows._validation import validate_graph_definition


def _n(node_id, agent_type):
    return {"node_id": node_id, "agent_type": agent_type}


def _e(src, dst, edge_id=None):
    return {"edge_id": edge_id or f"{src}-{dst}", "source": src, "target": dst, "edge_type": "flow"}


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def test_valid_linear_chain_passes():
    g = _graph(
        [_n("t", "triage"), _n("d", "developer"), _n("r", "reviewer"), _n("q", "qa"), _n("o", "output")],
        [_e("t", "d"), _e("d", "r"), _e("r", "q"), _e("q", "o")],
    )
    assert validate_graph_definition("x", g) == []


def test_empty_graph_rejected():
    assert validate_graph_definition("x", {"nodes": [], "edges": []})


def test_unknown_agent_type_rejected():
    g = _graph([_n("t", "triage"), _n("z", "wizard")], [_e("t", "z")])
    assert any("unknown agent_type" in p for p in validate_graph_definition("x", g))


def test_noncode_agent_types_accepted():
    """research/analysis/ops joined the executable vocabulary (RAV-604)."""
    for agent_type in ("research", "analysis", "ops"):
        g = _graph(
            [_n("t", "triage"), _n("a", agent_type), _n("o", "output")],
            [_e("t", "a"), _e("a", "o")],
        )
        assert validate_graph_definition("x", g) == [], agent_type


def test_cycle_rejected():
    g = _graph([_n("a", "developer"), _n("b", "reviewer")], [_e("a", "b"), _e("b", "a")])
    problems = validate_graph_definition("x", g)
    assert problems  # branching/root/cycle constraints trip


def test_branching_rejected():
    g = _graph(
        [_n("t", "triage"), _n("d", "developer"), _n("r", "reviewer")],
        [_e("t", "d"), _e("t", "r")],
    )
    assert any("branching" in p or "root" in p for p in validate_graph_definition("x", g))


def test_disconnected_rejected():
    g = _graph([_n("t", "triage"), _n("d", "developer"), _n("r", "reviewer")], [_e("t", "d")])
    assert any("root" in p or "disconnected" in p for p in validate_graph_definition("x", g))


def test_triage_not_root_rejected():
    g = _graph([_n("d", "developer"), _n("t", "triage")], [_e("d", "t")])
    assert any("triage" in p for p in validate_graph_definition("x", g))


def test_output_must_be_terminal():
    g = _graph([_n("o", "output"), _n("d", "developer")], [_e("o", "d")])
    assert any("terminal" in p for p in validate_graph_definition("x", g))


def test_triage_only_rejected():
    g = _graph([_n("t", "triage")], [])
    assert any("non-triage" in p for p in validate_graph_definition("x", g))


def test_unknown_edge_endpoint_rejected():
    g = _graph([_n("t", "triage"), _n("d", "developer")], [_e("t", "ghost")])
    assert any("unknown node" in p for p in validate_graph_definition("x", g))


def test_deferred_tools_still_rejected():
    g = _graph([_n("t", "triage"), _n("d", "developer")], [_e("t", "d")])
    g["agent_tools"] = {"developer": ["CreateIssue"]}
    assert any("deferred tool" in p for p in validate_graph_definition("x", g))


def test_custom_agent_row_is_a_valid_node_type():
    """Spec 3.5: an agent_type that names an existing agent_definitions row is executable."""
    g = _graph([_n("t", "triage"), _n("b", "release-bot"), _n("o", "output")], [_e("t", "b"), _e("b", "o")])
    assert validate_graph_definition("x", g, agent_types={"release-bot"}) == []


def test_custom_agent_type_without_a_row_is_unknown():
    g = _graph([_n("t", "triage"), _n("b", "release-bot"), _n("o", "output")], [_e("t", "b"), _e("b", "o")])
    problems = validate_graph_definition("x", g, agent_types={"developer"})
    assert any("unknown agent_type 'release-bot'" in p for p in problems), problems


def test_fixed_node_types_need_no_row():
    """triage/developer/reviewer/review/qa/output are node code, not rows."""
    g = _graph(
        [_n("t", "triage"), _n("d", "developer"), _n("r", "review"), _n("q", "qa"), _n("o", "output")],
        [_e("t", "d"), _e("d", "r"), _e("r", "q"), _e("q", "o")],
    )
    assert validate_graph_definition("x", g, agent_types=set()) == []


def test_default_known_types_are_the_builtin_seed():
    """With no rows passed, the builtin seed's types (research, analysis, ops, ...) are accepted, nothing else."""
    ok = _graph([_n("t", "triage"), _n("a", "research"), _n("o", "output")], [_e("t", "a"), _e("a", "o")])
    assert validate_graph_definition("x", ok) == []
    bad = _graph([_n("t", "triage"), _n("a", "release-bot"), _n("o", "output")], [_e("t", "a"), _e("a", "o")])
    assert validate_graph_definition("x", bad), "a custom type must not pass without its row"
