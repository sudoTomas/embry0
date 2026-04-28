"""Init node — sandbox creation and git credential wiring."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_extract_ask_user_events_normalizes_fields():
    """_extract_ask_user_events pulls agent_ask_user events out of the event
    stream and normalizes each to {question, category, options, asking_node,
    importance}. Non-matching events are ignored."""
    from athanor.workflows.issue_to_pr.nodes import _extract_ask_user_events

    agent_output = {
        "events": [
            {"type": "text", "text": "hello"},
            {
                "type": "agent_ask_user",
                "question": "q1",
                "category": "design",
                "options": ["a", "b"],
            },
            {"type": "agent_ask_user", "question": "q2", "category": "general"},
            {"type": "tool_call", "name": "Read"},
        ]
    }

    pending = _extract_ask_user_events(agent_output, calling_node="developer")

    assert len(pending) == 2
    # question is inlined with category prefix + options suffix so the
    # dashboard sees the context even though issue_inputs only persists
    # the question TEXT column.
    assert "q1" in pending[0]["question"]
    assert "[design]" in pending[0]["question"]
    assert "Options: a | b" in pending[0]["question"]
    assert pending[0]["category"] == "design"
    assert pending[0]["options"] == ["a", "b"]
    assert pending[0]["asking_node"] == "developer"
    assert pending[0]["importance"] == "blocking"
    # Second question has no options and 'general' category — no prefix/suffix added.
    assert pending[1]["question"] == "q2"
    assert pending[1]["category"] == "general"
    assert pending[1]["options"] == []


def test_extract_ask_user_events_calling_node_propagated():
    """calling_node is embedded in each question so triage/review questions
    are attributed correctly — not hardcoded to 'developer'."""
    from athanor.workflows.issue_to_pr.nodes import _extract_ask_user_events

    agent_output = {
        "events": [
            {"type": "agent_ask_user", "question": "Should we split?", "category": "general"},
        ]
    }

    for node in ("triage", "developer", "review"):
        pending = _extract_ask_user_events(agent_output, calling_node=node)
        assert len(pending) == 1
        assert pending[0]["asking_node"] == node, (
            f"asking_node must be '{node}', got '{pending[0]['asking_node']}'"
        )


@pytest.mark.asyncio
async def test_init_node_passes_git_proxy_url_to_sandbox():
    """Init node must pass ATHANOR_GIT_PROXY_URL into the sandbox env based on
    proxy_manager.git_proxy_url. Without this, git ops inside the sandbox
    can't authenticate."""
    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _event: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    _, create_kwargs = sandbox_mgr.create.call_args
    env = create_kwargs.get("env", {}) or {}
    assert env.get("ATHANOR_GIT_PROXY_URL") == "http://git-proxy:9101", (
        f"ATHANOR_GIT_PROXY_URL must be in sandbox env. Got: {sorted(env)}"
    )
    assert "GITHUB_TOKEN" not in env, f"GITHUB_TOKEN must NOT be in sandbox env. Got: {sorted(env)}"


@pytest.mark.asyncio
async def test_init_node_git_credential_helper_curls_proxy():
    """The git credential helper configured inside the sandbox must reference the
    proxy URL via curl, never $GITHUB_TOKEN."""
    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    all_run_cmd_calls = [c.args for c in docker.run_cmd.await_args_list]
    credential_setup_found = False
    for args in all_run_cmd_calls:
        cmd_str = " ".join(str(p) for p in args[0])
        if "credential.helper" in cmd_str:
            credential_setup_found = True
            assert "curl" in cmd_str, f"Helper must curl proxy, got: {cmd_str}"
            assert "git-proxy:9101" in cmd_str, f"Helper must reference proxy URL, got: {cmd_str}"
            assert "$GITHUB_TOKEN" not in cmd_str, f"Helper must NOT reference $GITHUB_TOKEN, got: {cmd_str}"
            break
    assert credential_setup_found, f"No credential.helper setup exec call found. All calls: {all_run_cmd_calls}"


@pytest.mark.asyncio
async def test_init_node_skips_git_setup_when_no_proxy_url():
    """If proxy_manager is missing or has no git_proxy_url, init node logs a
    warning and skips the git setup step rather than silently configuring a
    broken credential helper."""
    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    "proxy_manager": proxy_mgr,
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    sandbox_mgr.create.assert_awaited_once()
    run_cmd_calls = [c.args for c in docker.run_cmd.await_args_list]
    for args in run_cmd_calls:
        cmd_str = " ".join(str(p) for p in args[0])
        assert "credential.helper" not in cmd_str, (
            f"Must not configure credential helper without a proxy URL, got: {cmd_str}"
        )


@pytest.mark.asyncio
async def test_init_node_skips_git_setup_when_proxy_manager_is_none():
    """When proxy_manager is entirely absent from config (not just empty URL),
    init node must fall back to 'no credentials' path without crashing."""
    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        await init_node(
            state={"job_id": "job-xyz", "repo": "org/repo"},
            config={
                "configurable": {
                    "sandbox_manager": sandbox_mgr,
                    "docker": docker,
                    # Explicitly no proxy_manager entry
                }
            },
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    # Sandbox was still created
    sandbox_mgr.create.assert_awaited_once()
    # No credential helper setup was attempted
    run_cmd_calls = [c.args for c in docker.run_cmd.await_args_list]
    for args in run_cmd_calls:
        cmd_str = " ".join(str(p) for p in args[0])
        assert "credential.helper" not in cmd_str, (
            f"Must not configure credential helper without proxy_manager, got: {cmd_str}"
        )


@pytest.mark.asyncio
async def test_init_node_clone_failure_raises_runtime_error():
    """If the clone command exits non-zero, init node must raise RuntimeError
    (no silent success with empty /workspace)."""
    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    # First call (credential setup) succeeds, second (clone) raises
    async def _run_cmd_side(*args, **kwargs):
        cmd_str = " ".join(str(p) for p in args[0])
        if "git clone" in cmd_str:
            raise RuntimeError("exit 128: fatal: not a repo")
        return ""

    docker.run_cmd = AsyncMock(side_effect=_run_cmd_side)

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        with pytest.raises(RuntimeError) as exc_info:
            await init_node(
                state={"job_id": "job-xyz", "repo": "org/repo"},
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": docker,
                        "proxy_manager": proxy_mgr,
                    }
                },
            )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    assert "clone failed" in str(exc_info.value).lower()
    # Partial-cleanup: container was destroyed
    sandbox_mgr.destroy.assert_awaited_once()
