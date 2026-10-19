"""QA image retention (ADR 0017): unused QA images and per-repo volumes go; pinned or used ones never do."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.cache.qa_image_retention import QAImageRetentionSweep, Reason, decide_volume, qa_image, removal_refs
from embry0.cache.volume_ownership import volume_labels
from embry0.execution.runtime.protocol import ContainerSummary, DiskUsage, ImageSummary, VolumeSummary
from embry0.storage.repositories.qa_image_tags import QAImageTagRow
from embry0.storage.repositories.qa_volume_state import VolumeState

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
INSTANCE = "a1b2c3d4e5f60718"
REG = "registry:5000"
NAME = "embry0-qa-acme_widgets"


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _image(
    image_id: str, tag: str, created: datetime, *, digest: str = "", extra_tags: tuple[str, ...] = ()
) -> ImageSummary:
    tags = (f"{NAME}:{tag}", f"{REG}/{NAME}:{tag}", *extra_tags)
    digests = (f"{REG}/{NAME}@{digest}",) if digest else ()
    return ImageSummary(id=image_id, repo_tags=tags, repo_digests=digests, created_at=created.isoformat(), size=10)


def _row(tag: str, built: datetime, *, active: bool, digest: str = "") -> QAImageTagRow:
    return QAImageTagRow(
        repo="acme/widgets",
        image_tag=f"{NAME}:{tag}" + (f"@{digest}" if digest else ""),
        lockfile_sha=tag,
        built_at=built,
        built_by="cli",
        is_active=active,
        metadata={},
    )


def _volume(name: str, repo: str, created: datetime, *, instance: str | None = INSTANCE) -> VolumeSummary:
    return VolumeSummary(
        name=name, labels=volume_labels("per-repo", repo, instance_id=instance), created_at=created.isoformat()
    )


def _repository(ref: str) -> str:
    """``registry:5000/embry0-qa-x:t1`` and ``registry:5000/embry0-qa-x@sha256:d`` -> ``registry:5000/embry0-qa-x``."""
    if "@" in ref:
        return ref.partition("@")[0]
    repo, _, tag = ref.rpartition(":")
    return repo if "/" not in tag else ref


def _single_reference(refs: list[str]) -> bool:
    """Docker's test for "the last reference": one repository, at most one tag."""
    return len({_repository(r) for r in refs}) == 1 and sum("@" not in r for r in refs) <= 1


