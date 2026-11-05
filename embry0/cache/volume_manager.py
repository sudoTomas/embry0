"""Docker volume manager for the Phase-2 shared-volume cache layer.

Volume names:
  per-job  → embry0-qa-vol-per-job-<sha256(instance_id + scope_key)>
  per-repo → embry0-qa-vol-per-repo-<sha256(instance_id + scope_key)>

The name is a hash of the deployment's instance id and the exact scope key,
with a per-scope prefix, so two different scope keys never map to one volume
and a per-job key never maps onto a per-repo key.

Every volume carries ownership labels (``volume_ownership.volume_labels``:
``managed``, ``instance``, ``volume_scope``, ``scope_key`` and, per-job,
``job_id``). Before this module reuses, removes or hands out a volume for
mounting, it reads the volume's labels and requires every one of them to be
exactly what it would have stamped for that scope and key; a volume that does
not prove it is ours is never reused, removed or mounted. Volumes are created
explicitly, labelled, before any sandbox mounts them, so the daemon never
creates an unlabelled one at mount time.

A per-repo volume needs the deployment's instance id: without one, every
per-repo request gets a per-job volume instead (logged once).

per-org was rejected as a scope in RAV-1061: a shared volume holds ONE repo's
tree and ONE lockfile sha, so cross-repo sharing is not expressible here —
qa.yaml now refuses the value at parse time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

import structlog

from embry0.cache.volume_ownership import (
    LABEL_INSTANCE,
    LABEL_JOB_ID,
    LABEL_MANAGED,
    LABEL_SCOPE,
    LABEL_SCOPE_KEY,
    volume_labels,
)
from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository

if TYPE_CHECKING:
    from embry0.execution.runtime import AgentRuntime

logger = structlog.get_logger(__name__)

T = TypeVar("T")

#: Shared by every cache volume name; the scope follows it as its own segment.
VOLUME_NAME_PREFIX = "embry0-qa-vol-"

#: The ownership labels, every one of which must match before a volume is used or removed.
OWNERSHIP_LABELS = (LABEL_MANAGED, LABEL_INSTANCE, LABEL_SCOPE, LABEL_SCOPE_KEY, LABEL_JOB_ID)


class VolumeScope(StrEnum):
    PER_JOB = "per-job"
    PER_REPO = "per-repo"


class VolumeOwnershipError(RuntimeError):
    """A volume the run needs cannot be used: its labels are not ours, or it could not be replaced.

    Raised only when there is no safe fallback (a per-job request, or a
    per-repo request with no job id to name a per-job volume). Volume warming
    is non-fatal, so the run's sub-tasks then clone without the shared volume.
    """


@dataclass(frozen=True, slots=True)
class SharedVolume:
    """A volume this deployment created and labelled for ``(scope, scope_key)``."""

    name: str
    scope: VolumeScope
    scope_key: str


def volume_name_for(scope: VolumeScope | str, scope_key: str, *, instance_id: str | None = None) -> str:
    """The Docker volume name for ``(scope, scope_key)`` on this deployment.

    The name carries no part of ``scope_key`` verbatim: it is
    ``<prefix><scope>-<sha256(instance_id + scope_key)>``. Keying the digest on
    the instance id as well keeps two deployments that share a daemon from
    naming one volume for the same key.
    """
    scope = VolumeScope(scope)
    digest = hashlib.sha256(f"{instance_id or ''}\x00{scope_key}".encode()).hexdigest()
    return f"{VOLUME_NAME_PREFIX}{scope.value}-{digest}"


def label_mismatches(have: Mapping[str, str], want: Mapping[str, str]) -> list[str]:
    """The ownership labels on which a volume differs from what we would stamp; empty means ours.

    Every ownership label we would set must be present and equal, and one we
    would not set (a ``job_id`` on a per-repo volume, an ``instance`` when we
    have none) must be absent.
    """
    return [key for key in OWNERSHIP_LABELS if have.get(key) != want.get(key)]


class SharedVolumeManager:
    """Wraps the Docker volume API + state-tracking repo."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        state_repo: QAVolumeStateRepository,
        instance_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.state_repo = state_repo
        # Stamped on every volume created (#274). None leaves volumes without
        # an instance label, which the reaper will never touch, and turns
        # per-repo volumes off (a shared volume must be provably this
        # deployment's).
        self.instance_id = instance_id
        self._warned_no_instance = False

    def _want(self, scope: VolumeScope, scope_key: str) -> dict[str, str]:
        return volume_labels(str(scope), scope_key, instance_id=self.instance_id)

    def _name(self, scope: VolumeScope, scope_key: str) -> str:
        return volume_name_for(scope, scope_key, instance_id=self.instance_id)

    async def ensure(self, *, scope: VolumeScope, scope_key: str, job_id: str | None = None) -> SharedVolume:
        """The labelled volume for ``(scope, scope_key)``, created when missing; its state recorded.

        An existing volume is reused only when every ownership label matches.
        When one does not, a per-repo request uses a fresh per-job volume for
        ``job_id`` instead; a per-job request raises
        :class:`VolumeOwnershipError`.
        """
        scope = VolumeScope(scope)
        if scope is VolumeScope.PER_REPO and not self.instance_id:
            return await self._per_job_instead(job_id, reason="no_instance_id")
        name = self._name(scope, scope_key)
        want = self._want(scope, scope_key)
        if await self._exists(name):
            mismatched = label_mismatches(await self._labels_of(name), want)
            if not mismatched:
                # Reuse: keep the recorded warmth (COALESCE on the upsert).
                await self._record(scope, scope_key, name, reset_warmth=False)
                return SharedVolume(name, scope, scope_key)
            logger.warning(
                "qa_volume_label_mismatch",
                name=name,
                scope=str(scope),
                mismatched=mismatched,
                action="not reused",
            )
            return await self._fallback(scope, job_id, name, reason="labels")

        # #274: ownership labels are how the reaper tells an embry0 volume
        # (and its owning job) from anything else living in DinD.
        await self.runtime.create_volume(name, labels=want, timeout=10)
        # A freshly created volume is empty, so it carries no warmth — clear any
        # row a removed-then-recreated volume left behind (otherwise the warmer
        # skips and a sub-task mounts an empty tree).
        await self._record(scope, scope_key, name, reset_warmth=True)
        logger.info("qa_shared_volume_created", scope=str(scope), name=name)
        return SharedVolume(name, scope, scope_key)

    async def recreate(self, *, scope: VolumeScope, scope_key: str, job_id: str | None = None) -> SharedVolume:
        """Remove the volume (when it is ours) and create it empty.

        Used before a per-repo warm whose lockfile no longer matches the
        volume's, so the install starts from an empty tree. The labels are
        checked before anything is removed: a volume that is not ours is left
        alone and the run gets a per-job volume. Removal never forces, so a
        volume a concurrent run still mounts is refused by the daemon, and
        this run gets a per-job volume as well.
        """
        scope = VolumeScope(scope)
        if scope is VolumeScope.PER_REPO and not self.instance_id:
            return await self._per_job_instead(job_id, reason="no_instance_id")
        name = self._name(scope, scope_key)
        if await self._exists(name):
            mismatched = label_mismatches(await self._labels_of(name), self._want(scope, scope_key))
            if mismatched:
                logger.warning(
                    "qa_volume_remove_refused",
                    name=name,
                    scope=str(scope),
                    mismatched=mismatched,
                    reason="labels do not prove the volume is ours",
                )
                return await self._fallback(scope, job_id, name, reason="labels")
            try:
                await self.runtime.remove_volumes([name], timeout=15)  # no force
            except Exception as exc:  # noqa: BLE001 — the daemon refuses a mounted volume
                logger.warning(
                    "qa_volume_recreate_in_use",
                    name=name,
                    scope=str(scope),
                    error=str(exc)[:300],
                    reason="in use by another run",
                )
                return await self._fallback(scope, job_id, name, reason="in_use")
            await self._forget(name)
        return await self.ensure(scope=scope, scope_key=scope_key, job_id=job_id)

    async def verify(self, volume: SharedVolume) -> bool:
        """Whether ``volume`` may be mounted: this deployment's name for its key, present, and labelled as ours.

        Called right before every mount. A missing volume is refused rather
        than mounted, since the daemon would create it unlabelled. Never
        raises: a volume whose labels cannot be read is refused.
        """
        reason = ""
        mismatched: list[str] = []
        if volume.scope is VolumeScope.PER_REPO and not self.instance_id:
            reason = "no_instance_id"
        elif volume.name != self._name(volume.scope, volume.scope_key):
            reason = "name"
        else:
            try:
                exists = await self._exists(volume.name)
            except Exception as exc:  # noqa: BLE001 — unknown means not mounted
                logger.warning("qa_volume_list_failed", name=volume.name, error=str(exc)[:300])
                exists = False
            if not exists:
                reason = "missing"
            else:
                mismatched = label_mismatches(
                    await self._labels_of(volume.name), self._want(volume.scope, volume.scope_key)
                )
                if mismatched:
                    reason = "labels"
        if reason:
            logger.warning(
                "qa_volume_mount_refused",
                name=volume.name,
                scope=str(volume.scope),
                reason=reason,
                mismatched=mismatched or None,
            )
            return False
        return True

    async def mountable(self, name: str | None, *, repo: str, job_id: str) -> SharedVolume | None:
        """The volume named ``name`` as this run may mount it, or None.

        ``name`` comes from the job's state, so it is only accepted when it is
        this deployment's name for one of the two keys the run can use (the
        repository's per-repo key or the job's per-job key) and
        :meth:`verify` passes. Any other name, including one written by a
        release that named volumes differently, is re-resolved to the job's
        own per-job name and checked like any mount, so it is never mounted.
        """
        if not name:
            return None
        candidates = [SharedVolume(self._name(VolumeScope.PER_JOB, job_id), VolumeScope.PER_JOB, job_id)]
        if repo:
            candidates.append(SharedVolume(self._name(VolumeScope.PER_REPO, repo), VolumeScope.PER_REPO, repo))
        volume = next((c for c in candidates if c.name == name), None)
        if volume is None:
            volume = candidates[0]
            logger.info("qa_volume_name_reresolved", name=name, resolved=volume.name, job_id=job_id)
        return volume if await self.verify(volume) else None

    async def _per_job_instead(self, job_id: str | None, *, reason: str) -> SharedVolume:
        if not self._warned_no_instance:
            self._warned_no_instance = True
            logger.warning(
                "qa_per_repo_volume_unavailable",
                reason=reason,
                note="this deployment's instance id is unresolved: per-repo volumes are off, runs use per-job volumes",
            )
        if not job_id:
            raise VolumeOwnershipError("a per-repo volume needs the instance id, and no job id names a per-job one")
        return await self.ensure(scope=VolumeScope.PER_JOB, scope_key=job_id)

    async def _fallback(self, scope: VolumeScope, job_id: str | None, name: str, *, reason: str) -> SharedVolume:
        """A per-job volume in place of a per-repo one that cannot be used; raises for a per-job request."""
        if scope is VolumeScope.PER_REPO and job_id:
            return await self.ensure(scope=VolumeScope.PER_JOB, scope_key=job_id)
        raise VolumeOwnershipError(f"volume {name} cannot be used ({reason}) and there is no per-job fallback")

    async def _exists(self, name: str) -> bool:
        # Docker's name filter is a substring match, so compare whole names (#328).
        return name in await self.runtime.list_volumes(name=name, timeout=10)

    async def _labels_of(self, name: str) -> dict[str, str]:
        """The volume's labels; empty (so never ours) when they cannot be read."""
        try:
            summaries = await self.runtime.inspect_volumes([name], timeout=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa_volume_inspect_failed", name=name, error=str(exc)[:300])
            return {}
        for summary in summaries:
            if summary.name == name:
                return dict(summary.labels)
        return {}

    async def _record(self, scope: VolumeScope, scope_key: str, name: str, *, reset_warmth: bool) -> None:
        await self.state_repo.upsert(
            scope=str(scope),
            scope_key=scope_key,
            volume_name=name,
            last_warmed_sha=None,
            reset_warmth=reset_warmth,
        )

    async def _forget(self, volume_name: str) -> None:
        """Drop the state row of a removed volume (#328). Best-effort."""
        try:
            await self.state_repo.delete_by_volume_name(volume_name)
        except Exception as exc:
            logger.warning("qa_volume_state_delete_failed", name=volume_name, error=str(exc))

    async def destroy_for_job(self, job_id: str) -> list[str]:
        """Remove every volume owned by ``job_id``; return the names removed.

        Called once the job is terminal (#274). Finds candidates by the job's
        per-job name and by the ``embry0.job_id`` label, then reads each one's
        labels: only a volume whose ownership labels are exactly a per-job
        volume of ``job_id`` on this deployment is touched; any other is
        refused and logged. For an owned volume it force-removes any container
        still mounting it (a cancelled fan-out leaves sub-task sandboxes
        behind — they belong to the same dead job), then removes the volume.
        Best-effort: failures are logged, never raised, and the periodic
        ``VolumeReaper`` sweep retries whatever is left.
        """

        async def _run(step: str, call: Awaitable[T]) -> T | None:
            try:
                return await call
            except Exception as exc:
                logger.warning("qa_job_volume_cleanup_step_failed", job_id=job_id, step=step, error=str(exc))
                return None

        per_job_name = self._name(VolumeScope.PER_JOB, job_id)
        # Docker's name filter is a substring match — keep the exact name only.
        by_name = await _run("list by name", self.runtime.list_volumes(name=per_job_name, timeout=15))
        label_filters = [f"{LABEL_JOB_ID}={job_id}"]
        if self.instance_id:
            # Filters AND together: another instance's job ids are not ours.
            label_filters.append(f"{LABEL_INSTANCE}={self.instance_id}")
        by_label = await _run("list by label", self.runtime.list_volumes(labels=label_filters, timeout=15))
        names = [n for n in (by_name or []) if n == per_job_name]
        names += [n for n in (by_label or []) if n not in names]

        want = self._want(VolumeScope.PER_JOB, job_id)
        removed: list[str] = []
        for name in names:
            mismatched = label_mismatches(await self._labels_of(name), want)
            if mismatched:
                logger.warning(
                    "qa_job_volume_remove_refused",
                    job_id=job_id,
                    name=name,
                    mismatched=mismatched,
                    reason="labels do not prove the volume is this job's",
                )
                continue
            holders = await _run(f"list holders of {name}", self.runtime.list_containers(volume=name, timeout=15))
            if holders:
                await _run(
                    f"remove holders of {name}", self.runtime.remove_containers([c.id for c in holders], timeout=15)
                )
            if await _run(f"remove {name}", self._removed(name)) is not None:
                removed.append(name)
                await self._forget(name)
        if removed:
            logger.info("qa_job_volumes_removed", job_id=job_id, volumes=removed)
        return removed

    async def _removed(self, volume: str) -> bool:
        # No force: the daemon refuses a volume a container still mounts.
        await self.runtime.remove_volumes([volume], timeout=15)
        return True
