"""SharedVolumeManager: collision-free names, and labels checked before every reuse, removal and mount.

The tests reach the module's newer API through ``_api``/``_params`` so that,
run against a tree without it, each fails on an assertion that names what is
missing instead of erroring at import.
"""

import hashlib
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

import embry0.cache.volume_manager as volume_manager
from embry0.cache.volume_manager import SharedVolumeManager, VolumeScope
from embry0.cache.volume_ownership import volume_labels
from embry0.execution.runtime import DockerRuntime

INSTANCE = "a1b2c3d4e5f60718"


def _expected(scope: str, key: str, instance: str | None = INSTANCE) -> str:
    """The naming contract: a per-scope prefix, then sha256(instance id, NUL, key)."""
    digest = hashlib.sha256(((instance or "") + "\x00" + key).encode()).hexdigest()
    return f"embry0-qa-vol-{scope}-{digest}"


def _api(name: str):
    assert hasattr(volume_manager, name), f"embry0.cache.volume_manager has no {name}"
    return getattr(volume_manager, name)


def _params(fn, *names: str) -> None:
    have = inspect.signature(fn).parameters
    missing = [n for n in names if n not in have]
    assert not missing, f"{fn.__qualname__}() takes no {missing}"


def _has_recreate() -> None:
    assert hasattr(SharedVolumeManager, "recreate"), (
        "SharedVolumeManager has no recreate(): nothing empties a per-repo volume whose lockfile changed"
    )


def _volume(result, *, what: str = "ensure()"):
    """The SharedVolume a call returned: it must say which volume it handed out."""
    assert all(hasattr(result, a) for a in ("name", "scope", "scope_key")), (
        f"{what} must return the volume it handed out (name, scope, scope_key), got {result!r}"
    )
    return result


def _stub_docker():
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(return_value="")
    return docker


def _fake_daemon(existing: dict[str, dict[str, str]] | None = None, *, busy: frozenset[str] = frozenset()):
    """A stateful ``run_cmd`` backing a tiny volume daemon: ls, inspect, create, rm, ps, rm -f.

    ``existing`` maps volume name -> labels. A name in ``busy`` is refused by
    ``volume rm`` (mounted). ``volume inspect`` of a missing volume fails as
    the real daemon does.
    """
    volumes: dict[str, dict[str, str]] = dict(existing or {})

    async def run_cmd(cmd, timeout=None, **_kw):
        if cmd[1:3] == ["volume", "ls"]:
            names = [a.split("=", 1)[1] for a in cmd if a.startswith("name=")]
            labels = [a.split("=", 1)[1] for a in cmd if a.startswith("label=")]
            out = [
                n
                for n, lbls in volumes.items()
                if (not names or any(f in n for f in names))
                and all(lbls.get(k) == v for k, v in (f.split("=", 1) for f in labels))
            ]
            return "\n".join(out) + ("\n" if out else "")
        if cmd[1:3] == ["volume", "inspect"]:
            missing = [n for n in cmd[3:] if n not in volumes]
            if missing:
                raise RuntimeError(f"Error response from daemon: get {missing[0]}: no such volume")
            return json.dumps([{"Name": n, "Labels": volumes[n], "CreatedAt": None} for n in cmd[3:]])
        if cmd[1:3] == ["volume", "create"]:
            labels: dict[str, str] = {}
            for i, arg in enumerate(cmd):
                if arg == "--label":
                    k, v = cmd[i + 1].split("=", 1)
                    labels[k] = v
            volumes[cmd[-1]] = labels
            return cmd[-1]
        if cmd[1:3] == ["volume", "rm"]:
            for n in cmd[3:]:
                if n.startswith("-"):
                    continue
                if n not in volumes:
                    raise RuntimeError(f"Error response from daemon: get {n}: no such volume")
                if n in busy:
                    raise RuntimeError(f"Error response from daemon: remove {n}: volume is in use")
                volumes.pop(n)
            return ""
        return ""

    run_cmd.volumes = volumes  # type: ignore[attr-defined]
    return run_cmd


