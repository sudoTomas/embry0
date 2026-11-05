"""VolumeReaper: startup + periodic sweep of leaked DinD volumes (#274)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from embry0.cache.volume_ownership import volume_labels
from embry0.cache.volume_reaper import VolumeReaper
from embry0.execution.runtime import DockerRuntime

INSTANCE = "a1b2c3d4e5f60718"


def _labels(scope: str, key: str, instance_id: str | None = INSTANCE) -> dict[str, str]:
    return volume_labels(scope, key, instance_id=instance_id)


def _reaper(docker, jobs, **kwargs) -> VolumeReaper:
    kwargs.setdefault("instance_id", INSTANCE)
    kwargs.setdefault("state_repo", None)
    return VolumeReaper(DockerRuntime(docker), jobs, **kwargs)


def _vol(name: str, labels: dict[str, str] | None, created: str = "2026-07-19T00:14:06Z") -> dict:
    return {"Name": name, "Labels": labels, "CreatedAt": created, "Driver": "local"}


def _docker(volumes: list[dict], *, rm_fails: set[str] | None = None):
    """Stub DinD holding ``volumes``; honours the ls filters the reaper uses."""
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]

    async def run_cmd(cmd, timeout=None, **_kwargs):
        if cmd[1:3] == ["volume", "ls"]:
            flt = cmd[cmd.index("--filter") + 1]
            if flt.startswith("label="):
                key, _, value = flt[len("label=") :].partition("=")
                hits = [v for v in volumes if (v["Labels"] or {}).get(key) == value]
            else:
                hits = [v for v in volumes if flt[len("name=") :] in v["Name"]]
            return "\n".join(v["Name"] for v in hits)
        if cmd[1:3] == ["volume", "inspect"]:
            return json.dumps([v for v in volumes if v["Name"] in cmd[3:]])
        if cmd[1:3] == ["volume", "rm"]:
            if cmd[3] in (rm_fails or set()):
                raise RuntimeError("volume is in use")
            return cmd[3]
        raise AssertionError(f"unexpected docker command: {cmd}")

    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    return docker


def _jobs(statuses: dict[str, str]):
    repo = MagicMock()
    repo.fetch_statuses = AsyncMock(side_effect=lambda ids: {i: statuses[i] for i in ids if i in statuses})
    return repo


def _removed(docker) -> list[str]:
    return [c.args[0][3] for c in docker.run_cmd.call_args_list if c.args[0][1:3] == ["volume", "rm"]]


@pytest.mark.asyncio
async def test_sweep_removes_terminal_and_unknown_but_not_live():
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-done", _labels("per-job", "job-done")),
            _vol("embry0-qa-vol-job-live", _labels("per-job", "job-live")),
            _vol("embry0-qa-vol-job-gone", _labels("per-job", "job-gone")),
            _vol("embry0-qa-vol-org-r1", _labels("per-repo", "org/r1")),
            _vol("pgdata", None),
        ]
    )
    reaper = _reaper(docker, _jobs({"job-done": "failed", "job-live": "running"}))

    result = await reaper.sweep()

    assert sorted(_removed(docker)) == ["embry0-qa-vol-job-done", "embry0-qa-vol-job-gone"]
    assert sorted(result.removed) == ["embry0-qa-vol-job-done", "embry0-qa-vol-job-gone"]
    assert result.failed == []


@pytest.mark.asyncio
async def test_dry_run_logs_but_removes_nothing():
    docker = _docker([_vol("embry0-qa-vol-job-done", _labels("per-job", "job-done"))])
    reaper = _reaper(docker, _jobs({"job-done": "cancelled"}), dry_run=True)

    with capture_logs() as logs:
        result = await reaper.sweep()

    assert _removed(docker) == []
    assert result.removed == []
    assert result.would_remove == ["embry0-qa-vol-job-done"]
    would = [e for e in logs if e["event"] == "volume_reaper_would_remove"]
    assert would and would[0]["volume"] == "embry0-qa-vol-job-done"
    assert would[0]["job_id"] == "job-done"
    assert would[0]["reason"] == "owner_terminal"


@pytest.mark.asyncio
async def test_status_lookup_failure_removes_nothing():
    """Fail closed: unable to tell live from dead means touch nothing."""
    docker = _docker([_vol("embry0-qa-vol-job-done", _labels("per-job", "job-done"))])
    jobs = MagicMock()
    jobs.fetch_statuses = AsyncMock(side_effect=RuntimeError("db down"))

    result = await _reaper(docker, jobs).sweep()

    assert _removed(docker) == []
    assert result.removed == []


@pytest.mark.asyncio
async def test_legacy_unlabelled_volumes_are_ignored_by_default():
    docker = _docker([_vol("embry0-qa-vol-job-0a926d6c185e", None)])
    await _reaper(docker, _jobs({"job-0a926d6c185e": "failed"})).sweep()
    assert _removed(docker) == []


@pytest.mark.asyncio
async def test_legacy_unlabelled_volumes_are_adopted_when_enabled():
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-0a926d6c185e", None),
            _vol("embry0-qa-vol-job-1b4144f4faba", None),
            _vol("embry0-qa-vol-org-r1", None),
        ]
    )
    reaper = _reaper(
        docker,
        _jobs({"job-0a926d6c185e": "failed", "job-1b4144f4faba": "running"}),
        adopt_legacy_names=True,
    )
    await reaper.sweep()
    assert _removed(docker) == ["embry0-qa-vol-job-0a926d6c185e"]


@pytest.mark.asyncio
async def test_in_use_volume_is_reported_failed_and_sweep_continues():
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-a", _labels("per-job", "job-a")),
            _vol("embry0-qa-vol-job-b", _labels("per-job", "job-b")),
        ],
        rm_fails={"embry0-qa-vol-job-a"},
    )
    result = await _reaper(docker, _jobs({"job-a": "failed", "job-b": "failed"})).sweep()
    assert result.failed == ["embry0-qa-vol-job-a"]
    assert result.removed == ["embry0-qa-vol-job-b"]


@pytest.mark.asyncio
async def test_removal_never_uses_force():
    docker = _docker([_vol("embry0-qa-vol-job-a", _labels("per-job", "job-a"))])
    await _reaper(docker, _jobs({"job-a": "failed"})).sweep()
    rm = next(c.args[0] for c in docker.run_cmd.call_args_list if c.args[0][1:3] == ["volume", "rm"])
    assert "--force" not in rm and "-f" not in rm


@pytest.mark.asyncio
async def test_fresh_volume_with_unknown_owner_is_kept():
    from datetime import UTC, datetime

    fresh = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    docker = _docker([_vol("embry0-qa-vol-job-new", _labels("per-job", "job-new"), created=fresh)])
    await _reaper(docker, _jobs({})).sweep()
    assert _removed(docker) == []


@pytest.mark.asyncio
async def test_docker_list_failure_is_swallowed():
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("daemon down"))
    result = await _reaper(docker, _jobs({})).sweep()
    assert result.removed == []


@pytest.mark.asyncio
async def test_start_runs_a_sweep_immediately_then_stop_cancels():
    import asyncio

    docker = _docker([_vol("embry0-qa-vol-job-done", _labels("per-job", "job-done"))])
    reaper = _reaper(docker, _jobs({"job-done": "failed"}), interval_seconds=3600)
    reaper.start()
    for _ in range(50):
        if _removed(docker):
            break
        await asyncio.sleep(0.01)
    await reaper.stop()
    assert _removed(docker) == ["embry0-qa-vol-job-done"]


@pytest.mark.asyncio
async def test_volumes_of_another_instance_are_never_removed():
    """Booted against an empty or different database: every row is 'missing'."""
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-theirs", _labels("per-job", "job-theirs", "ffffffffffffffff")),
            _vol("embry0-qa-vol-job-nolabel", _labels("per-job", "job-nolabel", None)),
            _vol("embry0-qa-vol-job-ours", _labels("per-job", "job-ours")),
        ]
    )
    await _reaper(docker, _jobs({})).sweep()
    assert _removed(docker) == ["embry0-qa-vol-job-ours"]


@pytest.mark.asyncio
async def test_unresolved_instance_id_leaves_every_labelled_volume_alone():
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-ours", _labels("per-job", "job-ours")),
            _vol("embry0-qa-vol-job-0a926d6c185e", None),
        ]
    )
    jobs = _jobs({"job-ours": "failed", "job-0a926d6c185e": "failed"})
    await _reaper(docker, jobs, instance_id=None).sweep()
    assert _removed(docker) == []

    # The explicit legacy switch is independent of the instance id.
    await _reaper(docker, jobs, instance_id=None, adopt_legacy_names=True).sweep()
    assert _removed(docker) == ["embry0-qa-vol-job-0a926d6c185e"]


@pytest.mark.asyncio
async def test_decision_not_the_list_filter_is_what_protects_foreign_volumes():
    """Even if the daemon's label filter returned too much, nothing foreign goes."""
    theirs = _vol("embry0-qa-vol-job-theirs", _labels("per-job", "job-theirs", "ffffffffffffffff"))
    docker = _docker([theirs])
    inner = docker.run_cmd.side_effect

    async def leaky(cmd, timeout=None, **_kwargs):
        if cmd[1:3] == ["volume", "ls"]:
            return theirs["Name"]
        return await inner(cmd, timeout=timeout)

    docker.run_cmd = AsyncMock(side_effect=leaky)
    await _reaper(docker, _jobs({"job-theirs": "failed"})).sweep()
    assert _removed(docker) == []