class FakeRuntime:
    """The sandbox daemon, as far as the sweep reads and changes it."""

    def __init__(
        self,
        images: list[ImageSummary],
        *,
        image_users: dict[str, int] | None = None,
        volumes: list[VolumeSummary] | None = None,
        volume_users: dict[str, int] | None = None,
    ) -> None:
        self.images = images
        self.image_users = image_users or {}
        self.volumes = volumes or []
        self.volume_users = volume_users or {}
        # The references each image still has; an image with none left is deleted.
        self.refs = {i.id: [*i.repo_tags, *i.repo_digests] for i in images}
        self.removed_refs: list[list[str]] = []
        self.removed_volumes: list[str] = []
        self.volume_filters: list[list[str]] = []

    @property
    def deleted(self) -> set[str]:
        return {i.id for i in self.images} - set(self.refs)

    async def disk_usage(self) -> DiskUsage:
        return DiskUsage(
            image_unique_bytes={i.id: 1000 for i in self.images}, volume_bytes={v.name: 500 for v in self.volumes}
        )

    async def list_images(self) -> list[ImageSummary]:
        return [
            replace(
                i,
                repo_tags=tuple(r for r in self.refs[i.id] if "@" not in r),
                repo_digests=tuple(r for r in self.refs[i.id] if "@" in r),
            )
            for i in self.images
            if i.id in self.refs
        ]

    async def list_containers(self, *, ancestor: str | None = None, volume: str | None = None, **_: Any) -> list:
        n = self.image_users.get(ancestor or "", 0) if ancestor else self.volume_users.get(volume or "", 0)
        return [ContainerSummary(f"c{i}", f"sandbox-{i}", "", "Exited", "") for i in range(n)]

    async def remove_image_refs(self, refs: list[str]) -> None:
        """What ``docker rmi <refs...>`` does (Docker 27): each reference in turn, errors raised at the end.

        Untagging a repository's last tag also drops that repository's digest
        references, and an image left with no reference is deleted, so naming
        one of its references after that fails with "No such image". The last
        reference of an image a container uses is refused.
        """
        self.removed_refs.append(list(refs))
        errors: list[str] = []
        for ref in refs:
            image_id = next((i for i, held in self.refs.items() if ref in held), None)
            if image_id is None:
                errors.append(f"Error response from daemon: No such image: {ref}")
                continue
            held = self.refs[image_id]
            if self.image_users.get(image_id) and _single_reference(held):
                errors.append(f"conflict: unable to remove repository reference {ref!r} (must force)")
                continue
            held.remove(ref)
            repo = _repository(ref)
            if "@" not in ref and not any("@" not in r and _repository(r) == repo for r in held):
                held[:] = [r for r in held if not ("@" in r and _repository(r) == repo)]
            if not held:
                del self.refs[image_id]
        if errors:
            raise RuntimeError("\n".join(errors))

    async def list_volumes(self, *, labels: list[str], timeout: int = 10) -> list[str]:
        self.volume_filters.append(list(labels))
        wanted = dict(label.split("=", 1) for label in labels)
        return [v.name for v in self.volumes if all(v.labels.get(k) == val for k, val in wanted.items())]

    async def inspect_volumes(self, names: list[str], *, timeout: int = 30) -> list[VolumeSummary]:
        return [v for v in self.volumes if v.name in names]

    async def remove_volumes(self, names: list[str], *, force: bool = False, timeout: int = 15) -> None:
        assert not force, "retention must never force-remove a volume"
        self.removed_volumes += names


class FakeImageRepo:
    def __init__(self, rows: list[QAImageTagRow]) -> None:
        self.rows = rows

    async def list_all(self) -> list[QAImageTagRow]:
        return list(self.rows)


class FakeStateRepo:
    def __init__(self, warmed: dict[str, datetime] | None = None, *, later: dict[str, datetime] | None = None) -> None:
        self.warmed = warmed or {}
        self.later = later  # what every read after the first returns: a QA job warmed the volume meanwhile
        self.reads = 0
        self.deleted: list[str] = []

    async def get(self, *, scope: str, scope_key: str) -> VolumeState | None:
        self.reads += 1
        warmed = self.later if self.later is not None and self.reads > 1 else self.warmed
        at = warmed.get(scope_key)
        return VolumeState(scope, scope_key, f"vol-{scope_key}", "sha", at) if at else None

    async def delete_by_volume_name(self, name: str) -> None:
        self.deleted.append(name)


class FakeJobs:
    def __init__(self, live: set[str] | None = None, *, later: set[str] | None = None) -> None:
        self.live = live or set()
        self.later = later  # what every read after the first returns: a job started meanwhile
        self.reads = 0

    async def live_job_repos(self) -> set[str]:
        self.reads += 1
        return set(self.later if self.later is not None and self.reads > 1 else self.live)


def _sweep(
    runtime: FakeRuntime, rows: list[QAImageTagRow], *, days: int | None = 30, **kw: Any
) -> QAImageRetentionSweep:
    return QAImageRetentionSweep(
        runtime=runtime,  # type: ignore[arg-type]
        image_repo=FakeImageRepo(rows),  # type: ignore[arg-type]
        volume_state_repo=kw.pop("state", FakeStateRepo()),
        jobs_repo=kw.pop("jobs", FakeJobs()),
        instance_id=kw.pop("instance_id", INSTANCE),
        retention_days=days,
    )


