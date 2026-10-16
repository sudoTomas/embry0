from pathlib import Path

import pytest

from embry0.workflows.qa.orchestrator_helpers import (
    resolve_apps_to_qa,
    validate_against_qa_config,
)
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2
from embry0.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)
from embry0.workspace_providers.fakes import FakeWorkspaceProvider

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
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset({"@x/hub", "@x/ghost"})),
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


import asyncio  # noqa: E402

from embry0.workflows.qa.orchestrator_helpers import fan_out_subtasks  # noqa: E402
from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig  # noqa: E402
from embry0.workflows.qa.qa_yaml_v2 import QAReadyCheck  # noqa: E402
from embry0.workflows.qa.subtask_result_schema import (  # noqa: E402
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

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

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

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

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

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

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


# ── Task 22: init_orchestrator_node + qa_orchestrator_node ──────────────────

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from embry0.workflows.qa.orchestrator import (  # noqa: E402
    init_orchestrator_node,
    qa_orchestrator_node,
)

_QA_YAML_V2 = """
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


@pytest.mark.asyncio
async def test_orchestrator_node_happy_path_two_apps(monkeypatch, tmp_path):
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import (
        AffectedSet,
        WorkspaceApp,
    )
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub", "@x/companion"}),
            cascade_closure=frozenset({"@x/hub", "@x/companion"}),
            apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    repo_mock = AsyncMock()
    repo_mock.upsert_with_boot_phase = AsyncMock()

    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": repo_mock}}

    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert sorted(qa["apps_to_qa"]) == ["companion", "hub"]
    # Phase 5A: orchestrator routes through upsert_with_boot_phase so the
    # boot_phase JSONB column is populated. Each call carries boot_phase=None
    # for the passed-boot path here (sub-tasks above are mocked PASSED).
    assert repo_mock.upsert_with_boot_phase.await_count == 2
    for call in repo_mock.upsert_with_boot_phase.await_args_list:
        assert call.kwargs["boot_phase"] is None
    assert qa["final_status"] == "passed"


@pytest.mark.asyncio
async def test_orchestrator_node_aggregates_failure_summary_on_outcome(monkeypatch):
    """RAV-1249 regression (job-54b89ff5c429): the run-level outcome must
    carry the failing apps' summaries — triage's qa_failure_block reads
    outcome.failure_summary at re-entry, and without the aggregation it
    rendered "no failure summary recorded" and triage decided blind."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub", "@x/companion"}),
            cascade_closure=frozenset({"@x/hub", "@x/companion"}),
            apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        if resolved.app_name == "hub":
            return SubTaskResult(
                app_name="hub",
                status=SubTaskStatus.QA_FAILURE,
                duration_ms=10,
                cache_hits=CacheHits(),
                failure_summary="criterion 'Deals placeholder copy' failed: page shows lorem ipsum",
            )
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }

    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "failed"
    assert qa["final_status"] == "failed"
    summary = qa["outcome"]["failure_summary"]
    assert summary is not None
    assert "[hub] qa_failure:" in summary
    assert "criterion 'Deals placeholder copy' failed: page shows lorem ipsum" in summary
    # The passing app must not pollute the summary.
    assert "companion" not in summary


@pytest.mark.asyncio
async def test_orchestrator_node_failed_subtask_without_summary_still_notes_app(monkeypatch):
    """A failed app whose sub-task recorded no failure_summary still appears
    in the aggregate (with an explicit placeholder), so triage sees WHICH app
    failed even when the harness lost the details."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.BOOT_FAILURE,
            duration_ms=10,
            cache_hits=CacheHits(),
            failure_summary=None,
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }

    out = await qa_orchestrator_node(state, {"configurable": {}})
    summary = out["qa"]["outcome"]["failure_summary"]
    assert summary is not None
    assert "[hub] boot_failure:" in summary
    assert "no per-app failure summary recorded" in summary


@pytest.mark.asyncio
async def test_orchestrator_node_short_circuits_when_no_apps_affected(monkeypatch):
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    repo_mock = AsyncMock()
    repo_mock.upsert_with_boot_phase = AsyncMock()
    state = {
        "job_id": "run-2",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": repo_mock}}
    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    assert out["qa"]["apps_to_qa"] == []
    assert repo_mock.upsert_with_boot_phase.await_count == 0


@pytest.mark.asyncio
async def test_orchestrator_node_validation_errors_block_fan_out(monkeypatch):
    from embry0.workspace_providers import AffectedSet
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=[
            "error: app 'hub' declared in qa.yaml apps: but not found in workspace",
            "error: app 'companion' declared in qa.yaml apps: but not found in workspace",
        ],
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def crash_subtask(*a, **kw):
        raise AssertionError("fan-out should not have happened")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", crash_subtask)

    state = {
        "job_id": "run-3",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": [],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}
    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "infra_error"
    assert len(qa["outcome"]["validation_errors"]) == 2
    assert qa["final_status"] == "failed"


@pytest.mark.asyncio
async def test_qa_orchestrator_short_circuits_on_init_failure(monkeypatch):
    """If init_orchestrator_node already wrote outcome=infra_error, the
    orchestrator passes it through unchanged."""
    state = {
        "job_id": "run-5",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "outcome": {
                "overall_status": "infra_error",
                "apps_to_qa": [],
                "failure_summary": "bootstrap failed",
                "validation_errors": [],
                "validation_warnings": [],
            },
        },
    }
    out = await qa_orchestrator_node(state, {})
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"


_QA_YAML_NEVER = """
version: 2
qa_required: never
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
"""


@pytest.mark.asyncio
async def test_orchestrator_node_qa_required_never_short_circuits(monkeypatch):
    """qa_required: never short-circuits before workspace_provider load,
    returning overall_status=passed with no apps fanned out."""

    async def crash_subtask(*a, **kw):
        raise AssertionError("fan-out should not have happened when qa_required=never")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", crash_subtask)

    # load_provider should also never be called
    def crash_load_provider(*a, **kw):
        raise AssertionError("load_provider should not be called when qa_required=never")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", crash_load_provider)

    state = {
        "job_id": "run-never",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_NEVER,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}
    out = await qa_orchestrator_node(state, config)

    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["apps_to_qa"] == []
    assert qa["per_app_results"] == []
    assert qa["final_status"] == "passed"


# ── B5: prebaked image tag flows through orchestrator → fan_out → run_subtask ─


_QA_YAML_WITH_CACHE = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
cache:
  prebaked_image:
    enabled: true
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000"
packages: {}
"""


