"""Route-plan linearization + dispatcher (RAV-601)."""

import pytest

from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.route_plan import (
    LEGACY_ROUTE_PLAN,
    advance,
    build_route_plan,
    cursor_at,
    next_route,
)


def _plan(name: str):
    return build_route_plan(BUILTIN_PIPELINE_TEMPLATES[name]["graph_definition"])


# ---- linearization ---------------------------------------------------------


def test_builtin_seeds_linearize():
    assert [s["agent_type"] for s in _plan("quick-fix")] == ["developer"]
    assert [s["agent_type"] for s in _plan("review-only")] == ["reviewer"]
    assert [s["agent_type"] for s in _plan("full-magnum-opus")] == ["developer", "reviewer", "qa", "output"]


def test_builtin_seeds_validate():
    for name, t in BUILTIN_PIPELINE_TEMPLATES.items():
        assert validate_graph_definition(name, t["graph_definition"]) == []


def test_triage_node_dropped_from_plan():
    plan = _plan("quick-fix")
    assert all(s["agent_type"] != "triage" for s in plan)


def test_legacy_fallback_matches_todays_chain():
    assert [s["agent_type"] for s in LEGACY_ROUTE_PLAN] == ["developer", "reviewer", "qa"]


def test_cycle_raises():
    graph = {
        "nodes": [
            {"node_id": "a", "agent_type": "developer"},
            {"node_id": "b", "agent_type": "reviewer"},
        ],
        "edges": [
            {"edge_id": "e1", "source": "a", "target": "b", "edge_type": "flow"},
            {"edge_id": "e2", "source": "b", "target": "a", "edge_type": "flow"},
        ],
    }
    with pytest.raises(ValueError, match="root|cycle"):
        build_route_plan(graph)


# ---- dispatcher ------------------------------------------------------------


def _state(plan_name="full-magnum-opus", cursor=0, needs_qa=True):
    return {"route_plan": _plan(plan_name), "route_cursor": cursor, "qa": {"needs_qa": needs_qa}}


def test_dispatch_walks_full_plan_with_qa():
    s = _state()
    assert next_route(s) == "developer"
    s.update(advance(s))
    assert next_route(s) == "review"
    s.update(advance(s))
    assert next_route(s) == "qa"
    s.update(advance(s))
    assert next_route(s) == "output"
    s.update(advance(s))
    assert next_route(s) == "end"


def test_qa_step_skipped_when_not_needed():
    s = _state(cursor=2, needs_qa=False)  # cursor at the qa step
    assert next_route(s) == "output"


def test_advance_hops_skipped_qa_steps():
    s = _state(cursor=2, needs_qa=False)
    s.update(advance(s))  # advancing "past" the skipped qa lands past output? no — past qa+1
    # cursor was 2 (qa, skipped) → advance lands at 4 only if output executed;
    # here advance() skips qa (2→3) then +1 = 4 → end
    assert next_route(s) == "end"


def test_empty_plan_dispatches_end():
    assert next_route({"route_plan": [], "route_cursor": 0}) == "end"
    assert next_route({}) == "end"


def test_past_end_dispatches_end():
    assert next_route(_state(cursor=99)) == "end"


def test_cursor_at_finds_first_step_of_type():
    s = _state()
    assert cursor_at(s, "developer") == 0
    assert cursor_at(s, "qa") == 2
    assert cursor_at(s, "missing") == 0
