"""Docker volume manager for the Phase-2 shared-volume cache layer.

Volume naming convention:
  per-job  → embry0-qa-vol-<job_id>
  per-repo → embry0-qa-vol-<sanitized-repo>
  per-org  → embry0-qa-vol-<sanitized-org>   (Phase 2 raises NotImplementedError)

Sanitization replaces '/' and ':' with '-' so names are valid Docker volume IDs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import structlog

from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository

logger = structlog.get_logger(__name__)


class VolumeScope(StrEnum):
    PER_JOB = "per-job"
    PER_REPO = "per-repo"
    PER_ORG = "per-org"


def _sanitize(name: str) -> str:
    return name.replace("/", "-").replace(":", "-")


def volume_name_for(scope: VolumeScope, scope_key: str) -> str:
    if scope is VolumeScope.PER_JOB:
        return f"embry0-qa-vol-{_sanitize(scope_key)}"
    if scope is VolumeScope.PER_REPO:
        return f"embry0-qa-vol-{_sanitize(scope_key)}"
    if scope is VolumeScope.PER_ORG:
        raise NotImplementedError("per-org scope is parsed but not yet implemented (Phase 3)")
    raise ValueError(f"unknown scope: {scope!r}")


class SharedVolumeManager:
    """Wraps the Docker volume API + state-tracking repo."""

    def __init__(self, *, docker: Any, state_repo: QAVolumeStateRepository) -> None:
        self.docker = docker
        self.state_repo = state_repo

    async def ensure(self, *, scope: VolumeScope, scope_key: str) -> str:
        """Create the volume if missing, record state, return its name."""
        name = volume_name_for(scope, scope_key)
        base = self.docker._build_base_cmd() if hasattr(self.docker, "_build_base_cmd") else []

        # Idempotency: check if volume already exists
        existing = await self.docker.run_cmd(
            base + ["volume", "ls", "--quiet", "--filter", f"name={name}"],
            timeout=10,
        )
        if name in (existing or ""):
            await self.state_repo.upsert(
                scope=str(scope),
                scope_key=scope_key,
                volume_name=name,
                last_warmed_sha=None,
            )
            return name

        await self.docker.run_cmd(
            base + ["volume", "create", name],
            timeout=10,
        )
        await self.state_repo.upsert(
            scope=str(scope),
            scope_key=scope_key,
            volume_name=name,
            last_warmed_sha=None,
        )
        logger.info("qa_shared_volume_created", scope=str(scope), name=name)
        return name

    async def destroy(self, volume_name: str) -> None:
        """Remove the volume. Failures are logged, not raised."""
        base = self.docker._build_base_cmd() if hasattr(self.docker, "_build_base_cmd") else []
        try:
            await self.docker.run_cmd(
                base + ["volume", "rm", "--force", volume_name],
                timeout=10,
            )
        except Exception as exc:
            logger.warning("qa_shared_volume_destroy_failed", name=volume_name, error=str(exc))
