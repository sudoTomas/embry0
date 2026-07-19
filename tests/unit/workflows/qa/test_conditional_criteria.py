"""Tests for the conditional-criteria evaluator (EMB-39)."""

from __future__ import annotations

from embry0.workflows.qa.conditional_criteria import evaluate_conditional_criteria
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

_BASE = (
    "version: 2\n"
    "workspace_provider:\n  type: npm-workspaces-turbo\n"
    "defaults:\n  mode: process\n  sandbox_profile: slim\n"
    "  ready_checks: [{http: 'http://x'}]\n"
    "apps:\n"
    "  quoting:\n    boot_command: 'npm start'\n    frontend_url: 'http://localhost:3000'\n"
    "  hub:\n    boot_command: 'npm start'\n    frontend_url: 'http://localhost:3001'\n"
    "  live:\n    target: deployed\n    frontend_url: 'https://live.example.com'\n"
    "    ready_checks: [{http: 'https://live.example.com'}]\n"
)


def _cfg(conditional_yaml: str):
    return parse_qa_yaml_v2(_BASE + conditional_yaml)


PRICING_GROUP = (
    "conditional_acceptance_criteria:\n"
    "  - name: pricing\n"
    "    when: {changed_paths: ['platform/**/pricing/**']}\n"
    "    criteria: ['Exercise Price Now']\n"
)


def test_changed_paths_or_within_list():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g\n"
        "    when: {changed_paths: ['a/**', 'b/**']}\n"
        "    criteria: ['c1']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["b/x.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
    )
    assert ev.matched_groups == ["g"]
    assert ev.criteria_by_app == {"quoting": ["c1"]}


def test_fields_and_between():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g\n"
        "    when: {changed_paths: ['a/**'], labels: ['qa:deep']}\n"
        "    criteria: ['c1']\n"
    )
    # path matches but label missing -> AND fails
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
        labels=[],
    )
    assert ev.matched_groups == []
    # both present -> fires
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
        labels=["QA:Deep"],  # case-insensitive
    )
    assert ev.matched_groups == ["g"]


def test_groups_independent_union_dedup():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g1\n    when: {changed_paths: ['a/**']}\n    criteria: ['shared', 'one']\n"
        "  - name: g2\n    when: {changed_paths: ['b/**']}\n    criteria: ['shared', 'two']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py", "b/y.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
    )
    assert ev.matched_groups == ["g1", "g2"]
    assert ev.criteria_by_app == {"quoting": ["shared", "one", "two"]}


def test_app_scoping():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g\n    when: {changed_paths: ['a/**']}\n    criteria: ['c1']\n"
        "    apps: ['hub']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py"],
        apps_to_qa=["quoting", "hub"],
        affected_apps_from_diff=[],
    )
    assert ev.criteria_by_app == {"hub": ["c1"]}
    assert ev.group_apps == {"g": ["hub"]}


def test_scoped_app_not_in_run_appends_nothing():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g\n    when: {changed_paths: ['a/**']}\n    criteria: ['c1']\n"
        "    apps: ['hub']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
    )
    assert ev.matched_groups == ["g"]
    assert ev.criteria_by_app == {}
    assert ev.group_apps == {"g": []}


def test_empty_diff_predicate_groups_off():
    """Deployed/standalone default-OFF rule (EMB-39)."""
    cfg = _cfg(PRICING_GROUP)
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=[],
        apps_to_qa=["live"],
        affected_apps_from_diff=[],
    )
    assert ev.matched_groups == []
    assert ev.forced_groups == []
    assert ev.criteria_by_app == {}


def test_labels_only_group_fires_on_empty_diff():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n  - name: deep\n    when: {labels: ['qa:deep']}\n    criteria: ['c1']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=[],
        apps_to_qa=["live"],
        affected_apps_from_diff=[],
        labels=["qa:deep"],
    )
    assert ev.matched_groups == ["deep"]
    assert ev.criteria_by_app == {"live": ["c1"]}


def test_affected_apps_predicate():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n  - name: g\n    when: {affected_apps: ['hub']}\n    criteria: ['c1']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["whatever.py"],
        apps_to_qa=["hub", "live"],
        affected_apps_from_diff=["hub"],
    )
    assert ev.matched_groups == ["g"]
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["whatever.py"],
        apps_to_qa=["hub", "live"],
        affected_apps_from_diff=["quoting"],
    )
    assert ev.matched_groups == []


def test_forced_bypasses_predicates_on_empty_diff():
    cfg = _cfg(PRICING_GROUP)
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=[],
        apps_to_qa=["live"],
        affected_apps_from_diff=[],
        forced_group_names=["pricing"],
    )
    assert ev.forced_groups == ["pricing"]
    assert ev.matched_groups == []
    assert ev.criteria_by_app == {"live": ["Exercise Price Now"]}


def test_forced_and_matched_reports_as_forced():
    cfg = _cfg(PRICING_GROUP)
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["platform/api/pricing/rate.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
        forced_group_names=["pricing"],
    )
    assert ev.forced_groups == ["pricing"]
    assert ev.matched_groups == []


def test_star_forces_all_groups():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g1\n    when: {changed_paths: ['a/**']}\n    criteria: ['c1']\n"
        "  - name: g2\n    when: {changed_paths: ['b/**']}\n    criteria: ['c2']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=[],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
        forced_group_names=["*"],
    )
    assert ev.forced_groups == ["g1", "g2"]
    assert ev.criteria_by_app == {"quoting": ["c1", "c2"]}


def test_unknown_forced_group_reported():
    cfg = _cfg(PRICING_GROUP)
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=[],
        apps_to_qa=["live"],
        affected_apps_from_diff=[],
        forced_group_names=["pricign", "pricing"],
    )
    assert ev.unknown_forced_groups == ["pricign"]
    assert ev.forced_groups == ["pricing"]


def test_no_groups_noop():
    cfg = _cfg("")
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a.py"],
        apps_to_qa=["quoting"],
        affected_apps_from_diff=[],
        forced_group_names=[],
    )
    assert ev.criteria_by_app == {}
    assert ev.groups_persisted() == []


def test_groups_persisted_shape():
    cfg = _cfg(
        "conditional_acceptance_criteria:\n"
        "  - name: g1\n    when: {changed_paths: ['a/**']}\n    criteria: ['c1']\n"
        "  - name: g2\n    when: {changed_paths: ['b/**']}\n    criteria: ['c2']\n"
    )
    ev = evaluate_conditional_criteria(
        cfg,
        changed_files=["a/x.py"],
        apps_to_qa=["quoting", "hub"],
        affected_apps_from_diff=[],
        forced_group_names=["g2"],
    )
    assert ev.groups_persisted() == [
        {"name": "g2", "source": "forced", "apps": ["hub", "quoting"]},
        {"name": "g1", "source": "matched", "apps": ["hub", "quoting"]},
    ]
