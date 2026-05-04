"""ProxyManager launches proxies as containers in DinD.

We mock DockerClient and assert ProxyManager issues the right `docker run` /
`docker network connect` / `docker rm -f` commands and records DNS URLs.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.proxy.manager import ProxyManager

_ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _mock_backend_resolution(monkeypatch):
    """Phase 1.5 minio-proxy/presign-proxy resolve `minio` and `orchestrator`
    via the orchestrator's DNS at start() time. In unit tests that DNS isn't
    available; stub it to a fixed IP so start() doesn't blow up.
    """
    monkeypatch.setattr(
        "athanor.execution.proxy.manager.socket.gethostbyname",
        lambda host: "10.99.0.1",
    )


@pytest.fixture
def docker_mock():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value=b"ok")
    # build_run_proxy_cmd now returns (cmd, env_file_path) — return a fake tuple so
    # _launch can unpack it; the env_file_path uses /dev/null so os.unlink is a no-op.
    docker.build_run_proxy_cmd = MagicMock(
        side_effect=lambda **kwargs: (["docker", "run", "FAKE", kwargs.get("name", "")], "/dev/null")
    )
    docker.build_network_cmd = MagicMock(side_effect=lambda *args: ["docker", "network", *args])
    docker.build_rm_cmd = MagicMock(side_effect=lambda c: ["docker", "rm", "-f", c])
    return docker


@pytest.mark.asyncio
async def test_start_launches_git_proxy(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="ghp_test", anthropic_api_key="")
    docker_mock.build_run_proxy_cmd.assert_any_call(
        name="git-proxy",
        image="athanor-proxy:latest",
        network="sandbox-restricted",
        env={
            "PROXY_TYPE": "git",
            "GITHUB_TOKEN": "ghp_test",
            "LISTEN_PORT": "9101",
            "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
        },
    )
    assert pm.git_proxy_url == "http://git-proxy:9101"


@pytest.mark.asyncio
async def test_start_launches_github_proxy_dual_network(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="ghp_test", anthropic_api_key="")
    docker_mock.build_run_proxy_cmd.assert_any_call(
        name="github-proxy",
        image="athanor-proxy:latest",
        network="sandbox-restricted",
        env={
            "PROXY_TYPE": "github",
            "GITHUB_TOKEN": "ghp_test",
            "LISTEN_PORT": "9103",
            "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
        },
    )
    docker_mock.build_network_cmd.assert_any_call("connect", "sandbox-internet", "github-proxy")
    assert pm.github_proxy_url == "http://github-proxy:9103"


@pytest.mark.asyncio
async def test_start_launches_auth_proxy_when_key_set(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="", anthropic_api_key="sk-ant-test", enable_auth_proxy=True)
    docker_mock.build_run_proxy_cmd.assert_any_call(
        name="auth-proxy",
        image="athanor-proxy:latest",
        network="sandbox-restricted",
        env={
            "PROXY_TYPE": "auth",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "LISTEN_PORT": "9100",
            "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
        },
    )
    docker_mock.build_network_cmd.assert_any_call("connect", "sandbox-internet", "auth-proxy")
    assert pm.auth_proxy_url == "http://auth-proxy:9100"


@pytest.mark.asyncio
async def test_start_skips_credential_proxies_when_no_creds(docker_mock):
    """Without creds, the credential-injection proxies (git/github/auth) are
    skipped. The Phase 1.5 network-plumbing proxies (minio/presign) ALWAYS
    launch — they don't carry credentials, only network plumbing."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="", anthropic_api_key="")
    launched_names = [c.kwargs.get("name") for c in docker_mock.build_run_proxy_cmd.call_args_list]
    assert "git-proxy" not in launched_names
    assert "github-proxy" not in launched_names
    assert "auth-proxy" not in launched_names
    # minio/presign always launch — they're pure network plumbing.
    assert "minio-proxy" in launched_names
    assert "presign-proxy" in launched_names
    assert pm.git_proxy_url == ""
    assert pm.github_proxy_url == ""
    assert pm.auth_proxy_url == ""
    assert pm.minio_proxy_url == "http://minio-proxy:9100"
    assert pm.presign_proxy_url == "http://presign-proxy:9104"


