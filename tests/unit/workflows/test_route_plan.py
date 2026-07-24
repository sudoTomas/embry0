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
    # RAV-604: one default route per non-code kind.
    assert [s["agent_type"] for s in _plan("research-default")] == ["research", "output"]
    assert [s["agent_type"] for s in _plan("analysis-default")] == ["analysis", "output"]
    assert [s["agent_type"] for s in _plan("ops-default")] == ["ops", "output"]


def test_builtin_seeds_cover_every_kind_exactly_once():
    """Each JobKind has exactly one builtin default template (RAV-604)."""
    from embry0.orchestration.state import JobKind

    defaults = [t.get("default_for_kind") for t in BUILTIN_PIPELINE_TEMPLATES.values() if t.get("default_for_kind")]
    assert sorted(defaults) == sorted(k.value for k in JobKind)


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


def test_generic_steps_dispatch_to_agent():
    """research/analysis/ops steps all resolve to the 'agent' route key (RAV-604)."""
    for name in ("research-default", "analysis-default", "ops-default"):
        s = {"route_plan": _plan(name), "route_cursor": 0, "qa": {}}
        assert next_route(s) == "agent"
        s.update(advance(s))
        assert next_route(s) == "output"
        s.update(advance(s))
        assert next_route(s) == "end"


def test_cursor_at_finds_first_step_of_type():
    s = _state()
    assert cursor_at(s, "developer") == 0
    assert cursor_at(s, "qa") == 2
    assert cursor_at(s, "missing") == 0


# ---- step_template_config (RAV-602) ---------------------------------------


def test_step_template_config_maps_current_step():
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    state = {
        "route_plan": [
            {"agent_type": "developer", "node_id": "d", "config": {"tools": ["Read"], "model": "claude-haiku-4-5"}},
            {"agent_type": "reviewer", "node_id": "r", "config": {"skills": ["s1"]}},
        ],
        "route_cursor": 0,
    }
    cfg = step_template_config(state, "developer")
    assert cfg == {"agent_models": {"developer": "claude-haiku-4-5"}, "agent_tools": {"developer": ["Read"]}}
    # Wrong agent for the current step → None
    assert step_template_config(state, "review") is None
    # Reviewer step maps template type "reviewer" → runtime type "review"
    state["route_cursor"] = 1
    assert step_template_config(state, "review") == {"agent_skills": {"review": ["s1"]}}


def test_step_template_config_empty_or_out_of_range():
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    assert step_template_config({}, "developer") is None
    state = {"route_plan": [{"agent_type": "developer", "node_id": "d", "config": {}}], "route_cursor": 5}
    assert step_template_config(state, "developer") is None
    state["route_cursor"] = 0
    assert step_template_config(state, "developer") is None  # empty config → None


def test_step_template_config_generic_agents_use_own_type():
    """Non-code steps: template agent_type IS the runtime type (RAV-604)."""
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    state = {
        "route_plan": [{"agent_type": "research", "node_id": "r", "config": {"model": "claude-opus-4-7"}}],
        "route_cursor": 0,
    }
    assert step_template_config(state, "research") == {"agent_models": {"research": "claude-opus-4-7"}}
    assert step_template_config(state, "developer") is None
