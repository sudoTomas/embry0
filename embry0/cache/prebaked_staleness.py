"""Is a pre-built QA image still built on today's sandbox base? (#334)

A pre-built image is a ``docker commit`` of a QA sandbox, so it carries the
sandbox runner, agents and safety code of the day it was built. Rebuilding the
base sandbox image does not touch it. This module answers one question —
fresh, stale or unknown — and owns the docker reads needed to answer it.

Boundaries:
  * ``classify`` is pure.
  * ``BaseImageDigestResolver`` / ``container_base_image`` / ``image_digest``
    are the only docker calls; all go through the injected client so they hit
    the daemon that runs sandboxes (DinD in compose).
  * The comparison is against the REGISTRY, not that daemon's local copy.
    Nothing refreshes DinD's ``embry0-sandbox-qa`` on its own - the bootstrap
    pushes host->registry and ``SandboxImageManager.ensure_image`` covers only
    ``embry0-sandbox`` - so DinD can sit a rebuild behind indefinitely, and a
    local inspect would return the build-time id forever: every image would
    read FRESH, which is precisely the case this module exists for. So a
    registry-qualified base is pulled before it is inspected (the pull is work
    a sandbox launch would do anyway) and compared by manifest digest. An
    unqualified name has no registry to ask, and there the local id is the
    only truth there is.
  * Nothing here rebuilds or deactivates anything. A stale image is skipped
    for the run (the base image + warm volume is slower but correct) and
    reported; rebuilding is an operator action.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.execution.image_registry import (
    installed_image_pins,
    is_registry_qualified,
    pinned_digest,
    strip_digest,
)
from embry0.storage.repositories.qa_image_tags import QAImageTagRow

if TYPE_CHECKING:
    from embry0.execution.runtime import AgentRuntime

logger = structlog.get_logger(__name__)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REBUILD_ACTION = "rebuild it: embry0 build-qa-image --repo <repo> --branch <branch> --force"


class BaseFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    # No digest on the row (built before #334) or the current base could not
    # be inspected. Unknown images stay in use: the runner's argv fallback
    # (#326) covers an old one, and a docker hiccup must not turn every QA
    # run into a cold start.
    UNKNOWN = "unknown"


class DigestResolver(Protocol):
    async def current(self, image: str) -> str | None: ...


def classify(row: QAImageTagRow, current_digest: str | None) -> BaseFreshness:
    if not row.base_image_digest or not current_digest:
        return BaseFreshness.UNKNOWN
    return BaseFreshness.FRESH if row.base_image_digest == current_digest else BaseFreshness.STALE


def _repo_of(image: str) -> str:
    """``registry:5000/embry0-sandbox-qa:latest`` -> ``registry:5000/embry0-sandbox-qa``.

    A colon is a tag separator only after the last slash; before it, it is the
    registry's port. A pinned name (``…:latest@sha256:…``, #523) loses its digest
    first, or the digest's own colon would be read as the tag separator.
    """
    head, sep, tail = strip_digest(image).rpartition(":")
    return head if sep and "/" not in tail else strip_digest(image)


async def pull_image(runtime: AgentRuntime, image: str, *, timeout: int = 180) -> bool:
    """Refresh the daemon's copy of ``image`` from its registry.

    Returns False instead of raising: a registry hiccup means "cannot tell",
    which every caller treats as UNKNOWN rather than as a fault.
    """
    try:
        await runtime.pull_image(image, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.info("qa_base_image_pull_failed", image=image, error=str(exc)[:200])
        return False
    return True


async def image_digest(runtime: AgentRuntime, ref: str, *, repo_name: str) -> str | None:
    """The comparable digest of the image ``ref``, or None if there is none.

    ``ref`` may be a tag or an image ID - the builder passes the ID of the bits
    its container actually runs, since the tag can move while a bake runs.
    ``repo_name`` is the tag those bits are known by, and decides which digest
    is comparable: a registry-qualified name uses the manifest digest recorded
    for that repository, an unqualified one the daemon-local image ID. The two
    forms are never mixed - falling back to the local ID for a qualified name
    would read as STALE against a recorded manifest digest, flagging an image
    that is perfectly fresh.
    """
    try:
        repo_digests, local_id = await runtime.image_digests(ref, timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.info("qa_base_image_digest_unresolved", image=ref, error=str(exc)[:200])
        return None
    if not is_registry_qualified(repo_name):
        return local_id if _DIGEST_RE.fullmatch(local_id) else None
    prefix = f"{_repo_of(repo_name)}@"
    for entry in repo_digests:
        if entry.startswith(prefix) and _DIGEST_RE.fullmatch(entry[len(prefix) :]):
            return entry[len(prefix) :]
    logger.info("qa_base_image_digest_unresolved", image=ref, error="no registry digest for " + repo_name)
    return None


class BaseImageDigestResolver:
    """Current digest of a base image, briefly cached, one fetch at a time.

    Returns None whenever it cannot tell (registry unreachable, image absent,
    odd output). Failures are cached for their own short TTL: /stats resolves
    once per active row, and an unreachable daemon must not cost a fresh
    attempt every poll. A refresh is shielded from its caller's cancellation,
    so a caller that gives up on a budget still leaves the cache warm instead
    of starting another pull on the next call.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        ttl_seconds: float = 60.0,
        failure_ttl_seconds: float = 60.0,
        pull_timeout_seconds: int = 180,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._ttl = ttl_seconds
        self._failure_ttl = failure_ttl_seconds
        self._pull_timeout = pull_timeout_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, str | None]] = {}
        self._inflight: dict[str, asyncio.Task[str | None]] = {}

    async def current(self, image: str) -> str | None:
        # #523: a pinned base's current digest is its pin. That is the image
        # every new sandbox runs, and reading it needs no pull: the tag in the
        # registry is exactly what a pin must not be compared against.
        pinned = pinned_digest(image)
        if pinned is not None:
            return pinned
        hit = self._cache.get(image)
        if hit is not None:
            age = self._clock() - hit[0]
            if age < (self._ttl if hit[1] is not None else self._failure_ttl):
                return hit[1]
        task = self._inflight.get(image)
        if task is None or task.done():
            task = asyncio.create_task(self._refresh(image))
            self._inflight[image] = task
        # shield: the caller may be on a budget (GET /stats); the refresh is not.
        return await asyncio.shield(task)

    async def _refresh(self, image: str) -> str | None:
        digest: str | None = None
        if not is_registry_qualified(image) or await pull_image(self._runtime, image, timeout=self._pull_timeout):
            digest = await image_digest(self._runtime, image, repo_name=image)
        self._cache[image] = (self._clock(), digest)
        if self._inflight.get(image) is asyncio.current_task():
            del self._inflight[image]
        return digest


