"""init_orchestrator_node: which runs may use the repository's per-repo cache volume.

Only the forge's default branch at its current head, as ``git ls-remote
--symref`` through ``sandbox_git`` reports them, is trusted. A job's
``base_branch`` override or the repository's ``base_branch`` preference never
makes a run trusted. The node runs for real here, with a fake bootstrap
sandbox and the real ``SharedVolumeManager`` over a fake volume daemon.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.cache.volume_manager import SharedVolumeManager
from embry0.cache.volume_ownership import volume_labels
from embry0.execution.runtime import DockerRuntime
from embry0.storage.repositories.qa_volume_state import VolumeState

INSTANCE = "a1b2c3d4e5f60718"
REPO = "acme/widgets"
MAIN_HEAD = "1" * 40
OTHER_HEAD = "2" * 40
LOCKFILE = '{"lockfileVersion": 3}'
LOCK_SHA = hashlib.sha256(LOCKFILE.encode()).hexdigest()

_QA_YAML = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
cache:
  prebaked_image: { enabled: false }
  shared_volume: { enabled: true, scope: per-repo }
apps:
  hub:
    boot_command: "echo started"
    frontend_url: "http://localhost:3000"
"""


def _name(scope: str, key: str) -> str:
    digest = hashlib.sha256((INSTANCE + "\x00" + key).encode()).hexdigest()
    return f"embry0-qa-vol-{scope}-{digest}"


class _Configs:
    async def get(self, repo: str):
        return {"yaml_text": _QA_YAML}


class _Prefs:
    def __init__(self, base_branch: str | None) -> None:
        self._row = {"base_branch": base_branch} if base_branch else None

    async def get(self, repo: str):
        return self._row


def _volume_daemon(existing: dict[str, dict[str, str]] | None = None, busy: frozenset[str] = frozenset()):
    volumes: dict[str, dict[str, str]] = dict(existing or {})

    async def run_cmd(cmd, timeout=None, **_kw):
        if cmd[1:3] == ["volume", "ls"]:
            names = [a.split("=", 1)[1] for a in cmd if a.startswith("name=")]
            return "".join(f"{n}\n" for n in volumes if not names or any(f in n for f in names))
        if cmd[1:3] == ["volume", "inspect"]:
            if any(n not in volumes for n in cmd[3:]):
                raise RuntimeError("no such volume")
            return json.dumps([{"Name": n, "Labels": volumes[n], "CreatedAt": None} for n in cmd[3:]])
        if cmd[1:3] == ["volume", "create"]:
            labels = {
                cmd[i + 1].split("=", 1)[0]: cmd[i + 1].split("=", 1)[1] for i, a in enumerate(cmd) if a == "--label"
            }
            volumes[cmd[-1]] = labels
            return cmd[-1]
        if cmd[1:3] == ["volume", "rm"]:
            for n in cmd[3:]:
                if n not in volumes:
                    raise RuntimeError("no such volume")
                if n in busy:
                    raise RuntimeError("volume is in use")
                volumes.pop(n)
            return ""
        return ""

    run_cmd.volumes = volumes  # type: ignore[attr-defined]
    return run_cmd