@pytest.mark.asyncio
async def test_orchestrator_passes_image_tag_to_run_subtask_when_repo_configured(monkeypatch):
    """When qa_image_tags_repo is in configurable and cache.prebaked_image.enabled=True,
    the looked-up image_tag is forwarded to run_subtask via fan_out_subtasks."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import (
        AffectedSet,
        WorkspaceApp,
    )
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    # Fake image tags repo that returns a known tag.
    fake_image_repo = AsyncMock()

    class _FakeRow:
        # Shape of QAImageTagRow as selection reads it. No recorded base
        # digest (#334) = "unknown", which is still used.
        repo = "acme/hub"
        image_tag = "acme/hub:sha-deadbeef"
        base_image = None
        base_image_digest = None

    fake_image_repo.get_active = AsyncMock(return_value=_FakeRow())

    # Capture kwargs passed to run_subtask.
    captured_kwargs: dict = {}

    async def spy_run_subtask(resolved, **kw):
        captured_kwargs.update(kw)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", spy_run_subtask)

    state = {
        "job_id": "run-b5",
        "repo": "org/monorepo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_WITH_CACHE,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": AsyncMock(),
            "qa_image_tags_repo": fake_image_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)

    assert out["qa"]["outcome"]["overall_status"] == "passed"
    assert captured_kwargs.get("prebaked_image_tag") == "acme/hub:sha-deadbeef"
    fake_image_repo.get_active.assert_awaited_once_with("org/monorepo")


# ── E1: cache flag gating ────────────────────────────────────────────────────

_QA_YAML_CACHE_DISABLED = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
cache:
  prebaked_image:
    enabled: false
  shared_volume:
    enabled: false
  turbo_remote:
    enabled: false
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000"
packages: {}
"""

_QA_YAML_CACHE_ENABLED = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
cache:
  prebaked_image:
    enabled: true
  shared_volume:
    enabled: true
    scope: per-job
  turbo_remote:
    enabled: true
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000"
packages: {}
"""


@pytest.mark.asyncio
async def test_orchestrator_skips_image_lookup_when_disabled(monkeypatch):
    """image_repo.get_active must NOT be called when cache.prebaked_image.enabled=False."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    fake_image_repo = AsyncMock()
    fake_image_repo.get_active = AsyncMock(return_value=None)

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    state = {
        "job_id": "run-e1-image",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_CACHE_DISABLED,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": AsyncMock(),
            "qa_image_tags_repo": fake_image_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    # get_active must never be called when prebaked_image.enabled=False
    fake_image_repo.get_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_skips_warmer_when_volume_disabled(monkeypatch):
    """warm_shared_volume must NOT be called when cache.shared_volume.enabled=False."""

    warmer_called = []

    async def fake_warm(*a, **kw):
        warmer_called.append(True)

    monkeypatch.setattr(
        "embry0.cache.volume_warmer.warm_shared_volume",
        fake_warm,
    )

    class _Sb:
        async def create(self, job_id, profile, env, repo=None, forge="github", **_kw):
            return f"sb-{job_id}", "tok-" + "A" * 40

        async def destroy(self, cid):
            return None

    class _Profiles:
        async def get(self, name):
            return {"name": "slim"}

    class _Docker:
        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            joined = " ".join(cmd)
            if "rev-parse HEAD" in joined:
                return "abc123\n"
            if "/workspace/.embry0/qa.yaml" in joined:
                return _QA_YAML_CACHE_DISABLED
            return ""

    state = {
        "job_id": "run-e1-volume",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {},
    }
    config = {
        "configurable": {
            "docker": _Docker(),
            "sandbox_manager": _Sb(),
            "profiles_repo": _Profiles(),
            "qa_shared_volume_manager": AsyncMock(),
            "qa_volume_state_repo": AsyncMock(),
        }
    }

    out = await init_orchestrator_node(state, config)
    # shared_volume_name must NOT be set when enabled=False
    assert out["qa"].get("shared_volume_name") is None
    assert warmer_called == [], "warm_shared_volume should not have been called"


@pytest.mark.asyncio
async def test_orchestrator_skips_turbo_when_disabled(monkeypatch):
    """turbo_remote_enabled=False must reach run_subtask when cache.turbo_remote.enabled=False."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    captured_kwargs: dict = {}

    async def spy_run_subtask(resolved, **kw):
        captured_kwargs.update(kw)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", spy_run_subtask)

    state = {
        "job_id": "run-e1-turbo",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_CACHE_DISABLED,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_app_results_repo": AsyncMock()}}

    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    # turbo_remote_enabled=False must be forwarded to run_subtask
    assert captured_kwargs.get("turbo_remote_enabled") is False


# ── C2: init_orchestrator_node populates changed_files + repo_root ──────────


_MIN_QA_YAML_V2 = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
  config:
    affected_filter: "[origin/${base_branch}]"
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
"""


@pytest.mark.asyncio
async def test_init_orchestrator_populates_changed_files_and_repo_root(tmp_path, monkeypatch):
    """The bootstrap sandbox runs git diff + finds + cats package.jsons; the
    orchestrator stashes the staging dir on state['repo_root'] and the diff
    on state['qa']['changed_files']."""
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    # Stub docker.run_cmd to dispatch by command shape.
    async def fake_run(cmd, timeout=60):
        s = " ".join(cmd)
        if "git fetch" in s:
            return ""
        if "git diff --name-only" in s:
            return "apps/hub/app/page.tsx\npackages/auth/src/index.ts\n"
        if "find /workspace" in s:
            return "/workspace/package.json\n/workspace/apps/hub/package.json\n"
        if "cat /workspace/package.json" in s and "lock" not in s:
            return '{"name": "root", "workspaces": ["apps/*"]}'
        if "cat /workspace/apps/hub/package.json" in s:
            return '{"name": "@toy/hub"}'
        if "cat /workspace/.embry0/qa.yaml" in s:
            return _MIN_QA_YAML_V2
        if "cat /workspace/package-lock.json" in s:
            return '{"lockfileVersion": 3}'
        if "git rev-parse HEAD" in s:
            return "abc123\n"
        return ""

    docker = AsyncMock()
    docker.run_cmd = fake_run
    docker.build_exec_cmd = lambda c, cmd, workdir=None, env=None: ["docker", "exec", c, *cmd]
    docker._build_base_cmd = lambda: ["docker"]

    # Stub sandbox manager.
    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("bootstrap-c", "tok"))
    sandbox_mgr.destroy = AsyncMock(return_value=None)
    sandbox_mgr.last_forge_credential = MagicMock(return_value=None)

    # Stub profiles repo.
    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim"})

    # Force staging into tmp_path so the test doesn't write to /tmp.
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator._staging_dir_for",
        lambda job_id: tmp_path / f"embry0-workspace-{job_id}",
    )

    state = {
        "job_id": "job-1",
        "repo": "org/repo",
        "branch_name": "feature/x",
        "base_branch": "main",
    }
    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_configs_repo": _FakeQAConfigs({"org/repo": _MIN_QA_YAML_V2}),
        }
    }

    out = await init_orchestrator_node(state, config)
    qa = out["qa"]

    # changed_files populated.
    assert qa["changed_files"] == ["apps/hub/app/page.tsx", "packages/auth/src/index.ts"]
    # repo_root set to the local staging dir (so provider.affected can read).
    assert out["repo_root"] == str(tmp_path / "embry0-workspace-job-1")
    # Staged files exist.
    assert (tmp_path / "embry0-workspace-job-1" / "package.json").is_file()
    assert (tmp_path / "embry0-workspace-job-1" / "apps" / "hub" / "package.json").is_file()


