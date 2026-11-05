"""embry0.cache.volume_warmer.warm_shared_volume: every round's volume is at the round's commit and clean.

Each round the warmer checks the volume's labels, checks its tree out to the
round's commit (``requested_head``), removes every untracked and ignored file
but the dependency directories, and installs only when the lockfile changed.
The tests reach the round's parameters through ``_params`` so that, run against
a tree without them, they fail on an assertion naming what is missing.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.cache.volume_warmer import warm_shared_volume
from embry0.storage.repositories.qa_volume_state import VolumeState
from embry0.workflows.qa._subtask_prep import ClonedSandbox

HEAD = "c" * 40
STALE = "d" * 40


def _params(*names: str) -> None:
    have = inspect.signature(warm_shared_volume).parameters
    missing = [n for n in names if n not in have]
    assert not missing, f"warm_shared_volume() takes no {missing}"


def _volume_mgr(*, verified: bool = True) -> MagicMock:
    """A volume manager whose mount check answers ``verified``."""
    mgr = MagicMock()
    mgr.verify = AsyncMock(return_value=verified)
    return mgr


def _deps(*, recorded: str | None = None):
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd]
    docker.run_cmd = AsyncMock(return_value="")

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("sb-warmer", "tok-" + "A" * 40))
    sandbox_mgr.destroy = AsyncMock()

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim", "extra_networks": []})

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    state_repo = AsyncMock()
    state_repo.get = AsyncMock(
        return_value=VolumeState("per-job", "job-1", "vol-1", recorded, datetime.now(UTC)) if recorded else None
    )
    return docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo


def _fake_clone(monkeypatch, *, head: str = HEAD) -> dict:
    """prep_qa_sandbox_clone as the warmer calls it; returns the kwargs it was given."""
    seen: dict = {}

    async def fake(**kw):
        seen.update(kw)
        return ClonedSandbox(head_sha=head)

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake)
    return seen


async def _warm(deps, **overrides):
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = deps
    kwargs = {
        "repo": "org/r1",
        "branch": "main",
        "volume_name": "embry0-qa-vol-per-job-x",
        "current_lockfile_sha": "aaa",
        "scope": "per-job",
        "scope_key": "job-1",
        "docker": docker,
        "sandbox_mgr": sandbox_mgr,
        "profiles_repo": profiles_repo,
        "proxy_mgr": proxy_mgr,
        "state_repo": state_repo,
        "volume_mgr": _volume_mgr(),
        "requested_head": HEAD,
        **overrides,
    }
    return await warm_shared_volume(**kwargs)


def _cmds(docker) -> list[str]:
    return [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]


def _clean(docker) -> list[str]:
    return [c for c in _cmds(docker) if "embry0_git clean" in c]


@pytest.mark.asyncio
async def test_the_warmer_mounts_a_volume_only_after_its_labels_are_checked(monkeypatch):
    """A volume that fails the mount check (missing, so the daemon would create it unlabelled,
    or not this deployment's for the key) is never mounted: no warmer sandbox is created."""
    from embry0.cache.volume_manager import VolumeOwnershipError

    _params("requested_head")
    deps = _deps()
    _fake_clone(monkeypatch)
    refusing = _volume_mgr(verified=False)
    with pytest.raises(VolumeOwnershipError):
        await _warm(deps, volume_mgr=refusing)
    deps[1].create.assert_not_awaited()
    checked = refusing.verify.await_args.args[0]
    assert (checked.name, str(checked.scope), checked.scope_key) == ("embry0-qa-vol-per-job-x", "per-job", "job-1"), (
        f"the mount check must be asked about the volume, scope and key being mounted; asked about {checked}"
    )


@pytest.mark.parametrize("head", ["", "main", "c" * 39, "-c" + "c" * 38])
@pytest.mark.asyncio
async def test_a_warm_needs_the_round_s_commit(monkeypatch, head):
    """A warm with no commit to pin would leave the tree at whatever the branch tip or a previous round left."""
    _params("requested_head")
    deps = _deps()
    _fake_clone(monkeypatch)
    with pytest.raises(ValueError):
        await _warm(deps, requested_head=head)
    deps[1].create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_full_warm_pins_cleans_then_installs_and_records(monkeypatch):
    _params("requested_head", "keep")
    deps = _deps()
    docker, sandbox_mgr, _, _, state_repo = deps
    clone = _fake_clone(monkeypatch)

    result = await _warm(deps)

    assert result == "warmed"
    assert clone.get("head_sha") == HEAD, (
        f"the warmer must check the volume out to the round's commit; prep got {clone}"
    )
    cmds = _cmds(docker)
    clean = next((i for i, c in enumerate(cmds) if "embry0_git clean" in c), None)
    install = next((i for i, c in enumerate(cmds) if "npm ci" in c), None)
    assert clean is not None and install is not None and clean < install, (
        f"the tree is cleaned before the install: {cmds}"
    )
    assert state_repo.upsert.await_args.kwargs["last_warmed_sha"] == "aaa"
    assert sandbox_mgr.create.call_args.kwargs.get("volumes") == [("embry0-qa-vol-per-job-x", "/workspace")]
    sandbox_mgr.destroy.assert_awaited_once_with("sb-warmer")


@pytest.mark.asyncio
async def test_a_round_with_the_same_lockfile_still_moves_and_cleans_the_tree(monkeypatch):
    """#371: the lockfile matching skips only the install; the tree is checked out to this round's
    commit and cleaned, so round 2 never serves round 1's build output."""
    _params("requested_head", "keep")
    deps = _deps(recorded="aaa")
    docker, sandbox_mgr, _, _, state_repo = deps
    clone = _fake_clone(monkeypatch)

    result = await _warm(deps)

    assert result == "refreshed", f"a lockfile match must refresh the tree, not skip the round; got {result!r}"
    assert clone.get("head_sha") == HEAD, "the tree is checked out to this round's commit"
    assert _clean(docker), "the tree is cleaned every round, not only when the lockfile changed"
    assert not any("npm ci" in c for c in _cmds(docker)), "the install is skipped while the lockfile matches"
    assert state_repo.upsert.await_args.kwargs["last_warmed_sha"] is None, "the recorded sha is kept, not wiped"


@pytest.mark.asyncio
async def test_a_changed_lockfile_runs_the_install(monkeypatch):
    """A volume warmed for another lockfile is installed again, and the new sha recorded."""
    _params("requested_head")
    deps = _deps(recorded="old-lockfile-sha")
    docker, _, _, _, state_repo = deps
    _fake_clone(monkeypatch)

    result = await _warm(deps, current_lockfile_sha="new-lockfile-sha")

    assert result == "warmed", f"a changed lockfile must install again; got {result!r}"
    assert any("npm ci" in c for c in _cmds(docker)), f"no install ran for a changed lockfile: {_cmds(docker)}"
    assert state_repo.upsert.await_args.kwargs["last_warmed_sha"] == "new-lockfile-sha"


@pytest.mark.asyncio
async def test_the_clean_keeps_the_dependency_directories_and_the_config_s_patterns(monkeypatch):
    _params("requested_head", "keep")
    deps = _deps(recorded="aaa")
    _fake_clone(monkeypatch)

    await _warm(deps, keep=["src/generated", ".husky/_"])

    clean = _clean(deps[0])
    assert len(clean) == 1, f"one clean per round, got {clean}"
    body = next(line for line in clean[0].splitlines() if line.startswith("embry0_git clean"))
    assert body.startswith("embry0_git clean -ffdxq "), f"untracked and ignored files are removed: {body}"
    for pattern in ("node_modules", "/.pnpm-store", "/.venv", "/vendor", "src/generated", ".husky/_"):
        assert f"--exclude={pattern}" in body, f"the clean must keep {pattern}: {body}"


@pytest.mark.asyncio
async def test_a_tree_that_did_not_reach_the_round_s_commit_is_not_installed_or_recorded(monkeypatch):
    _params("requested_head")
    deps = _deps()
    docker, _, _, _, state_repo = deps
    _fake_clone(monkeypatch, head=STALE)

    with pytest.raises(RuntimeError, match="not " + HEAD[:12]):
        await _warm(deps)
    assert not any("npm ci" in c for c in _cmds(docker)), "nothing is installed into a tree at the wrong commit"
    state_repo.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_uses_custom_install_command_and_prebaked_image(monkeypatch):
    """RAV-1400: cache.install_command replaces the npm ci default verbatim,
    and a prebaked image tag overrides the warmer profile's base image."""
    deps = _deps()
    _fake_clone(monkeypatch)

    result = await _warm(
        deps,
        install_command="npm ci --prefer-offline --legacy-peer-deps",
        prebaked_image_tag="embry0-qa-acme_console:2026-08-12",
    )

    assert result == "warmed"
    assert any("npm ci --prefer-offline --legacy-peer-deps" in s for s in _cmds(deps[0]))
    profile_used = deps[1].create.call_args.kwargs.get("profile")
    assert profile_used["base_image"] == "embry0-qa-acme_console:2026-08-12"


@pytest.mark.asyncio
async def test_warm_default_install_without_overrides(monkeypatch):
    """No install_command / no prebaked tag → historical npm ci default, slim profile untouched."""
    deps = _deps()
    _fake_clone(monkeypatch)

    await _warm(deps)

    assert any("npm ci --prefer-offline 2>&1" in s for s in _cmds(deps[0]))
    assert "base_image" not in deps[1].create.call_args.kwargs.get("profile")


@pytest.mark.asyncio
async def test_warm_binds_repo_into_sandbox_enrollment(monkeypatch):
    """RAV-1400: create(repo=...) so the git-proxy serves the owner's token —
    without it, per-owner-token repos 403 on the warmer's clone."""
    deps = _deps()
    _fake_clone(monkeypatch)

    await _warm(deps, repo="acme/console")

    assert deps[1].create.call_args.kwargs.get("repo") == "acme/console"


@pytest.mark.asyncio
async def test_warmer_clones_the_repo_forge(monkeypatch):
    """A gitlab repo's warm clone must not go to github.com (spec 3.6)."""
    deps = _deps()
    seen = _fake_clone(monkeypatch)

    await _warm(deps, repo="acme/group/backend", forge="gitlab", gitlab_url="https://gitlab.example.com")

    assert seen["forge"] == "gitlab"
    assert seen["gitlab_url"] == "https://gitlab.example.com"
    assert deps[1].create.await_args.kwargs["forge"] == "gitlab"


# --- the clean itself, with real git ------------------------------------------------------

_needs_git = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash"
)


