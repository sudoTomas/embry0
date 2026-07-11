"""Orchestrator-side volume warmer.

Runs ``npm ci --prefer-offline`` into the shared volume once per QA run,
mounting the pre-created named volume at ``/workspace/node_modules`` inside a
short-lived bootstrap sandbox.

Idempotent: the warmer checks ``state_repo.get(scope, scope_key)`` first.  If
the recorded ``last_warmed_sha`` matches ``current_lockfile_sha`` the function
returns ``"skipped"`` immediately without creating any containers.

The bootstrap sandbox is always destroyed in a ``finally`` block so resources
are freed even if ``npm ci`` fails (the volume may be partially populated, but
a subsequent warm will overwrite it).

Phase-2 scope: per-job and per-repo volumes.  per-org is not implemented.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository
from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

logger = structlog.get_logger(__name__)


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
    state_repo:
        ``QAVolumeStateRepository`` for idempotency tracking.
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
    warmer_job_id = f"warmer__{scope_key}"

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
    container_id, sandbox_token = await sandbox_mgr.create(
        warmer_job_id,
        profile=profile,
        env={"EMBRY0_GIT_PROXY_URL": getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""},
        volumes=[(volume_name, "/workspace")],
    )
    base: list[str] = docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []

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
            base=base,
        )

        # Run npm ci with --prefer-offline so a warm Turbo/npm cache shortens
        # subsequent installs.  Output is piped to logs; non-zero exit propagates
        # as a RuntimeError from docker.run_cmd and causes the finally-destroy.
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                ["bash", "-c", "cd /workspace && npm ci --prefer-offline 2>&1"],
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