def _removed_ids(runtime: FakeRuntime) -> set[str]:
    by_ref = {ref: image.id for image in runtime.images for ref in (*image.repo_tags, *image.repo_digests)}
    return {by_ref[refs[0]] for refs in runtime.removed_refs}


# --- images ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_active_image_is_never_removed_however_old() -> None:
    runtime = FakeRuntime([_image("sha256:active", "t1", _ago(400), digest="sha256:d1")])
    rows = [_row("t1", _ago(400), active=True, digest="sha256:d1")]

    result = await _sweep(runtime, rows).run_once(now=NOW)

    assert runtime.removed_refs == [], "the image an active qa_image_tags row names was removed"
    assert result.kept[Reason.PINNED] == 1


@pytest.mark.asyncio
async def test_a_pin_by_digest_alone_still_holds() -> None:
    """A row records `<tag>@<digest>`; an image re-tagged since is still the pinned one."""
    image = ImageSummary(
        id="sha256:x", repo_tags=(), repo_digests=(f"{REG}/{NAME}@sha256:d1",), created_at=_ago(90).isoformat(), size=1
    )
    runtime = FakeRuntime([image])

    await _sweep(runtime, [_row("gone", _ago(90), active=True, digest="sha256:d1")]).run_once(now=NOW)

    assert runtime.removed_refs == [], "an image pinned by its digest was removed"


@pytest.mark.asyncio
async def test_an_image_a_container_uses_is_never_removed() -> None:
    runtime = FakeRuntime(
        [_image("sha256:old", "t1", _ago(90)), _image("sha256:new", "t2", _ago(60))],
        image_users={"sha256:old": 1},
    )
    rows = [_row("t1", _ago(90), active=False), _row("t2", _ago(60), active=True)]

    result = await _sweep(runtime, rows).run_once(now=NOW)

    assert runtime.removed_refs == [], "an image a (stopped) container was started from was removed"
    assert result.kept[Reason.IN_USE] == 1 and result.kept[Reason.PINNED] == 1


@pytest.mark.asyncio
async def test_a_replaced_image_goes_once_its_replacement_is_older_than_the_retention() -> None:
    runtime = FakeRuntime(
        [
            _image("sha256:t1", "t1", _ago(120), digest="sha256:d1"),
            _image("sha256:t2", "t2", _ago(90)),
            _image("sha256:t3", "t3", _ago(10)),
        ]
    )
    rows = [
        _row("t1", _ago(120), active=False, digest="sha256:d1"),
        _row("t2", _ago(90), active=False),
        _row("t3", _ago(10), active=True),
    ]

    result = await _sweep(runtime, rows).run_once(now=NOW)

    assert _removed_ids(runtime) == {"sha256:t1"}, (
        "t1 was replaced 90 days ago and must go; t2 was the selected image until 10 days ago and must stay"
    )
    assert runtime.removed_refs[0] == [f"{NAME}:t1", f"{REG}/{NAME}:t1"], "the digest goes with the registry tag"
    assert result.counts()["image_bytes"] == 1000


@pytest.mark.asyncio
async def test_a_pushed_image_is_reported_removed_not_failed() -> None:
    """Every bake is pushed: local tag, registry tag and registry digest. rmi of all three removed
    the image, then failed on the digest the last tag had already taken with it (measured on
    Docker 27.5.1), so the sweep logged a removed image as failed and left it out of its counts."""
    runtime = FakeRuntime(
        [_image("sha256:t1", "t1", _ago(120), digest="sha256:d1"), _image("sha256:t2", "t2", _ago(90))]
    )
    rows = [_row("t1", _ago(120), active=False, digest="sha256:d1"), _row("t2", _ago(90), active=True)]

    result = await _sweep(runtime, rows).run_once(now=NOW)

    assert runtime.deleted == {"sha256:t1"}
    assert result.failed == [], f"a removed image was reported as failed: {result.failed}"
    assert [i["id"] for i in result.images] == ["sha256:t1"] and result.counts()["image_bytes"] == 1000