# ---------------------------------------------------------------------------
# Phase 5C: qa_orchestrator_node publishes "done" to QAEventBus when wired.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_orchestrator_publishes_done_event_on_success(monkeypatch):
    """After a happy-path run, the orchestrator publishes a single ``done``
    event with type/run_id/overall_status to the QA event bus so the SSE
    route can close the stream."""
    from unittest.mock import MagicMock

    from embry0.qa.event_bus import QAEventBus
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node
    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    bus = MagicMock(spec=QAEventBus)
    bus.publish = AsyncMock()

    state = {
        "job_id": "RUN-DONE-1",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {"configurable": {"qa_event_bus": bus}}

    await qa_orchestrator_node(state, config)

    # The bus now also carries per-app "running" subtask_status events (QA
    # live streaming) — assert on the run-level "done" specifically.
    published = [call.args for call in bus.publish.await_args_list]
    assert all(args[0] == "RUN-DONE-1" for args in published)
    running = [e for _rid, e in published if e["type"] == "subtask_status" and e["status"] == "running"]
    assert [e["app"] for e in running] == ["hub"]
    done_events = [e for _rid, e in published if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["run_id"] == "RUN-DONE-1"
    assert done_events[0]["overall_status"] == "passed"


@pytest.mark.asyncio
async def test_qa_orchestrator_publishes_done_on_init_failure_pass_through():
    """Even on the early-return ``existing_outcome=infra_error`` path, the
    orchestrator still publishes ``done`` so the SSE route closes the stream."""
    from unittest.mock import MagicMock

    from embry0.qa.event_bus import QAEventBus
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node

    bus = MagicMock(spec=QAEventBus)
    bus.publish = AsyncMock()

    state = {
        "job_id": "RUN-INFRA-1",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "outcome": {
                "overall_status": "infra_error",
                "apps_to_qa": [],
                "failure_summary": "bootstrap failed",
                "validation_errors": [],
                "validation_warnings": [],
            },
        },
    }
    config = {"configurable": {"qa_event_bus": bus}}

    await qa_orchestrator_node(state, config)

    bus.publish.assert_awaited_once()
    args, _kwargs = bus.publish.await_args
    event = args[1]
    assert event["type"] == "done"
    assert event["run_id"] == "RUN-INFRA-1"
    assert event["overall_status"] == "infra_error"


@pytest.mark.asyncio
async def test_qa_orchestrator_skips_publish_when_no_event_bus():
    """No bus wired → no publish; existing tests continue to pass."""
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node

    state = {
        "job_id": "RUN-NO-BUS",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "outcome": {
                "overall_status": "infra_error",
                "apps_to_qa": [],
                "failure_summary": "bootstrap failed",
                "validation_errors": [],
                "validation_warnings": [],
            },
        },
    }
    out = await qa_orchestrator_node(state, {})
    # The function still returns the same shape — just no SSE publish.
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"


