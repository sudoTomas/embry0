"""Removing a Docker object that is already gone is "nothing to do", not an error (#720).

A job's cleanup removes its QA network, its containers and volumes, and the sandbox's own container; any of them may
already be gone (a cancelled job, a reaper that came first, a container that exited and was removed). The daemon
answers that the object does not exist; that is logged at debug and the removal is done. Any other failure (the daemon
unreachable, a network still in use, a volume still mounted) stays an error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from embry0.execution.docker_client import DockerClient, ObjectGone, already_gone
from tests.unit.execution.test_docker_client import _Pipe, _Proc

UNREACHABLE = b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n"
NETWORK_GONE = b"Error response from daemon: network qa-net-job-1 not found\nexit status 1\n"
CONTAINER_GONE = b"Error response from daemon: No such container: sandbox-job-1\n"
VOLUME_GONE = b"Error response from daemon: get qa-vol-1: no such volume\n"


def _levels(logs: list[dict[str, Any]]) -> list[str]:
    return [entry["log_level"] for entry in logs if entry["event"] == "docker_cmd_failed"]


def _errors(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["log_level"] in ("error", "warning")]


@pytest.mark.parametrize(
    ("stderr", "gone"),
    [
        ("Error response from daemon: network qa-net-job-1 not found\nexit status 1", True),
        ("Error response from daemon: No such container: sandbox-job-1", True),
        ("Error: No such container: sandbox-job-1", True),
        ("Error response from daemon: get qa-vol-1: no such volume", True),
        ("Error: No such volume: qa-vol-1", True),
        ("Error response from daemon: No such network: qa-net-job-1", True),
        ("Error response from daemon: No such container: a\nError response from daemon: No such container: b", True),
        (UNREACHABLE.decode(), False),
        ("Error response from daemon: error while removing network: network qa-net-job-1 has active endpoints", False),
        ("Error response from daemon: remove qa-vol-1: volume is in use - [abc]", False),
        (
            "Error response from daemon: No such container: a\nError response from daemon: conflict: unable to remove",
            False,
        ),
        (
            "Error response from daemon: failed to set up container networking: network sandbox-internet not found",
            False,
        ),
        ("", False),
        ("exit status 1", False),
    ],
    ids=[
        "network",
        "container",
        "container-cli",
        "volume",
        "volume-cli",
        "network-cli",
        "two-gone",
        "daemon-unreachable",
        "network-in-use",
        "volume-in-use",
        "one-gone-one-refused",
        "not-there-inside-another-failure",
        "silent",
        "exit-status-alone",
    ],
)
def test_only_the_daemon_s_answer_that_the_object_is_not_there_is_gone(stderr: str, gone: bool) -> None:
    assert already_gone(stderr) is gone


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "level", "raised"),
    [(NETWORK_GONE, "debug", ObjectGone), (UNREACHABLE, "error", RuntimeError)],
    ids=["gone", "daemon-unreachable"],
)
async def test_a_removal_that_finds_nothing_is_logged_at_debug(stderr: bytes, level: str, raised: type) -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(), _Pipe(data=stderr), returncode=1)
        with pytest.raises(raised) as caught:
            await DockerClient().run_cmd(["docker", "network", "rm", "qa-net-job-1"], gone_ok=True)
    assert (raised is ObjectGone) is isinstance(caught.value, ObjectGone), caught.value
    assert _levels(logs) == [level], logs


@pytest.mark.asyncio
async def test_without_gone_ok_a_missing_object_is_still_an_error() -> None:
    """A command that is not a removal, an inspect of a container that should exist, keeps its error."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(), _Pipe(data=CONTAINER_GONE), returncode=1)
        with pytest.raises(RuntimeError) as caught:
            await DockerClient().run_cmd(["docker", "inspect", "sandbox-job-1"])
    assert not isinstance(caught.value, ObjectGone)
    assert _levels(logs) == ["error"], logs


def _runtime(docker: DockerClient) -> Any:
    from embry0.execution.runtime.docker import DockerRuntime

    return DockerRuntime(docker)