@pytest.mark.asyncio
async def test_an_image_a_container_takes_after_the_check_stays_and_is_reported_failed() -> None:
    """Without --force the daemon refuses the last tag of an image a container uses, digests named or not."""
    runtime = FakeRuntime([_image("sha256:t1", "t1", _ago(120), digest="sha256:d1")], image_users={"sha256:t1": 1})
    runtime.list_containers = AsyncMock(return_value=[])  # type: ignore[method-assign]  # it started after the check

    result = await _sweep(runtime, []).run_once(now=NOW)

    assert runtime.deleted == set(), "an image a container uses was removed"
    assert (result.failed, result.images) == (["sha256:t1"], [])


def test_rmi_names_a_digest_only_when_no_tag_of_its_repository_is_named() -> None:
    local, tag, digest = f"{NAME}:t1", f"{REG}/{NAME}:t1", f"{REG}/{NAME}@sha256:d1"
    assert removal_refs([local, tag, digest]) == [local, tag]
    assert removal_refs([local, digest]) == [local, digest], "the local tag does not take the registry digest"
    assert removal_refs([digest]) == [digest]


@pytest.mark.asyncio
async def test_a_failed_bake_with_no_row_goes_after_the_retention() -> None:
    runtime = FakeRuntime([_image("sha256:active", "t1", _ago(200)), _image("sha256:failed", "t2", _ago(40))])

    await _sweep(runtime, [_row("t1", _ago(200), active=True)]).run_once(now=NOW)

    assert _removed_ids(runtime) == {"sha256:failed"}, "a committed image no row names was never used: it must go"


@pytest.mark.asyncio
async def test_only_qa_images_are_considered() -> None:
    sandbox = ImageSummary(
        id="sha256:sb",
        repo_tags=(f"{REG}/embry0-sandbox:latest",),
        repo_digests=(),
        created_at=_ago(900).isoformat(),
        size=1,
    )
    assert qa_image(sandbox) is None
    runtime = FakeRuntime([sandbox])

    await _sweep(runtime, []).run_once(now=NOW)

    assert runtime.removed_refs == []


@pytest.mark.asyncio
async def test_a_non_qa_reference_on_a_qa_image_is_left_on_it() -> None:
    runtime = FakeRuntime([_image("sha256:t1", "t1", _ago(90), extra_tags=("mine:keep",))])

    await _sweep(runtime, []).run_once(now=NOW)

    assert runtime.removed_refs == [[f"{NAME}:t1", f"{REG}/{NAME}:t1"]], "only the QA references may be removed"


# --- volumes ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unused_per_repo_volume_goes_and_its_state_row_with_it() -> None:
    runtime = FakeRuntime([], volumes=[_volume("embry0-qa-vol-acme-widgets", "acme/widgets", _ago(100))])
    state = FakeStateRepo({"acme/widgets": _ago(45)})

    result = await _sweep(runtime, [], state=state).run_once(now=NOW)

    assert runtime.removed_volumes == ["embry0-qa-vol-acme-widgets"]
    assert state.deleted == ["embry0-qa-vol-acme-widgets"]
    assert runtime.volume_filters == [[f"embry0.instance={INSTANCE}", "embry0.volume_scope=per-repo"]]
    assert result.counts()["volume_bytes"] == 500


@pytest.mark.asyncio
async def test_a_volume_is_kept_while_used_recently_mounted_or_its_repo_has_a_live_job() -> None:
    runtime = FakeRuntime(
        [],
        volumes=[
            _volume("vol-warm", "acme/warm", _ago(300)),
            _volume("vol-mounted", "acme/mounted", _ago(300)),
            _volume("vol-live", "Acme/Live", _ago(300)),
        ],
        volume_users={"vol-mounted": 1},
    )
    state = FakeStateRepo({"acme/warm": _ago(2)})

    result = await _sweep(runtime, [], state=state, jobs=FakeJobs({"acme/live"})).run_once(now=NOW)

    assert runtime.removed_volumes == [], "a recently used, mounted or live-job volume was removed"
    assert (result.kept[Reason.RECENT], result.kept[Reason.IN_USE], result.kept[Reason.LIVE_JOB]) == (1, 1, 1)


