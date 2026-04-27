"""Sandbox image lifecycle — auto-build, hash detection, profile images, reaper."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import structlog

from athanor.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)


class SandboxImageManager:
    """Manages sandbox Docker images inside the DinD daemon."""

    def __init__(self, docker: DockerClient, app_root: str = "/app") -> None:
        self._docker = docker
        self._app_root = Path(app_root)
        self._build_lock = asyncio.Lock()

    def _compute_build_hash(self) -> str:
        """Compute SHA256 hash of Dockerfile.sandbox + sandbox source files."""
        hasher = hashlib.sha256()

        dockerfile = self._app_root / "infra" / "Dockerfile.sandbox"
        if dockerfile.exists():
            hasher.update(dockerfile.read_bytes())

        source_dirs = [
            self._app_root / "athanor" / "sandbox",
            self._app_root / "athanor" / "safety",
        ]
        source_files = [
            self._app_root / "athanor" / "execution" / "events.py",
        ]

        for src_dir in source_dirs:
            if src_dir.exists():
                for f in sorted(src_dir.rglob("*.py")):
                    hasher.update(f.read_bytes())

        for f in source_files:
            if f.exists():
                hasher.update(f.read_bytes())

        return hasher.hexdigest()[:16]

    async def _get_image_hash(self, image: str) -> str | None:
        """Get the build hash label from a Docker image."""
        try:
            cmd = self._docker._build_base_cmd()
            cmd.extend(["inspect", "--format", '{{index .Config.Labels "athanor.build-hash"}}', image])
            result = await self._docker.run_cmd(cmd, timeout=10)
            return result.strip() if result.strip() else None
        except RuntimeError:
            return None

    async def _image_exists(self, image: str) -> bool:
        """Check if an image exists in DinD."""
        try:
            cmd = self._docker._build_base_cmd()
            cmd.extend(["image", "inspect", image])
            await self._docker.run_cmd(cmd, timeout=10)
            return True
        except RuntimeError:
            return False

    async def ensure_image(self, image: str = "legion-sandbox:latest", force: bool = False) -> bool:
        """Ensure the sandbox image is built and up-to-date. Returns True if build occurred."""
        async with self._build_lock:
            current_hash = self._compute_build_hash()

            if not force:
                existing_hash = await self._get_image_hash(image)
                if existing_hash == current_hash:
                    logger.info("sandbox_image_up_to_date", image=image, hash=current_hash)
                    return False

                if existing_hash:
                    logger.info("sandbox_image_stale", image=image, existing=existing_hash, current=current_hash)
                else:
                    exists = await self._image_exists(image)
                    if exists:
                        logger.info("sandbox_image_no_hash", image=image)
                    else:
                        logger.info("sandbox_image_missing", image=image)

            return await self._build_image(image, current_hash)

    async def _build_image(self, image: str, build_hash: str) -> bool:
        """Build the sandbox Docker image."""
        logger.info("sandbox_image_building", image=image, hash=build_hash)

        try:
            dockerfile_path = self._app_root / "infra" / "Dockerfile.sandbox"
            if not dockerfile_path.exists():
                dockerfile_path = Path("/app/infra/Dockerfile.sandbox")

            build_context = str(self._app_root)

            cmd = self._docker._build_base_cmd()
            cmd.extend(
                [
                    "build",
                    "-t",
                    image,
                    "--label",
                    f"athanor.build-hash={build_hash}",
                    "-f",
                    str(dockerfile_path),
                    build_context,
                ]
            )

            await self._docker.run_cmd(cmd, timeout=300)
            logger.info("sandbox_image_built", image=image, hash=build_hash)
            return True

        except RuntimeError as exc:
            logger.error("sandbox_image_build_failed", image=image, error=str(exc))
            return False

    async def build_profile_image(
        self,
        profile_name: str,
        base_image: str = "legion-sandbox:latest",
        additional_packages: list[str] | None = None,
        setup_commands: list[str] | None = None,
    ) -> bool:
        """Build a profile-specific sandbox image."""
        async with self._build_lock:
            image_tag = f"athanor-sandbox:{profile_name}"

            dockerfile_content = f"FROM {base_image}\nUSER root\n"
            if additional_packages:
                pkg_list = " ".join(additional_packages)
                dockerfile_content += f"RUN pip install --no-cache-dir {pkg_list}\n"
            if setup_commands:
                for cmd_str in setup_commands:
                    dockerfile_content += f"RUN {cmd_str}\n"
            dockerfile_content += "USER agent\nWORKDIR /workspace\n"

            import os
            import tempfile

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".dockerfile", delete=False) as f:
                    f.write(dockerfile_content)
                    temp_path = f.name

                cmd = self._docker._build_base_cmd()
                cmd.extend(["build", "-t", image_tag, "-f", temp_path, "."])
                await self._docker.run_cmd(cmd, timeout=300)

                logger.info("sandbox_profile_image_built", image=image_tag, profile=profile_name)
                return True

            except Exception as exc:
                logger.error("sandbox_profile_image_failed", profile=profile_name, error=str(exc))
                return False
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    async def remove_image(self, image: str) -> None:
        """Remove an image from DinD."""
        try:
            cmd = self._docker._build_base_cmd()
            cmd.extend(["rmi", "-f", image])
            await self._docker.run_cmd(cmd, timeout=30)
            logger.info("sandbox_image_removed", image=image)
        except RuntimeError:
            logger.warning("sandbox_image_remove_failed", image=image)


class ContainerReaper:
    """Background task that destroys stale sandbox containers and expires paused jobs."""

    def __init__(
        self,
        docker: DockerClient,
        max_age_hours: int = 24,
        check_interval_seconds: int = 3600,
        db: Any = None,
        paused_ttl_hours: int = 48,
    ) -> None:
        self._docker = docker
        self._max_age_hours = max_age_hours
        self._interval = check_interval_seconds
        self._task: asyncio.Task | None = None
        self._db = db
        self._paused_ttl_hours = paused_ttl_hours

    def start(self) -> None:
        """Start the reaper background task."""
        self._task = asyncio.create_task(self._reap_loop())
        logger.info("container_reaper_started", max_age_hours=self._max_age_hours)

    async def stop(self) -> None:
        """Stop the reaper."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _reap_loop(self) -> None:
        """Periodically check for stale containers."""
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._reap()
                await self._expire_paused_jobs()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("reaper_error")

    async def _reap(self) -> None:
        """Find and destroy stale sandbox containers."""
        try:
            cmd = self._docker._build_base_cmd()
            cmd.extend(
                [
                    "ps",
                    "-a",
                    "--filter",
                    "name=sandbox-",
                    "--format",
                    "{{.ID}} {{.Names}} {{.RunningFor}}",
                ]
            )
            output = await self._docker.run_cmd(cmd, timeout=15)

            for line in output.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue

                container_id = parts[0]
                name = parts[1]
                running_for = " ".join(parts[2:])

                if "hours" in running_for or "days" in running_for:
                    hours = 0
                    if "days" in running_for:
                        try:
                            hours = int(running_for.split()[0]) * 24
                        except ValueError:
                            continue
                    elif "hours" in running_for:
                        try:
                            hours = int(running_for.split()[0])
                        except ValueError:
                            continue

                    if hours >= self._max_age_hours:
                        logger.info("reaping_stale_container", name=name, age_hours=hours)
                        try:
                            stop_cmd = self._docker.build_stop_cmd(container_id, timeout=5)
                            await self._docker.run_cmd(stop_cmd)
                        except RuntimeError:
                            pass
                        try:
                            rm_cmd = self._docker.build_rm_cmd(container_id)
                            await self._docker.run_cmd(rm_cmd)
                        except RuntimeError:
                            pass

        except RuntimeError:
            logger.warning("reaper_list_failed")

    async def _expire_paused_jobs(self) -> None:
        """Transition paused jobs past their TTL to expired, destroy their sandboxes."""
        if not self._db:
            return
        try:
            rows = await self._db.fetch(
                "SELECT job_id FROM jobs WHERE status = 'paused' AND updated_at < NOW() - INTERVAL '1 hour' * $1",
                self._paused_ttl_hours,
            )
            for row in rows:
                job_id = row["job_id"]
                container_name = f"sandbox-{job_id}"
                logger.info("expiring_paused_job", job_id=job_id)
                try:
                    stop_cmd = self._docker.build_stop_cmd(container_name, timeout=5)
                    await self._docker.run_cmd(stop_cmd)
                except RuntimeError:
                    pass
                try:
                    rm_cmd = self._docker.build_rm_cmd(container_name)
                    await self._docker.run_cmd(rm_cmd)
                except RuntimeError:
                    pass
                await self._db.execute(
                    "UPDATE jobs SET status = 'expired' WHERE job_id = $1",
                    job_id,
                )
                issue_row = await self._db.fetchrow("SELECT issue_id FROM jobs WHERE job_id = $1", job_id)
                if issue_row and issue_row.get("issue_id"):
                    await self._db.execute(
                        "UPDATE issues SET status = 'open' WHERE id = $1 AND status = 'paused'",
                        issue_row["issue_id"],
                    )
                logger.info("paused_job_expired", job_id=job_id)
        except Exception:
            logger.exception("expire_paused_jobs_error")