async def _remove_network(docker: DockerClient) -> None:
    await _runtime(docker).remove_network("qa-net-job-1")


async def _remove_containers(docker: DockerClient) -> None:
    await _runtime(docker).remove_containers(["sandbox-job-1"])


async def _remove_volumes(docker: DockerClient) -> None:
    await _runtime(docker).remove_volumes(["qa-vol-1"])


async def _qa_cleanup(docker: DockerClient) -> None:
    from embry0.workflows.qa.cleanup import cleanup_qa_resources

    runtime = _runtime(docker)
    runtime.list_containers = AsyncMock(return_value=[])
    runtime.list_volumes = AsyncMock(return_value=[])
    await cleanup_qa_resources(runtime, job_id="job-1")


async def _destroy_sandbox(docker: DockerClient) -> None:
    from embry0.execution.sandbox_manager import SandboxManager

    manager = SandboxManager(docker=docker, proxy_manager=MagicMock(unenroll_sandbox=AsyncMock()))
    await manager.destroy("sandbox-job-1")


async def _disconnect(docker: DockerClient) -> None:
    from embry0.execution.sandbox_manager import SandboxManager

    await SandboxManager(docker=docker, proxy_manager=MagicMock()).disconnect_network("sandbox-job-1", "qa-net-job-1")


async def _rollback(docker: DockerClient) -> None:
    from embry0.execution.sandbox_manager import SandboxManager

    await SandboxManager(docker=docker, proxy_manager=MagicMock())._rollback_rm("sandbox-job-1")  # noqa: SLF001


async def _remove_proxies(docker: DockerClient) -> list[str]:
    """The names it could not remove (a proxy already gone is removed)."""
    from embry0.execution.proxy.manager import ProxyManager

    manager = ProxyManager(docker, proxy_admin_token="admin-secret")
    manager._http = None  # noqa: SLF001
    with patch("embry0.execution.proxy.manager.asyncio.sleep", new=AsyncMock()):  # its retries' backoff
        return await manager._remove_proxies(["git-proxy"], phase="stop")  # noqa: SLF001


REMOVALS = [
    pytest.param(_remove_network, NETWORK_GONE, id="network"),
    pytest.param(_remove_containers, CONTAINER_GONE, id="containers"),
    pytest.param(_remove_volumes, VOLUME_GONE, id="volumes"),
    pytest.param(_qa_cleanup, NETWORK_GONE, id="qa-cleanup"),
    pytest.param(_destroy_sandbox, CONTAINER_GONE, id="sandbox-destroy"),
    pytest.param(_disconnect, CONTAINER_GONE, id="network-disconnect"),
    pytest.param(_rollback, CONTAINER_GONE, id="sandbox-rollback"),
    pytest.param(_remove_proxies, CONTAINER_GONE, id="proxy-cleanup"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("remove", "gone"), REMOVALS)
async def test_removing_what_is_already_gone_is_done_and_logs_no_error(remove: Any, gone: bytes) -> None:
    """Through the real client: each removal a cleanup makes, the object already gone."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        # A fresh answer for every command: the teardown may run several.
        proc.side_effect = lambda *_a, **_k: _Proc(_Pipe(), _Pipe(data=gone), returncode=1)
        left = await remove(DockerClient())
    assert not left, f"a removal of what is gone left something to remove: {left}"
    assert proc.await_count >= 1, "the removal reached the daemon"
    assert _errors(logs) == [], f"a removal of what is gone logged a fault: {_errors(logs)}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("remove", "gone"), REMOVALS)
async def test_a_removal_the_daemon_cannot_make_is_still_an_error(remove: Any, gone: bytes) -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.side_effect = lambda *_a, **_k: _Proc(_Pipe(), _Pipe(data=UNREACHABLE), returncode=1)
        try:
            await remove(DockerClient())
        except RuntimeError:
            pass  # the runtime's removals raise; the cleanups log and go on
    assert "error" in _levels(logs), logs
