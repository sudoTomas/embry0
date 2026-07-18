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

from unittest.mock import AsyncMock  # noqa: E402

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
async def test_init_orchestrator_node_writes_yaml_to_state(monkeypatch):
    """init_orchestrator_node bootstrap-loads qa.yaml v2."""

    class _Sb:
        def __init__(self):
            self.last_env: dict = {}

        async def create(self, job_id, profile, env, repo=None):
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
            if "/workspace/.embry0/qa.yaml" in joined:
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
    # I3: bootstrap sandbox must receive EMBRY0_GIT_PROXY_URL + QA_JOB_ID.
    assert sb.last_env.get("EMBRY0_GIT_PROXY_URL") == "http://git-proxy:9101"
    assert sb.last_env.get("QA_JOB_ID") == "run-4__bootstrap"
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
        image_tag = "raven/hub:sha-deadbeef"

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
    assert captured_kwargs.get("prebaked_image_tag") == "raven/hub:sha-deadbeef"
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
        async def create(self, job_id, profile, env, repo=None):
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

    bus.publish.assert_awaited_once()
    args, _kwargs = bus.publish.await_args
    assert args[0] == "RUN-DONE-1"
    event = args[1]
    assert event["type"] == "done"
    assert event["run_id"] == "RUN-DONE-1"
    assert event["overall_status"] == "passed"


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


# ── Phase 5G: workspace_provider override from qa_workspace_provider_overrides_repo ──


@pytest.mark.asyncio
async def test_orchestrator_applies_workspace_provider_override(monkeypatch):
    """When the override repo returns a row for the current repo, the
    orchestrator MUST call load_provider with the override's type+config,
    not the qa.yaml's workspace_provider."""
    from datetime import UTC, datetime
    from pathlib import Path

    import structlog.testing

    from embry0.storage.repositories.qa_workspace_provider_overrides import (
        WorkspaceProviderOverride,
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
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )

    captured: dict = {}

    def fake_load_provider(name, root, config):
        captured["name"] = name
        captured["config"] = dict(config)
        return fake_provider

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", fake_load_provider)

    async def fake_run_subtask(resolved, **kw):
        from embry0.workflows.qa.subtask_result_schema import (
            CacheHits,
            SubTaskResult,
            SubTaskStatus,
        )

        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=1,
            cache_hits=CacheHits(),
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)

    override_repo = AsyncMock()
    override_repo.get = AsyncMock(
        return_value=WorkspaceProviderOverride(
            repo="org/repo",
            provider_type="overridden-provider",
            config={"affected_filter": "[HEAD^1]", "apps_glob": "apps/*"},
            updated_at=datetime.now(UTC),
        )
    )

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()

    state = {
        "job_id": "run-override-applied",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": ["apps/hub/app/page.tsx"],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_workspace_provider_overrides_repo": override_repo,
        }
    }

    with structlog.testing.capture_logs() as log_entries:
        out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    # The override's type + config must have been passed to load_provider —
    # not the qa.yaml's "fake".
    assert captured["name"] == "overridden-provider"
    assert captured["config"] == {
        "affected_filter": "[HEAD^1]",
        "apps_glob": "apps/*",
    }
    override_repo.get.assert_awaited_once_with("org/repo")

    # The override-applied log line must carry both the pre-override qa.yaml
    # snapshot AND the override values so the diff is reconstructable from
    # one line — not just provider_type.
    applied = [e for e in log_entries if e.get("event") == "qa_workspace_provider_override_applied"]
    assert len(applied) == 1, f"expected one override-applied log, got: {applied}"
    entry = applied[0]
    assert entry["repo"] == "org/repo"
    assert entry["qa_yaml_provider_type"] == "fake"
    assert entry["qa_yaml_provider_config"] == {}
    assert entry["override_provider_type"] == "overridden-provider"
    assert entry["override_provider_config"] == {
        "affected_filter": "[HEAD^1]",
        "apps_glob": "apps/*",
    }


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_qa_yaml_when_no_override(monkeypatch):
    """When the override repo returns None, the qa.yaml workspace_provider
    is used unchanged."""
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )

    captured: dict = {}

    def fake_load_provider(name, root, config):
        captured["name"] = name
        captured["config"] = dict(config)
        return fake_provider

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", fake_load_provider)

    override_repo = AsyncMock()
    override_repo.get = AsyncMock(return_value=None)

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()

    state = {
        "job_id": "run-override-absent",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": [],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_workspace_provider_overrides_repo": override_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    # qa.yaml's workspace_provider.type is "fake" — no override applied.
    assert captured["name"] == "fake"
    override_repo.get.assert_awaited_once_with("org/repo")


@pytest.mark.asyncio
async def test_orchestrator_handles_override_lookup_failure(monkeypatch):
    """If the override repo raises during get(), the run continues with the
    qa.yaml-based config — admin-side hiccup must not block QA."""
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    fake_provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )

    captured: dict = {}

    def fake_load_provider(name, root, config):
        captured["name"] = name
        return fake_provider

    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", fake_load_provider)

    override_repo = AsyncMock()
    override_repo.get = AsyncMock(side_effect=RuntimeError("db blew up"))

    qa_app_results_repo = AsyncMock()
    qa_app_results_repo.upsert_with_boot_phase = AsyncMock()

    state = {
        "job_id": "run-override-broken",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2,
            "changed_files": [],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": qa_app_results_repo,
            "qa_workspace_provider_overrides_repo": override_repo,
        }
    }

    out = await qa_orchestrator_node(state, config)
    # The run continues and finishes with overall_status=passed (no apps).
    assert out["qa"]["outcome"]["overall_status"] == "passed"
    # Fell through to qa.yaml's fake provider.
    assert captured["name"] == "fake"


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