# ─── Phase 5D: qa_run_metadata persistence ───────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_persists_run_metadata_after_resolution(monkeypatch):
    """qa_orchestrator_node upserts qa_run_metadata once `apps` is resolved.

    The row captures the affected-set decision: which apps ran, which were
    skipped, the changed_files that drove selection, the diff base, and
    whether force_all_apps was active.
    """
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        # Only @x/hub is affected — companion should land in apps_skipped.
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()
    md_repo = AsyncMock()
    md_repo.upsert = AsyncMock()

    state = {
        "job_id": "run-md-1",
        "repo": "org/repo",
        "branch_name": "feature/x",
        "base_branch": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
            # Phase 5F: init_orchestrator_node stashes head_sha here when it
            # clones the workspace; pre-seed it for tests that inject raw yaml
            # without going through init.
            "head_sha": "deadbeef00112233",
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_run_metadata_repo": md_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["apps_to_qa"] == ["hub"]

    # Exactly one upsert, with the expected affected-set shape.
    md_repo.upsert.assert_awaited_once()
    kwargs = md_repo.upsert.await_args.kwargs
    assert kwargs["job_id"] == "run-md-1"
    assert kwargs["apps_to_qa"] == ["hub"]
    assert kwargs["apps_skipped"] == ["companion"]
    assert kwargs["force_all_apps"] is False
    assert kwargs["changed_files"] == ["apps/hub/app/page.tsx"]
    assert kwargs["base_branch"] == "main"
    assert kwargs["dep_graph"] == []
    # Phase 5F: head_sha is forwarded to the upsert so flake-heatmap can
    # group consecutive runs on the same workspace head.
    assert kwargs["head_sha"] == "deadbeef00112233"


@pytest.mark.asyncio
async def test_orchestrator_persists_run_metadata_with_force_all_apps(monkeypatch):
    """When force_all_apps is set, every declared app runs and apps_skipped
    is empty. The diff path is bypassed, so changed_files may be empty.
    """
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()
    md_repo = AsyncMock()
    md_repo.upsert = AsyncMock()

    state = {
        "job_id": "run-md-force",
        "repo": "org/repo",
        "branch_name": "main",
        "base_branch": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": [],
            "force_all_apps": True,
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_run_metadata_repo": md_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    assert sorted(out["qa"]["apps_to_qa"]) == ["companion", "hub"]

    md_repo.upsert.assert_awaited_once()
    kwargs = md_repo.upsert.await_args.kwargs
    assert kwargs["force_all_apps"] is True
    assert sorted(kwargs["apps_to_qa"]) == ["companion", "hub"]
    assert kwargs["apps_skipped"] == []
    assert kwargs["changed_files"] == []
    assert kwargs["base_branch"] == "main"


@pytest.mark.asyncio
async def test_orchestrator_metadata_persist_failure_does_not_break_run(monkeypatch):
    """Persistence is best-effort. If qa_run_metadata.upsert raises, the run
    still finishes through fan-out and produces an outcome.
    """
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            frozenset({"@x/hub"}),
            frozenset({"@x/hub"}),
            frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()
    md_repo = AsyncMock()
    md_repo.upsert = AsyncMock(side_effect=RuntimeError("db down"))

    state = {
        "job_id": "run-md-fail",
        "repo": "org/repo",
        "branch_name": "feature/x",
        "base_branch": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_run_metadata_repo": md_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    # Run completed despite the persist failure.
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    md_repo.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_no_metadata_repo_does_nothing(monkeypatch):
    """Absent qa_run_metadata_repo (older callers / unit tests) is a no-op.
    The run still proceeds normally.
    """
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            frozenset({"@x/hub"}),
            frozenset({"@x/hub"}),
            frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()

    state = {
        "job_id": "run-md-none",
        "repo": "org/repo",
        "branch_name": "feature/x",
        "base_branch": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    # No qa_run_metadata_repo in configurable — must not raise.
    config = {"configurable": {"qa_app_results_repo": qa_app_results_repo}}

    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"


# -------- target: deployed (EMB-27) --------

_QA_YAML_ALL_DEPLOYED = """
version: 2
qa_required: always
defaults:
  sandbox_profile: qa-external
apps:
  web:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
        expect_status: [200, 302]
"""


@pytest.mark.asyncio
async def test_orchestrator_all_deployed_runs_without_provider(monkeypatch):
    """No workspace_provider at all: the orchestrator must not try to load
    one and must fan out every declared deployed app."""
    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )

    def _boom(name, root, config):
        raise AssertionError("load_provider must not be called for all-deployed configs")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", _boom)

    async def fake_run_subtask(resolved, **kw):
        assert resolved.target == "deployed"
        assert resolved.boot_command is None
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    state = {
        "job_id": "22222222-2222-2222-2222-222222222222",
        "repo": "org/deployed-repo",
        "branch_name": "main",
        "qa": {"qa_yaml_v2_raw": _QA_YAML_ALL_DEPLOYED, "changed_files": []},
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert qa["apps_to_qa"] == ["web"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert qa["final_status"] == "passed"


@pytest.mark.asyncio
async def test_orchestrator_mixed_deployed_always_included_in_affected_run(monkeypatch):
    """qa_required: auto — managed apps follow the affected-set; deployed
    apps are unioned in unconditionally."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    # Insert the deployed app under apps: (NOT appended at the end, which
    # would nest it under the trailing packages: block).
    mixed_yaml = _QA_YAML_V2.replace(
        "packages:",
        """  live:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
packages:""",
    )

    # Only hub is affected; companion (managed) should be skipped; live
    # (deployed) must run regardless.
    fake_provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    validated_names: list[list[str]] = []
    original_validate = fake_provider.validate

    def _spy_validate(app_names):
        validated_names.append(list(app_names))
        return original_validate(app_names)

    fake_provider.validate = _spy_validate
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    state = {
        "job_id": "33333333-3333-3333-3333-333333333333",
        "repo": "org/mixed-repo",
        "branch_name": "main",
        "qa": {"qa_yaml_v2_raw": mixed_yaml, "changed_files": ["apps/hub/app/page.tsx"]},
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert sorted(qa["apps_to_qa"]) == ["hub", "live"]
    # The provider was only asked to validate MANAGED apps — the deployed
    # app is not a workspace package and must not be rejected by it.
    assert validated_names and all("live" not in names for names in validated_names)
    assert qa["final_status"] == "passed"


@pytest.mark.asyncio
async def test_orchestrator_node_job_level_criteria_override_replaces_resolved(monkeypatch):
    """A non-empty QAJobOverrides.acceptance_criteria (stashed by
    issue_executor at qa["acceptance_criteria"]) must replace every app's
    resolved criteria for the run; empty/absent leaves qa.yaml's resolution
    untouched."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    seen_criteria: list[list[str]] = []

    async def fake_run_subtask(resolved, **kw):
        seen_criteria.append(list(resolved.acceptance_criteria))
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    override = ["only criterion: page loads"]
    state = {
        "job_id": "22222222-2222-2222-2222-222222222222",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
            "acceptance_criteria": override,
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert seen_criteria == [override]

    # Control: without the override the resolved criteria come from qa.yaml
    # (empty template in this fixture).
    seen_criteria.clear()
    state["qa"] = {
        "qa_yaml_v2_raw": _QA_YAML_V2,
        "changed_files": ["apps/hub/app/page.tsx"],
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert seen_criteria == [[]]


# -------- Conditional acceptance criteria (EMB-39) --------

_QA_YAML_V2_CONDITIONAL = (
    _QA_YAML_V2
    + """
conditional_acceptance_criteria:
  - name: pricing
    when:
      changed_paths: ["apps/hub/**/pricing/**"]
    criteria:
      - "Exercise Place Order"
  - name: hub-only
    when:
      affected_apps: ["hub"]
    apps: ["hub"]
    criteria:
      - "Hub deep check"
  - name: deep
    when:
      labels: ["qa:deep"]
    criteria:
      - "Label-gated check"
"""
)


def _fake_hub_companion_provider():
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    return FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )


def _capture_subtasks(monkeypatch):
    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )

    seen: dict[str, list[str]] = {}

    async def fake_run_subtask(resolved, **kw):
        seen[resolved.app_name] = list(resolved.acceptance_criteria)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)
    return seen


@pytest.mark.asyncio
async def test_orchestrator_conditional_group_appends_on_changed_paths(monkeypatch):
    """A changed_paths-matched group's criteria are appended to the resolved set."""
    fake_provider = _fake_hub_companion_provider()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa1111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": ["apps/hub/src/pricing/calc.ts"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    # pricing (changed_paths) fires for all apps in run; hub-only (affected
    # AND scoped to hub) fires for hub only; deep (labels) does not fire.
    assert seen["hub"] == ["Exercise Place Order", "Hub deep check"]
    assert "Label-gated check" not in seen["hub"]


@pytest.mark.asyncio
async def test_orchestrator_conditional_append_survives_job_override_replace(monkeypatch):
    """4c composition: the job-level whole-list replace happens FIRST, then
    fired conditional criteria are appended on top. On issue→PR runs triage
    supplies criteria via the same replace path — appending before it would
    dead-letter the feature."""
    fake_provider = _fake_hub_companion_provider()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa2222-2222-2222-2222-222222222222",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": ["apps/hub/src/pricing/calc.ts"],
            "acceptance_criteria": ["triage criterion"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert seen["hub"] == ["triage criterion", "Exercise Place Order", "Hub deep check"]


@pytest.mark.asyncio
async def test_orchestrator_conditional_off_on_empty_diff(monkeypatch):
    """Default-OFF rule: empty diff (deployed/standalone) + no labels + no
    force -> predicate groups never fire. Empty diff also means the affected
    set is empty, so every app runs (fall-back-to-all-apps)."""
    from embry0.workspace_providers import AffectedSet

    fake_provider = _fake_hub_companion_provider()
    fake_provider.affected_result = AffectedSet(
        directly_changed=frozenset(),
        cascade_closure=frozenset(),
        apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa3333-3333-3333-3333-333333333333",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": [],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    for app, criteria in seen.items():
        assert "Exercise Place Order" not in criteria, app
        assert "Hub deep check" not in criteria, app
        assert "Label-gated check" not in criteria, app


@pytest.mark.asyncio
async def test_orchestrator_conditional_forced_fires_on_empty_diff(monkeypatch):
    """qa.force_conditional_groups fires a group with no diff at all."""
    from embry0.workspace_providers import AffectedSet

    fake_provider = _fake_hub_companion_provider()
    fake_provider.affected_result = AffectedSet(
        directly_changed=frozenset(),
        cascade_closure=frozenset(),
        apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa4444-4444-4444-4444-444444444444",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": [],
            "force_conditional_groups": ["pricing"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert "Exercise Place Order" in seen["hub"]
    assert "Exercise Place Order" in seen["companion"]


@pytest.mark.asyncio
async def test_orchestrator_conditional_labels_fire_from_state(monkeypatch):
    """A labels-only group fires from JobState.issue_labels even with an
    empty diff — the issue-path escape hatch."""
    from embry0.workspace_providers import AffectedSet

    fake_provider = _fake_hub_companion_provider()
    fake_provider.affected_result = AffectedSet(
        directly_changed=frozenset(),
        cascade_closure=frozenset(),
        apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa5555-5555-5555-5555-555555555555",
        "repo": "org/repo",
        "branch_name": "main",
        "issue_labels": ["QA:DEEP"],
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": [],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert "Label-gated check" in seen["hub"]
    assert "Exercise Place Order" not in seen["hub"]


@pytest.mark.asyncio
async def test_orchestrator_unknown_forced_group_is_infra_error(monkeypatch):
    """A typo'd force_conditional_groups name fails the run before fan-out —
    the operator explicitly requested a costly check."""
    fake_provider = _fake_hub_companion_provider()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa6666-6666-6666-6666-666666666666",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": ["apps/hub/app/page.tsx"],
            "force_conditional_groups": ["pricign"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert qa["final_status"] == "failed"
    assert qa["outcome"]["overall_status"] == "infra_error"
    assert "pricign" in qa["validation_errors"][0]
    assert seen == {}  # no fan-out


@pytest.mark.asyncio
async def test_orchestrator_conditional_groups_persisted_to_metadata(monkeypatch):
    """The metadata upsert carries which groups fired and why."""
    from unittest.mock import AsyncMock, MagicMock

    fake_provider = _fake_hub_companion_provider()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    _capture_subtasks(monkeypatch)

    metadata_repo = MagicMock()
    metadata_repo.upsert = AsyncMock()

    state = {
        "job_id": "aaaa7777-7777-7777-7777-777777777777",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": ["apps/hub/src/pricing/calc.ts"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {"qa_run_metadata_repo": metadata_repo}})
    assert out["qa"]["final_status"] == "passed"
    kwargs = metadata_repo.upsert.await_args.kwargs
    groups = kwargs["conditional_groups"]
    assert {g["name"] for g in groups} == {"pricing", "hub-only"}
    by_name = {g["name"]: g for g in groups}
    assert by_name["pricing"]["source"] == "matched"
    assert by_name["hub-only"]["apps"] == ["hub"]


@pytest.mark.asyncio
async def test_orchestrator_early_failures_set_error_message(monkeypatch):
    """Early infra_error aborts must set qa["error_message"] so the jobs row
    surfaces the structured reason instead of the generic
    "QA final_status=failed" (issue_executor falls back to it only when the
    key is absent)."""
    fake_provider = _fake_hub_companion_provider()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    state = {
        "job_id": "aaaa8888-8888-8888-8888-888888888888",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_CONDITIONAL,
            "changed_files": [],
            "force_conditional_groups": ["not-a-real-group"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    assert qa["final_status"] == "failed"
    assert "not-a-real-group" in qa["error_message"]
    assert seen == {}


@pytest.mark.asyncio
async def test_orchestrator_browserless_profile_fails_capability_check(monkeypatch):
    """EMB-34 fix #3: a resolved sandbox profile whose base_image has no
    browser fails pre-fan-out with infra_error instead of burning boot spend
    on a run the Playwright-requiring QA agent cannot complete."""
    from unittest.mock import AsyncMock, MagicMock

    from embry0.workspace_providers import AffectedSet

    fake_provider = _fake_hub_companion_provider()
    fake_provider.affected_result = AffectedSet(
        directly_changed=frozenset({"@x/hub"}),
        cascade_closure=frozenset({"@x/hub"}),
        apps_to_qa=frozenset({"@x/hub"}),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    _capture_subtasks(monkeypatch)

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim", "base_image": "embry0-sandbox:latest"})

    state = {
        "job_id": "bbbb5555-5555-5555-5555-555555555555",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {"profiles_repo": profiles_repo}})
    assert out["qa"]["final_status"] == "failed"
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"
    assert any("has no browser" in e for e in out["qa"]["validation_errors"])


@pytest.mark.asyncio
async def test_orchestrator_qa_image_profile_passes_capability_check(monkeypatch):
    """A profile built on the qa image family passes the check."""
    from unittest.mock import AsyncMock, MagicMock

    from embry0.workspace_providers import AffectedSet

    fake_provider = _fake_hub_companion_provider()
    fake_provider.affected_result = AffectedSet(
        directly_changed=frozenset({"@x/hub"}),
        cascade_closure=frozenset({"@x/hub"}),
        apps_to_qa=frozenset({"@x/hub"}),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    seen = _capture_subtasks(monkeypatch)

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(
        return_value={"name": "qa-external", "base_image": "registry:5000/embry0-sandbox-qa:latest"}
    )

    state = {
        "job_id": "cccc6666-6666-6666-6666-666666666666",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {"profiles_repo": profiles_repo}})
    assert out["qa"]["final_status"] == "passed"
    assert "hub" in seen


@pytest.mark.asyncio
async def test_orchestrator_provider_root_subdirectory(monkeypatch):
    """EMB-32: `workspace_provider.config.root` constructs the provider on
    repo_root/<root> with the key stripped, wrapped in RootScopedProvider."""
    from embry0.workspace_providers import AffectedSet

    captured = {}

    def _fake_load(name, root, config):
        captured["name"] = name
        captured["root"] = root
        captured["config"] = config
        provider = _fake_hub_companion_provider()
        provider.affected_result = AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        )
        captured["provider"] = provider
        return provider

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", _fake_load)
    _capture_subtasks(monkeypatch)

    yaml_with_root = _QA_YAML_V2.replace(
        "workspace_provider:\n  type: fake\n",
        'workspace_provider:\n  type: fake\n  config:\n    root: "frontend"\n',
    )
    assert 'root: "frontend"' in yaml_with_root, "fixture substitution failed"

    state = {
        "job_id": "dddd7777-7777-7777-7777-777777777777",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": yaml_with_root,
            "changed_files": ["frontend/apps/hub/app/page.tsx"],
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert str(captured["root"]).endswith("/frontend")
    assert "root" not in captured["config"]
    # The wrapper rebased the changed file before the inner provider saw it.
    rebased = [str(c.changed_files[0]) for c in captured["provider"].affected_calls]
    assert rebased and rebased[0] == "apps/hub/app/page.tsx"


# ── RAV-1251: DB-backed QA config resolution ─────────────────────────────────


class _FakeQAConfigs:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.upserts = []

    async def get(self, repo):
        row = self.rows.get(repo)
        return {"repo": repo, "yaml_text": row, "updated_by": "", "updated_at": "now"} if row else None

    async def upsert(self, repo, yaml_text, updated_by=""):
        self.rows[repo] = yaml_text
        self.upserts.append((repo, updated_by))
        return {"repo": repo, "yaml_text": yaml_text, "updated_by": updated_by, "updated_at": "now"}


def _rav1251_harness(cat_result=""):
    class _Sb:
        async def create(self, job_id, profile, env, repo=None, forge="github", **_kw):
            return f"sb-{job_id}", "tok-" + "A" * 40

        async def destroy(self, container_id):
            return None

    class _Profiles:
        async def get(self, name):
            return {"name": "slim", "extra_networks": []}

    class _Docker:
        def __init__(self):
            self.calls = []

        def _build_base_cmd(self):
            return ["docker"]

        def build_exec_cmd(self, cid, cmd):
            return ["docker", "exec", cid, *cmd]

        async def run_cmd(self, cmd, timeout=None):
            self.calls.append(cmd)
            joined = " ".join(cmd)
            if "rev-parse HEAD" in joined:
                return "abc123\n"
            if "/workspace/.embry0/qa.yaml" in joined:
                return cat_result
            return ""

    return _Sb(), _Profiles(), _Docker()


@pytest.mark.asyncio
async def test_init_orchestrator_db_config_wins(monkeypatch, tmp_path):
    """The DB row is the source of truth; no in-repo cat ever runs (RAV-1253)."""
    db_yaml = _QA_YAML_V2.replace("version: 2", "version: 2  # from-db")

    sb, profiles, docker = _rav1251_harness()
    configs = _FakeQAConfigs({"acme/dbwins": db_yaml})
    state = {"job_id": "run-db", "repo": "acme/dbwins", "branch_name": "main", "qa": {}}
    out = await init_orchestrator_node(
        state,
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "qa_configs_repo": configs,
            }
        },
    )
    assert out["qa"]["qa_yaml_v2_raw"] == db_yaml
    assert configs.upserts == []
    assert not any("/workspace/.embry0/qa.yaml" in " ".join(c) for c in docker.calls)


@pytest.mark.asyncio
async def test_orchestrator_document_app_local_outranks_tree_files(monkeypatch):
    """RAV-1251: the stored document's app_local section wins over
    tree-collected apps/<name>/.embry0/app.yaml content."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    captured = {}

    async def fake_run_subtask(resolved, **kw):
        captured[resolved.app_name] = resolved
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    doc_yaml = _QA_YAML_V2 + ("app_local:\n  hub:\n    acceptance_criteria:\n      - document criterion wins\n")
    state = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": doc_yaml,
            "changed_files": ["apps/hub/app/page.tsx"],
            # Tree-collected app-local file for the same app (deprecated channel).
            "app_local_raw": {"apps/hub": "acceptance_criteria:\n  - tree criterion loses\n"},
        },
    }

    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert captured["hub"].acceptance_criteria == ["document criterion wins"]


@pytest.mark.asyncio
async def test_init_orchestrator_missing_config_is_clear_error(monkeypatch):
    """RAV-1253: no DB row (and no legacy channels) → infra_error whose
    failure_summary tells the operator exactly how to store a config."""
    sb, profiles, docker = _rav1251_harness(cat_result=_QA_YAML_V2)
    configs = _FakeQAConfigs()
    state = {"job_id": "run-none", "repo": "acme/unconfigured", "branch_name": "main", "qa": {}}
    out = await init_orchestrator_node(
        state,
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "qa_configs_repo": configs,
            }
        },
    )
    summary = out["qa"]["outcome"]["failure_summary"]
    assert "no QA config stored in embry0" in summary
    assert "qa-config" in summary
    # The legacy in-repo file (which the fake docker would happily serve)
    # must not be read or imported.
    assert configs.upserts == []
    assert not any("/workspace/.embry0/qa.yaml" in " ".join(c) for c in docker.calls)


# -------- browser_backend override + deployment default (#229 / #232) --------


@pytest.mark.asyncio
async def test_orchestrator_node_browser_backend_override_and_deployment_default(monkeypatch):
    """QAJobOverrides.browser_backend (stashed at qa["browser_backend"])
    replaces every resolved app's backend; absent, the deployment default
    from configurable["embry0_config"].qa_browser_backend_default applies;
    absent both, resolution lands on "playwright". Synthetic names ride a
    stubbed registry — the plumbing, not the registry, is under test."""
    from pathlib import Path
    from types import SimpleNamespace

    from embry0.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )
    import embry0.workflows.qa.browser_backends as backends_mod
    import embry0.workflows.qa.qa_yaml_resolve as resolve_mod

    # Two patch points: resolve_app_config bound the name at module load;
    # the orchestrator node imports it function-locally at call time.
    monkeypatch.setattr(resolve_mod, "backend_for", lambda name: name)
    monkeypatch.setattr(backends_mod, "backend_for", lambda name: name)

    seen_backends: list[str] = []

    async def fake_run_subtask(resolved, **kw):
        seen_backends.append(resolved.browser_backend)
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    def make_state(**qa_extra):
        return {
            "job_id": "33333333-3333-3333-3333-333333333333",
            "repo": "org/repo",
            "branch_name": "main",
            "qa": {
                "qa_yaml_v2_raw": _QA_YAML_V2,
                "changed_files": ["apps/hub/app/page.tsx"],
                **qa_extra,
            },
        }

    # 1. Job override wins over the deployment default.
    cfg_obj = SimpleNamespace(qa_browser_backend_default="bk-deploy")
    out = await qa_orchestrator_node(
        make_state(browser_backend="bk-override"),
        {"configurable": {"embry0_config": cfg_obj}},
    )
    assert out["qa"]["final_status"] == "passed"
    assert seen_backends == ["bk-override"]

    # 2. No override: the deployment default applies (yaml is silent here).
    seen_backends.clear()
    out = await qa_orchestrator_node(make_state(), {"configurable": {"embry0_config": cfg_obj}})
    assert out["qa"]["final_status"] == "passed"
    assert seen_backends == ["bk-deploy"]

    # 3. Nothing anywhere: playwright.
    seen_backends.clear()
    out = await qa_orchestrator_node(make_state(), {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert seen_backends == ["playwright"]


@pytest.mark.asyncio
async def test_orchestrator_node_unknown_override_backend_is_validation_failure(monkeypatch):
    """An unregistered override name (belt-and-suspenders behind the API
    validator) fails the run as a validation error, never a node crash."""
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    state = {
        "job_id": "44444444-4444-4444-4444-444444444444",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
            "browser_backend": "netscape",
        },
    }
    out = await qa_orchestrator_node(state, {"configurable": {}})
    assert out["qa"]["final_status"] == "failed"
    assert any("unknown browser backend 'netscape'" in e for e in out["qa"]["validation_errors"])


# ── spec 3.6: the bootstrap sandbox follows the repo's forge ────────────────


class _ForgeRepos:
    def __init__(self, forge="gitlab", raises=None):
        self._forge = forge
        self._raises = raises

    async def forge_for(self, repo):
        if self._raises is not None:
            raise self._raises
        return self._forge


@pytest.mark.asyncio
async def test_init_orchestrator_clones_the_repo_forge(monkeypatch):
    from types import SimpleNamespace

    sb, profiles, docker = _rav1251_harness()
    created: dict = {}

    async def create(job_id, profile=None, env=None, repo=None, forge="github", **_kw):
        created["forge"] = forge
        return f"sb-{job_id}", "tok-" + "A" * 40

    sb.create = create
    cloned: dict = {}

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        cloned.update(kw)
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)

    configs = _FakeQAConfigs({"acme/group/backend": _QA_YAML_V2})
    state = {
        "job_id": "run-gitlab",
        "repo": "acme/group/backend",
        "branch_name": "dev",
        "qa": {},
    }
    out = await init_orchestrator_node(
        state,
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "qa_configs_repo": configs,
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    assert out["qa"].get("outcome", {}).get("overall_status") != "infra_error"
    assert created["forge"] == "gitlab"
    assert cloned["forge"] == "gitlab"
    assert cloned["gitlab_url"] == "https://gitlab.example.com"


@pytest.mark.asyncio
async def test_init_orchestrator_fails_loudly_on_an_unregistered_repo():
    from embry0.safety.error_codes import ErrorCode
    from embry0.storage.repositories.repos import UnregisteredRepoError

    sb, profiles, docker = _rav1251_harness()

    async def never(*a, **kw):
        raise AssertionError("no sandbox may be created for an unregistered repo")

    sb.create = never
    state = {"job_id": "run-ghost", "repo": "org/ghost", "branch_name": "main", "qa": {}}
    out = await init_orchestrator_node(
        state,
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "repos_repo": _ForgeRepos(raises=UnregisteredRepoError("org/ghost is not registered")),
            }
        },
    )
    outcome = out["qa"]["outcome"]
    assert outcome["overall_status"] == "infra_error"
    assert outcome["failure_summary"].startswith(ErrorCode.REPO_UNREGISTERED)


# -------- sandbox_profile job override + the profile recorded (#537) --------


@pytest.mark.asyncio
async def test_orchestrator_node_sandbox_profile_override_reaches_subtasks_and_is_recorded(monkeypatch):
    """QAJobOverrides.sandbox_profile (stashed at qa["sandbox_profile"])
    replaces every resolved app's profile, and the profile the sub-tasks
    ran on is recorded at qa["sandbox_profile_used"] for the jobs row."""
    from pathlib import Path

    from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub"), WorkspaceApp("companion", Path("apps/c"), "@x/c")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub", "@x/c"}),
            cascade_closure=frozenset({"@x/hub", "@x/c"}),
            apps_to_qa=frozenset({"@x/hub", "@x/c"}),
        ),
    )
    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", lambda name, root, config: fake_provider)

    seen: list[str] = []

    async def fake_run_subtask(resolved, **kw):
        seen.append(resolved.sandbox_profile)
        return SubTaskResult(
            app_name=resolved.app_name, status=SubTaskStatus.PASSED, duration_ms=10, cache_hits=CacheHits()
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    def make_state(**qa_extra):
        return {
            "job_id": "55555555-5555-5555-5555-555555555555",
            "repo": "org/repo",
            "branch_name": "main",
            "qa": {"qa_yaml_v2_raw": _QA_YAML_V2, "changed_files": ["apps/hub/x.tsx"], **qa_extra},
        }

    out = await qa_orchestrator_node(make_state(sandbox_profile="qa-custom"), {"configurable": {}})
    assert out["qa"]["final_status"] == "passed"
    assert seen == ["qa-custom", "qa-custom"], (
        f"the job's sandbox_profile override was ignored: sub-tasks ran on {seen}"
    )
    assert out["qa"]["sandbox_profile_used"] == "qa-custom"

    # No override: the qa.yaml profile stands and is what gets recorded.
    seen.clear()
    out = await qa_orchestrator_node(make_state(), {"configurable": {}})
    assert seen == ["slim", "slim"]
    assert out["qa"]["sandbox_profile_used"] == "slim"


# -------- the per-job cap covers the QA fan-out (#264) --------


def _budget_harness(monkeypatch, *, trace_total: float):
    from pathlib import Path
    from types import SimpleNamespace

    from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", lambda name, root, config: fake_provider)
    seen_configs: list[dict] = []

    async def fake_run_subtask(resolved, **kw):
        seen_configs.append(kw["config"]["configurable"])
        return SubTaskResult(
            app_name=resolved.app_name, status=SubTaskStatus.PASSED, duration_ms=10, cache_hits=CacheHits()
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)
    traces = MagicMock()
    # $1 before the fan-out (the pre-start check), then the QA agents' bill.
    calls = iter([1.0])
    traces.total_cost_for_job = AsyncMock(side_effect=lambda job_id: next(calls, trace_total))

    def config(mode: str) -> dict:
        settings = SimpleNamespace(budget_overrun_mode=mode, max_budget_usd=0.0, qa_browser_backend_default=None)
        return {"configurable": {"job_id": "job-qa", "traces_repo": traces, "embry0_config": settings}}

    state = {
        "job_id": "job-qa",
        "repo": "org/repo",
        "branch_name": "main",
        "total_cost_usd": 1.0,  # state never sees the QA agents' cost
        "pipeline_config": {"budget_usd": 5.0},
        "qa": {"qa_yaml_v2_raw": _QA_YAML_V2, "changed_files": ["apps/hub/x.tsx"]},
    }
    return state, config, seen_configs


@pytest.mark.asyncio
async def test_a_qa_heavy_job_hits_a_hard_cap(monkeypatch):
    """#264: $1 in state, $7 in traces after QA, $5 cap — hard mode stops the job."""
    from embry0.orchestration.budget import BudgetOverrunError

    state, config, seen = _budget_harness(monkeypatch, trace_total=7.0)
    with pytest.raises(BudgetOverrunError) as excinfo:
        await qa_orchestrator_node(state, config("hard"))
    assert excinfo.value.current_cost == pytest.approx(7.0)
    assert excinfo.value.max_budget == pytest.approx(5.0)
    assert seen[0]["qa_job_budget"] == {"max_budget_usd": 5.0, "budget_overrun_mode_override": None}


@pytest.mark.asyncio
async def test_a_qa_heavy_job_records_a_soft_overrun(monkeypatch):
    state, config, _ = _budget_harness(monkeypatch, trace_total=7.0)
    out = await qa_orchestrator_node(state, config("soft"))
    assert out.get("budget_overrun_usd") == pytest.approx(2.0), (
        f"the QA fan-out's spend must reach the job's overrun; got {out.get('budget_overrun_usd')}"
    )
    assert out["qa"]["final_status"] == "passed"


@pytest.mark.asyncio
async def test_a_qa_run_under_the_cap_records_nothing(monkeypatch):
    state, config, _ = _budget_harness(monkeypatch, trace_total=3.0)
    out = await qa_orchestrator_node(state, config("hard"))
    assert "budget_overrun_usd" not in out


@pytest.mark.asyncio
async def test_no_sub_task_starts_once_a_hard_cap_is_crossed():
    from embry0.workflows.qa.orchestrator_helpers import fan_out_subtasks
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
    from embry0.workflows.qa.subtask_result_schema import SubTaskStatus

    started: list[str] = []

    async def fake_run_subtask(resolved, **kw):  # pragma: no cover - must not run
        started.append(resolved.app_name)

    resolved = [MagicMock(spec=ResolvedAppConfig, app_name=n, target="managed") for n in ("a", "b")]
    with patch("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask):
        results = await fan_out_subtasks(
            resolved,
            parent_run_id="job-qa",
            repo="o/r",
            branch_name="main",
            max_concurrent=1,
            config={"configurable": {}},
            budget_spent=AsyncMock(return_value=True),
        )
    assert started == []
    assert [r.status for r in results] == [SubTaskStatus.INFRA_FAILURE] * 2
    assert "budget" in (results[0].failure_summary or "")


@pytest.mark.asyncio
async def test_qa_orchestrator_node_brackets_itself_on_the_job_stream(monkeypatch):
    """spec 3.7: the run-level done is node_completed(node="orchestrate_qa", action=<overall_status>)."""
    from embry0.workflows.qa import orchestrator as orch

    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    monkeypatch.setattr(
        orch,
        "_qa_orchestrator_node_impl",
        AsyncMock(return_value={"qa": {"outcome": {"overall_status": "passed"}}}),
    )
    await orch.qa_orchestrator_node({"job_id": "JOB-1"}, {"configurable": {}})
    started = next((e for e in events_out if e["type"] == "node_started"), None)
    completed = next((e for e in events_out if e["type"] == "node_completed"), None)
    assert started is not None and completed is not None, f"the wrapper brackets itself, got {events_out}"
    assert started["node"] == "orchestrate_qa" and completed["node"] == "orchestrate_qa"
    assert completed["action"] == "passed"
    assert events_out.index(started) < events_out.index(completed)


@pytest.mark.asyncio
async def test_init_orchestrator_node_brackets_itself_on_an_early_return(monkeypatch):
    """spec 3.7: init_orchestrator emits node_started/node_completed even when it bails out early."""
    from embry0.workflows.qa import orchestrator as orch

    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    out = await orch.init_orchestrator_node({}, {"configurable": {}})
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"
    nodes = [(e["type"], e.get("node")) for e in events_out]
    assert nodes == [
        ("node_started", "init_orchestrator"),
        ("pipeline_graph", "init_orchestrator"),
        ("node_completed", "init_orchestrator"),
    ], f"init_orchestrator brackets its missing-deps return, got {nodes}"


@pytest.mark.asyncio
async def test_qa_report_node_brackets_itself_on_the_job_stream(monkeypatch):
    """spec 3.7: qa_report emits node_started/node_completed (here on its no-token return)."""
    from embry0.workflows.qa import orchestrator_report

    events_out: list[dict] = []
    monkeypatch.setattr(orchestrator_report, "get_stream_writer", lambda: events_out.append)
    out = await orchestrator_report.qa_report_node({"qa": {}}, {"configurable": {}})
    assert out == {}
    nodes = [(e["type"], e.get("node")) for e in events_out]
    assert nodes == [("node_started", "qa_report"), ("node_completed", "qa_report")], (
        f"qa_report brackets its skipped-write return, got {nodes}"
    )


@pytest.mark.asyncio
async def test_init_orchestrator_emits_the_pipeline_graph(monkeypatch):
    from types import SimpleNamespace

    from embry0.workflows.qa import orchestrator as orch

    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    sb, profiles, docker = _rav1251_harness()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    await init_orchestrator_node(
        {"job_id": "run-graph", "repo": "acme/group/backend", "branch_name": "dev", "qa": {}},
        {
            "configurable": {
                "docker": docker,
                "sandbox_manager": sb,
                "profiles_repo": profiles,
                "proxy_manager": None,
                "qa_configs_repo": _FakeQAConfigs({"acme/group/backend": _QA_YAML_V2}),
                "repos_repo": _ForgeRepos("gitlab"),
                "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com"),
            }
        },
    )
    types = [e["type"] for e in events_out]
    assert types.index("node_started") < types.index("pipeline_graph"), (
        "node_started (Task 9) comes first, then the graph"
    )
    graph = next(e for e in events_out if e["type"] == "pipeline_graph")
    assert graph["node"] == "init_orchestrator" and graph["graph"]["graph_id"] == "qa-run-graph"
    assert [n["node_id"] for n in graph["graph"]["nodes"]] == ["init_orchestrator", "orchestrate_qa", "qa_report"]