def _setup(
    tmp_path,
    monkeypatch,
    *,
    run_head: str,
    forge_default: tuple[str, str] | None,
    prefs: str | None = None,
    prior: VolumeState | None = None,
    existing: dict[str, dict[str, str]] | None = None,
    busy: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[str], dict[str, Any], Any]:
    seen: list[str] = []

    async def bootstrap_run(cmd, timeout=60, **_kw):
        s = " ".join(cmd)
        seen.append(s)
        if "symbolic-ref" in s:
            return "main\n"
        if "ls-remote --symref" in s:
            if forge_default is None:
                raise RuntimeError("fatal: unable to access the repository")
            branch, sha = forge_default
            return f"ref: refs/heads/{branch}\tHEAD\n{sha}\tHEAD\n"
        if "rev-parse HEAD" in s:
            return f"{run_head}\n"
        if "package-lock.json" in s:
            return LOCKFILE
        if "find /workspace" in s:
            return "/workspace/package.json\n"
        if "cat /workspace/package.json" in s:
            return '{"name": "root", "workspaces": ["apps/*"]}'
        return ""

    docker = AsyncMock()
    docker.run_cmd = bootstrap_run
    docker.build_exec_cmd = lambda c, cmd, **_kw: ["docker", "exec", c, *cmd]
    docker._build_base_cmd = lambda: ["docker"]

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("bootstrap-c", "tok"))
    sandbox_mgr.destroy = AsyncMock(return_value=None)
    sandbox_mgr.last_forge_credential = MagicMock(return_value=None)

    profiles_repo = AsyncMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim"})

    daemon = _volume_daemon(existing, busy)
    volume_docker = MagicMock()
    volume_docker._build_base_cmd = lambda: ["docker"]
    volume_docker.run_cmd = AsyncMock(side_effect=daemon)
    state_repo = AsyncMock()
    state_repo.get = AsyncMock(return_value=prior)
    volume_mgr = SharedVolumeManager(runtime=DockerRuntime(volume_docker), state_repo=state_repo, instance_id=INSTANCE)

    warmed: dict[str, Any] = {}

    async def fake_warm(**kw):
        warmed.update(kw)
        return "warmed"

    monkeypatch.setattr("embry0.cache.volume_warmer.warm_shared_volume", fake_warm)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator._staging_dir_for", lambda job_id: tmp_path / f"embry0-workspace-{job_id}"
    )

    config = {
        "configurable": {
            "docker": docker,
            "sandbox_manager": sandbox_mgr,
            "profiles_repo": profiles_repo,
            "qa_configs_repo": _Configs(),
            "repo_preferences_repo": _Prefs(prefs),
            "qa_shared_volume_manager": volume_mgr,
            "qa_volume_state_repo": state_repo,
        }
    }
    return config, seen, warmed, daemon


async def _run(config, **state):
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    return await init_orchestrator_node({"job_id": "job-1", "repo": REPO, **state}, config)


def _warmed_scope(warmed: dict[str, Any]) -> tuple[str, str, str]:
    assert warmed, "the run warmed no shared volume at all"
    return warmed["scope"], warmed["scope_key"], warmed["volume_name"]


@pytest.mark.asyncio
async def test_the_forge_default_branch_at_its_head_gets_the_per_repo_volume(tmp_path, monkeypatch):
    config, seen, warmed, daemon = _setup(tmp_path, monkeypatch, run_head=MAIN_HEAD, forge_default=("main", MAIN_HEAD))
    out = await _run(config, branch_name="main")
    assert _warmed_scope(warmed) == ("per-repo", REPO, _name("per-repo", REPO)), (
        f"the forge's default branch at its current head must use the per-repo volume; warmed {warmed}"
    )
    assert out["qa"]["shared_volume_name"] == _name("per-repo", REPO)
    asked = [s for s in seen if f"embry0_git ls-remote --symref https://github.com/{REPO}.git HEAD" in s]
    assert asked, "the default branch must come from the forge, through sandbox_git"


@pytest.mark.asyncio
async def test_a_job_override_naming_the_run_s_branch_does_not_make_it_trusted(tmp_path, monkeypatch):
    config, _seen, warmed, _ = _setup(tmp_path, monkeypatch, run_head=OTHER_HEAD, forge_default=("main", MAIN_HEAD))
    await _run(config, branch_name="feature-x", base_branch="feature-x")
    assert _warmed_scope(warmed) == ("per-job", "job-1", _name("per-job", "job-1")), (
        f"a job's base_branch override must not make its branch trusted; warmed {warmed}"
    )


@pytest.mark.asyncio
async def test_a_preference_naming_the_run_s_branch_does_not_make_it_trusted(tmp_path, monkeypatch):
    config, _seen, warmed, _ = _setup(
        tmp_path, monkeypatch, run_head=OTHER_HEAD, forge_default=("main", MAIN_HEAD), prefs="feature-x"
    )
    await _run(config, branch_name="feature-x")
    assert _warmed_scope(warmed) == ("per-job", "job-1", _name("per-job", "job-1")), (
        f"a base_branch preference must not make its branch trusted; warmed {warmed}"
    )


@pytest.mark.asyncio
async def test_the_default_branch_name_at_another_commit_is_not_trusted(tmp_path, monkeypatch):
    """The run's resolved head must be the forge default's current head, not just carry its name."""
    config, _seen, warmed, _ = _setup(tmp_path, monkeypatch, run_head=OTHER_HEAD, forge_default=("main", MAIN_HEAD))
    await _run(config, branch_name="main")
    assert _warmed_scope(warmed) == ("per-job", "job-1", _name("per-job", "job-1")), (
        f"a run of 'main' at a commit that is not main's head must get a per-job volume; warmed {warmed}"
    )


