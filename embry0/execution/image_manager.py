"""Sandbox image lifecycle — auto-build, hash detection, profile images, reaper."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from embry0.execution.image_registry import is_registry_qualified, qualify_image
from embry0.execution.runtime import AgentRuntime
from embry0.execution.sandbox_listing import list_sandbox_containers, parse_ps_created
from embry0.storage.repositories.jobs import StatusTransitionConflict

logger = structlog.get_logger(__name__)

# The sandbox image is pulled at startup only when the daemon lacks it; the
# in-stack registry is local, so this bounds a hung registry, not a slow one.
_REGISTRY_PULL_TIMEOUT_SECONDS = 300


class SandboxImageManager:
    """Manages sandbox Docker images inside the DinD daemon."""

    def __init__(
        self,
        runtime: AgentRuntime,
        app_root: str = "/app",
        *,
        image_registry: str = "",
    ) -> None:
        self._runtime = runtime
        self._app_root = Path(app_root)
        self._build_lock = asyncio.Lock()
        self._image_registry = image_registry

    def _qualify(self, image: str) -> str:
        return qualify_image(image, self._image_registry)

    def _build_context_available(self) -> bool:
        """True iff the sandbox Dockerfile is shipped in this container.

        In the compose / K8s deployment the sandbox image is built and
        pushed to the in-stack ``registry:5000`` by the
        ``init-push-images`` / ``sandbox-image-builder`` service, and the
        orchestrator image deliberately does NOT ship
        ``infra/Dockerfile.sandbox`` or the build context. Only the
        standalone / dev layout has it. When it's absent the orchestrator
        must defer to the registry-provided image instead of attempting an
        impossible ``docker build`` (which fails with
        ``lstat /app/infra: no such file or directory``).
        """
        return (self._app_root / "infra" / "Dockerfile.sandbox").exists() or Path(
            "/app/infra/Dockerfile.sandbox"
        ).exists()

    def _compute_build_hash(self) -> str:
        """Compute SHA256 hash of Dockerfile.sandbox + sandbox source files."""
        hasher = hashlib.sha256()

        dockerfile = self._app_root / "infra" / "Dockerfile.sandbox"
        if dockerfile.exists():
            hasher.update(dockerfile.read_bytes())

        source_dirs = [
            self._app_root / "embry0" / "sandbox",
            self._app_root / "embry0" / "safety",
        ]
        source_files = [
            self._app_root / "embry0" / "execution" / "events.py",
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
            result = await self._runtime.image_label(self._qualify(image), "embry0.build-hash", timeout=10)
            return result or None
        except RuntimeError:
            return None

    async def _image_exists(self, image: str) -> bool:
        """Check if an image exists in DinD."""
        return await self._runtime.image_exists(self._qualify(image), timeout=10)

    async def check_proxy_image_present(self, image: str = "embry0-proxy:latest") -> bool:
        """Check whether the proxy image is loaded in DinD.

        Production pattern is to build on the host and `docker save | docker load`
        into DinD on deployment. This check runs at orchestrator startup to fail
        fast with a clear message if that step was skipped.

        Right after init-push-images pins a new digest the daemon has not
        pulled it yet, so a miss in its local store is retried as a registry
        pull before it counts as absent (#655, as #622 does for the sandbox).
        """
        qualified = self._qualify(image)
        if await self._runtime.image_exists(qualified):
            return True
        pull_error = await self._pull_from_registry(qualified)
        if pull_error is None:
            logger.info("proxy_image_pulled_from_registry", image=qualified)
            return True
        return False

    async def ensure_image(self, image: str = "embry0-sandbox:latest", force: bool = False) -> bool:
        """Ensure the sandbox image is built and up-to-date. Returns True if build occurred."""
        async with self._build_lock:
            # Registry-sidecar deployment: no Dockerfile shipped → never
            # self-build. Defer to the image init-push-images pushed to the
            # registry; only error if it's genuinely absent everywhere.
            if not self._build_context_available():
                qualified = self._qualify(image)
                if await self._image_exists(image):
                    logger.info("sandbox_image_provided_by_registry", image=qualified)
                    return False
                # #622: right after init-push-images pushes a new digest the
                # daemon has not pulled it yet (it pulls on the first sandbox
                # run), so its local store alone is no evidence of absence.
                pull_error = await self._pull_from_registry(qualified)
                if pull_error is None:
                    logger.info("sandbox_image_pulled_from_registry", image=qualified)
                    return False
                logger.error(
                    "sandbox_image_unavailable_no_build_context",
                    image=qualified,
                    pull_error=pull_error,
                    hint="run the init-push-images bootstrap to push embry0-sandbox to the in-stack registry",
                )
                return False

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

    async def _pull_from_registry(self, qualified: str) -> str | None:
        """Pull ``qualified`` into the daemon. Returns None on success, else why it failed.

        An unqualified name has no registry to pull from (it would resolve to
        Docker Hub), so it is not tried.
        """
        if not is_registry_qualified(qualified):
            return "image name is not registry-qualified"
        try:
            await self._runtime.pull_image(qualified, timeout=_REGISTRY_PULL_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — any pull failure means "absent from the registry too"
            return str(exc)[:500]
        return None

    async def _build_image(self, image: str, build_hash: str) -> bool:
        """Build the sandbox Docker image."""
        logger.info("sandbox_image_building", image=image, hash=build_hash)

        try:
            dockerfile_path = self._app_root / "infra" / "Dockerfile.sandbox"
            if not dockerfile_path.exists():
                dockerfile_path = Path("/app/infra/Dockerfile.sandbox")

            build_context = str(self._app_root)

            await self._runtime.build_image(
                self._qualify(image),
                dockerfile=str(dockerfile_path),
                context=build_context,
                labels={"embry0.build-hash": build_hash},
                timeout=300,
            )
            logger.info("sandbox_image_built", image=image, hash=build_hash)
            return True

        except RuntimeError as exc:
            logger.error("sandbox_image_build_failed", image=image, error=str(exc))
            return False

    async def build_profile_image(
        self,
        profile_name: str,
        base_image: str = "embry0-sandbox:latest",
        additional_packages: list[str] | None = None,
        setup_commands: list[str] | None = None,
    ) -> bool:
        """Build a profile-specific sandbox image."""
        async with self._build_lock:
            image_tag = self._qualify(f"embry0-sandbox:{profile_name}")

            dockerfile_content = f"FROM {self._qualify(base_image)}\nUSER root\n"
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

                await self._runtime.build_image(image_tag, dockerfile=temp_path, context=".", timeout=300)

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
        qualified = self._qualify(image)
        try:
            await self._runtime.remove_image(qualified, timeout=30)
            logger.info("sandbox_image_removed", image=qualified)
        except RuntimeError:
            logger.warning("sandbox_image_remove_failed", image=qualified)


class ContainerReaper:
    """Background task that destroys stale sandbox containers and expires paused jobs."""

    def __init__(
        self,
        runtime: AgentRuntime,
        max_age_hours: int = 24,
        check_interval_seconds: int = 3600,
        db: Any = None,
        paused_ttl_hours: int = 48,
        jobs_repo: Any = None,
        issues_repo: Any = None,
        database_url: str | None = None,
        on_job_finished: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._runtime = runtime
        # Stop and rm of a sandbox still go through the CLI client (#268).
        self._docker = runtime.client
        self._max_age_hours = max_age_hours
        self._interval = check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._db = db
        self._paused_ttl_hours = paused_ttl_hours
        self._jobs_repo = jobs_repo
        self._issues_repo = issues_repo
        self._database_url = database_url
        # spec 3.5: expiry is terminal, so each expired job is announced. Set
        # by the lifespan once the executor exists (the reaper is built first).
        self.on_job_finished = on_job_finished

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
                await self._sweep_qa_orphans()
                await self._expire_paused_jobs()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("reaper_error")

    def _older_than_max_age(self, created: str) -> bool:
        """Return True if the given ``docker inspect --format {{.Created}}`` timestamp is older than max_age.

        Docker emits RFC3339 with nanosecond precision and a trailing "Z" suffix
        (e.g. ``2026-04-28T07:05:11.123456789Z``). Python's ``fromisoformat``
        handles up to microseconds and (3.11+) accepts trailing ``Z``, so we
        clamp the fractional component to 6 digits before parsing.
        """
        s = created.strip()
        if not s:
            return False
        # Clamp fractional seconds to 6 digits if present.
        if "." in s:
            head, _, tail = s.partition(".")
            # tail looks like "123456789Z" or "123456789+00:00"
            digits = ""
            i = 0
            while i < len(tail) and tail[i].isdigit():
                digits += tail[i]
                i += 1
            rest = tail[i:]
            s = f"{head}.{digits[:6]}{rest}"
        # Python <3.11 doesn't accept "Z" — normalise to +00:00.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(s)
        except ValueError:
            logger.warning("qa_orphan_unparseable_timestamp", created=created)
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - ts).total_seconds() / 3600
        return age_hours >= self._max_age_hours

    async def _sweep_qa_orphans(self) -> None:
        """Remove QA containers/networks older than max_age_hours.

        Identifies QA artifacts via:
          - com.docker.compose.project=qa_*  (compose-spawned)
          - embry0.qa_job_id=*              (agent-spawned)
          - networks named qa-net-*

        Best-effort: in-use resources are skipped silently and retried next sweep.
        """
        # Containers carrying either label family.
        for label_prefix in ("com.docker.compose.project=qa_", "embry0.qa_job_id="):
            try:
                containers = await self._runtime.list_containers(label=label_prefix, timeout=15)
            except RuntimeError as exc:
                logger.warning("qa_orphan_list_failed", label_prefix=label_prefix, error=str(exc))
                continue
            for cid in (c.id for c in containers):
                try:
                    created = await self._runtime.container_created(cid, timeout=10)
                except RuntimeError:
                    continue  # container vanished between list and inspect
                if self._older_than_max_age(created):
                    try:
                        await self._runtime.remove_containers([cid], timeout=15)
                        logger.info("qa_orphan_container_removed", container=cid)
                    except RuntimeError as exc:
                        logger.warning("qa_orphan_rm_failed", container=cid, error=str(exc))

        # qa-net-* networks (no age check — empty networks are safe to remove).
        try:
            nets = await self._runtime.list_networks(name="^qa-net-", timeout=10)
        except RuntimeError as exc:
            logger.warning("qa_orphan_network_list_failed", error=str(exc))
            return

        for net in nets:
            try:
                await self._runtime.remove_network(net, timeout=10)
                logger.info("qa_orphan_network_removed", network=net)
            except RuntimeError:
                # In-use; will retry next sweep.
                pass

    async def _reap(self) -> None:
        """Find and destroy stale sandbox containers, skipping live jobs."""
        try:
            containers = await list_sandbox_containers(self._runtime, timeout=15)
        except RuntimeError:
            logger.warning("reaper_list_failed")
            return

        active: set[str] = set()
        if self._jobs_repo is not None:
            try:
                active = await self._jobs_repo.fetch_active_sandbox_containers()
            except Exception:
                logger.warning("reaper_active_fetch_failed", exc_info=True)
                return  # Fail-closed: if we can't tell which jobs are active, don't reap.

        now = datetime.now(UTC)
        for container in containers:
            container_id, name, created_at = container.id, container.name, container.created_at

            if name in active:
                logger.info("reaper_skip_active_job", name=name)
                continue

            created = parse_ps_created(created_at)
            if created is None:
                logger.warning("reaper_unparseable_timestamp", name=name, created_at=created_at)
                continue

            age_hours = (now - created).total_seconds() / 3600
            if age_hours < self._max_age_hours:
                continue

            logger.info("reaping_stale_container", name=name, age_hours=round(age_hours, 1))
            try:
                await self._docker.run_cmd(self._docker.build_stop_cmd(container_id, timeout=5))
            except RuntimeError:
                pass
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(container_id))
            except RuntimeError:
                pass

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
                # RAV-1063: claim FIRST (CAS paused -> expired), destroy after.
                # Previously the sandbox was destroyed before the status write,
                # so losing the race to a user resume still killed the resumed
                # job's sandbox, purged its checkpoint, and reopened its issue.
                try:
                    if self._jobs_repo is not None:
                        # Repository update() validates the transition and CAS-guards it.
                        await self._jobs_repo.update(job_id, status="expired")
                    else:
                        # Fallback for environments where jobs_repo was not
                        # injected — same from-state guard inline.
                        claimed = await self._db.fetchrow(
                            # RAV-1271 / #345: a terminal status carries finished_at.
                            "UPDATE jobs SET status = 'expired', finished_at = COALESCE(finished_at, NOW()) "
                            "WHERE job_id = $1 AND status = 'paused' RETURNING job_id",
                            job_id,
                        )
                        if claimed is None:
                            raise StatusTransitionConflict(f"job {job_id} no longer paused")
                except (StatusTransitionConflict, ValueError):
                    # ValueError: repo pre-read saw a non-paused status (e.g.
                    # running after a resume) — same race, different surface.
                    logger.warning("expire_paused_job_status_race", job_id=job_id)
                    continue
                logger.info("expiring_paused_job", job_id=job_id)
                if self.on_job_finished is not None:
                    try:
                        await self.on_job_finished(job_id)
                    except Exception:
                        logger.warning("job_finished_announce_failed", job_id=job_id, exc_info=True)
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
                # Purge checkpoint state immediately (spec § 3.7). Failure is
                # non-fatal — the daily orphan sweep is a fallback.
                if self._database_url:
                    try:
                        from embry0.orchestration.checkpoint import purge_thread

                        await purge_thread(self._database_url, job_id)
                    except Exception:
                        logger.warning("expire_paused_job_purge_failed", job_id=job_id, exc_info=True)
                issue_row = await self._db.fetchrow("SELECT issue_id FROM jobs WHERE job_id = $1", job_id)
                if issue_row and issue_row.get("issue_id"):
                    issue_id = issue_row["issue_id"]
                    if self._issues_repo is not None:
                        # paused -> open ONLY (an issue re-dispatched onto a new
                        # job must not be knocked back to open). The helper
                        # accepts any fetchrow-capable executor; the pool
                        # qualifies — no transaction needed for a single CAS.
                        reopened = await self._issues_repo.cas_transition_on_conn(
                            self._db, issue_id, "open", {"paused"}
                        )
                        if not reopened:
                            logger.warning("expire_paused_issue_reopen_race", issue_id=issue_id, job_id=job_id)
                    else:
                        await self._db.execute(
                            "UPDATE issues SET status = 'open' WHERE id = $1 AND status = 'paused'",
                            issue_id,
                        )
                logger.info("paused_job_expired", job_id=job_id)
        except Exception:
            logger.exception("expire_paused_jobs_error")
