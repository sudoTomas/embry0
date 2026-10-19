"""QA image retention (ADR 0017): pre-built QA images and per-repo warm volumes, removed once unused.

A pre-built QA image (``embry0/cache/image_builder.py``) is a ``docker commit``
of a builder sandbox after it cloned the repository and installed its
dependencies, so it holds a full checkout, ``.git`` included. A per-repo warm
volume (``cache.shared_volume.scope: per-repo``) holds one too, shared by
that repo's QA jobs. Nothing else removes either: each bake leaves the
previous image behind, and a bake that failed after its commit leaves an image
no ``qa_image_tags`` row names at all.

With ``QA_IMAGE_RETENTION_DAYS`` set, a daily sweep removes from the sandbox
daemon:

* each QA image (a repository named ``embry0-qa-*``) last used more than that
  many days ago, unless an active ``qa_image_tags`` row names it (the image a
  job selects now) or a container, running or stopped, was started from it;
* each per-repo volume this deployment created (its ``embry0.instance``
  label) last used more than that many days ago, unless a job of its repo is
  still live or a container mounts it.

Docker does not record when an image was last used. An image could be
selected until a newer image of the same repository was recorded, so that is
when it was last used; an image nothing newer has replaced counts from its
own build (a failed bake was never used at all). A volume was last used when
a QA job last asked for it (``qa_volume_state.last_warmed_at``, refreshed by
every job that mounts it), or when it was created if it has no row.

Right before removing a volume the sweep reads its repo's live jobs and its
``qa_volume_state`` row again, so a QA job that started or warmed it after the
first reads keeps it. Removal never uses ``--force``, so the daemon itself
also refuses an image a container uses and a volume a container mounts. Copies in the in-stack
registry are not touched; SECURITY.md says so. ``run_once(dry_run=True)``
decides the same way and removes nothing: the admin preview.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from embry0.cache.image_builder import QA_IMAGE_PREFIX
from embry0.cache.volume_ownership import LABEL_INSTANCE, LABEL_MANAGED, LABEL_SCOPE, LABEL_SCOPE_KEY

if TYPE_CHECKING:
    from embry0.execution.runtime import AgentRuntime
    from embry0.execution.runtime.protocol import DiskUsage, ImageSummary, VolumeSummary
    from embry0.storage.repositories.jobs import JobsRepository
    from embry0.storage.repositories.qa_image_tags import QAImageTagRow, QAImageTagsRepository
    from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository

logger = structlog.get_logger(__name__)

_PER_REPO = "per-repo"
_INSPECT_CHUNK = 50
_DAY_SECONDS = 86400


class Reason(StrEnum):
    PINNED = "pinned"  # an active qa_image_tags row names the image
    RECENT = "recent"  # used within the retention, or when is unknown
    IN_USE = "in_use"  # a container was started from it, or mounts it
    LIVE_JOB = "live_job"  # a job of the volume's repo has not ended
    NOT_OURS = "not_ours"  # a volume another deployment (or nobody) labelled
    UNCHECKED = "unchecked"  # a lookup failed: kept rather than guessed
    UNUSED = "unused"  # removed


def parse_time(raw: Any) -> datetime | None:
    """A timestamp the daemon printed (RFC 3339, nanoseconds and all), or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _name_tag(ref: str) -> str:
    """``registry:5000/embry0-qa-x:tag`` -> ``embry0-qa-x:tag``: the registry host is not part of the name."""
    return ref.rsplit("/", 1)[-1]


def _repository(ref: str) -> str:
    """The repository a tag or digest reference is in: ``registry:5000/embry0-qa-x``."""
    return ref.partition("@")[0] if "@" in ref else ref.rpartition(":")[0]


def removal_refs(refs: Sequence[str]) -> list[str]:
    """The references to give ``rmi`` so that all of ``refs`` go, none of them named once gone.

    Untagging a repository's last tag also drops that repository's digest
    references. Every pushed bake has a local tag, a registry tag and a
    registry digest, and ``rmi`` of all three deleted the image at the second
    tag, then failed on the digest with "No such image" (Docker 27.5.1). So a
    digest reference is named only when no tag of its repository is: an image
    left with a digest alone.
    """
    tagged = {_repository(ref) for ref in refs if "@" not in ref}
    return [ref for ref in refs if "@" not in ref or _repository(ref) not in tagged]


