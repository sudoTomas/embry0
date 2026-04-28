"""Athanor API proxy is deferred — calling it must fail loudly."""

from unittest.mock import MagicMock

import pytest

from athanor.execution.proxy.manager import ProxyManager


@pytest.mark.asyncio
async def test_start_athanor_proxy_raises_not_implemented():
    docker = MagicMock()
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    with pytest.raises(NotImplementedError, match="Athanor API proxy is deferred"):
        await mgr.start_athanor_proxy_for_job(
            issues_repo=None,
            inputs_repo=None,
            issue_id="iss-x",
            job_id="job-x",
        )


def test_workflow_registry_rejects_deferred_tools():
    from athanor.workflows.registry import _validate_workflow_tools

    bad = {"agent_tools": {"developer": ["Read", "Write", "CreateIssue"]}}
    with pytest.raises(ValueError, match="declares deferred tool"):
        _validate_workflow_tools("custom_workflow", bad)


def test_workflow_registry_accepts_normal_tools():
    from athanor.workflows.registry import _validate_workflow_tools

    good = {"agent_tools": {"developer": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]}}
    _validate_workflow_tools("custom_workflow", good)  # no raise
