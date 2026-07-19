"""Phase-C3: force-all-apps short-circuits affected-set computation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from embry0.workflows.qa.orchestrator import qa_orchestrator_node
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from embry0.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)
from embry0.workspace_providers.fakes import FakeWorkspaceProvider

_QA_YAML_ALWAYS = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
parallelism:
  max_concurrent_apps: 4
qa_required: always
apps:
  hub:
    boot_command: "echo started"
    frontend_url: "http://localhost:3000"
  companion:
    boot_command: "echo started"
    frontend_url: "http://localhost:3001"
  lane:
    boot_command: "echo started"
    frontend_url: "http://localhost:3002"
"""

_QA_YAML_AUTO = _QA_YAML_ALWAYS.replace("qa_required: always", "qa_required: auto")


@pytest.fixture
def fake_provider():
    """3 declared apps; affected set is EMPTY (so a 'normal' run with
    qa_required: auto would short-circuit to apps_to_qa=[])."""
    return FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
            WorkspaceApp("lane", Path("apps/lane"), "@x/lane"),
        ],
        packages=[
            WorkspacePackage("@x/hub", Path("apps/hub"), is_app=True),
            WorkspacePackage("@x/companion", Path("apps/companion"), is_app=True),
            WorkspacePackage("@x/lane", Path("apps/lane"), is_app=True),
        ],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )


@pytest.mark.asyncio
async def test_qa_required_always_runs_all_apps(monkeypatch, fake_provider):
    """qa_required: always in qa.yaml => fan-out runs every declared app
    even when the affected-set is empty."""
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    captured = []

    async def fake_run_subtask(resolved, **kw):
        captured.append(resolved.app_name)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    state = {
        "job_id": "force-always-job",
        "repo": "org/r",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_ALWAYS,
            "head_sha": "abc",
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}
    out = await qa_orchestrator_node(state, config)

    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert sorted(qa["apps_to_qa"]) == ["companion", "hub", "lane"]
    assert sorted(captured) == ["companion", "hub", "lane"]


@pytest.mark.asyncio
async def test_force_all_apps_state_flag_runs_all_apps(monkeypatch, fake_provider):
    """state['qa']['force_all_apps'] = True overrides qa_required: auto and
    forces every declared app to run."""
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    captured = []

    async def fake_run_subtask(resolved, **kw):
        captured.append(resolved.app_name)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    state = {
        "job_id": "force-flag-job",
        "repo": "org/r",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_AUTO,  # NOT 'always'
            "head_sha": "abc",
            "changed_files": [],
            "force_all_apps": True,
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}
    out = await qa_orchestrator_node(state, config)

    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert sorted(qa["apps_to_qa"]) == ["companion", "hub", "lane"]


@pytest.mark.asyncio
async def test_qa_required_auto_with_no_diff_still_short_circuits(monkeypatch, fake_provider):
    """Regression: qa_required: auto + empty affected set still short-circuits
    to 'no apps to QA'. Force-all paths are opt-in, not the default."""
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        raise AssertionError("force-all should not have triggered")

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    state = {
        "job_id": "auto-empty-job",
        "repo": "org/r",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_AUTO,
            "head_sha": "abc",
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}
    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["apps_to_qa"] == []
    assert qa["outcome"]["overall_status"] == "passed"


# -------- Conditional criteria under force_all_apps (EMB-39) --------

_QA_YAML_ALWAYS_CONDITIONAL = (
    _QA_YAML_ALWAYS
    + """
conditional_acceptance_criteria:
  - name: hub-affected
    when:
      affected_apps: ["hub"]
    criteria:
      - "Hub affected check"
"""
)


def _capture(monkeypatch):
    seen: dict[str, list[str]] = {}

    async def fake_run_subtask(resolved, **kw):
        seen[resolved.app_name] = list(resolved.acceptance_criteria)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )
    return seen


@pytest.mark.asyncio
async def test_force_all_apps_recomputes_affected_for_predicates(monkeypatch):
    """force_all_apps skips affected-set computation for app selection, but a
    group predicating on affected_apps triggers an on-demand recompute of the
    diff-derived set."""
    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
            WorkspaceApp("lane", Path("apps/lane"), "@x/lane"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: provider,
    )
    seen = _capture(monkeypatch)

    state = {
        "job_id": "force-cond-1",
        "repo": "org/r",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_ALWAYS_CONDITIONAL,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    # All apps ran (qa_required: always)...
    assert sorted(qa["apps_to_qa"]) == ["companion", "hub", "lane"]
    # ...and the affected-predicate group fired via the on-demand recompute.
    assert "Hub affected check" in seen["hub"]
    assert "Hub affected check" in seen["companion"]  # group has no apps: scope


@pytest.mark.asyncio
async def test_force_all_apps_empty_diff_predicate_groups_off(monkeypatch, fake_provider):
    """force_all_apps + empty diff: every app runs but predicate-gated
    groups stay OFF (default-OFF rule holds under force_all_apps too)."""
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture(monkeypatch)

    state = {
        "job_id": "force-cond-2",
        "repo": "org/r",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_ALWAYS_CONDITIONAL,
            "changed_files": [],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert sorted(qa["apps_to_qa"]) == ["companion", "hub", "lane"]
    for app, criteria in seen.items():
        assert "Hub affected check" not in criteria, app
