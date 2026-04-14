"""Init node — sandbox creation and git credential wiring."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_init_node_passes_git_proxy_url_to_sandbox():
    """Init node must pass LEGION_GIT_PROXY_URL into the sandbox env based on
    proxy_manager.git_proxy_url. Without this, git ops inside the sandbox
    can't authenticate."""
    from legion.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-abc")
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://host.docker.internal:9101"

    import legion.workflows.issue_to_pr.nodes as nodes_module

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
    assert env.get("LEGION_GIT_PROXY_URL") == "http://host.docker.internal:9101", (
        f"LEGION_GIT_PROXY_URL must be in sandbox env. Got: {sorted(env)}"
    )
    assert "GITHUB_TOKEN" not in env, f"GITHUB_TOKEN must NOT be in sandbox env. Got: {sorted(env)}"


@pytest.mark.asyncio
async def test_init_node_git_credential_helper_curls_proxy():
    """The git credential helper configured inside the sandbox must reference the
    proxy URL via curl, never $GITHUB_TOKEN."""
    from legion.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-abc")
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://host.docker.internal:9101"

    import legion.workflows.issue_to_pr.nodes as nodes_module

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
            assert "host.docker.internal:9101" in cmd_str, f"Helper must reference proxy URL, got: {cmd_str}"
            assert "$GITHUB_TOKEN" not in cmd_str, f"Helper must NOT reference $GITHUB_TOKEN, got: {cmd_str}"
            break
    assert credential_setup_found, f"No credential.helper setup exec call found. All calls: {all_run_cmd_calls}"


@pytest.mark.asyncio
async def test_init_node_skips_git_setup_when_no_proxy_url():
    """If proxy_manager is missing or has no git_proxy_url, init node logs a
    warning and skips the git setup step rather than silently configuring a
    broken credential helper."""
    from legion.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-abc")
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    import legion.workflows.issue_to_pr.nodes as nodes_module

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
    from legion.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-abc")
    sandbox_mgr.destroy = AsyncMock()

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = AsyncMock(return_value="")

    import legion.workflows.issue_to_pr.nodes as nodes_module

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
    from legion.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-abc")
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
    proxy_mgr.git_proxy_url = "http://host.docker.internal:9101"

    import legion.workflows.issue_to_pr.nodes as nodes_module

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
