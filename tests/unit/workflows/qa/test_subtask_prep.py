"""Tests for prep_qa_sandbox_clone — xai-proxy bearer delivery (EMB-45 Phase C)."""

from __future__ import annotations

from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone


class _FakeDocker:
    def __init__(self):
        self.execs: list[list[str]] = []

    def build_exec_cmd(self, container_id, cmd):
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd, timeout=None):
        self.execs.append(list(cmd))
        return "abc123headsha\n"


class _FakeProxyMgr:
    def __init__(self, git_proxy_url="", xai_proxy_url=""):
        self.git_proxy_url = git_proxy_url
        self.xai_proxy_url = xai_proxy_url


async def _prep(docker, proxy_mgr):
    return await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=proxy_mgr,
        container_id="cid-1",
        sandbox_token="tok-secret",
        job_id="job-1",
        repo="owner/repo",
        branch="main",
        is_dind=False,
        qa_net="",
        base=[],
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
    assert "tok-secret" in script
    assert 'chmod 600 "$HOME/.embry0/xai_proxy_token"' in script


async def test_no_token_write_when_proxy_down():
    docker = _FakeDocker()
    await _prep(docker, _FakeProxyMgr())
    assert _token_execs(docker) == []


async def test_no_token_write_without_proxy_mgr():
    docker = _FakeDocker()
    await _prep(docker, None)
    assert _token_execs(docker) == []
