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


# ── Task 22: init_orchestrator_node + qa_orchestrator_node ──────────────────

from unittest.mock import AsyncMock  # noqa: E402

from athanor.workflows.qa.orchestrator import (  # noqa: E402
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
    from athanor.workspace_providers import (
        AffectedSet,
        WorkspaceApp,
        WorkspacePackage,
    )
    from athanor.workspace_providers.fakes import FakeWorkspaceProvider
    from athanor.workflows.qa.subtask_result_schema import (
        CacheHits,
        SubTaskResult,
        SubTaskStatus,
    )

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
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=10,
            cache_hits=CacheHits(),
        )
    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", fake_run_subtask)

    repo_mock = AsyncMock()
    repo_mock.upsert = AsyncMock()

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
    assert sorted(qa["apps_to_qa"]) == ["hub", "companion"]
    assert repo_mock.upsert.await_count == 2
    assert qa["final_status"] == "passed"


@pytest.mark.asyncio
async def test_orchestrator_node_short_circuits_when_no_apps_affected(monkeypatch):
    from pathlib import Path
    from athanor.workspace_providers import AffectedSet, WorkspaceApp
    from athanor.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    repo_mock = AsyncMock()
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
    assert repo_mock.upsert.await_count == 0


@pytest.mark.asyncio
async def test_orchestrator_node_validation_errors_block_fan_out(monkeypatch):
    from athanor.workspace_providers import AffectedSet
    from athanor.workspace_providers.fakes import FakeWorkspaceProvider

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
        "athanor.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    async def crash_subtask(*a, **kw):
        raise AssertionError("fan-out should not have happened")
    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", crash_subtask)

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
async def test_init_orchestrator_node_writes_yaml_to_state(monkeypatch):
    """init_orchestrator_node bootstrap-loads qa.yaml v2."""

    class _Sb:
        def __init__(self):
            self.last_env: dict = {}

        async def create(self, job_id, profile, env):
            self.last_env = dict(env)
            return f"sb-{job_id}", "tok-" + "A" * 40

        async def destroy(self, container_id):
            return None

    class _Profiles:
        async def get(self, name):
            return {"name": "slim", "extra_networks": []}

    class _ProxyMgr:
        git_proxy_url = "http://git-proxy:9101"

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
            if "/workspace/.athanor/qa.yaml" in joined:
                return _QA_YAML_V2
            return ""

    sb = _Sb()
    state = {"job_id": "run-4", "repo": "org/repo", "branch_name": "main", "qa": {}}
    config = {
        "configurable": {
            "docker": _Docker(),
            "sandbox_manager": sb,
            "profiles_repo": _Profiles(),
            "proxy_manager": _ProxyMgr(),
        }
    }
    out = await init_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["qa_yaml_v2_raw"] == _QA_YAML_V2
    assert qa["qa_yaml_v2_parsed"]["version"] == 2
    # I3: bootstrap sandbox must receive ATHANOR_GIT_PROXY_URL + QA_JOB_ID.
    assert sb.last_env.get("ATHANOR_GIT_PROXY_URL") == "http://git-proxy:9101"
    assert sb.last_env.get("QA_JOB_ID") == "run-4::bootstrap"
    assert sb.last_env.get("QA_ATTEMPT_N") == "1"


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

    monkeypatch.setattr("athanor.workflows.qa.orchestrator.run_subtask", crash_subtask)

    # load_provider should also never be called
    def crash_load_provider(*a, **kw):
        raise AssertionError("load_provider should not be called when qa_required=never")

    monkeypatch.setattr("athanor.workflows.qa.orchestrator.load_provider", crash_load_provider)

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
