"""Who owns a DinD volume, and may the reaper remove it? (#274)

Pure decision logic — no Docker, no database. ``VolumeReaper`` gathers the
facts (volume labels, job statuses) and asks ``decide_volume`` for a verdict,
so the safety rules are unit-testable in isolation:

  * a volume embry0 did not create is never removed;
  * a volume stamped by a different instance — another stack on the same
    daemon, or this stack while it pointed at a different database — is never
    removed: "no jobs row" only means something in the database that was
    recording jobs when the volume was created;
  * a volume whose owning job is live is never removed;
  * a per-repo volume is shared across jobs and is never removed;
  * only a per-job volume whose owner is terminal — or has no ``jobs`` row at
    all and is older than the grace window — is removed.

Ownership is carried by labels stamped at ``docker volume create`` time
(``volume_labels``). Volumes created before the labels existed carry no
instance either; they are invisible to the reaper unless the operator sets
``adopt_legacy_names``, in which case the exact per-job name
``embry0-qa-vol-job-<12 hex>`` is accepted as proof of ownership.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from embry0.storage.schemas import JobStatus

LABEL_MANAGED = "embry0.managed"
LABEL_INSTANCE = "embry0.instance"
LABEL_SCOPE = "embry0.volume_scope"
LABEL_SCOPE_KEY = "embry0.scope_key"
LABEL_JOB_ID = "embry0.job_id"

_PER_JOB_SCOPE = "per-job"

# Statuses after which a job can never touch its volume again. Deliberately an
# allow-list: a status missing from here (including one added to JobStatus
# later) reads as "live", so the failure mode is a kept volume, never a
# removed one.
_RELEASED_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.EXPIRED,
        JobStatus.SKIPPED,
        JobStatus.PR_MERGED,
        JobStatus.PR_CLOSED,
    }
)

# The per-job name of a volume created before ownership labels existed, when
# volumes were named after the raw job id ("job-" + uuid4().hex[:12]).
_LEGACY_PER_JOB_NAME = re.compile(r"embry0-qa-vol-(job-[0-9a-f]{12})")


class VolumeReason(StrEnum):
    NOT_EMBRY0 = "not_embry0"
    FOREIGN_INSTANCE = "foreign_instance"
    SHARED_SCOPE = "shared_scope"
    NO_OWNER_LABEL = "no_owner_label"
    OWNER_ACTIVE = "owner_active"
    OWNER_TERMINAL = "owner_terminal"
    OWNER_UNKNOWN = "owner_unknown"
    OWNER_UNKNOWN_IN_GRACE = "owner_unknown_in_grace"


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    name: str
    labels: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VolumeDecision:
    remove: bool
    reason: VolumeReason
    job_id: str | None = None


def volume_labels(scope: str, scope_key: str, *, instance_id: str | None) -> dict[str, str]:
    """Labels stamped on a volume at creation so the reaper can find its owner.

    Without an ``instance_id`` the volume gets no instance label, which puts it
    permanently out of the reaper's reach — the safe direction.
    """
    labels = {
        LABEL_MANAGED: "true",
        LABEL_SCOPE: scope,
        LABEL_SCOPE_KEY: scope_key,
    }
    if instance_id:
        labels[LABEL_INSTANCE] = instance_id
    if scope == _PER_JOB_SCOPE:
        labels[LABEL_JOB_ID] = scope_key
    return labels


def _is_managed(vol: VolumeInfo) -> bool:
    return vol.labels.get(LABEL_MANAGED) == "true"


def _is_ours(vol: VolumeInfo, instance_id: str | None) -> bool:
    return bool(instance_id) and vol.labels.get(LABEL_INSTANCE) == instance_id


def owning_job_id(vol: VolumeInfo, *, adopt_legacy_names: bool) -> str | None:
    """The job that owns ``vol``, or None when it has no single owning job.

    Says nothing about whether the volume is this instance's — that is
    ``decide_volume``'s first question.
    """
    if _is_managed(vol):
        if vol.labels.get(LABEL_SCOPE) != _PER_JOB_SCOPE:
            return None
        return vol.labels.get(LABEL_JOB_ID) or None
    if adopt_legacy_names and not vol.labels:
        match = _LEGACY_PER_JOB_NAME.fullmatch(vol.name)
        if match:
            return match.group(1)
    return None


def decide_volume(
    vol: VolumeInfo,
    *,
    instance_id: str | None,
    job_statuses: Mapping[str, str],
    now: datetime,
    unknown_owner_grace: timedelta,
    adopt_legacy_names: bool = False,
) -> VolumeDecision:
    """Verdict for one volume.

    ``instance_id`` identifies this deployment's database; a labelled volume
    must carry the same value before anything else about it is considered
    (None — unresolved — matches nothing). ``job_statuses`` maps job_id ->
    status for every owning job that has a ``jobs`` row; an owner absent from
    it is "unknown".
    """
    if _is_managed(vol) and not _is_ours(vol, instance_id):
        return VolumeDecision(False, VolumeReason.FOREIGN_INSTANCE)
    job_id = owning_job_id(vol, adopt_legacy_names=adopt_legacy_names)
    if job_id is None:
        if not _is_managed(vol):
            return VolumeDecision(False, VolumeReason.NOT_EMBRY0)
        if vol.labels.get(LABEL_SCOPE) != _PER_JOB_SCOPE:
            return VolumeDecision(False, VolumeReason.SHARED_SCOPE)
        return VolumeDecision(False, VolumeReason.NO_OWNER_LABEL)

    status = job_statuses.get(job_id)
    if status is None:
        # No jobs row in the database this volume was created against. The
        # row is written before the workflow starts and rows are never
        # deleted, so the job was rolled back out of existence and can never
        # resume — but hold off inside the grace window regardless.
        if vol.created_at is None or now - vol.created_at < unknown_owner_grace:
            return VolumeDecision(False, VolumeReason.OWNER_UNKNOWN_IN_GRACE, job_id)
        return VolumeDecision(True, VolumeReason.OWNER_UNKNOWN, job_id)
    if status in _RELEASED_STATUSES:
        return VolumeDecision(True, VolumeReason.OWNER_TERMINAL, job_id)
    return VolumeDecision(False, VolumeReason.OWNER_ACTIVE, job_id)
