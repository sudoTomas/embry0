"""Git initializer command assembly + none initializer."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workspace_init.base import InitContext, WorkspaceInitError
from embry0.workspace_init.git import GitWorkspaceInitializer, build_clone_shell
from embry0.workspace_init.none import NoneWorkspaceInitializer


def _docker():
    d = MagicMock()
    d.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **kw: ["docker", "exec", cid, *cmd])
    d.run_cmd = AsyncMock(return_value="")
    return d


def test_clone_shell_brace_scopes_best_effort_fetch():
    shell = build_clone_shell("owner/repo")
    assert "{ git -C /workspace fetch origin main:main --depth=50 || true; }" in shell
    assert shell.startswith("git clone --depth=50 https://github.com/owner/repo.git /workspace")


def test_clone_shell_ref_appends_fetch_and_checkout():
    shell = build_clone_shell("owner/repo", "feature/x")
    assert "git -C /workspace checkout feature/x" in shell


def test_validate_rejects_shell_metacharacter_refs():
    init = GitWorkspaceInitializer()
    with pytest.raises(WorkspaceInitError, match="disallowed characters"):
        init.validate({"repo": "o/r", "ref": "x; rm -rf /"}, None)
    init.validate({"repo": "o/r", "ref": "release/v1.2.3"}, None)  # sane refs pass


def test_validate_requires_repo():
    with pytest.raises(WorkspaceInitError, match="requires 'repo'"):
        GitWorkspaceInitializer().validate({}, None)


@pytest.mark.asyncio
async def test_initialize_runs_cred_setup_then_clone():
    docker = _docker()
    ctx = InitContext(
        job_id="j",
        context={"type": "git", "repo": "o/r"},
        container_id="c1",
        sandbox_token="a" * 43,  # git_ops validates the token shape
        docker=docker,
        git_proxy_url="http://git-proxy:9101",
    )
    await GitWorkspaceInitializer().initialize(ctx)
    calls = [" ".join(str(p) for p in c.args[0]) for c in docker.run_cmd.await_args_list]
    assert any("credential" in c or "git config" in c for c in calls[:1])
    assert any("git clone" in c for c in calls)


@pytest.mark.asyncio
async def test_initialize_without_proxy_skips_cred_setup_but_clones():
    docker = _docker()
    ctx = InitContext(
        job_id="j",
        context={"type": "git", "repo": "o/r"},
        container_id="c1",
        sandbox_token="tok",
        docker=docker,
        git_proxy_url="",
    )
    await GitWorkspaceInitializer().initialize(ctx)
    calls = [" ".join(str(p) for p in c.args[0]) for c in docker.run_cmd.await_args_list]
    assert len(calls) == 1 and "git clone" in calls[0]


@pytest.mark.asyncio
async def test_clone_failure_raises_workspace_init_error():
    docker = _docker()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("exit 128"))
    ctx = InitContext(
        job_id="j",
        context={"type": "git", "repo": "o/r"},
        container_id="c1",
        sandbox_token="tok",
        docker=docker,
        git_proxy_url="",
    )
    with pytest.raises(WorkspaceInitError, match="clone failed"):
        await GitWorkspaceInitializer().initialize(ctx)


@pytest.mark.asyncio
async def test_none_initializer_creates_workspace_dir():
    docker = _docker()
    ctx = InitContext(
        job_id="j",
        context={"type": "none"},
        container_id="c1",
        sandbox_token="tok",
        docker=docker,
    )
    update = await NoneWorkspaceInitializer().initialize(ctx)
    assert update == {}
    cmd = " ".join(str(p) for p in docker.run_cmd.await_args.args[0])
    assert "mkdir -p /workspace" in cmd