@pytest.mark.parametrize("claim", ["a job of its repo started", "a job warmed it"])
@pytest.mark.asyncio
async def test_a_volume_a_job_claims_after_the_decision_is_kept(claim: str) -> None:
    """A QA job can start, and ensure() the volume, between the sweep's reads and its rm. Removing it
    then leaves the job an empty, unlabelled volume that neither the reaper nor this sweep knows."""
    runtime = FakeRuntime([], volumes=[_volume("embry0-qa-vol-acme-widgets", "acme/widgets", _ago(100))])
    if claim == "a job of its repo started":
        jobs, state, reason = FakeJobs(later={"Acme/Widgets"}), FakeStateRepo(), Reason.LIVE_JOB
    else:
        jobs, state, reason = FakeJobs(), FakeStateRepo(later={"acme/widgets": NOW}), Reason.RECENT

    result = await _sweep(runtime, [], state=state, jobs=jobs).run_once(now=NOW)

    assert runtime.removed_volumes == [], f"the volume was removed after {claim}"
    assert result.kept[reason] == 1 and result.volumes == []


def test_a_volume_another_deployment_labelled_is_not_ours() -> None:
    decision = decide_volume(
        _volume("v", "acme/widgets", _ago(300), instance="someone-else"),
        instance_id=INSTANCE,
        last_warmed_at=None,
        live_repos=set(),
        cutoff=_ago(30),
    )
    assert (decision.remove, decision.reason) == (False, Reason.NOT_OURS)


@pytest.mark.asyncio
async def test_without_an_instance_id_no_volume_is_touched() -> None:
    runtime = FakeRuntime([], volumes=[_volume("v", "acme/widgets", _ago(300))])

    result = await _sweep(runtime, [], instance_id=None).run_once(now=NOW)

    assert runtime.removed_volumes == [] and runtime.volume_filters == []
    assert result.errors, "skipping the volumes must be reported"


# --- dry run and off -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dry_run_decides_the_same_and_removes_nothing() -> None:
    images = [
        _image("sha256:t1", "t1", _ago(120), digest="sha256:d1"),
        _image("sha256:t2", "t2", _ago(10), digest="sha256:d2"),
    ]
    rows = [_row("t1", _ago(120), active=False, digest="sha256:d1"), _row("t2", _ago(100), active=True)]
    volumes = [_volume("vol-old", "acme/old", _ago(100))]
    dry_runtime = FakeRuntime(list(images), volumes=list(volumes))

    preview = await _sweep(dry_runtime, rows).run_once(dry_run=True, now=NOW)

    assert dry_runtime.removed_refs == [] and dry_runtime.removed_volumes == []
    real_runtime = FakeRuntime(list(images), volumes=list(volumes))
    real = await _sweep(real_runtime, rows).run_once(now=NOW)
    assert real_runtime.deleted == {"sha256:t1"}
    assert preview.as_dict() == real.as_dict(), "the dry run must report exactly what the sweep then removes"
    assert (preview.counts()["images"], preview.counts()["volumes"]) == (1, 1)


@pytest.mark.asyncio
async def test_unset_means_nothing_is_read_or_removed() -> None:
    runtime = FakeRuntime([_image("sha256:t1", "t1", _ago(900))], volumes=[_volume("v", "acme/w", _ago(900))])
    sweep = _sweep(runtime, [], days=None)

    result = await sweep.run_once(now=NOW)

    assert not sweep.enabled
    assert runtime.removed_refs == [] and runtime.removed_volumes == [] and runtime.volume_filters == []
    assert result.counts()["images"] == 0
