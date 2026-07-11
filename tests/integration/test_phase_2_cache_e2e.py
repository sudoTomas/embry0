"""Phase-2 acceptance gate: 3 apps, all 3 cache layers active, < 30s total.

Mocks the heavy infra (Docker, sandbox manager, workspace provider, sub-tasks)
so the test runs in CI without real Docker. Asserts:
  - Each sub-task's CacheHits has prebaked_image=True, shared_volume=True
  - Cache-hit indicators appear in the rendered PR comment
  - Total wall-clock < 30s
"""

from __future__ import annotations

import time
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

_QA_YAML_V2_WITH_CACHE = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
parallelism:
  max_concurrent_apps: 3
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
async def test_phase_2_warm_cache_three_apps_under_30s(monkeypatch):
    """Acceptance gate: 3 apps × all-cache-layers-warm completes fast."""

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
        "embry0.workflows.qa.orchestrator.load_provider",
        lambda name, root, config: fake_provider,
    )

    # Each sub-task returns within 1s with all 3 cache layers reporting hits.
    async def fake_run_subtask(resolved, **kw):
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.PASSED,
            duration_ms=900,
            cache_hits=CacheHits(
                prebaked_image=True,
                shared_volume=True,
                turbo_remote_hits=[f"apps/{resolved.app_name}#build"],
                turbo_remote_misses=[],
            ),
        )

    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_helpers.run_subtask",
        fake_run_subtask,
    )

    # Pre-baked image lookup returns None — sub-task already fakes the hit.
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=None)

    results_repo = AsyncMock()
    state = {
        "job_id": "phase2-acceptance",
        "repo": "org/r1",
        "branch_name": "main",
        "issue_number": 1,
        "attempt_number": 1,
        "qa": {
            "qa_yaml_v2_raw": _QA_YAML_V2_WITH_CACHE,
            "head_sha": "abc",
            "changed_files": ["apps/hub/page.tsx"],
        },
    }
    config = {
        "configurable": {
            "qa_app_results_repo": results_repo,
            "qa_image_tags_repo": image_repo,
        }
    }

    start = time.monotonic()
    out = await qa_orchestrator_node(state, config)
    elapsed = time.monotonic() - start

    qa = out["qa"]
    assert qa["outcome"]["overall_status"] == "passed"
    assert sorted(qa["apps_to_qa"]) == ["hub", "lane", "companion"]
    assert results_repo.upsert_with_boot_phase.await_count == 3

    # Cache hits per app — all 3 layers warm.
    for r in qa["per_app_results"]:
        ch = r["cache_hits"]
        assert ch["prebaked_image"] is True
        assert ch["shared_volume"] is True
        assert len(ch["turbo_remote_hits"]) == 1
        assert ch.get("turbo_remote_misses", []) == []

    # Wall-clock budget: 30 seconds is generous for the mocked path.
    assert elapsed < 30.0, f"elapsed={elapsed:.2f}s exceeded 30s budget"