@_needs_git
def test_the_clean_removes_build_output_and_keeps_only_the_dependency_directories(tmp_path):
    """The warmer's own clean script, run by bash and git on a real checkout: build output goes
    wherever it is, the defaults keep `node_modules` at any depth and every other dependency
    directory only at the top of the tree, and a config pattern is anchored when it has a slash
    before its end (`src/generated`, `tools/**`) and matches at any depth when it has none (`gen`)."""
    from embry0.cache import volume_warmer
    from tests.fixtures.sandbox_git_local import local_config, localize

    assert hasattr(volume_warmer, "clean_tree") and hasattr(volume_warmer, "DEFAULT_KEEP"), (
        "the warmer has no clean step (clean_tree, DEFAULT_KEEP): an earlier round's files stay in the tree"
    )
    DEFAULT_KEEP, clean_tree = volume_warmer.DEFAULT_KEEP, volume_warmer.clean_tree

    ws = tmp_path / "ws"
    ws.mkdir()
    git = ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    (ws / "public").mkdir()
    (ws / "public" / "index.html").write_text("tracked")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "one"], check=True)

    kept = [
        "node_modules/a/index.js",
        "apps/web/node_modules/b/index.js",
        ".pnpm-store/v3/x",
        ".yarn/cache/x.zip",
        ".pnp.cjs",
        ".pnp.loader.mjs",
        ".venv/bin/python",
        "__pypackages__/3.12/lib/x",
        "vendor/autoload.php",
        "src/generated/api.ts",
        "gen/a.ts",
        "apps/web/gen/b.ts",
        "tools/bin/x",
    ]
    removed = [
        "dist/app.js",
        ".next/cache/x",
        ".turbo/cache/x",
        "public/vendor/lib.js",
        "apps/web/.yarn/x",
        "apps/web/.pnp.cjs",
        "apps/py/.venv/bin/python",
        "apps/py/__pypackages__/x",
        "apps/web/.pnpm-store/x",
        "apps/web/src/generated/api.ts",
        "untracked.txt",
        "apps/web/tools/bin/x",
    ]
    for rel in kept + removed:
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("x")

    argv = localize(clean_tree([*DEFAULT_KEEP, "src/generated", "gen", "tools/**"]), local_config(tmp_path))
    argv[2] = argv[2].replace("\nembry0_git_repo /workspace\n", f"\nembry0_git_repo {ws}\n")
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
    run = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr

    survived = {rel for rel in kept + removed if (ws / rel).exists()}
    assert survived == set(kept), (
        f"left behind: {sorted(survived - set(kept))}; removed what it must keep: {sorted(set(kept) - survived)}"
    )
    assert (ws / "public" / "index.html").read_text() == "tracked", "tracked files are not the clean's to remove"
    assert (ws / ".qa" / "logs").is_dir() and (ws / ".qa" / "pids").is_dir(), ".qa is there for the round"