async def container_base_image(runtime: AgentRuntime, container_id: str) -> tuple[str, str]:
    """``(image name, image ID)`` the container was started from.

    Read from the container itself rather than rebuilt from the profile, so
    it is the registry-qualified name the sandbox manager really used. Raises
    ValueError on output it cannot trust — recording a wrong digest would
    mark a good image stale, or worse, a stale one fresh.
    """
    name, digest = await runtime.container_image(container_id, timeout=10)
    if not name or not _DIGEST_RE.fullmatch(digest):
        raise ValueError(f"unexpected docker inspect output for container {container_id}")
    return name, digest


# One line per (repo, image, reason) per process — selection runs twice per QA
# job, and a fleet-wide base rebuild would otherwise repeat on every one.
_warned: set[tuple[str, ...]] = set()


def reset_warning_memory() -> None:
    _warned.clear()


def _once(*key: str) -> bool:
    if key in _warned:
        return False
    _warned.add(key)
    return True


async def freshness(row: QAImageTagRow, resolver: DigestResolver | None) -> tuple[BaseFreshness, str | None]:
    """Classify ``row`` against the live base image; also returns the current
    digest. Never raises — a resolver fault reads as UNKNOWN."""
    if not row.base_image or not row.base_image_digest or resolver is None:
        return BaseFreshness.UNKNOWN, None
    try:
        current = await resolver.current(row.base_image)
    except Exception as exc:  # noqa: BLE001 — callers must never fail on this
        logger.info("qa_base_image_digest_unresolved", image=row.base_image, error=str(exc)[:200])
        current = None
    return classify(row, current), current


async def usable_image_tag(row: QAImageTagRow, resolver: DigestResolver | None) -> str | None:
    """The row's image tag, or None when its base has been rebuilt since."""
    tag = await _usable_image_tag(row, resolver)
    if tag is not None and installed_image_pins() and "@" not in tag and _once("unpinned", row.repo, tag):
        # #523: baked before bakes recorded their digest, so it still runs by
        # tag. Kept in use; a rebuild records the digest and pins it.
        logger.warning(
            "qa_prebuilt_image_unpinned",
            repo=row.repo,
            image_tag=tag,
            detail="built before pre-built images were pinned by digest; it runs by tag until rebuilt",
            action=REBUILD_ACTION,
        )
    return tag


async def _usable_image_tag(row: QAImageTagRow, resolver: DigestResolver | None) -> str | None:
    if not row.base_image or not row.base_image_digest:
        if _once("unknown", row.repo, row.image_tag):
            logger.info(
                "qa_prebuilt_image_base_unknown",
                repo=row.repo,
                image_tag=row.image_tag,
                detail="built before base digests were recorded; still used",
                action=REBUILD_ACTION,
            )
        return row.image_tag
    verdict, current = await freshness(row, resolver)
    if verdict is not BaseFreshness.STALE or current is None:
        return row.image_tag
    if _once("stale", row.repo, row.image_tag, current):
        logger.warning(
            "qa_prebuilt_image_stale",
            repo=row.repo,
            image_tag=row.image_tag,
            base_image=row.base_image,
            built_on=row.base_image_digest,
            current=current,
            detail="base sandbox image was rebuilt after this image was committed; using the base image instead",
            action=REBUILD_ACTION,
        )
    return None


async def summarize_prebuilt_images(
    image_repo: Any,
    resolver: DigestResolver | None,
    *,
    budget_seconds: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Active pre-built images grouped by freshness, for ``GET /stats``.

    One wall-clock budget covers the whole summary: a dashboard poll must not
    wait on a registry, however many rows there are. Rows left unresolved when
    the budget runs out are reported UNKNOWN - the honest answer, and the
    refresh they started keeps running, so the next poll has it.
    """
    if image_repo is None or resolver is None:
        return None
    rows: list[QAImageTagRow] = await image_repo.list_active()
    groups: dict[BaseFreshness, list[dict[str, str]]] = {f: [] for f in BaseFreshness}
    deadline = clock() + budget_seconds
    for row in rows:
        remaining = deadline - clock()
        verdict = BaseFreshness.UNKNOWN
        if remaining > 0:
            try:
                verdict, _ = await asyncio.wait_for(freshness(row, resolver), remaining)
            except TimeoutError:
                logger.info("stats_qa_prebuilt_images_budget_exhausted", repo=row.repo)
        groups[verdict].append({"repo": row.repo, "image_tag": row.image_tag})
    return {
        "active": len(rows),
        "fresh": len(groups[BaseFreshness.FRESH]),
        "stale": groups[BaseFreshness.STALE],
        "unknown": groups[BaseFreshness.UNKNOWN],
    }
