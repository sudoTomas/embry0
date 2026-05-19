"""End-to-end integration: full QA orchestrator pipeline against a synthetic monorepo.

Stubs:
  - workspace_provider via FakeWorkspaceProvider — declares 3 apps, returns
    all apps as affected (matches Phase 1 contract).
  - sub-task runner — short-circuited via run_subtask monkeypatch to emit
    canned per-app results.
  - qa_app_results repository — AsyncMock; assertions confirm upsert called
    once per app.
  - GitHub aggregate check writer + sticky comment upsert — AsyncMock; assertions
    confirm they're called with the right args.

What's exercised end-to-end:
  - qa.yaml v2 parsing.
  - apps_to_qa resolution via FakeWorkspaceProvider.
  - validate_against_qa_config.
  - fan_out_subtasks running with concurrency cap.
  - overall_status aggregation.
  - persistence to qa_app_results (mocked).
  - GitHub check + PR comment writing (mocked).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from athanor.workflows.qa.orchestrator import qa_orchestrator_node
from athanor.workflows.qa.orchestrator_report import qa_report_node
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from athanor.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)
from athanor.workspace_providers.fakes import FakeWorkspaceProvider

_QA_YAML_V2 = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
parallelism:
  max_concurrent_apps: 2
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


@pytest.mark.asyncio
async def test_e2e_three_apps_two_pass_one_fails(monkeypatch):
    """Phase-1 acceptance gate: 3 affected apps, 1 fails QA, 2 pass.
    Confirms: provider lookup, fan-out, aggregation, persistence, reporting."""
    fake_provider = FakeWorkspaceProvider(
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
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub", "@x/companion", "@x/lane"}),
            cascade_closure=frozenset({"@x/hub", "@x/companion", "@x/lane"}),
            apps_to_qa=frozenset({"@x/hub", "@x/companion", "@x/lane"}),
        ),
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        if resolved.app_name == "companion":
            return SubTaskResult(
                app_name="companion",
                status=SubTaskStatus.QA_FAILURE,
                duration_ms=2200,
                cache_hits=CacheHits(),
                trace_url="https://x/companion-trace.zip",
                failure_summary="customer list did not render",
            )
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1200,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    results_repo = AsyncMock()
    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "issue_number": 42,
        "attempt_number": 1,
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "head_sha": "abc123",
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": results_repo}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]

    # Aggregation: failed (one sub-task failed).
    assert qa["outcome"]["overall_status"] == "failed"
    assert sorted(qa["apps_to_qa"]) == ["hub", "lane", "companion"]
    assert qa["final_status"] == "failed"

    # Persistence: one upsert per app.
    assert results_repo.upsert_with_boot_phase.await_count == 3

    # Per-app results contain the right verdicts.
    by_app = {r["app_name"]: r for r in qa["per_app_results"]}
    assert by_app["companion"]["status"] == "qa_failure"
    assert by_app["hub"]["status"] == "passed"
    assert by_app["lane"]["status"] == "passed"
    assert by_app["companion"]["trace_url"] == "https://x/companion-trace.zip"


@pytest.mark.asyncio
async def test_e2e_qa_report_writes_check_and_comment(monkeypatch):
    """qa_report_node integration: aggregate check + sticky PR comment both
    written via the GitHub API helpers (mocked here)."""
    written_check = AsyncMock(return_value={"id": 1})
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "issue_number": 42,
        "attempt_number": 1,
        "qa": {
            "head_sha": "abc123",
            "outcome": {
                "overall_status": "failed",
                "apps_to_qa": ["hub", "companion"],
                "failure_summary": None,
                "validation_errors": [],
                "validation_warnings": [],
            },
            "apps_to_qa": ["hub", "companion"],
            "validation_warnings": [],
            "per_app_results": [
                {
                    "app_name": "hub",
                    "status": "passed",
                    "duration_ms": 1000,
                    "cache_hits": {
                        "prebaked_image": False,
                        "shared_volume": False,
                        "turbo_remote_hits": [],
                        "turbo_remote_misses": [],
                    },
                    "trace_url": None,
                    "failure_summary": None,
                    "raw_result": {},
                },
                {
                    "app_name": "companion",
                    "status": "qa_failure",
                    "duration_ms": 2000,
                    "cache_hits": {
                        "prebaked_image": False,
                        "shared_volume": False,
                        "turbo_remote_hits": [],
                        "turbo_remote_misses": [],
                    },
                    "trace_url": "https://x/trace.zip",
                    "failure_summary": "loads page did not render",
                    "raw_result": {},
                },
            ],
        },
    }
    config = {"configurable": {"github_token": "tok"}}

    await qa_report_node(state, config)

    # Aggregate check written
    written_check.assert_awaited_once()
    check_kwargs = written_check.await_args.kwargs
    assert check_kwargs["repo"] == "org/repo"
    assert check_kwargs["head_sha"] == "abc123"
    assert check_kwargs["overall_status"] == "failed"

    # Sticky comment written
    written_comment.assert_awaited_once()
    comment_kwargs = written_comment.await_args.kwargs
    assert comment_kwargs["repo"] == "org/repo"
    assert comment_kwargs["issue_number"] == 42
    body = comment_kwargs["body"]
    assert "Status: Failed" in body
    assert "companion" in body
    assert "hub" in body


@pytest.mark.asyncio
async def test_e2e_no_affected_apps_short_circuits_pass(monkeypatch):
    """Skipped path: 0 affected apps yields overall_status=passed with empty list,
    no upsert, no fan-out."""
    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        raise AssertionError("no fan-out should happen for empty apps_to_qa")

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    results_repo = AsyncMock()
    state = {
        "job_id": "run-skip",
        "repo": "org/repo",
        "branch_name": "main",
        "issue_number": 1,
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "head_sha": "abc",
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": results_repo}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["apps_to_qa"] == []
    assert qa["final_status"] == "passed"
    assert results_repo.upsert_with_boot_phase.await_count == 0


@pytest.mark.asyncio
async def test_e2e_validation_error_blocks_fan_out(monkeypatch):
    """An app declared in qa.yaml that doesn't exist in workspace yields
    overall_status=infra_error WITHOUT any sub-task fan-out."""
    fake_provider = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=[
            "error: app 'hub' declared in qa.yaml apps: but not found in workspace",
            "error: app 'companion' declared in qa.yaml apps: but not found in workspace",
            "error: app 'lane' declared in qa.yaml apps: but not found in workspace",
        ],
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(*a, **kw):
        raise AssertionError("fan-out should not happen on validation error")

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    state = {
        "job_id": "run-bad",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "head_sha": "abc",
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "infra_error"
    assert len(qa["outcome"]["validation_errors"]) == 3
    assert qa["final_status"] == "failed"


@pytest.mark.asyncio
async def test_e2e_single_app_passes(monkeypatch):
    """Migrated-from-v1 N=1 case: one app declared in qa.yaml v2, passes.
    This proves the multi-app graph correctly handles the legacy single-app
    contract that `athanor migrate-qa-config` produces."""
    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("app", Path("apps/app"), "@x/app")],
        packages=[
            WorkspacePackage("@x/app", Path("apps/app"), is_app=True),
        ],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/app"}),
            cascade_closure=frozenset({"@x/app"}),
            apps_to_qa=frozenset({"@x/app"}),
        ),
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1100,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    qa_yaml = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
apps:
  app:
    boot_command: "npm run dev"
    frontend_url: "http://localhost:3000"
"""

    results_repo = AsyncMock()
    state = {
        "job_id": "single-app-run",
        "repo": "org/repo",
        "branch_name": "main",
        "issue_number": 7,
        "attempt_number": 1,
        "qa": {
            "qa_yaml_v2_raw": qa_yaml,
            "head_sha": "abc",
            "changed_files": ["apps/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": results_repo}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["apps_to_qa"] == ["app"]
    assert qa["final_status"] == "passed"
    assert results_repo.upsert_with_boot_phase.await_count == 1


@pytest.mark.asyncio
async def test_phase_3_acceptance_real_provider_one_app_affected(monkeypatch):
    """Phase-3 acceptance gate.

    Wires the REAL NpmWorkspacesTurboProvider against the committed
    toy-monorepo fixture. The orchestrator skips its bootstrap-sandbox
    code path (init_orchestrator_node) by jumping straight into
    qa_orchestrator_node with state['repo_root'] pointing at the fixture
    on disk and state['qa']['changed_files'] preset to the toy diff.

    Asserts:
      - apps_to_qa = ['hub']  (only @toy/hub is affected by an apps/hub diff)
      - per_app_results contains exactly one entry, for hub
      - overall_status = 'passed' (mocked sub-task always passes)
    """
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "toy-monorepo"

    # Real provider is loaded via the registry — DO NOT monkeypatch load_provider.
    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=900,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    qa_yaml = (fixture / ".athanor" / "qa.yaml").read_text()

    results_repo = AsyncMock()
    state = {
        "job_id": "phase-3-acceptance",
        "repo": "org/toy-monorepo",
        "branch_name": "feature/touch-hub",
        "base_branch": "main",
        "issue_number": 1,
        "repo_root": str(fixture),  # Point provider at the on-disk fixture
        "qa": {
            "qa_yaml_v2_raw": qa_yaml,
            "head_sha": "abc",
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": results_repo}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]

    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["apps_to_qa"] == ["hub"]
    assert len(qa["per_app_results"]) == 1
    assert qa["per_app_results"][0]["app_name"] == "hub"


@pytest.mark.asyncio
async def test_phase_3_acceptance_no_cascade_blocks_all_apps(monkeypatch):
    """Phase-3 acceptance: changed file is in a no_cascade package → 0 apps QA'd.

    Tests against the real provider + real toy-monorepo fixture. With the
    diff confined to packages/types/ and qa.yaml declaring @toy/types as
    no_cascade, the orchestrator must short-circuit through "no affected
    apps" → overall_status=passed, apps_to_qa=[], no fan-out."""
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "toy-monorepo"
    qa_yaml = (fixture / ".athanor" / "qa.yaml").read_text()

    async def fake_run_subtask(resolved, **kw):
        raise AssertionError("no fan-out should happen with @toy/types-only diff")

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    state = {
        "job_id": "phase-3-no-cascade",
        "repo": "org/toy-monorepo",
        "branch_name": "feature/types-tweak",
        "base_branch": "main",
        "repo_root": str(fixture),
        "qa": {
            "qa_yaml_v2_raw": qa_yaml,
            "head_sha": "abc",
            "changed_files": ["packages/types/src/index.ts"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["apps_to_qa"] == []


@pytest.mark.asyncio
async def test_phase_3_acceptance_package_change_cascades_to_two_apps(monkeypatch):
    """Phase-3 acceptance: @toy/auth diff → @toy/hub + @toy/companion QA'd."""
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "toy-monorepo"
    qa_yaml = (fixture / ".athanor" / "qa.yaml").read_text()

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=900,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    results_repo = AsyncMock()
    state = {
        "job_id": "phase-3-cascade",
        "repo": "org/toy-monorepo",
        "branch_name": "feature/auth-tweak",
        "base_branch": "main",
        "repo_root": str(fixture),
        "qa": {
            "qa_yaml_v2_raw": qa_yaml,
            "head_sha": "abc",
            "changed_files": ["packages/auth/src/index.ts"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": results_repo}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert sorted(qa["apps_to_qa"]) == ["hub", "companion"]
    assert results_repo.upsert_with_boot_phase.await_count == 2
