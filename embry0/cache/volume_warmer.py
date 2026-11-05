"""Orchestrator-side volume warmer.

Runs the repo's install command (``cache.install_command``, default
``npm ci --prefer-offline``) into the shared volume once per QA run,
mounting the pre-created named volume at ``/workspace`` inside a
short-lived bootstrap sandbox (based on the repo's prebaked image when
one exists — its baked ~/.npm cache makes the install mostly link-time).

Idempotent: the warmer checks ``state_repo.get(scope, scope_key)`` first.  If
the recorded ``last_warmed_sha`` matches ``current_lockfile_sha`` the function
returns ``"skipped"`` immediately without creating any containers.

The bootstrap sandbox is always destroyed in a ``finally`` block so resources
are freed even if ``npm ci`` fails (the volume may be partially populated, but
a subsequent warm will overwrite it).

Scopes: per-job and per-repo volumes. per-org is rejected at qa.yaml parse
time (RAV-1061) — a warm holds one repo's tree and one lockfile sha, so
cross-repo sharing is not expressible here.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from embry0.cache.volume_manager import (
    VOLUME_NAME_PREFIX,
    SharedVolume,
    SharedVolumeManager,
    VolumeOwnershipError,
    VolumeScope,
)
from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository
from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

logger = structlog.get_logger(__name__)


DEFAULT_INSTALL_COMMAND = "npm ci --prefer-offline"


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
    install_command: str | None = None,
    prebaked_image_tag: str | None = None,
    forge: str = "github",
    gitlab_url: str = "",
) -> Literal["warmed", "skipped"]:
    """Idempotent volume warmer.

    Returns ``"warmed"`` when ``npm ci`` ran successfully and the state was
    updated, or ``"skipped"`` when the volume is already warm for the given
    ``current_lockfile_sha``.

    Parameters
    ----------
    repo:
        GitHub repo in ``owner/name`` form.
    branch:
        Branch to clone (typically the PR's head branch).
    volume_name:
        Name of the Docker named volume to populate (already created by
        ``SharedVolumeManager.ensure``).
    current_lockfile_sha:
        SHA-256 hex digest of the current lockfile contents.  Used for
        idempotency comparison against the stored ``last_warmed_sha``.
    scope / scope_key:
        Passed through to ``state_repo`` for the upsert after warming.
    docker:
        ``DockerClient`` instance.
    sandbox_mgr:
        ``SandboxManager`` instance.  Called with ``volumes=[(volume_name,
        "/workspace/node_modules")]`` so the volume is mounted before
        ``npm ci`` writes to it.
    profiles_repo:
        Repository that exposes ``async get(profile_name) -> dict``.  The
        warmer uses the ``"slim"`` profile.
    proxy_mgr:
        Proxy manager for git-credential injection (may be ``None`` for
        public repos or tests).
    forge / gitlab_url:
        Which forge the repo lives on, resolved by the caller via
        ``resolve_forge``. Passed to ``SandboxManager.create`` (it decides the
        git-proxy credential host) and to ``prep_qa_sandbox_clone`` (it decides
        the clone origin). Defaults keep the pre-#162 GitHub behaviour.
    state_repo:
        ``QAVolumeStateRepository`` for idempotency tracking.
    volume_mgr:
        ``SharedVolumeManager``: checks the volume's labels right before the
        warmer sandbox mounts it, and refuses (``VolumeOwnershipError``) a
        volume that is missing or not this deployment's for ``scope_key``.
    """
    # --- Idempotency check ---
    existing = await state_repo.get(scope=scope, scope_key=scope_key)
    if existing is not None and existing.last_warmed_sha == current_lockfile_sha:
        logger.info(
            "qa_shared_volume_skipped",
            scope=scope,
            scope_key=scope_key,
            volume_name=volume_name,
            reason="lockfile sha matches",
        )
        return "skipped"

    # --- Bootstrap sandbox ---
    profile = await profiles_repo.get("slim")
    # RAV-1400: run the warmer ON the repo's prebaked image when one exists.
    # The baked image carries a hot ~/.npm cache from bake time, so the
    # warmer's `npm ci` is mostly extract/link instead of a full cold
    # download — this is what makes the prebaked image load-bearing for
    # monorepos (the fresh volume mounted at /workspace shadows the image's
    # baked workspace, so the clone below starts clean either way).
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

    # Mount the shared volume at /workspace (the WHOLE workspace tree, not
    # just node_modules) so that:
    #   1. The warmer's `git clone` populates the volume with the repo source.
    #   2. The warmer's `npm ci` populates /workspace/node_modules INSIDE the
    #      same volume.
    #   3. Sub-tasks then mount this same volume at /workspace and skip their
    #      own clone (subtask_nodes.py passes shared_volume_name -> cached_volume
    #      to prep_qa_sandbox_clone, which mkdir's .qa/logs and head_sha-reads
    #      without re-cloning).
    #
    # The previous mount point was /workspace/node_modules, which (a) created
    # /workspace/node_modules/ before clone ran, causing `git clone /workspace`
    # to fail with "destination path '/workspace' already exists and is not an
    # empty directory", AND (b) was inconsistent with sub-tasks expecting the
    # whole tree at /workspace.
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
        # Clone the repo so /workspace/package.json (and any workspace-root
        # lockfile) is present before running npm ci.
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
        )

        # Run the repo's install command (qa.yaml cache.install_command,
        # default `npm ci --prefer-offline`) so a warm npm cache shortens
        # subsequent installs.  Output is piped to logs; non-zero exit propagates
        # as a RuntimeError from docker.run_cmd and causes the finally-destroy.
        install = install_command or DEFAULT_INSTALL_COMMAND
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                ["bash", "-c", f"cd /workspace && {install} 2>&1"],
            ),
            timeout=600,
        )

        # Record the new warm state so the next call can skip.
        await state_repo.upsert(
            scope=scope,
            scope_key=scope_key,
            volume_name=volume_name,
            last_warmed_sha=current_lockfile_sha,
        )

        logger.info(
            "qa_shared_volume_warmed",
            scope=scope,
            scope_key=scope_key,
            volume_name=volume_name,
            lockfile_sha=current_lockfile_sha[:12],
            head_sha=cloned.head_sha,
        )
        return "warmed"

    finally:
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("warmer_sandbox_destroy_failed", container_id=container_id, error=str(exc))
