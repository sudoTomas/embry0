"""Orchestrator-side volume warmer.

Brings the shared volume to the QA round's commit before the round's
sub-tasks mount it, inside a short-lived bootstrap sandbox that mounts the
volume at ``/workspace`` (based on the repo's prebaked image when one exists —
its baked ~/.npm cache makes the install mostly link-time). Every round:

1. checks the volume's labels (``SharedVolumeManager.verify``) before mounting;
2. clones the repository into the volume, or fetches the branch into the
   checkout already there, and checks the tree out to ``requested_head``, the
   commit the round tests (``qa["head_sha"]``);
3. removes every untracked and ignored file except the dependency
   directories (:data:`DEFAULT_KEEP` plus the stored config's
   ``cache.shared_volume.keep``), so no build output or other file from an
   earlier round is served to this one;
4. runs the repo's install command (``cache.install_command``, default
   ``npm ci --prefer-offline``) only when the recorded lockfile sha differs
   from this round's (or nothing was recorded), and records it.

The bootstrap sandbox is always destroyed in a ``finally`` block so resources
are freed even if a step fails (the volume may be partially populated; the
next warm cleans it and, with no recorded lockfile, installs again).

Every git command goes through :mod:`embry0.execution.sandbox_git`.

Scopes: per-job and per-repo volumes. per-org is rejected at qa.yaml parse
time (RAV-1061) — a warm holds one repo's tree and one lockfile sha, so
cross-repo sharing is not expressible here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import structlog

from embry0.cache.trusted_run import is_commit_id
from embry0.cache.volume_manager import (
    VOLUME_NAME_PREFIX,
    SharedVolume,
    SharedVolumeManager,
    VolumeOwnershipError,
    VolumeScope,
)
from embry0.execution import sandbox_git
from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository
from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

logger = structlog.get_logger(__name__)


DEFAULT_INSTALL_COMMAND = "npm ci --prefer-offline"

#: What the clean between rounds keeps: the directories dependency installs
#: write into the tree, as gitignore-style patterns. ``node_modules`` matches
#: at any depth, since workspace installs write one into each package; a build
#: that copies dependencies into its own output (``.next/standalone/node_modules``,
#: a bundled ``dist/node_modules``) keeps that copy too, while the rest of the
#: output is removed. Every other entry is anchored
#: at the top of the tree, where its install writes it, so a build output of
#: the same name deeper down (``public/vendor/``) is removed: the pnpm store,
#: Yarn's directory and its Plug'n'Play files, a Python virtual environment
#: and PEP 582 packages, Composer and Bundler vendoring. Each entry passes the
#: rule ``shared_volume.keep`` applies (qa_yaml_v2.keep_pattern_problem).
#: Everything else untracked or ignored is removed.
DEFAULT_KEEP: tuple[str, ...] = (
    "node_modules",
    "/.pnpm-store",
    "/.yarn",
    "/.pnp.cjs",
    "/.pnp.js",
    "/.pnp.loader.mjs",
    "/.pnp.data.json",
    "/.venv",
    "/__pypackages__",
    "/vendor",
)


def clean_tree(keep: Sequence[str]) -> list[str]:
    """The exec argv that removes every untracked and ignored file under ``/workspace`` except ``keep``.

    ``git clean -ffdx`` with an ``--exclude`` per pattern: ``-x`` drops the
    repository's own ignore rules, so build output and caches go too, while
    the excludes keep the dependency directories. ``.qa/`` (embry0's own
    per-round directory) is created again afterwards.
    """
    g = sandbox_git.git
    excludes = [f"--exclude={pattern}" for pattern in keep]
    return sandbox_git.script(f"set -e\n{g('clean', '-ffdxq', *excludes)}\nmkdir -p .qa/logs .qa/pids\n")


async def warm_shared_volume(
    *,
    repo: str,
    branch: str,
    volume_name: str,
    current_lockfile_sha: str,
    scope: str,
    scope_key: str,
    docker: Any,
    sandbox_mgr: Any,
    profiles_repo: Any,
    proxy_mgr: Any,
    state_repo: QAVolumeStateRepository,
    volume_mgr: SharedVolumeManager,
    requested_head: str,
    keep: Sequence[str] = (),
    install_command: str | None = None,
    prebaked_image_tag: str | None = None,
    forge: str = "github",
    gitlab_url: str = "",
) -> Literal["warmed", "refreshed"]:
    """Bring the shared volume to ``requested_head``, clean, installing deps when the lockfile changed.

    Returns:

    - ``"warmed"`` when the dependency install ran (the lockfile changed, or
      the volume had never been warmed) and the state was updated;
    - ``"refreshed"`` when the recorded lockfile sha already matched: the tree
      was moved to ``requested_head`` and cleaned, and the install skipped.

    Raises ``ValueError`` when ``requested_head`` is not a full commit id,
    ``VolumeOwnershipError`` when the volume fails its mount check, and
    ``RuntimeError`` when the tree does not end up at ``requested_head``.

    Parameters
    ----------
    repo:
        Repository path (``owner/name``, or a GitLab group path).
    branch:
        Branch to fetch (the run's branch); ``requested_head`` is on it.
    volume_name:
        Name of the Docker named volume to populate (already created by
        ``SharedVolumeManager.ensure``/``recreate``).
    current_lockfile_sha:
        SHA-256 hex digest of the current lockfile contents, compared with
        the stored ``last_warmed_sha`` to decide whether to install.
    scope / scope_key:
        The volume's scope and key: checked against its labels, and the
        ``state_repo`` key.
    docker:
        ``DockerClient`` instance.
    sandbox_mgr:
        ``SandboxManager`` instance. Called with ``volumes=[(volume_name,
        "/workspace")]``.
    profiles_repo:
        Repository that exposes ``async get(profile_name) -> dict``.  The
        warmer uses the ``"slim"`` profile.
    proxy_mgr:
        Proxy manager for git-credential injection (may be ``None`` for
        public repos or tests).
    state_repo:
        ``QAVolumeStateRepository``: the recorded lockfile sha.
    volume_mgr:
        ``SharedVolumeManager``: checks the volume's labels right before the
        warmer sandbox mounts it, and refuses (``VolumeOwnershipError``) a
        volume that is missing or not this deployment's for ``scope_key``.
    requested_head:
        The commit the round tests (``qa["head_sha"]``).
    keep:
        Extra gitignore-style patterns the clean keeps, after
        :data:`DEFAULT_KEEP` (``cache.shared_volume.keep``).
    forge / gitlab_url:
        Which forge the repo lives on, resolved by the caller via
        ``resolve_forge``. Passed to ``SandboxManager.create`` (it decides the
        git-proxy credential host) and to ``prep_qa_sandbox_clone`` (it decides
        the clone origin).
    """
    if not is_commit_id(requested_head):
        raise ValueError(f"the warmer needs the round's commit, got {requested_head!r}")

    # The install is the expensive step; moving the tree and cleaning it are
    # cheap and happen every round. Only the install is skipped while the
    # recorded lockfile sha matches.
    existing = await state_repo.get(scope=scope, scope_key=scope_key)
    install_needed = existing is None or existing.last_warmed_sha != current_lockfile_sha

    # --- Bootstrap sandbox ---
    profile = await profiles_repo.get("slim")
    # RAV-1400: run the warmer ON the repo's prebaked image when one exists.
    # The baked image carries a hot ~/.npm cache from bake time, so the
    # warmer's `npm ci` is mostly extract/link instead of a full cold
    # download — this is what makes the prebaked image load-bearing for
    # monorepos.
    if prebaked_image_tag and profile is not None:
        profile = {**profile, "base_image": prebaked_image_tag}
    # Name the warmer sandbox after the VOLUME, not the raw scope key: the
    # volume name is a hash of the key, so two scope keys never share a
    # warmer container.
    warmer_job_id = f"warmer__{volume_name.removeprefix(VOLUME_NAME_PREFIX)}"

    # The labels are checked right before the mount: a volume that is missing
    # (the daemon would create it unlabelled) or not ours for this key is
    # never mounted.
    if not await volume_mgr.verify(SharedVolume(volume_name, VolumeScope(scope), scope_key)):
        raise VolumeOwnershipError(f"volume {volume_name} is not this deployment's {scope} volume for the run")

    # The volume is mounted at /workspace (the whole tree): the warmer's clone
    # or fetch and its install land in it, and the round's sub-tasks mount the
    # same volume at /workspace and skip their own clone.
    # repo= binds the proxy enrollment so the git-proxy serves the OWNER'S
    # token (RAV-1400: without it, per-owner-token repos 403 on clone).
    container_id, sandbox_token = await sandbox_mgr.create(
        warmer_job_id,
        profile=profile,
        env={"EMBRY0_GIT_PROXY_URL": getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""},
        volumes=[(volume_name, "/workspace")],
        repo=repo,
        forge=forge,
        llm_providers=(),
    )

    try:
        # Clone into the volume, or fetch the branch into the checkout already
        # there, and check the tree out to the round's commit.
        cloned = await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=warmer_job_id,
            repo=repo,
            branch=branch,
            is_dind=False,
            qa_net="",
            forge=forge,
            gitlab_url=gitlab_url,
            head_sha=requested_head,
        )
        if cloned.head_sha != requested_head:
            raise RuntimeError(f"the volume's tree is at {cloned.head_sha[:12]}, not {requested_head[:12]}")

        # Nothing an earlier round built or wrote survives into this one,
        # except the dependency directories.
        await sandbox_git.run(
            docker,
            container_id,
            clean_tree([*DEFAULT_KEEP, *keep]),
            step=sandbox_git.GitStep.QA_CHECKOUT,
            timeout=300,
            job_id=warmer_job_id,
        )

        if install_needed:
            # Run the repo's install command (qa.yaml cache.install_command,
            # default `npm ci --prefer-offline`) so a warm npm cache shortens
            # subsequent installs.  Output is piped to logs; non-zero exit
            # propagates as a RuntimeError and causes the finally-destroy.
            install = install_command or DEFAULT_INSTALL_COMMAND
            await docker.run_cmd(
                docker.build_exec_cmd(
                    container_id,
                    ["bash", "-c", f"cd /workspace && {install} 2>&1"],
                ),
                timeout=600,
            )
            await state_repo.upsert(
                scope=scope,
                scope_key=scope_key,
                volume_name=volume_name,
                last_warmed_sha=current_lockfile_sha,
            )
            logger.info(
                "qa_shared_volume_warmed",
                scope=scope,
                volume_name=volume_name,
                lockfile_sha=current_lockfile_sha[:12],
                head_sha=requested_head,
            )
            return "warmed"

        # Dependencies already match the lockfile: refresh last_warmed_at
        # (keep the sha) without reinstalling.
        await state_repo.upsert(
            scope=scope,
            scope_key=scope_key,
            volume_name=volume_name,
            last_warmed_sha=None,
        )
        logger.info(
            "qa_shared_volume_refreshed",
            scope=scope,
            volume_name=volume_name,
            lockfile_sha=current_lockfile_sha[:12],
            head_sha=requested_head,
            reason="lockfile sha matches: tree moved and cleaned, install skipped",
        )
        return "refreshed"

    finally:
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("warmer_sandbox_destroy_failed", container_id=container_id, error=str(exc))
