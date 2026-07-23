import pytest

from embry0.orchestration.state import UnsupportedContextError
from embry0.workflows.issue_to_pr.nodes import init_node
from embry0.workspace_init.base import HttpFetchError, LocalContextDeniedError


@pytest.mark.asyncio
async def test_init_node_rejects_unknown_context_before_sandbox():
    # No sandbox_manager in config: proves the guard fires before any sandbox use.
    state = {"job_id": "job-x", "repo": "", "context": {"type": "bogus"}}
    with pytest.raises(UnsupportedContextError) as ei:
        await init_node(state, {"configurable": {}})
    assert ei.value.context_type == "bogus"


@pytest.mark.asyncio
async def test_init_node_rejects_unresolvable_http_host_before_sandbox():
    # validate() runs pre-sandbox: a host that doesn't resolve fails with no
    # sandbox manager ever consulted (none is present in config).
    state = {"job_id": "job-x", "repo": "", "context": {"type": "http", "url": "https://nx.invalid/y"}}
    with pytest.raises(HttpFetchError):
        await init_node(state, {"configurable": {}})


@pytest.mark.asyncio
async def test_init_node_rejects_local_with_empty_allowlist_before_sandbox():
    # Empty LOCAL_CONTEXT_ALLOWLIST = local contexts disabled, fail closed.
    state = {"job_id": "job-x", "repo": "", "context": {"type": "local", "path": "/etc"}}
    with pytest.raises(LocalContextDeniedError):
        await init_node(state, {"configurable": {"embry0_config": None}})
