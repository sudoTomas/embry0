"""Git initializer command assembly + none initializer."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.git_refs import valid_ref
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
    assert "{ embry0_git fetch --no-recurse-submodules origin main:main --depth=50 || true; }" in shell
    assert shell.startswith("embry0_git clone --depth=50 https://github.com/owner/repo.git /workspace")


def test_clone_shell_ref_checks_out_local_branch_from_tracking_ref():
    """Branch refs must land as a LOCAL branch (follow-up jobs push back to
    it) — a bare `fetch origin <branch>` only reaches FETCH_HEAD and the old
    `checkout <branch>` died with "pathspec did not match" (RAV-1092)."""
    shell = build_clone_shell("owner/repo", "feature/x")
    assert "+refs/heads/feature/x:refs/remotes/origin/feature/x" in shell
    assert "embry0_git checkout -B feature/x refs/remotes/origin/feature/x" in shell
    # SHA/tag fallback: raw fetch + detached checkout.
    assert "embry0_git checkout --detach FETCH_HEAD" in shell
    # The clone itself must NOT be rescued by the fallback's || — the chain
    # still starts with clone && ...
    assert shell.startswith("embry0_git clone --depth=50")


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
    # embry0's own git config is written first (as root, from stdin), then the
    # agent's: #521, the git-proxy is the remote for o/r. The clone goes through it.
    assert "/run/embry0/git/config" in calls[0]
    assert "url.http://git-proxy:9101/git/o/r.git.insteadOf https://github.com/o/r.git" in calls[1]
    assert "embry0_git clone" in calls[2]


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
    assert len(calls) == 2, "embry0's own git config, then the clone; the agent's ~/.gitconfig is left alone"
    assert "/run/embry0/git/config" in calls[0] and "embry0_git clone" in calls[1]


@pytest.mark.asyncio
async def test_clone_failure_raises_workspace_init_error():
    docker = _docker()

    async def run(cmd, **_kw):
        if "embry0_git clone" in " ".join(cmd):
            raise RuntimeError("exit 128")
        return ""

    docker.run_cmd = AsyncMock(side_effect=run)
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


def test_validate_refuses_a_ref_ending_in_a_newline():
    """``$`` matches before a final newline, so ``match`` took ``main\\n``."""
    try:
        GitWorkspaceInitializer().validate({"repo": "o/r", "ref": "main\n"}, None)
    except WorkspaceInitError as exc:
        assert "disallowed characters" in str(exc), f"the refusal must name the ref: {exc}"
    else:
        raise AssertionError("a ref ending in a newline must be refused before the clone line is built")


@pytest.mark.parametrize("ref", ["-x", "--depth", "-"])
def test_valid_ref_refuses_a_ref_git_would_not_accept(ref):
    """git takes no branch or tag name that starts with ``-``."""
    assert not valid_ref(ref), f"a ref starting with '-' must not be valid: {ref!r}"


@pytest.mark.parametrize("ref", ["main", "fix-x", "release/v1-2", "a" * 40, "x" * 255])
def test_valid_ref_keeps_a_dash_inside_a_ref(ref):
    assert valid_ref(ref), f"a ref with a '-' after its first character is valid: {ref!r}"


@pytest.mark.asyncio
async def test_a_failure_to_configure_git_is_a_workspace_init_error():
    docker = _docker()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("Read-only file system"))
    ctx = InitContext(
        job_id="j", context={"type": "git", "repo": "o/r"}, container_id="c1", sandbox_token="tok", docker=docker
    )
    with pytest.raises(WorkspaceInitError, match="git could not be configured"):
        await GitWorkspaceInitializer().initialize(ctx)
