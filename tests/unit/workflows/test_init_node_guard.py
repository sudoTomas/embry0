import pytest

from embry0.orchestration.state import UnsupportedContextError
from embry0.workflows.issue_to_pr.nodes import init_node


@pytest.mark.asyncio
async def test_init_node_rejects_non_git_context_before_sandbox():
    # No sandbox_manager in config: proves the guard fires before any sandbox use.
    state = {"job_id": "job-x", "repo": "", "context": {"type": "http", "url": "https://x/y"}}
    with pytest.raises(UnsupportedContextError) as ei:
        await init_node(state, {"configurable": {}})
    assert ei.value.context_type == "http"
