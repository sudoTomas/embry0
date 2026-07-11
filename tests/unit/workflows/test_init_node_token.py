from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.issue_to_pr import nodes


@pytest.mark.asyncio
async def test_init_node_passes_repo_to_create(monkeypatch):
    # Silence the LangGraph stream writer (needs a runtime otherwise).
    monkeypatch.setattr(nodes, "get_stream_writer", lambda: lambda *a, **k: None)

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("sandbox-xyz", "tok-abc"))
    docker = AsyncMock()
    proxy_mgr = MagicMock(git_proxy_url="")  # empty => skip clone/cred branch

    state = {
        "job_id": "job-1",
        "repo": "acme-corp/widgets",
        "context": {"type": "git", "repo": "acme-corp/widgets"},
    }
    config = {"configurable": {"sandbox_manager": sandbox_mgr, "docker": docker, "proxy_manager": proxy_mgr}}

    await nodes.init_node(state, config)

    _, kwargs = sandbox_mgr.create.call_args
    assert kwargs.get("repo") == "acme-corp/widgets"
