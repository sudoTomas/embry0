"""Tests for prep_qa_sandbox_clone — xai-proxy bearer delivery (EMB-45 Phase C)."""

from __future__ import annotations

import pytest

from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone


class _FakeDocker:
    def __init__(self):
        self.execs: list[list[str]] = []
        self.stdin: list[bytes | None] = []

    def build_exec_cmd(self, container_id, cmd, **kwargs):
        return ["docker", "exec", *(["-u", kwargs["user"]] if kwargs.get("user") else []), container_id, *cmd]

    async def run_cmd(self, cmd, timeout=None, stdin_data=None):
        self.execs.append(list(cmd))
        self.stdin.append(stdin_data)
        return "abc123headsha\n"


class _FakeProxyMgr:
    def __init__(self, git_proxy_url="", xai_proxy_url=""):
        self.git_proxy_url = git_proxy_url
        self.xai_proxy_url = xai_proxy_url


async def _prep(docker, proxy_mgr, prefs_repo=None, sandbox_token="tok-secret"):
    return await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=proxy_mgr,
        container_id="cid-1",
        sandbox_token=sandbox_token,
        job_id="job-1",
        repo="owner/repo",
        branch="main",
        is_dind=False,
        qa_net="",
        prefs_repo=prefs_repo,
    )


def _token_execs(docker):
    return [c for c in docker.execs if any("xai_proxy_token" in part for part in c)]


async def test_writes_xai_token_when_proxy_up():
    docker = _FakeDocker()
    cloned = await _prep(docker, _FakeProxyMgr(xai_proxy_url="http://xai-proxy:9106"))
    assert cloned.head_sha == "abc123headsha"
    token_execs = _token_execs(docker)
    assert len(token_execs) == 1
    script = token_execs[0][-1]
    assert "tok-secret" not in script, "the bearer goes on stdin, never on the command line"
    assert docker.stdin[docker.execs.index(token_execs[0])] == b"tok-secret"
    assert 'chmod 600 "$HOME/.embry0/xai_proxy_token"' in script


async def test_no_token_write_when_proxy_down():
    docker = _FakeDocker()
    await _prep(docker, _FakeProxyMgr())
    assert _token_execs(docker) == []


async def test_no_token_write_without_proxy_mgr():
    docker = _FakeDocker()
    await _prep(docker, None)
    assert _token_execs(docker) == []


# --- git identity (EMB-51) ---------------------------------------------------

# The git-proxy config builder validates token shape (^[A-Za-z0-9_-]{40,80}$);
# the git-proxy branch these tests exercise runs it, so the token must be real-shaped.
_VALID_TOKEN = "a" * 40


class _FakePrefsRepo:
    def __init__(self, row=None):
        self.row = row

    async def get(self, repo):
        return self.row


def _identity_script(docker):
    """The agent's ~/.gitconfig script (the identity its own commits use)."""
    scripts = [c[-1] for c in docker.execs if '--file "$HOME/.gitconfig" user.email' in c[-1]]
    assert len(scripts) == 1
    return scripts[0]


def _embry0_config(docker):
    """What embry0's own, root-owned git config was written with."""
    (data,) = [d for c, d in zip(docker.execs, docker.stdin, strict=True) if c[:4] == ["docker", "exec", "-u", "root"]]
    assert data is not None
    return data.decode()


async def test_default_identity_is_branding_not_fabricated_local():
    """The QA path must never emit the qa-agent@embry0.local identity again."""
    from embry0.branding import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME

    docker = _FakeDocker()
    await _prep(docker, _FakeProxyMgr(git_proxy_url="http://git-proxy:9101"), sandbox_token=_VALID_TOKEN)
    script = _identity_script(docker)
    assert "embry0.local" not in script
    assert GIT_AUTHOR_EMAIL in script
    assert GIT_AUTHOR_NAME in script
    assert GIT_AUTHOR_EMAIL in _embry0_config(docker) and "embry0.local" not in _embry0_config(docker)


async def test_per_repo_identity_override_applies():
    docker = _FakeDocker()
    prefs = _FakePrefsRepo({"git_author_name": "Acme Bot", "git_author_email": "bot@example.com"})
    await _prep(
        docker,
        _FakeProxyMgr(git_proxy_url="http://git-proxy:9101"),
        prefs_repo=prefs,
        sandbox_token=_VALID_TOKEN,
    )
    script = _identity_script(docker)
    assert "bot@example.com" in script
    assert "'Acme Bot'" in script
    assert '\tname = "Acme Bot"' in _embry0_config(docker)


async def test_the_qa_sandbox_reaches_its_repo_through_the_git_proxy():
    """#521: the repo URL is rewritten to the proxy, and nothing asks the proxy for the forge token."""
    docker = _FakeDocker()
    await _prep(docker, _FakeProxyMgr(git_proxy_url="http://git-proxy:9101"), sandbox_token=_VALID_TOKEN)
    script = _identity_script(docker)
    assert "url.http://git-proxy:9101/git/owner/repo.git.insteadOf https://github.com/owner/repo.git" in script
    assert _VALID_TOKEN not in script, "the bearer goes on stdin, never on the command line"
    assert f"Authorization: Bearer {_VALID_TOKEN}" in _embry0_config(docker)
    assert "/git-credentials" not in script