def _manager(daemon=None, *, instance: str | None = INSTANCE, state_repo=None):
    docker = _stub_docker()
    if daemon is not None:
        docker.run_cmd = AsyncMock(side_effect=daemon)
    mgr = SharedVolumeManager(runtime=DockerRuntime(docker), state_repo=state_repo or AsyncMock(), instance_id=instance)
    return mgr, docker


def _cmds(docker: MagicMock) -> list[str]:
    return [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]


# --- names ------------------------------------------------------------------------


def test_names_are_scoped_hashes_of_instance_and_key():
    name_for = _api("volume_name_for")
    _params(name_for, "instance_id")
    assert name_for(VolumeScope.PER_JOB, "job-1", instance_id=INSTANCE) == _expected("per-job", "job-1"), (
        "a per-job name is embry0-qa-vol-per-job-<sha256(instance id, key)>"
    )
    assert name_for(VolumeScope.PER_REPO, "acme/widgets", instance_id=INSTANCE) == _expected(
        "per-repo", "acme/widgets"
    ), "a per-repo name is embry0-qa-vol-per-repo-<sha256(instance id, key)>"


def test_two_repository_paths_never_share_a_volume_name():
    name_for = _api("volume_name_for")
    _params(name_for, "instance_id")
    a = name_for(VolumeScope.PER_REPO, "acme/widgets-x", instance_id=INSTANCE)
    b = name_for(VolumeScope.PER_REPO, "acme-widgets/x", instance_id=INSTANCE)
    assert a != b, "two different repository paths must never map to one volume name"


def test_a_per_job_key_never_names_a_per_repo_volume():
    name_for = _api("volume_name_for")
    _params(name_for, "instance_id")
    assert name_for(VolumeScope.PER_JOB, "shared", instance_id=INSTANCE) != name_for(
        VolumeScope.PER_REPO, "shared", instance_id=INSTANCE
    ), "the scope is part of the name"


def test_two_instances_never_share_a_volume_name():
    name_for = _api("volume_name_for")
    _params(name_for, "instance_id")
    assert name_for(VolumeScope.PER_REPO, "acme/widgets", instance_id="inst-a") != name_for(
        VolumeScope.PER_REPO, "acme/widgets", instance_id="inst-b"
    ), "the instance id is part of the name"


# --- ensure ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_creates_a_labelled_volume_and_says_which():
    daemon = _fake_daemon()
    mgr, docker = _manager(daemon)
    vol = _volume(await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-1"))
    assert vol.name == _expected("per-job", "job-1"), f"ensure() created {vol.name}"
    assert daemon.volumes[vol.name] == volume_labels("per-job", "job-1", instance_id=INSTANCE), (
        "the created volume must carry every ownership label"
    )


@pytest.mark.asyncio
async def test_a_volume_labelled_for_another_repo_is_never_reused():
    """The per-repo name is taken by a volume whose labels name another key: a per-job volume instead."""
    _params(SharedVolumeManager.ensure, "job_id")
    name = _expected("per-repo", "acme/widgets")
    daemon = _fake_daemon({name: volume_labels("per-repo", "someone/else", instance_id=INSTANCE)})
    mgr, _ = _manager(daemon)
    vol = _volume(await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="acme/widgets", job_id="job-9"))
    assert (vol.name, vol.scope, vol.scope_key) == (_expected("per-job", "job-9"), VolumeScope.PER_JOB, "job-9"), (
        f"a label mismatch must not reuse {name}; expected a fresh per-job volume, got {vol}"
    )
    assert daemon.volumes[name] == volume_labels("per-repo", "someone/else", instance_id=INSTANCE), (
        "the foreign volume must be left as it was"
    )


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param({"embry0.managed": "true", "embry0.volume_scope": "per-repo"}, id="no-instance-no-key"),
        pytest.param(volume_labels("per-repo", "acme/widgets", instance_id="another-instance"), id="other-instance"),
        pytest.param(volume_labels("per-job", "acme/widgets", instance_id=INSTANCE), id="per-job-labels"),
        pytest.param({}, id="unlabelled"),
        pytest.param(
            {**volume_labels("per-repo", "acme/widgets", instance_id=INSTANCE), "embry0.job_id": "job-3"},
            id="extra-job-label",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_ownership_label_must_match_before_reuse(labels):
    _params(SharedVolumeManager.ensure, "job_id")
    name = _expected("per-repo", "acme/widgets")
    mgr, docker = _manager(_fake_daemon({name: labels}))
    vol = _volume(await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="acme/widgets", job_id="job-9"))
    assert vol.name != name, f"a volume labelled {labels} must not be reused as acme/widgets' per-repo volume"