@pytest.mark.asyncio
async def test_sweep_forgets_the_state_rows_of_removed_volumes_only():
    """A qa_volume_state row must not outlive its volume (#328)."""
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-a", _labels("per-job", "job-a")),
            _vol("embry0-qa-vol-job-b", _labels("per-job", "job-b")),
            _vol("embry0-qa-vol-job-live", _labels("per-job", "job-live")),
        ],
        rm_fails={"embry0-qa-vol-job-b"},
    )
    state_repo = MagicMock()
    state_repo.delete_by_volume_name = AsyncMock()
    jobs = _jobs({"job-a": "failed", "job-b": "failed", "job-live": "running"})
    await _reaper(docker, jobs, state_repo=state_repo).sweep()
    forgotten = [c.args[0] for c in state_repo.delete_by_volume_name.await_args_list]
    assert forgotten == ["embry0-qa-vol-job-a"], (
        f"the reaper must forget exactly the volumes it removed; got {forgotten}"
    )


@pytest.mark.asyncio
async def test_dry_run_forgets_nothing():
    docker = _docker([_vol("embry0-qa-vol-job-a", _labels("per-job", "job-a"))])
    state_repo = MagicMock()
    state_repo.delete_by_volume_name = AsyncMock()
    await _reaper(docker, _jobs({"job-a": "failed"}), state_repo=state_repo, dry_run=True).sweep()
    state_repo.delete_by_volume_name.assert_not_awaited()
