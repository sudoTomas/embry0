"""Pre-baked sandbox image builder.

Pure orchestration: spawn bootstrap sandbox → clone → npm ci → turbo build →
docker commit → record tag → destroy sandbox. All side-effecting deps are
injected for testability.

Phase-2 scope:
  - Single-host Docker daemon (image lives in the daemon's image cache;
    multi-host registry replication is Phase 3).
  - Lockfile hash drives the tag's lockfile_sha column → next build skipped
    if same hash already has an active tag.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from embry0.cache.lockfile_hash import compute_lockfile_sha
from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

logger = structlog.get_logger(__name__)

# Lockfile candidates in priority order (must match lockfile_hash.py)
_LOCKFILE_CANDIDATES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


class ImageBuildError(Exception):
    """Raised when image building fails at any stage."""


@dataclass(frozen=True, slots=True)
class ImageBuildResult:
    image_tag: str
    lockfile_sha: str
    head_sha: str


def _make_image_tag(repo: str) -> str:
    """Synthesize an image tag like `embry0-qa-org_r1:2026-05-05T06-00`.

    The tag prefix is the repo with `/` replaced by `_` (Docker tags can't
    contain slashes); the suffix is a UTC timestamp. Lockfile-sha-tagged
    images are looked up by `(repo, lockfile_sha)` UNIQUE in the DB; the
    image tag itself is just a human-readable handle.
    """
    sanitized = repo.lower().replace("/", "_").replace(":", "_")
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M")
    return f"embry0-qa-{sanitized}:{ts}"


async def build_qa_image(
    *,
    repo: str,
    branch: str,
    built_by: str,
    sandbox_mgr: Any,
    docker: Any,
    profiles_repo: Any,
    proxy_mgr: Any,
    image_repo: Any,
) -> ImageBuildResult:
    """Build a pre-baked QA sandbox image for `repo` at `branch`.

    Steps:
      1. Allocate a builder sandbox using the `slim` profile.
      2. Clone the repo (via prep_qa_sandbox_clone).
      3. Fetch lockfile from container, compute lockfile_sha via shared helper.
      4. npm ci inside the sandbox.
      5. turbo run build --output-logs=hash-only (warm the turbo cache).
      6. docker commit container_id → new image tag (return value used as tag).
      7. Record the tag in qa_image_tags.
      8. Destroy the builder sandbox.
    """
    profile = await profiles_repo.get("slim")
    if profile is None:
        raise ImageBuildError("builder profile 'slim' not found")

    builder_job_id = f"image-builder__{repo.replace('/', '_')}__{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    container_id, sandbox_token = await sandbox_mgr.create(
        builder_job_id,
        profile=profile,
        env={"EMBRY0_GIT_PROXY_URL": getattr(proxy_mgr, "git_proxy_url", "")},
    )

    try:
        cloned = await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=builder_job_id,
            repo=repo,
            branch=branch,
            is_dind=False,
            qa_net="",
            base=docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else [],
        )

        # Fetch lockfile text from the container and compute sha via shared helper.
        # We write it to a temp directory with its canonical filename so
        # compute_lockfile_sha can find it by name.
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
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "package-lock.json").write_text(lockfile_text or "", encoding="utf-8")
            lockfile_sha = compute_lockfile_sha(tmp_root)

        # npm ci
        try:
            await docker.run_cmd(
                docker.build_exec_cmd(
                    container_id,
                    ["bash", "-c", "cd /workspace && npm ci --prefer-offline 2>&1"],
                ),
                timeout=600,
            )
        except Exception as exc:
            raise ImageBuildError(f"npm ci failed: {exc}") from exc

        # turbo run build --output-logs=hash-only (non-fatal if it fails)
        try:
            await docker.run_cmd(
                docker.build_exec_cmd(
                    container_id,
                    [
                        "bash",
                        "-c",
                        "cd /workspace && npx turbo run build --output-logs=hash-only 2>&1 || true",
                    ],
                ),
                timeout=900,
            )
        except Exception as exc:
            logger.warning("turbo_warm_failed", error=str(exc))
            # Non-fatal: image still useful with just node_modules baked.

        proposed_tag = _make_image_tag(repo)
        # commit_container returns the final tag (may differ from proposed if the
        # helper normalises it); use its return value as the canonical image_tag.
        image_tag = await docker.commit_container(container_id, proposed_tag)

        await image_repo.record(
            repo=repo,
            image_tag=image_tag,
            lockfile_sha=lockfile_sha,
            built_by=built_by,
            metadata={"head_sha": cloned.head_sha, "branch": branch},
        )

        logger.info(
            "qa_image_built",
            repo=repo,
            image_tag=image_tag,
            lockfile_sha=lockfile_sha[:12],
            head_sha=cloned.head_sha,
        )

        return ImageBuildResult(
            image_tag=image_tag,
            lockfile_sha=lockfile_sha,
            head_sha=cloned.head_sha,
        )
    finally:
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception as exc:
            logger.warning(
                "image_builder_sandbox_destroy_failed",
                container_id=container_id,
                error=str(exc),
            )
