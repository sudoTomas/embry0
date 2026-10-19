"""Sandbox teardown survives a hung docker, through one path.

``DockerClient.run_cmd`` raises ``TimeoutError`` when a command outlives its
timeout, and ``TimeoutError`` is not a ``RuntimeError``. Teardown caught only
the latter, so one hung ``docker stop`` raised out of it: the forced ``rm``
was never sent, the reaper stopped at that container, and the rest of its
tick (the QA orphan sweep, the paused-job expiry) was skipped. The reaper
also ran its own ``stop`` and ``rm`` instead of ``SandboxManager.destroy``,
so it skipped the proxy unenroll, the egress-rule removal and the bearer
release. And ``destroy`` keyed the unenroll and the bearer release by the
reference it was handed, while ``create`` keys them by the full container
ID: the reaper hands it the short ID ``docker ps`` prints, the expiry the
container's name. One test per caller, and one for the keying.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.image_manager import ContainerReaper
from embry0.execution.runtime import DockerRuntime
from embry0.execution.sandbox_manager import SandboxManager
from embry0.safety.redaction import known_secret_values

_HUNG = TimeoutError("docker command timed out after 60s")

# Full IDs as `docker run` prints them, which `create` keys everything by.
FULL_A, FULL_B = "a" * 64, "b" * 64
FULL_JOB_A, FULL_JOB_B = "c" * 64, "d" * 64


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S +0000 UTC")


class _Daemon:
    """A docker CLI stand-in whose ``stop`` hangs for the named containers.

    ``containers`` are ``(full id, name)``. ``ps`` prints the 12-character
    short ID, as docker does without ``--no-trunc``; ``inspect --format
    {{.Id}}`` resolves a name or a short ID to the full ID.
    """

    def __init__(
        self,
        containers: list[tuple[str, str]] | None = None,
        *,
        hung_stop: set[str],
        listed: bool = True,
    ) -> None:
        self.containers = containers or []
        self.listed = listed  # False: on the daemon, but not in the reaper's `ps`
        self.hung_stop = hung_stop
        self.sent: list[list[str]] = []
        self.docker = MagicMock()
        self.docker._build_base_cmd = MagicMock(return_value=["docker"])
        self.docker.build_stop_cmd = MagicMock(
            side_effect=lambda c, timeout=10: ["docker", "stop", "-t", str(timeout), c]
        )
        self.docker.build_rm_cmd = MagicMock(side_effect=lambda c: ["docker", "rm", "-f", c])
        self.docker.build_inspect_id_cmd = MagicMock(
            side_effect=lambda c: ["docker", "inspect", "--format", "{{.Id}}", c]
        )
        self.docker.run_cmd = AsyncMock(side_effect=self._run)
        self.runtime = DockerRuntime(self.docker)
        self.proxy = MagicMock(unenroll_sandbox=AsyncMock())
        self.sandbox = SandboxManager(self.docker, proxy_manager=self.proxy, runtime=self.runtime)

    async def _run(self, cmd: list[str], timeout: int = 60, **_: object) -> str:
        self.sent.append(cmd)
        if cmd[1] == "ps":
            if not self.listed:
                return ""
            return "\n".join(f"{cid[:12]}\t{name}\t{_ago(48)}\tUp\tago" for cid, name in self.containers)
        if cmd[1] == "stop" and cmd[-1] in self.hung_stop:
            raise _HUNG
        if cmd[1] == "inspect" and cmd[-2] == "{{.Id}}":
            for full, name in self.containers:
                if cmd[-1] in (full, full[:12], name):
                    return full + "\n"
            raise RuntimeError("Error: No such object")
        if cmd[1] == "inspect":
            raise RuntimeError("no such object")  # no egress rules to clear
        return ""

    def removed(self) -> list[str]:
        return [c[-1] for c in self.sent if c[1] == "rm"]

    def unenrolled(self) -> list[str]:
        return [c.args[0] for c in self.proxy.unenroll_sandbox.await_args_list]


def _reaper(daemon: _Daemon, **kwargs: object) -> ContainerReaper:
    jobs = MagicMock(fetch_active_sandbox_containers=AsyncMock(return_value=set()))
    return ContainerReaper(daemon.runtime, max_age_hours=24, jobs_repo=jobs, sandbox_manager=daemon.sandbox, **kwargs)


@pytest.mark.asyncio
async def test_destroy_still_removes_a_container_whose_stop_timed_out() -> None:
    daemon = _Daemon(hung_stop={"sandbox-a"})
    try:
        await daemon.sandbox.destroy("sandbox-a")
    except TimeoutError as exc:
        pytest.fail(f"destroy raised on a hung stop and sent no rm: {exc}; sent {daemon.sent}")
    assert daemon.removed() == ["sandbox-a"], f"a hung stop left the container behind; sent {daemon.sent}"


@pytest.mark.asyncio
async def test_the_reaper_tears_down_every_stale_sandbox_through_destroy() -> None:
    daemon = _Daemon([(FULL_A, "sandbox-a"), (FULL_B, "sandbox-b")], hung_stop={FULL_A})
    await _reaper(daemon)._reap()
    assert daemon.removed() == [FULL_A, FULL_B], f"one hung stop ended the reap; sent {daemon.sent}"
    assert daemon.unenrolled() == [FULL_A, FULL_B], "the reaper's teardown skipped the proxy unenroll"


@pytest.mark.asyncio
async def test_paused_job_expiry_tears_down_through_destroy_despite_a_hung_stop() -> None:
    daemon = _Daemon(
        [(FULL_JOB_A, "sandbox-job-a"), (FULL_JOB_B, "sandbox-job-b")], hung_stop={FULL_JOB_A}, listed=False
    )
    db = AsyncMock(fetch=AsyncMock(return_value=[{"job_id": "job-a"}, {"job_id": "job-b"}]))
    db.fetchrow = AsyncMock(return_value=None)
    jobs = AsyncMock()
    reaper = _reaper(daemon, db=db, paused_ttl_hours=0)
    reaper._jobs_repo = jobs
    await reaper._expire_paused_jobs()
    assert jobs.update.await_count == 2, "a hung stop ended the expiry sweep"
    assert daemon.removed() == [FULL_JOB_A, FULL_JOB_B], f"sent {daemon.sent}"
    assert daemon.unenrolled() == [FULL_JOB_A, FULL_JOB_B], "expiry skipped the proxy unenroll"


@pytest.mark.asyncio
async def test_teardown_by_short_id_or_name_releases_what_create_keyed_by_full_id() -> None:
    """The reaper passes ``docker ps``'s short ID and the expiry the name;
    ``create`` enrolled and remembered the bearer under the full ID."""
    daemon = _Daemon([(FULL_A, "sandbox-a"), (FULL_JOB_A, "sandbox-job-a")], hung_stop=set(), listed=False)
    daemon.sandbox._remember_bearer(FULL_A, "bearer-of-a")
    daemon.sandbox._remember_bearer(FULL_JOB_A, "bearer-of-job-a")
    try:
        await daemon.sandbox.destroy(FULL_A[:12], timeout=5)
        await daemon.sandbox.destroy("sandbox-job-a", timeout=5)
        assert daemon.unenrolled() == [FULL_A, FULL_JOB_A], (
            "the proxies hold the enrollment under the full ID; unenrolling the short ID or the name is a no-op"
        )
        assert "bearer-of-a" not in known_secret_values(), "the bearer stayed a scrub target after teardown"
        assert "bearer-of-job-a" not in known_secret_values(), "the bearer stayed a scrub target after teardown"
    finally:
        daemon.sandbox._release_bearers(FULL_A)
        daemon.sandbox._release_bearers(FULL_JOB_A)


@pytest.mark.asyncio
async def test_one_failing_step_does_not_cost_the_rest_of_the_tick() -> None:
    reaper = _reaper(_Daemon(hung_stop=set()), check_interval_seconds=0)
    reaper._reap = AsyncMock(side_effect=_HUNG)
    reaper._sweep_qa_orphans = AsyncMock()
    # Cancelling from the last step ends the loop after exactly one tick.
    reaper._expire_paused_jobs = AsyncMock(side_effect=asyncio.CancelledError)
    try:
        await asyncio.wait_for(reaper._reap_loop(), timeout=2)
    except TimeoutError:
        pass
    assert reaper._sweep_qa_orphans.await_count >= 1, "a failed reap skipped the QA orphan sweep"
    assert reaper._expire_paused_jobs.await_count == 1, "a failed reap skipped the paused-job expiry"