@pytest.mark.asyncio
async def test_start_launches_minio_and_presign_proxies(docker_mock):
    """Phase 1.5: minio-proxy and presign-proxy are spawned with resolved
    upstream IPs and are attached to sandbox-internet (so they can NAT back
    to host backend services)."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="", anthropic_api_key="")

    docker_mock.build_run_proxy_cmd.assert_any_call(
        name="minio-proxy",
        image="athanor-proxy:latest",
        network="sandbox-restricted",
        env={
            "PROXY_TYPE": "minio",
            # 10.99.0.1 is the stub from _mock_backend_resolution.
            "UPSTREAM_URL": "http://10.99.0.1:9000",
            "LISTEN_PORT": "9100",
        },
    )
    docker_mock.build_run_proxy_cmd.assert_any_call(
        name="presign-proxy",
        image="athanor-proxy:latest",
        network="sandbox-restricted",
        env={
            "PROXY_TYPE": "presign",
            "UPSTREAM_URL": "http://10.99.0.1:8000",
            "LISTEN_PORT": "9104",
        },
    )
    # Both must be attached to sandbox-internet for NAT egress.
    docker_mock.build_network_cmd.assert_any_call("connect", "sandbox-internet", "minio-proxy")
    docker_mock.build_network_cmd.assert_any_call("connect", "sandbox-internet", "presign-proxy")


@pytest.mark.asyncio
async def test_stop_removes_all_launched_containers(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="ghp_test", anthropic_api_key="sk-ant-test", enable_auth_proxy=True)
    docker_mock.run_cmd.reset_mock()
    await pm.stop()
    rm_calls = [c.args[0] for c in docker_mock.run_cmd.call_args_list]
    flattened = [arg for call in rm_calls for arg in call]
    for name in ("git-proxy", "github-proxy", "auth-proxy"):
        assert name in flattened


@pytest.mark.asyncio
async def test_start_cleans_existing_containers_first(docker_mock):
    """start() must remove pre-existing proxy containers before launching new ones,
    so an orchestrator restart with old containers still around doesn't conflict."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="ghp_test", anthropic_api_key="")
    # Should have issued `docker rm -f git-proxy` etc. before the run commands.
    rm_call_args = [c.args[0] for c in docker_mock.run_cmd.call_args_list]
    flattened = [arg for call in rm_call_args for arg in call]
    # Confirm at least one rm command included git-proxy
    assert any("git-proxy" in arg or arg == "git-proxy" for arg in flattened)


@pytest.mark.asyncio
async def test_start_athanor_proxy_for_job_is_stub(docker_mock):
    """The per-job Athanor API proxy is deferred — the method must raise
    NotImplementedError loudly rather than silently returning an empty URL."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    with pytest.raises(NotImplementedError, match="Athanor API proxy is deferred"):
        await pm.start_athanor_proxy_for_job(
            issues_repo=None,
            inputs_repo=None,
            issue_id="iss-test",
            job_id="job-test",
            repo="owner/repo",
            db=None,
        )


@pytest.mark.asyncio
async def test_auth_proxy_not_launched_when_flag_disabled(docker_mock):
    """Default behavior: no auth-proxy even with api_key set."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="", anthropic_api_key="sk-ant-test", enable_auth_proxy=False)
    # auth-proxy must NOT be in the launched run commands
    run_calls = docker_mock.build_run_proxy_cmd.call_args_list
    launched_names = [c.kwargs.get("name") for c in run_calls]
    assert "auth-proxy" not in launched_names
    assert pm.auth_proxy_url == ""


@pytest.mark.asyncio
async def test_auth_proxy_launched_when_flag_enabled(docker_mock):
    """When the flag is explicitly true and api_key is set, launch the proxy."""
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="", anthropic_api_key="sk-ant-test", enable_auth_proxy=True)
    run_calls = docker_mock.build_run_proxy_cmd.call_args_list
    launched_names = [c.kwargs.get("name") for c in run_calls]
    assert "auth-proxy" in launched_names
    assert pm.auth_proxy_url == "http://auth-proxy:9100"
