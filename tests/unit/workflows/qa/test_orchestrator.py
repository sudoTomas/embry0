from pathlib import Path

import pytest

from athanor.workflows.qa.orchestrator import (
    OrchestratorContext,
    OrchestratorOutcome,
    resolve_apps_to_qa,
    validate_against_qa_config,
)
from athanor.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2
from athanor.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)
from athanor.workspace_providers.fakes import FakeWorkspaceProvider


_QA_YAML = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000"
  companion:
    boot_command: "y"
    frontend_url: "http://localhost:3001"
packages:
  "@x/types":
    no_cascade: true
"""


def test_resolve_apps_to_qa_uses_provider_and_intersects_with_qa_yaml():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[
            WorkspacePackage("@x/hub", Path("apps/hub"), is_app=True),
            WorkspacePackage("@x/companion", Path("apps/companion"), is_app=True),
        ],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    apps = resolve_apps_to_qa(provider, cfg, changed_files=[Path("apps/hub/app/page.tsx")])
    assert apps == ["hub"]


def test_resolve_apps_to_qa_passes_no_cascade_packages_to_provider():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset({"@x/hub"})),
    )
    resolve_apps_to_qa(provider, cfg, changed_files=[])
    assert provider.affected_calls[0].no_cascade_packages == frozenset({"@x/types"})


def test_resolve_apps_to_qa_filters_out_apps_not_in_qa_yaml():
    """Provider may know about an app the user hasn't declared in qa.yaml.
    Such apps are silently skipped (they appear as warnings via validate)."""
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("ghost", Path("apps/ghost"), "@x/ghost"),
        ],
        packages=[],
        affected_result=AffectedSet(
            frozenset(), frozenset(), frozenset({"@x/hub", "@x/ghost"})
        ),
    )
    apps = resolve_apps_to_qa(provider, cfg, changed_files=[])
    assert apps == ["hub"]  # ghost dropped


def test_validate_against_qa_config_fails_fast_on_unknown_app():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=["error: app 'companion' declared in qa.yaml apps: but not found in workspace"],
    )
    errors, warnings = validate_against_qa_config(provider, cfg)
    assert any("companion" in e for e in errors)
    assert warnings == []


def test_validate_against_qa_config_separates_warnings_from_errors():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=[
            "warning: app 'lane' present in workspace but missing from qa.yaml apps:",
            "error: app 'hub' declared in qa.yaml apps: but not found in workspace",
        ],
    )
    errors, warnings = validate_against_qa_config(provider, cfg)
    assert len(errors) == 1
    assert len(warnings) == 1


import asyncio

from athanor.workflows.qa.orchestrator import fan_out_subtasks
from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.qa_yaml_v2 import QAReadyCheck
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)


def _resolved(name: str) -> ResolvedAppConfig:
    return ResolvedAppConfig(
        app_name=name,
        boot_command="x",
        frontend_url="http://localhost:3000",
        mode="process",
        sandbox_profile="slim",
        ready_checks=[QAReadyCheck(http="http://x")],
        boot_timeout_seconds=10,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["loads"],
    )


@pytest.mark.asyncio
async def test_fan_out_respects_max_concurrent_apps(monkeypatch):
    """At any moment no more than max_concurrent_apps sub-tasks should be
    in-flight. Track in-flight count as sub-tasks acquire/release."""
    in_flight = 0
    peak = 0

    async def fake_run_subtask(resolved, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=50,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", fake_run_subtask)

    resolved_configs = [_resolved(f"app{i}") for i in range(8)]
    results = await fan_out_subtasks(
        resolved_configs,
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        max_concurrent=3,
        config={},
    )
    assert len(results) == 8
    assert peak <= 3


@pytest.mark.asyncio
async def test_fan_out_collects_all_results_in_input_order(monkeypatch):
    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )
    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", fake_run_subtask)

    resolved_configs = [_resolved("hub"), _resolved("companion"), _resolved("lane")]
    results = await fan_out_subtasks(
        resolved_configs,
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        max_concurrent=4,
        config={},
    )
    assert [r.app_name for r in results] == ["hub", "companion", "lane"]


@pytest.mark.asyncio
async def test_fan_out_isolates_individual_subtask_crashes(monkeypatch):
    """A single sub-task raising should not poison sibling sub-tasks."""
    async def fake_run_subtask(resolved, **kw):
        if resolved.app_name == "companion":
            raise RuntimeError("simulated crash")
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=5,
            cache_hits=CacheHits(),
        )
    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", fake_run_subtask)

    results = await fan_out_subtasks(
        [_resolved("hub"), _resolved("companion"), _resolved("lane")],
        parent_run_id="run-1",
        repo="org/repo",
        branch_name="main",
        max_concurrent=2,
        config={},
    )
    by_app = {r.app_name: r for r in results}
    assert by_app["hub"].status == SubTaskStatus.PASSED
    assert by_app["lane"].status == SubTaskStatus.PASSED
    assert by_app["companion"].status == SubTaskStatus.INFRA_FAILURE
    assert "simulated crash" in (by_app["companion"].failure_summary or "")