@pytest.mark.asyncio
async def test_a_per_job_volume_that_is_not_ours_raises():
    error = _api("VolumeOwnershipError")
    name = _expected("per-job", "job-1")
    mgr, _ = _manager(_fake_daemon({name: volume_labels("per-job", "job-other", instance_id=INSTANCE)}))
    try:
        got = await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-1")
    except error:
        return
    raise AssertionError(f"a per-job volume whose labels are not ours must be refused; ensure() returned {got!r}")


@pytest.mark.asyncio
async def test_reuse_keeps_the_recorded_warmth_and_creation_resets_it():
    _params(SharedVolumeManager.ensure, "job_id")
    name = _expected("per-repo", "acme/r")
    state_repo = AsyncMock()
    mgr, docker = _manager(
        _fake_daemon({name: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)}), state_repo=state_repo
    )
    await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-1")
    assert state_repo.upsert.await_args.kwargs.get("reset_warmth") is False, (
        "reusing a volume must not wipe its recorded warmth"
    )
    assert not any("volume create" in c for c in _cmds(docker)), "a volume that is ours is reused, not re-created"
    await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-2")
    assert state_repo.upsert.await_args.kwargs.get("reset_warmth") is True, (
        "a newly created (empty) volume must clear any recorded warmth, or the warmer skips an empty tree"
    )


@pytest.mark.asyncio
async def test_ensure_creates_a_volume_whose_name_only_prefixes_an_existing_one():
    """#328: Docker's name filter is a substring match; a longer name is a different volume."""
    target = _expected("per-repo", "org/r1")
    daemon = _fake_daemon({target + "0": volume_labels("per-repo", "x/y", instance_id=INSTANCE)})
    mgr, docker = _manager(daemon)
    vol = _volume(await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="org/r1"))
    assert vol.name == target, f"ensure() must create {target}, got {vol.name}"
    assert target in daemon.volumes, f"ensure() took {target}0 for {target} and skipped the labelled create"