@pytest.mark.asyncio
async def test_no_answer_from_the_forge_trusts_nothing(tmp_path, monkeypatch):
    config, _seen, warmed, _ = _setup(tmp_path, monkeypatch, run_head=MAIN_HEAD, forge_default=None)
    out = await _run(config, branch_name="main")
    assert _warmed_scope(warmed)[0] == "per-job", f"with no answer from the forge, nothing is trusted; warmed {warmed}"
    assert out["qa"].get("forge_default_branch") is None, "no default branch is recorded for image selection"


@pytest.mark.asyncio
async def test_a_trusted_run_that_falls_back_warms_and_records_the_per_job_volume(tmp_path, monkeypatch):
    """The per-repo volume needs recreating but a concurrent run holds it: the run warms a per-job volume,
    and records that one, never the per-repo key."""
    repo_volume = _name("per-repo", REPO)
    prior = VolumeState(
        scope="per-repo", scope_key=REPO, volume_name=repo_volume, last_warmed_sha="0" * 64, last_warmed_at=None
    )
    config, _seen, warmed, daemon = _setup(
        tmp_path,
        monkeypatch,
        run_head=MAIN_HEAD,
        forge_default=("main", MAIN_HEAD),
        prior=prior,
        existing={repo_volume: volume_labels("per-repo", REPO, instance_id=INSTANCE)},
        busy=frozenset({repo_volume}),
    )
    out = await _run(config, branch_name="main")
    assert _warmed_scope(warmed) == ("per-job", "job-1", _name("per-job", "job-1")), (
        f"the warmer must be given the volume the manager handed out (per-job), not the per-repo key; warmed {warmed}"
    )
    assert out["qa"]["shared_volume_name"] == _name("per-job", "job-1")
    assert repo_volume in daemon.volumes, "the per-repo volume another run holds is left alone"


@pytest.mark.asyncio
async def test_a_round_does_not_inherit_the_last_round_s_volume(tmp_path, monkeypatch):
    """No lockfile this round: no volume is warmed, and the previous round's name must not survive."""
    config, _seen, warmed, _ = _setup(tmp_path, monkeypatch, run_head=MAIN_HEAD, forge_default=("main", MAIN_HEAD))

    original = config["configurable"]["docker"].run_cmd

    async def run_without_lockfile(cmd, timeout=60, **kw):
        if "package-lock.json" in " ".join(cmd):
            return ""
        return await original(cmd, timeout=timeout, **kw)

    config["configurable"]["docker"].run_cmd = run_without_lockfile
    out = await _run(config, branch_name="main", qa={"shared_volume_name": _name("per-job", "job-1")})
    assert not warmed
    assert out["qa"].get("shared_volume_name") is None, (
        "a round that prepared no volume must not mount the one the previous round left in state"
    )


@pytest.mark.parametrize(("recorded", "recreated"), [(LOCK_SHA, False), ("0" * 64, True), (None, True)])
@pytest.mark.asyncio
async def test_the_per_repo_volume_is_emptied_only_when_its_lockfile_changed(
    tmp_path, monkeypatch, recorded, recreated
):
    repo_volume = _name("per-repo", REPO)
    prior = (
        VolumeState(
            scope="per-repo", scope_key=REPO, volume_name=repo_volume, last_warmed_sha=recorded, last_warmed_at=None
        )
        if recorded
        else None
    )
    config, _seen, warmed, _daemon = _setup(
        tmp_path,
        monkeypatch,
        run_head=MAIN_HEAD,
        forge_default=("main", MAIN_HEAD),
        prior=prior,
        existing={repo_volume: volume_labels("per-repo", REPO, instance_id=INSTANCE)},
    )
    await _run(config, branch_name="main")
    volume_cmds = [
        " ".join(c.args[0])
        for c in config["configurable"]["qa_shared_volume_manager"].runtime.client.run_cmd.call_args_list
    ]
    removed = f"docker volume rm {repo_volume}" in volume_cmds
    assert removed is recreated, (
        f"recorded lockfile {recorded and recorded[:8]}: the per-repo volume must "
        f"{'be removed and created empty' if recreated else 'be reused as it is'}; removed={removed}"
    )
    assert _warmed_scope(warmed) == ("per-repo", REPO, repo_volume), (
        f"the trusted run warms the repository's per-repo volume either way; warmed {warmed}"
    )