# ---- workspace reuse for prebaked-image sandboxes (RAV-1400) ----------------
#
# Sandboxes based on a pre-baked image carry the bake-time /workspace (and
# Docker copies it into any fresh named volume mounted there), so the
# standard path must fetch+checkout when /workspace is already a git repo
# instead of dying on "destination path '/workspace' already exists".


def _reuse_docker():
    from unittest.mock import AsyncMock, Mock

    docker = Mock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = Mock(side_effect=lambda cid, cmd, **_kw: ["exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="abc123\n")
    return docker


@pytest.mark.asyncio
async def test_standard_path_reuses_existing_git_workspace() -> None:
    docker = _reuse_docker()
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=None,
        container_id="c1",
        sandbox_token="tok-" + "A" * 40,
        job_id="job-1",
        repo="acme/console",
        branch="embry0-smoke/eng-1400-qa",
        is_dind=False,
        qa_net="",
    )
    shells = [" ".join(c.args[0]) for c in docker.run_cmd.await_args_list]
    clone_shell = next(s for s in shells if "embry0_git clone" in s)
    # Guard: reuse a baked checkout via fetch+checkout, after checking it: another sandbox made it...
    assert "if [ -e /workspace/.git ] || [ -L /workspace/.git ]; then\n  embry0_git_repo /workspace\n" in clone_shell
    assert "fetch --no-recurse-submodules origin +refs/heads/embry0-smoke/eng-1400-qa" in clone_shell
    # ...forcing the checkout (-f) so the bake-time `npm install` that dirties
    # package-lock.json can't abort the branch switch with "local changes would
    # be overwritten"; node_modules is gitignored and survives the force.
    assert "checkout -f -B embry0-smoke/eng-1400-qa" in clone_shell
    # ...with the plain clone kept as the empty-workspace fallback.
    assert "embry0_git clone --depth=50 --branch embry0-smoke/eng-1400-qa" in clone_shell
    assert ".qa/logs" in clone_shell


@pytest.mark.asyncio
async def test_cached_volume_path_still_skips_clone_entirely() -> None:
    docker = _reuse_docker()
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=None,
        container_id="c1",
        sandbox_token="tok-" + "A" * 40,
        job_id="job-1",
        repo="org/r1",
        branch="main",
        is_dind=False,
        qa_net="",
        cached_volume="vol-1",
    )
    shells = [" ".join(c.args[0]) for c in docker.run_cmd.await_args_list]
    assert not any("embry0_git clone" in s for s in shells)
    assert any("mkdir -p .qa/logs .qa/pids" in s for s in shells)


# --- forge-aware clone origin (spec 3.6) ------------------------------------

_SELF_MANAGED = "https://gitlab.example.com"
_NESTED = "acme/group/backend"


def _clone_shell(docker):
    return next(c[-1] for c in docker.execs if "embry0_git clone" in c[-1])


async def test_clone_defaults_to_github():
    docker = _FakeDocker()
    await _prep(docker, None)
    assert "https://github.com/owner/repo.git" in _clone_shell(docker)


async def test_clone_uses_the_self_managed_gitlab_origin_and_nested_path():
    docker = _FakeDocker()
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=None,
        container_id="cid-1",
        sandbox_token="tok-secret",
        job_id="job-1",
        repo=_NESTED,
        branch="dev",
        is_dind=False,
        qa_net="",
        forge="gitlab",
        gitlab_url=_SELF_MANAGED,
    )
    shell = _clone_shell(docker)
    assert f"{_SELF_MANAGED}/{_NESTED}.git" in shell
    assert "github.com" not in shell


async def test_gitlab_without_a_url_falls_back_to_gitlab_com():
    docker = _FakeDocker()
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=None,
        container_id="cid-1",
        sandbox_token="tok-secret",
        job_id="job-1",
        repo="group/project",
        branch="main",
        is_dind=False,
        qa_net="",
        forge="gitlab",
    )
    assert "https://gitlab.com/group/project.git" in _clone_shell(docker)


@pytest.mark.asyncio
async def test_a_dind_sandbox_joins_its_qa_network_through_the_runtime() -> None:
    from embry0.execution.runtime import DockerRuntime

    docker = _reuse_docker()
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=None,
        container_id="c1",
        sandbox_token="tok-" + "A" * 40,
        job_id="job-1",
        repo="org/r1",
        branch="main",
        is_dind=True,
        qa_net="qa-net-job-1",
        runtime=DockerRuntime(docker),
    )
    first = docker.run_cmd.await_args_list[0]
    assert first.args[0] == ["docker", "network", "connect", "qa-net-job-1", "c1"]


@pytest.mark.asyncio
async def test_a_dind_sandbox_without_a_runtime_is_refused() -> None:
    docker = _reuse_docker()
    with pytest.raises(RuntimeError, match="needs the runtime"):
        await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=None,
            container_id="c1",
            sandbox_token="tok-" + "A" * 40,
            job_id="job-1",
            repo="org/r1",
            branch="main",
            is_dind=True,
            qa_net="qa-net-job-1",
        )
    docker.run_cmd.assert_not_awaited()