@dataclass(frozen=True, slots=True)
class QAImage:
    """A daemon image that carries at least one ``embry0-qa-*`` reference."""

    id: str
    name: str
    tags: frozenset[str]  # name:tag, registry host stripped
    digests: frozenset[str]  # sha256:...
    refs: tuple[str, ...]  # its QA references as the daemon spells them: what removal takes away
    created_at: datetime | None


def qa_image(image: ImageSummary) -> QAImage | None:
    """The QA view of a daemon image, or None when no reference of it is a QA image's."""
    tags: set[str] = set()
    digests: set[str] = set()
    refs: list[str] = []
    names: set[str] = set()
    for ref in image.repo_tags:
        name_tag = _name_tag(ref)
        name = name_tag.rpartition(":")[0]
        if name.startswith(QA_IMAGE_PREFIX):
            tags.add(name_tag)
            names.add(name)
            refs.append(ref)
    for ref in image.repo_digests:
        repo, _, digest = ref.partition("@")
        name = _name_tag(repo)
        if name.startswith(QA_IMAGE_PREFIX) and digest:
            digests.add(digest)
            names.add(name)
            refs.append(ref)
    if not names:
        return None
    return QAImage(
        id=image.id,
        name=sorted(names)[0],
        tags=frozenset(tags),
        digests=frozenset(digests),
        refs=tuple(refs),
        created_at=parse_time(image.created_at),
    )


@dataclass(frozen=True, slots=True)
class _Row:
    """A ``qa_image_tags`` row as the sweep compares it with an image."""

    name: str
    name_tag: str
    digest: str
    built_at: datetime
    is_active: bool


def _row(row: QAImageTagRow) -> _Row:
    ref, _, digest = row.image_tag.partition("@")
    name_tag = _name_tag(ref)
    return _Row(
        name=name_tag.rpartition(":")[0],
        name_tag=name_tag,
        digest=digest,
        built_at=row.built_at,
        is_active=row.is_active,
    )


def _names(row: _Row, image: QAImage) -> bool:
    return row.name_tag in image.tags or bool(row.digest and row.digest in image.digests)


@dataclass(frozen=True, slots=True)
class ImageDecision:
    remove: bool
    reason: Reason
    last_used: datetime | None = None


def decide_image(image: QAImage, rows: Sequence[_Row], *, cutoff: datetime) -> ImageDecision:
    """Whether ``image`` is past the retention, before the container check. Pure."""
    own = [r for r in rows if _names(r, image)]
    if any(r.is_active for r in own):
        return ImageDecision(False, Reason.PINNED)
    since = [t for t in [image.created_at, *(r.built_at for r in own)] if t is not None]
    if not since:
        return ImageDecision(False, Reason.RECENT)
    floor = max(since)
    newer = [r.built_at for r in rows if r.name == image.name and r not in own and r.built_at > floor]
    last_used = min(newer) if newer else floor
    if last_used >= cutoff:
        return ImageDecision(False, Reason.RECENT, last_used)
    return ImageDecision(True, Reason.UNUSED, last_used)


@dataclass(frozen=True, slots=True)
class VolumeDecision:
    remove: bool
    reason: Reason
    last_used: datetime | None = None


def decide_volume(
    volume: VolumeSummary,
    *,
    instance_id: str | None,
    last_warmed_at: datetime | None,
    live_repos: set[str],
    cutoff: datetime,
) -> VolumeDecision:
    """Whether a per-repo volume is past the retention, before the mount check. Pure."""
    labels = volume.labels
    if not (
        instance_id
        and labels.get(LABEL_MANAGED) == "true"
        and labels.get(LABEL_INSTANCE) == instance_id
        and labels.get(LABEL_SCOPE) == _PER_REPO
    ):
        return VolumeDecision(False, Reason.NOT_OURS)
    if labels.get(LABEL_SCOPE_KEY, "").lower() in live_repos:
        return VolumeDecision(False, Reason.LIVE_JOB)
    since = [t for t in (parse_time(volume.created_at), last_warmed_at) if t is not None]
    if not since:
        return VolumeDecision(False, Reason.RECENT)
    last_used = max(since)
    if last_used >= cutoff:
        return VolumeDecision(False, Reason.RECENT, last_used)
    return VolumeDecision(True, Reason.UNUSED, last_used)


