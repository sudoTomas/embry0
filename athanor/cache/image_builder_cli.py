"""Image-builder CLI / service entry point.

Wraps build_qa_image with idempotency: skip when the active tag's lockfile_sha
already matches the current default-branch lockfile (avoids redundant rebuilds).
A `--force` flag bypasses the check.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from athanor.cache.image_builder import build_qa_image

logger = structlog.get_logger(__name__)


async def _compute_remote_lockfile_sha(
    *, repo: str, branch: str, deps: dict[str, Any]
) -> str:
    """Spin up a tiny probe sandbox, clone the repo, hash the lockfile, destroy.

    Phase-2 inelegance: we re-clone just to read the lockfile. Phase-3
    optimization could use the GitHub Contents API to fetch the file
    directly without a sandbox. For now the CLI tolerates the ~10s cost
    since image builds are infrequent.
    """
    from athanor.workflows.qa._subtask_prep import prep_qa_sandbox_clone

    sandbox_mgr = deps["sandbox_mgr"]
    docker = deps["docker"]
    profiles_repo = deps["profiles_repo"]
    proxy_mgr = deps["proxy_mgr"]

    profile = await profiles_repo.get("slim")
    job_id = (
        f"image-builder-probe::{repo.replace('/', '_')}"
        f"::{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    )
    container_id, sandbox_token = await sandbox_mgr.create(
        job_id,
        profile=profile,
        env={"ATHANOR_GIT_PROXY_URL": getattr(proxy_mgr, "git_proxy_url", "")},
    )
    try:
        await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=job_id,
            repo=repo,
            branch=branch,
            is_dind=False,
            qa_net="",
            base=docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else [],
        )
        lockfile_text = await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "bash",
                    "-c",
                    (
                        "cat /workspace/package-lock.json 2>/dev/null || "
                        "cat /workspace/pnpm-lock.yaml 2>/dev/null || "
                        "cat /workspace/yarn.lock 2>/dev/null || "
                        "true"
                    ),
                ],
            ),
            timeout=10,
        )
        return hashlib.sha256((lockfile_text or "").encode("utf-8")).hexdigest()
    finally:
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception as exc:
            logger.warning("probe_sandbox_destroy_failed", error=str(exc))


async def run_build_qa_image(
    *,
    repo: str,
    branch: str,
    force: bool,
    deps: dict[str, Any],
) -> Literal["built", "skipped"]:
    """Service entry point — idempotent unless ``force=True``.

    Returns ``"skipped"`` when the currently active image tag for *repo* was
    already built from the same lockfile that lives on *branch* today.
    Returns ``"built"`` after a successful fresh build.

    Args:
        repo:   GitHub repo in ``owner/name`` form.
        branch: Branch whose lockfile is used as the freshness probe.
        force:  When ``True``, bypass the idempotency check and always rebuild.
        deps:   Dict of injected service objects (see ``build_qa_image`` for the
                full set; must additionally include ``image_repo``).
    """
    image_repo = deps["image_repo"]

    if not force:
        active = await image_repo.get_active(repo)
        if active is not None:
            current_sha = await _compute_remote_lockfile_sha(
                repo=repo, branch=branch, deps=deps
            )
            if active.lockfile_sha == current_sha:
                logger.info(
                    "qa_image_build_skipped",
                    repo=repo,
                    image_tag=active.image_tag,
                    reason="lockfile unchanged",
                )
                return "skipped"

    await build_qa_image(
        repo=repo,
        branch=branch,
        built_by="cli",
        sandbox_mgr=deps["sandbox_mgr"],
        docker=deps["docker"],
        profiles_repo=deps["profiles_repo"],
        proxy_mgr=deps["proxy_mgr"],
        image_repo=image_repo,
    )
    return "built"
