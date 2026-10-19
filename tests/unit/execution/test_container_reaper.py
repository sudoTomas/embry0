"""Tests for ContainerReaper: timestamp parsing, active-job intersection, label selection."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.image_manager import ContainerReaper
from embry0.execution.runtime import DockerRuntime
from embry0.execution.sandbox_manager import SandboxManager

SANDBOX_LABELS = ("embry0.managed=true", "embry0.role=sandbox")
# Before any sandbox carried the label (#616).
PRE_LABEL = datetime(2026, 10, 1, tzinfo=UTC)


def _ago(hours: float, tz_abbrev: str = "UTC") -> str:
    """Format a UTC timestamp as Docker would: '<ts> <offset> <tz>'."""
    t = datetime.now(UTC) - timedelta(hours=hours)
    return t.strftime(f"%Y-%m-%d %H:%M:%S %z {tz_abbrev}")


@dataclass
class _Container:
    id: str
    name: str
    created: str
    labels: tuple[str, ...] = SANDBOX_LABELS


@dataclass
class _Daemon:
    """A docker CLI stand-in that applies `ps`'s name (substring) and label filters."""

    containers: list[_Container]
    removed: list[str] = field(default_factory=list)

    def client(self) -> MagicMock:
        docker = MagicMock()
        docker._build_base_cmd = MagicMock(return_value=["docker"])
        docker.build_stop_cmd = MagicMock(side_effect=lambda cid, timeout=5: ["docker", "stop", cid])
        docker.build_rm_cmd = MagicMock(side_effect=lambda cid: ["docker", "rm", cid])
        docker.run_cmd = AsyncMock(side_effect=self._run)
        return docker

    async def _run(self, cmd, timeout=None, **_):
        if cmd[1] == "ps":
            found = self.containers
            for i, arg in enumerate(cmd):
                if arg != "--filter":
                    continue
                key, _, value = cmd[i + 1].partition("=")
                if key == "name":
                    found = [c for c in found if value in c.name]
                elif key == "label":
                    found = [c for c in found if value in c.labels]
            return "\n".join(f"{c.id}\t{c.name}\t{c.created}\tUp\tago" for c in found)
        if cmd[1] == "rm":
            self.removed.append(cmd[2])
        return ""


def _reaper(daemon: _Daemon, active: set[str] | None = None, error: Exception | None = None) -> ContainerReaper:
    jobs_repo = MagicMock()
    if error is not None:
        jobs_repo.fetch_active_sandbox_containers = AsyncMock(side_effect=error)
    else:
        jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value=set(active or ()))
    docker = daemon.client()
    runtime = DockerRuntime(docker)
    sandbox = SandboxManager(docker, proxy_manager=MagicMock(unenroll_sandbox=AsyncMock()), runtime=runtime)
    return ContainerReaper(runtime, max_age_hours=24, jobs_repo=jobs_repo, sandbox_manager=sandbox)


@pytest.mark.asyncio
async def test_reaps_old_container_with_no_active_job():
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", _ago(48))])
    await _reaper(daemon)._reap()
    assert daemon.removed == ["abc123"]


@pytest.mark.asyncio
async def test_skips_container_backed_by_active_job():
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", _ago(48))])
    await _reaper(daemon, active={"sandbox-job-1"})._reap()
    assert daemon.removed == []


@pytest.mark.asyncio
async def test_skips_container_younger_than_threshold():
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", _ago(2))])
    await _reaper(daemon)._reap()
    assert daemon.removed == []


@pytest.mark.asyncio
async def test_handles_unparseable_timestamp_gracefully():
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", "About an hour ago")])
    await _reaper(daemon)._reap()  # no raise; logs warning and skips
    assert daemon.removed == []


@pytest.mark.asyncio
async def test_fails_closed_when_active_fetch_errors():
    """If we can't determine which jobs are active, do NOT reap (might destroy live work)."""
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", _ago(48))])
    await _reaper(daemon, error=Exception("db down"))._reap()
    assert daemon.removed == []


@pytest.mark.asyncio
async def test_reaps_old_container_with_non_utc_tz_abbrev():
    """Production runs with TZ=America/New_York — Docker emits EDT/EST suffix."""
    t = datetime.now(UTC) - timedelta(hours=48)
    edt = t.strftime("%Y-%m-%d %H:%M:%S -0400 EDT")
    daemon = _Daemon([_Container("abc123", "sandbox-job-1", edt)])
    await _reaper(daemon)._reap()
    assert daemon.removed == ["abc123"]


# ---- #620: selection by label, not by name ---------------------------------


@pytest.fixture
def labels_long_established(monkeypatch):
    """Move the label's introduction a month back, so a 48-hour-old unlabelled container post-dates it."""
    try:
        import embry0.execution.sandbox_listing as listing
    except ImportError:  # a tree from before #620 has no cutoff to move
        return
    monkeypatch.setattr(listing, "LABELLED_SINCE", datetime.now(UTC) - timedelta(days=30))


@pytest.mark.asyncio
@pytest.mark.usefixtures("labels_long_established")
async def test_an_unlabelled_container_named_like_a_sandbox_survives():
    """A container on the daemon that is not embry0's is left alone, whatever its name."""
    daemon = _Daemon(
        [
            _Container("theirs", "sandbox-dev-db", _ago(48), labels=()),
            _Container("also-theirs", "my-sandbox-thing", _ago(48), labels=()),
            _Container("ours", "sandbox-job-1", _ago(48)),
        ]
    )
    await _reaper(daemon)._reap()
    assert daemon.removed == ["ours"], f"only the labelled sandbox may be reaped, removed {daemon.removed}"


@pytest.mark.asyncio
async def test_a_labelled_sandbox_is_reaped_whatever_its_name():
    daemon = _Daemon([_Container("ours", "renamed-job-1", _ago(48))])
    await _reaper(daemon)._reap()
    assert daemon.removed == ["ours"], f"a labelled sandbox must be reaped, removed {daemon.removed}"


@pytest.mark.asyncio
async def test_a_pre_label_sandbox_is_still_reaped():
    """A sandbox created before #616 has no label; its name prefix and creation date identify it."""
    created = PRE_LABEL.strftime("%Y-%m-%d %H:%M:%S %z UTC")
    daemon = _Daemon(
        [
            _Container("legacy", "sandbox-job-1", created, labels=()),
            _Container("not-a-prefix", "my-sandbox-job-2", created, labels=()),
        ]
    )
    await _reaper(daemon)._reap()
    assert daemon.removed == ["legacy"], f"only the pre-label sandbox must be reaped, removed {daemon.removed}"
