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
from typing import TYPE_CHECKING, Any

import structlog

from embry0.cache.lockfile_hash import compute_lockfile_sha
from embry0.cache.prebaked_staleness import container_base_image, image_digest, pull_image
from embry0.execution.image_registry import pushed_digest, qualify_image, strip_digest
from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

if TYPE_CHECKING:
    from embry0.execution.runtime import AgentRuntime

logger = structlog.get_logger(__name__)

# Lockfile candidates in priority order (must match lockfile_hash.py)
_LOCKFILE_CANDIDATES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


#: Every pre-built QA image's repository name starts with this; the QA image
#: retention sweep (embry0/cache/qa_image_retention.py) finds them by it.
QA_IMAGE_PREFIX = "embry0-qa-"


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
    return f"{QA_IMAGE_PREFIX}{sanitized}:{ts}"


async def build_qa_image(
    *,
    repo: str,
    branch: str,
    built_by: str,
    sandbox_mgr: Any,
    runtime: AgentRuntime,
    profiles_repo: Any,
    proxy_mgr: Any,
    image_repo: Any,
    install_command: str | None = None,
    image_registry: str = "",
    forge: str = "github",
    gitlab_url: str = "",
) -> ImageBuildResult:
    """Build a pre-baked QA sandbox image for `repo` at `branch`.

    Steps:
      1. Allocate a builder sandbox using the `slim` profile.
      2. Clone the repo (via prep_qa_sandbox_clone).
      3. Fetch lockfile from container, compute lockfile_sha via shared helper.
      4. npm ci inside the sandbox.
      5. turbo run build --output-logs=hash-only (warm the turbo cache).
      6. docker commit container_id → new image tag (return value used as tag).
      7. Record the tag in qa_image_tags, with the base image it is a
         commit of (#334 — selection skips it once that base is rebuilt).
      8. Destroy the builder sandbox.
    """
    # RAV-1400: bake on the QA image lineage, NOT slim. The baked tag
    # REPLACES the subtask profile's base_image (qa-external -> browser +
    # Playwright tooling); a slim-based bake silently stripped the browser
    # from every subtask, downgrading QA agents to HTTP-only verification.
    # slim remains the fallback for deployments without the QA profile.
    # Commands inside the builder sandbox still go through the CLI client (#268).
    docker = runtime.client
    profile = await profiles_repo.get("qa-external") or await profiles_repo.get("slim")
    if profile is None:
        raise ImageBuildError("builder profile 'qa-external'/'slim' not found")

    builder_job_id = f"image-builder__{repo.replace('/', '_')}__{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    # #334: refresh the sandbox daemon's copy of the base image BEFORE baking
    # on it. Nothing else does - the bootstrap pushes host->registry and
    # SandboxImageManager.ensure_image covers only embry0-sandbox - so DinD can
    # sit a rebuild behind, and a bake on that copy would be committed stale
    # and flagged by selection the moment it was recorded. Tolerant: a registry
    # hiccup must not fail a bake, it only leaves the base unrecorded below.
    profile_base = qualify_image(profile.get("base_image", ""), image_registry)
    if profile_base:
        await pull_image(runtime, profile_base)

    # repo= binds the proxy enrollment so the git-proxy serves the OWNER'S
    # token (RAV-1400: without it, per-owner-token repos 403 on clone).
    container_id, sandbox_token = await sandbox_mgr.create(
        builder_job_id,
        profile=profile,
        env={"EMBRY0_GIT_PROXY_URL": getattr(proxy_mgr, "git_proxy_url", "")},
        repo=repo,
        forge=forge,
        llm_providers=(),
    )

    try:
        # #334: which base image is this build a commit of? Read off the
        # container, so it is the name the sandbox manager really used, and
        # take the digest of the bits it runs (by image ID - the tag can move
        # while a bake runs). Fail open: an unrecorded base reads as "unknown"
        # at selection, which keeps using the image, and a bake is far too
        # expensive to throw away over a bookkeeping read.
        base_image: str | None = None
        base_image_digest: str | None = None
        try:
            base_image, base_local_id = await container_base_image(runtime, container_id)
            # #523: a pinned base launches as `…:latest@sha256:…`. Record the
            # tag: staleness asks whether that tag has moved on since.
            base_image = strip_digest(base_image)
            base_image_digest = await image_digest(runtime, base_local_id, repo_name=base_image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa_image_base_unreadable", repo=repo, error=str(exc)[:200])
        if base_image_digest is None:
            # Never half a pair: a name without a digest can never be compared.
            base_image = None
            logger.warning(
                "qa_image_base_not_recorded",
                repo=repo,
                detail="staleness cannot be tracked for this image; it will read as unknown",
            )

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
            forge=forge,
            gitlab_url=gitlab_url,
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

        # Dependency install — the repo's qa.yaml cache.install_command wins
        # (RAV-1400: e.g. a repo that needs --legacy-peer-deps); default
        # keeps the historical `npm ci --prefer-offline`. 1200s budget: a
        # 40-app cold install is exactly the case this image exists to bake.
        install = install_command or "npm ci --prefer-offline"
        try:
            await docker.run_cmd(
                docker.build_exec_cmd(
                    container_id,
                    ["bash", "-c", f"cd /workspace && {install} 2>&1"],
                ),
                timeout=1200,
            )
        except Exception as exc:
            raise ImageBuildError(f"dependency install ({install!r}) failed: {exc}") from exc

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

        # RAV-1400: sandbox launches registry-qualify every embry0-* image
        # (see execution/image_registry.qualify_image), so the committed
        # image must ALSO exist under the qualified name or every launch
        # 404s with "manifest not found". Tag + push to the sidecar
        # registry; fatal — an unpushed bake is unusable.
        if image_registry:
            qualified = qualify_image(image_tag, image_registry)
            if qualified != image_tag:
                try:
                    pushed = await runtime.push_image(image_tag, qualified)
                except Exception as exc:
                    raise ImageBuildError(f"image push to registry failed ({qualified}): {exc}") from exc
                # #523: record the digest this push reported, so the image runs
                # by digest and a later push to the same tag is never used.
                # Fatal like the push: an unpinned bake would run by tag.
                digest = pushed_digest(pushed)
                if digest is None:
                    raise ImageBuildError(f"push of {qualified} reported no digest; refusing to record it unpinned")
                logger.info("qa_image_pushed", image_tag=image_tag, qualified=qualified, digest=digest)
                image_tag = f"{image_tag}@{digest}"

        await image_repo.record(
            repo=repo,
            image_tag=image_tag,
            lockfile_sha=lockfile_sha,
            built_by=built_by,
            metadata={"head_sha": cloned.head_sha, "branch": branch},
            base_image=base_image,
            base_image_digest=base_image_digest,
        )

        logger.info(
            "qa_image_built",
            repo=repo,
            image_tag=image_tag,
            lockfile_sha=lockfile_sha[:12],
            head_sha=cloned.head_sha,
            base_image=base_image,
            base_image_digest=base_image_digest,
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