@pytest.mark.asyncio
async def test_without_an_instance_id_per_repo_requests_get_per_job_volumes_logged_once():
    _params(SharedVolumeManager.ensure, "job_id")
    daemon = _fake_daemon()
    mgr, _ = _manager(daemon, instance=None)
    with structlog.testing.capture_logs() as logs:
        first = _volume(await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-1"))
        second = _volume(await mgr.recreate(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-2"))
    assert (first.scope, second.scope) == (VolumeScope.PER_JOB, VolumeScope.PER_JOB), (
        f"with no instance id, a per-repo request must get a per-job volume; got {first.scope}, {second.scope}"
    )
    assert not any("per-repo" in n for n in daemon.volumes), f"no per-repo volume may exist: {list(daemon.volumes)}"
    warned = [e for e in logs if e["event"] == "qa_per_repo_volume_unavailable"]
    assert len(warned) == 1, f"the missing instance id is logged once, not per run; logged {len(warned)} times"


# --- recreate --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_removes_its_own_volume_then_creates_it_empty():
    _has_recreate()
    name = _expected("per-repo", "acme/r")
    daemon = _fake_daemon({name: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)})
    mgr, docker = _manager(daemon)
    vol = _volume(await mgr.recreate(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-1"), what="recreate()")
    cmds = _cmds(docker)
    assert vol.name == name and vol.scope is VolumeScope.PER_REPO, f"recreate() must hand back {name}, got {vol}"
    rm = cmds.index(f"docker volume rm {name}")
    create = next(i for i, c in enumerate(cmds) if c.startswith("docker volume create") and c.endswith(name))
    assert rm < create, "recreate() removes the volume before it creates it again"


@pytest.mark.asyncio
async def test_recreate_creates_the_volume_when_there_is_none_yet():
    """The first trusted run: nothing to remove, so the per-repo volume is created, not swapped for a per-job one."""
    _has_recreate()
    daemon = _fake_daemon()
    mgr, _ = _manager(daemon)
    vol = _volume(await mgr.recreate(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-1"), what="recreate()")
    assert vol.scope is VolumeScope.PER_REPO and vol.name == _expected("per-repo", "acme/r"), (
        f"a missing per-repo volume must be created, not replaced by a per-job one; got {vol}"
    )


@pytest.mark.asyncio
async def test_recreate_checks_the_labels_before_removing_anything():
    _has_recreate()
    name = _expected("per-repo", "acme/r")
    foreign = volume_labels("per-repo", "someone/else", instance_id=INSTANCE)
    daemon = _fake_daemon({name: foreign})
    mgr, docker = _manager(daemon)
    with structlog.testing.capture_logs() as logs:
        vol = _volume(
            await mgr.recreate(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-1"), what="recreate()"
        )
    assert f"docker volume rm {name}" not in _cmds(docker), "a volume whose labels are not ours must never be removed"
    assert daemon.volumes.get(name) == foreign, "the foreign volume must be left as it was"
    assert vol.scope is VolumeScope.PER_JOB, f"the run gets a per-job volume instead, got {vol}"
    assert any(e["event"] == "qa_volume_remove_refused" for e in logs), "the refusal must be logged"


@pytest.mark.asyncio
async def test_recreate_of_a_volume_in_use_falls_back_to_per_job():
    _has_recreate()
    name = _expected("per-repo", "acme/r")
    daemon = _fake_daemon({name: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)}, busy=frozenset({name}))
    mgr, _ = _manager(daemon)
    vol = _volume(await mgr.recreate(scope=VolumeScope.PER_REPO, scope_key="acme/r", job_id="job-7"), what="recreate()")
    assert (vol.name, vol.scope) == (_expected("per-job", "job-7"), VolumeScope.PER_JOB), (
        f"a busy per-repo volume must fall back to a per-job volume, got {vol}"
    )
    assert name in daemon.volumes, "the busy volume stays"


# --- the mount check ---------------------------------------------------------------------


def _shared(name: str, scope: VolumeScope, key: str):
    return _api("SharedVolume")(name, scope, key)


@pytest.mark.asyncio
async def test_verify_passes_only_a_present_volume_labelled_ours():
    verify = getattr(SharedVolumeManager, "verify", None)
    assert verify is not None, "SharedVolumeManager has no verify(): nothing checks a volume before it is mounted"
    name = _expected("per-job", "job-1")
    mgr, _ = _manager(_fake_daemon({name: volume_labels("per-job", "job-1", instance_id=INSTANCE)}))
    assert await mgr.verify(_shared(name, VolumeScope.PER_JOB, "job-1")) is True, "our own volume may be mounted"


@pytest.mark.asyncio
async def test_verify_refuses_a_missing_volume_and_creates_nothing():
    verify = getattr(SharedVolumeManager, "verify", None)
    assert verify is not None, "SharedVolumeManager has no verify(): nothing checks a volume before it is mounted"
    daemon = _fake_daemon()
    mgr, docker = _manager(daemon)
    name = _expected("per-repo", "acme/r")
    with structlog.testing.capture_logs() as logs:
        assert await mgr.verify(_shared(name, VolumeScope.PER_REPO, "acme/r")) is False, (
            "a missing volume must not be mounted: the daemon would create it unlabelled"
        )
    reasons = [e.get("reason") for e in logs if e["event"] == "qa_volume_mount_refused"]
    assert reasons == ["missing"], f"the refusal must say the volume is missing; logged {reasons}"
    assert daemon.volumes == {} and not any("volume create" in c for c in _cmds(docker)), (
        "the mount check must not create anything"
    )


@pytest.mark.asyncio
async def test_verify_refuses_a_volume_whose_labels_are_not_ours():
    verify = getattr(SharedVolumeManager, "verify", None)
    assert verify is not None, "SharedVolumeManager has no verify(): nothing checks a volume before it is mounted"
    name = _expected("per-repo", "acme/r")
    mgr, _ = _manager(_fake_daemon({name: volume_labels("per-repo", "acme/r", instance_id="another")}))
    assert await mgr.verify(_shared(name, VolumeScope.PER_REPO, "acme/r")) is False, (
        "a volume another instance labelled must not be mounted"
    )


@pytest.mark.asyncio
async def test_verify_refuses_a_name_that_is_not_ours_for_the_key():
    verify = getattr(SharedVolumeManager, "verify", None)
    assert verify is not None, "SharedVolumeManager has no verify(): nothing checks a volume before it is mounted"
    old = "embry0-qa-vol-acme-r"
    mgr, _ = _manager(_fake_daemon({old: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)}))
    assert await mgr.verify(_shared(old, VolumeScope.PER_REPO, "acme/r")) is False, (
        "a volume is mounted only under this deployment's name for its key"
    )


@pytest.mark.asyncio
async def test_a_name_from_an_older_checkpoint_is_re_resolved_not_mounted():
    """A job state written before volumes were named by hash: the old name is never mounted."""
    mountable = getattr(SharedVolumeManager, "mountable", None)
    assert mountable is not None, "SharedVolumeManager has no mountable(): a name from job state is mounted unchecked"
    old = "embry0-qa-vol-acme-r"
    daemon = _fake_daemon({old: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)})
    mgr, docker = _manager(daemon)
    assert await mgr.mountable(old, repo="acme/r", job_id="job-1") is None, (
        "an old-format name must be re-resolved to this release's per-job name, which does not exist, so nothing is mounted"
    )
    assert any(f"name={_expected('per-job', 'job-1')}" in c for c in _cmds(docker)), (
        "the re-resolved name is the job's per-job name"
    )


@pytest.mark.asyncio
async def test_mountable_hands_back_a_checked_volume_of_the_run():
    mountable = getattr(SharedVolumeManager, "mountable", None)
    assert mountable is not None, "SharedVolumeManager has no mountable(): a name from job state is mounted unchecked"
    repo_name = _expected("per-repo", "acme/r")
    mgr, _ = _manager(_fake_daemon({repo_name: volume_labels("per-repo", "acme/r", instance_id=INSTANCE)}))
    vol = await mgr.mountable(repo_name, repo="acme/r", job_id="job-1")
    assert vol is not None and (vol.scope, vol.scope_key) == (VolumeScope.PER_REPO, "acme/r"), (
        f"the run's per-repo volume may be mounted, got {vol}"
    )


# --- destroy_for_job ------------------------------------------------------------------


def _job_volume(job_id: str = "job-1", instance: str | None = INSTANCE) -> tuple[str, dict[str, str]]:
    return _expected("per-job", job_id, instance), volume_labels("per-job", job_id, instance_id=instance)


@pytest.mark.asyncio
async def test_destroy_for_job_removes_named_and_labelled_volumes():
    name, labels = _job_volume()
    other = "embry0-maven-vol-" + "e" * 64  # any volume labelled as this job's per-job volume
    daemon = _fake_daemon(
        {name: labels, other: labels, name + "0": volume_labels("per-job", "job-10", instance_id=INSTANCE)}
    )
    mgr, docker = _manager(daemon)
    removed = await mgr.destroy_for_job("job-1")
    assert sorted(removed) == sorted([name, other]), f"destroy_for_job removed {removed}"
    assert name + "0" in daemon.volumes, "a name that only starts with the job's volume name is another job's"


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param(volume_labels("per-repo", "job-1", instance_id=INSTANCE), id="per-repo-scope"),
        pytest.param(
            {**volume_labels("per-job", "job-1", instance_id=INSTANCE), "embry0.scope_key": "job-2"}, id="key"
        ),
        pytest.param(
            {**volume_labels("per-job", "job-1", instance_id=INSTANCE), "embry0.managed": "false"}, id="managed"
        ),
        pytest.param(volume_labels("per-job", "job-1", instance_id="another"), id="instance"),
        pytest.param({}, id="unlabelled"),
    ],
)
@pytest.mark.asyncio
async def test_destroy_for_job_checks_every_label_before_removing(labels):
    name = _expected("per-job", "job-1")
    daemon = _fake_daemon({name: labels})
    mgr, docker = _manager(daemon)
    with structlog.testing.capture_logs() as logs:
        removed = await mgr.destroy_for_job("job-1")
    assert removed == [] and name in daemon.volumes, f"a volume labelled {labels} is not this job's and must stay"
    assert not any(c.startswith("docker rm ") for c in _cmds(docker)), "no container is removed for a volume not ours"
    assert any(e["event"] == "qa_job_volume_remove_refused" for e in logs), "the refusal must be logged"


@pytest.mark.asyncio
async def test_destroy_for_job_first_removes_containers_holding_the_volume():
    """A cancelled fan-out leaves sub-task containers mounting the volume."""
    name, labels = _job_volume()
    daemon = _fake_daemon({name: labels})

    async def run_cmd(cmd, timeout=None, **kw):
        if cmd[1] == "ps":
            return "c1\tqa-sub-1\tcreated\tExited (1)\t1 hour ago\nc2\tqa-sub-2\tcreated\tUp\t1 hour ago\n"
        return await daemon(cmd, timeout=timeout)

    mgr, docker = _manager(run_cmd)
    await mgr.destroy_for_job("job-1")
    cmds = _cmds(docker)
    ps = next(i for i, c in enumerate(cmds) if c.startswith(f"docker ps -a --filter volume={name} "))
    rm_c = cmds.index("docker rm -f c1 c2")
    rm_v = cmds.index(f"docker volume rm {name}")
    assert ps < rm_c < rm_v


@pytest.mark.asyncio
async def test_destroy_for_job_never_raises():
    docker = _stub_docker()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("daemon down"))
    mgr = SharedVolumeManager(runtime=DockerRuntime(docker), state_repo=AsyncMock(), instance_id=INSTANCE)
    assert await mgr.destroy_for_job("job-1") == []


@pytest.mark.asyncio
async def test_destroy_for_job_without_a_volume_issues_no_removal():
    """Most jobs never create a volume — that must be a silent no-op."""
    mgr, docker = _manager(_fake_daemon())
    assert await mgr.destroy_for_job("job-1") == []
    assert not any(" rm" in s for s in _cmds(docker))


@pytest.mark.asyncio
async def test_destroy_for_job_in_use_volume_is_not_reported_as_removed():
    name, labels = _job_volume()
    mgr, _ = _manager(_fake_daemon({name: labels}, busy=frozenset({name})))
    assert await mgr.destroy_for_job("job-1") == []


@pytest.mark.asyncio
async def test_create_without_an_instance_id_stamps_no_instance_label():
    mgr, docker = _manager(_fake_daemon(), instance=None)
    await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-1")
    create = next(s for s in _cmds(docker) if "volume create" in s)
    assert "embry0.instance" not in create


@pytest.mark.asyncio
async def test_destroy_for_job_label_lookup_is_scoped_to_this_instance():
    mgr, docker = _manager(_fake_daemon())
    await mgr.destroy_for_job("job-1")
    assert (
        f"docker volume ls --quiet --filter label=embry0.job_id=job-1 --filter label=embry0.instance={INSTANCE}"
        in _cmds(docker)
    )


@pytest.mark.asyncio
async def test_destroy_for_job_forgets_the_state_rows_of_removed_volumes_only():
    """A qa_volume_state row must not outlive its volume (#328)."""
    name, labels = _job_volume()
    held = "held-vol"
    state_repo = AsyncMock()
    mgr, _ = _manager(_fake_daemon({name: labels, held: labels}, busy=frozenset({held})), state_repo=state_repo)
    await mgr.destroy_for_job("job-1")
    forgotten = [c.args[0] for c in state_repo.delete_by_volume_name.await_args_list]
    assert forgotten == [name], (
        f"destroy_for_job must delete the row of each volume it removed, and only those; deleted {forgotten}"
    )


@pytest.mark.asyncio
async def test_a_state_delete_failure_does_not_fail_the_cleanup():
    name, labels = _job_volume()
    state_repo = AsyncMock()
    state_repo.delete_by_volume_name = AsyncMock(side_effect=RuntimeError("db down"))
    mgr, _ = _manager(_fake_daemon({name: labels}), state_repo=state_repo)
    assert await mgr.destroy_for_job("job-1") == [name]