@dataclass(slots=True)
class QAImageRetentionResult:
    retention_days: int | None
    images: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    kept: Counter[str] = field(default_factory=Counter)
    failed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def _total(self, items: list[dict[str, Any]]) -> int | None:
        sizes = [item["bytes"] for item in items]
        return None if any(s is None for s in sizes) else sum(sizes)

    def counts(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "images": len(self.images),
            "image_bytes": self._total(self.images),
            "volumes": len(self.volumes),
            "volume_bytes": self._total(self.volumes),
            "kept": {str(reason): n for reason, n in sorted(self.kept.items())},
            "failed": len(self.failed),
            "errors": list(self.errors),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.counts(), "removed_images": self.images, "removed_volumes": self.volumes}


class QAImageRetentionSweep:
    """Removes QA images and per-repo volumes unused for ``retention_days``; off when that is None."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        image_repo: QAImageTagsRepository,
        volume_state_repo: QAVolumeStateRepository,
        jobs_repo: JobsRepository,
        instance_id: str | None,
        retention_days: int | None,
        interval_seconds: int = _DAY_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._image_repo = image_repo
        self._state_repo = volume_state_repo
        self._jobs_repo = jobs_repo
        # The deployment's volume label (storage.instance_id); None leaves every volume alone.
        self._instance_id = instance_id
        self._days = retention_days
        self._interval = interval_seconds

    @property
    def enabled(self) -> bool:
        return self._days is not None

    @property
    def retention_days(self) -> int | None:
        return self._days

    async def run_once(
        self,
        *,
        dry_run: bool = False,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> QAImageRetentionResult:
        """One pass at ``retention_days`` (default: the configured value). Never raises."""
        days = retention_days if retention_days is not None else self._days
        result = QAImageRetentionResult(retention_days=days)
        if days is None:
            return result
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        usage: DiskUsage | None = None
        try:
            usage = await self._runtime.disk_usage()
        except Exception as exc:  # noqa: BLE001 — sizes are reported, never decided on
            logger.warning("qa_image_retention_sizes_unavailable", error=str(exc)[:200])
        await self._images(result, cutoff=cutoff, usage=usage, dry_run=dry_run)
        await self._volumes(result, cutoff=cutoff, usage=usage, dry_run=dry_run)
        logger.info("qa_image_retention_swept", dry_run=dry_run, cutoff=cutoff.isoformat(), **result.counts())
        return result

    async def _images(
        self, result: QAImageRetentionResult, *, cutoff: datetime, usage: DiskUsage | None, dry_run: bool
    ) -> None:
        try:
            images = [qa for image in await self._runtime.list_images() if (qa := qa_image(image)) is not None]
            rows = [_row(r) for r in await self._image_repo.list_all()]
        except Exception as exc:  # noqa: BLE001 — without both lists nothing can be decided
            logger.warning("qa_image_retention_list_failed", error=str(exc)[:200])
            result.errors.append(f"images not read: {str(exc)[:200]}")
            return
        for image in images:
            decision = decide_image(image, rows, cutoff=cutoff)
            if decision.remove:
                try:
                    if await self._runtime.list_containers(ancestor=image.id):
                        decision = ImageDecision(False, Reason.IN_USE, decision.last_used)
                except Exception as exc:  # noqa: BLE001 — unknown holders: keep
                    logger.warning("qa_image_retention_holders_unreadable", image=image.id, error=str(exc)[:200])
                    decision = ImageDecision(False, Reason.UNCHECKED, decision.last_used)
            if not decision.remove:
                result.kept[decision.reason] += 1
                continue
            item = {
                "id": image.id,
                "refs": list(image.refs),
                "last_used": decision.last_used.isoformat() if decision.last_used else None,
                "bytes": usage.image_unique_bytes.get(image.id) if usage is not None else None,
            }
            if not dry_run:
                try:
                    await self._runtime.remove_image_refs(removal_refs(image.refs))
                except Exception as exc:  # noqa: BLE001 — e.g. a container appeared since the check
                    result.failed.append(image.id)
                    logger.warning("qa_image_retention_remove_failed", image=image.id, error=str(exc)[:200])
                    continue
                logger.info("qa_image_retention_removed", image=image.id, refs=list(image.refs))
            result.images.append(item)

    async def _volumes(
        self, result: QAImageRetentionResult, *, cutoff: datetime, usage: DiskUsage | None, dry_run: bool
    ) -> None:
        if not self._instance_id:
            # No label to recognise this deployment's volumes by (the volume reaper's rule too).
            result.errors.append("volumes not read: this deployment's instance id is unresolved")
            return
        try:
            names = await self._runtime.list_volumes(
                labels=[f"{LABEL_INSTANCE}={self._instance_id}", f"{LABEL_SCOPE}={_PER_REPO}"], timeout=15
            )
            volumes: list[VolumeSummary] = []
            for i in range(0, len(names), _INSPECT_CHUNK):
                volumes += await self._runtime.inspect_volumes(names[i : i + _INSPECT_CHUNK], timeout=30)
            live = {repo.lower() for repo in await self._jobs_repo.live_job_repos()}
        except Exception as exc:  # noqa: BLE001 — without the facts nothing is removed
            logger.warning("qa_volume_retention_list_failed", error=str(exc)[:200])
            result.errors.append(f"volumes not read: {str(exc)[:200]}")
            return
        for volume in volumes:
            decision = await self._decide_volume(volume, live=live, cutoff=cutoff)
            if decision.remove and not dry_run:
                # A QA job of this repo can start and warm the volume after
                # `live` was read: decide again on fresh reads right before the
                # removal. Only the instant between that read and the rm is left.
                decision = await self._decide_volume(volume, live=None, cutoff=cutoff)
            if not decision.remove:
                result.kept[decision.reason] += 1
                continue
            item = {
                "name": volume.name,
                "last_used": decision.last_used.isoformat() if decision.last_used else None,
                "bytes": usage.volume_bytes.get(volume.name) if usage is not None else None,
            }
            if not dry_run:
                try:
                    # No force: the daemon refuses a volume a container still mounts.
                    await self._runtime.remove_volumes([volume.name], timeout=30)
                except Exception as exc:  # noqa: BLE001
                    result.failed.append(volume.name)
                    logger.warning("qa_volume_retention_remove_failed", volume=volume.name, error=str(exc)[:200])
                    continue
                logger.info("qa_volume_retention_removed", volume=volume.name)
                try:
                    await self._state_repo.delete_by_volume_name(volume.name)
                except Exception as exc:  # noqa: BLE001 — a stale row only claims a warm tree that is gone
                    logger.warning("qa_volume_state_delete_failed", volume=volume.name, error=str(exc)[:200])
            result.volumes.append(item)

    async def _decide_volume(self, volume: VolumeSummary, *, live: set[str] | None, cutoff: datetime) -> VolumeDecision:
        """decide_volume on the volume's state row, then the mount check; ``live`` None reads the live repos."""
        try:
            if live is None:
                live = {repo.lower() for repo in await self._jobs_repo.live_job_repos()}
            state = await self._state_repo.get(scope=_PER_REPO, scope_key=volume.labels.get(LABEL_SCOPE_KEY, ""))
            decision = decide_volume(
                volume,
                instance_id=self._instance_id,
                last_warmed_at=state.last_warmed_at if state is not None else None,
                live_repos=live,
                cutoff=cutoff,
            )
            if decision.remove and await self._runtime.list_containers(volume=volume.name):
                decision = VolumeDecision(False, Reason.IN_USE, decision.last_used)
        except Exception as exc:  # noqa: BLE001 — unknown state or holders: keep
            logger.warning("qa_volume_retention_unreadable", volume=volume.name, error=str(exc)[:200])
            decision = VolumeDecision(False, Reason.UNCHECKED)
        return decision

    async def start(self) -> None:  # pragma: no cover - the loop is exercised by the app
        """One sweep now, then one per interval, until the task is cancelled."""
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.warning("qa_image_retention_sweep_failed", exc_info=True)
            await asyncio.sleep(self._interval)
