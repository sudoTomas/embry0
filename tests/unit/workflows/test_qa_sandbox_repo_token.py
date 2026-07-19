"""Regression: every QA sandbox create() must pass repo= so ProxyManager
enrollment resolves the per-owner GitHub token (GITHUB_TOKEN__<OWNER>).

Without repo=, enrollment falls back to the default token and the in-sandbox
clone of another owner's private repo 403s ("Write access to repository not
granted"), failing the QA run before any agent time is spent.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa import nodes, orchestrator, subtask_nodes


class _Sentinel(RuntimeError):
    """Raised by the mocked create() to stop the node right after the call."""


def _sandbox_mgr() -> AsyncMock:
    mgr = AsyncMock()
    mgr.create = AsyncMock(side_effect=_Sentinel("stop after create"))
    return mgr


def _docker() -> MagicMock:
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=[])
    docker.run_cmd = AsyncMock(return_value="")
    return docker


def _profiles_repo(profile: dict | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=profile or {"dind_enabled": False})
    return repo


@pytest.mark.asyncio
async def test_init_qa_node_passes_repo_to_create():
    sandbox_mgr = _sandbox_mgr()
    config = {
        "configurable": {
            "docker": _docker(),
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": _profiles_repo(),
            "qa_minio_sandbox": MagicMock(),
            "qa_token_registry": MagicMock(),
            "proxy_manager": MagicMock(git_proxy_url=""),
        }
    }
    state = {"job_id": "job-1", "repo": "acme-corp/widgets", "branch_name": "main"}

    with pytest.raises(_Sentinel):
        await nodes.init_qa_node(state, config)

    _, kwargs = sandbox_mgr.create.call_args
    assert kwargs.get("repo") == "acme-corp/widgets"


@pytest.mark.asyncio
async def test_init_orchestrator_node_passes_repo_to_create():
    sandbox_mgr = _sandbox_mgr()
    config = {
        "configurable": {
            "docker": _docker(),
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": _profiles_repo(),
            "proxy_manager": MagicMock(git_proxy_url=""),
        }
    }
    state = {"job_id": "job-1", "repo": "acme-corp/widgets", "branch_name": "main"}

    try:
        await orchestrator.init_orchestrator_node(state, config)
    except _Sentinel:
        pass  # node may catch into an infra outcome or re-raise; either is fine

    _, kwargs = sandbox_mgr.create.call_args
    assert kwargs.get("repo") == "acme-corp/widgets"


@pytest.mark.asyncio
async def test_acquire_sandbox_node_passes_repo_to_create():
    sandbox_mgr = _sandbox_mgr()
    config = {
        "configurable": {
            "docker": _docker(),
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": _profiles_repo(),
            "qa_minio_sandbox": MagicMock(),
            "qa_token_registry": MagicMock(),
            "proxy_manager": MagicMock(git_proxy_url=""),
        }
    }
    resolved = SimpleNamespace(
        app_name="quoting",
        boot_command=None,
        frontend_url="http://example.test/",
        mode="process",
        sandbox_profile="qa-external",
        ready_checks=[],
        boot_timeout_seconds=60,
        seed_command=None,
        e2e=None,
        acceptance_criteria=[],
        target="deployed",
        auth=None,
    )
    state = {
        "parent_run_id": "job-1",
        "repo": "acme-corp/widgets",
        "branch_name": "main",
        "resolved": resolved,
    }

    result = await subtask_nodes.acquire_sandbox_node(state, config)

    _, kwargs = sandbox_mgr.create.call_args
    assert kwargs.get("repo") == "acme-corp/widgets"
    # The node swallows the sentinel into an infra-failure result.
    assert result["status"] == subtask_nodes.SubTaskStatus.INFRA_FAILURE
