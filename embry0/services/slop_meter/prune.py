"""What the Slop-O-Meter keeps of a repository that left its scope, removed after a grace period (#165).

A repository leaves the meter's scope when its ``background.slop_meter``
resolves to false (an operator opted it, or its organization, out) or when
it is unregistered. Making it inactive only stops the reading: what was read
is kept. A mirror on disk with no row (one whose rows were lost) leaves the
scope the same way when no enrolled repository has its name, but only when
it carries the meter's marker (:data:`embry0.services.slop_meter.mirror.MARKER`,
written at the clone): one without it is logged and audited once and never
removed, since it may not be the meter's.

Nothing is removed at once. Each tick, once the meter's enrollment is
settled (:meth:`embry0.services.background_scope.Enrollment.settled`):

1. **The floor.** When the registry read lists no repository at all while
   the meter keeps something, the tick removes nothing and records nothing,
   logged at error level and audited once: an empty registry read must not
   cost every repository's data. Nothing enrolled is no such case: a
   repository opted out, or the meter turned off everywhere, goes through
   the grace period below, which a ``REPO_RENAMES`` mistake or a switch
   flipped by mistake put right in time cancels.
2. **The clock.** Each repository found out of scope for the first time is
   recorded with the time (``background_scope_exits``) and logged with when
   it will be removed: ``background.prune_after_days`` later. One that comes
   back in scope before then has its record deleted and keeps everything
   (``slop_meter_prune_cancelled``).
3. **The removal**, once the grace period has passed: its mirror first,
   through :meth:`GitMirror.remove`, which deletes only
   ``<root>/<group>/.../<name>.git``, then each directory above it left
   empty, and never follows a link; then its rows, the
   history as read with its authors' names and emails, the forge events with
   their logins, the judgments, the series and the scores, and each login no
   remaining event refers to. Each removal is logged and audited
   (``slop_meter.pruned``).

A mirror that cannot be deleted for now (an ``OSError``) keeps the rows and
the record, so the next tick tries again. One the guard refuses (a link or a
file where a mirror should be) is logged at error level and audited with its
path (``slop_meter.prune.refused``) for a person to look at, and the rows go
anyway: the personal data in them does not wait on the disk.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import structlog

from embry0.audit.actions import AuditAction
from embry0.services.slop_meter.mirror import MirrorRefused

logger = structlog.get_logger(__name__)

#: The feature whose scope the meter's exits are recorded under (``background_scope_exits.feature``).
FEATURE: Final = "slop_meter"


class _Store(Protocol):
    async def held(self) -> list[str]: ...

    async def forget(self, repo: str) -> dict[str, int]: ...


class _Mirror(Protocol):
    async def held(self) -> list[str]: ...

    async def marked(self, repo: str) -> bool: ...

    async def remove(self, repo: str) -> bool: ...

    def path(self, repo: str) -> Any: ...


class _Exits(Protocol):
    async def left(self, feature: str) -> dict[str, datetime]: ...

    async def mark(self, feature: str, repos: Iterable[str], *, at: datetime) -> list[str]: ...

    async def clear(self, feature: str, repos: Iterable[str]) -> list[str]: ...


class Audit(Protocol):
    """Writes one audit row for the meter (``emit_audit`` with its actor and the deployment's audit log)."""

    async def __call__(self, *, action: AuditAction, details: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class PruneOutcome:
    """What one tick did: removed, still waiting (with when each goes), back in scope, or refused whole."""

    removed: tuple[str, ...] = ()
    pending: dict[str, datetime] = field(default_factory=dict)
    cancelled: tuple[str, ...] = ()
    #: Why the tick removed and recorded nothing, when it did not.
    refused: str = ""


@dataclass
class Pruner:
    """Removes, after the grace period, what the meter keeps of each repository out of its scope."""

    store: _Store
    mirror: _Mirror
    exits: _Exits
    audit: Audit
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    #: Repositories whose pending removal this process has logged; each is logged once.
    _announced: set[str] = field(default_factory=set, init=False, repr=False)
    #: Whether this process audited the floor's refusal; audited again only after a tick that passed it.
    _floor_audited: bool = field(default=False, init=False, repr=False)
    #: Mirrors with no row and no marker this process logged and audited; each once.
    _unmarked: set[str] = field(default_factory=set, init=False, repr=False)

    async def run(self, keep: Iterable[str], *, grace: timedelta, registered: int) -> PruneOutcome:
        """One tick: ``keep`` is every repository in scope (registered, active or not, and enrolled), and
        ``registered`` how many repositories the registry read listed, in scope or not."""
        now = self.clock()
        kept = {name.lower() for name in keep}
        held = await self.store.held()
        on_disk = await self.mirror.held()
        spellings: dict[str, list[str]] = {}
        for name in held:
            spellings.setdefault(name.lower(), []).append(name)
        if not registered and (held or on_disk):
            return await self._floor(len(spellings), len(on_disk))
        self._floor_audited = False
        orphans = [name for name in on_disk if name not in spellings and name not in kept]
        out = {*spellings, *(await self._marked(orphans))} - kept
        left = await self.exits.left(FEATURE)
        back = sorted(name for name in left if name in kept)
        gone = sorted(name for name in left if name not in kept and name not in out)
        if back or gone:
            await self.exits.clear(FEATURE, [*back, *gone])
        for name in back:
            self._announced.discard(name)
            logger.info("slop_meter_prune_cancelled", repo=name, left_at=left[name].isoformat())
        if any(name not in left for name in out):
            await self.exits.mark(FEATURE, [name for name in out if name not in left], at=now)
            left = await self.exits.left(FEATURE)
        pending: dict[str, datetime] = {}
        removed: list[str] = []
        for name in sorted(out):
            since = left.get(name, now)
            due = since + grace
            if due > now:
                pending[name] = due
                if name not in self._announced:
                    self._announced.add(name)
                    logger.warning(
                        "slop_meter_prune_pending",
                        repo=name,
                        left_at=since.isoformat(),
                        removed_at=due.isoformat(),
                        hint="it left background.slop_meter; enroll it again before then to keep what was read",
                    )
                continue
            if await self._remove(name, spellings.get(name, []), since):
                removed.append(name)
        return PruneOutcome(removed=tuple(removed), pending=pending, cancelled=tuple(back))

    async def _marked(self, orphans: list[str]) -> list[str]:
        """Of ``orphans`` (mirrors on disk with no row), the ones carrying the meter's marker. One without it may
        be a mirror the meter cloned before the marker existed, or none of the meter's at all: it is logged and
        audited once, and never removed."""
        marked: list[str] = []
        for name in orphans:
            if await self.mirror.marked(name):
                marked.append(name)
                continue
            if name in self._unmarked:
                continue
            self._unmarked.add(name)
            path = self._path(name)
            reason = "a mirror with no row and without the meter's marker: it may not be the meter's"
            logger.error(
                "slop_meter_orphan_unmarked",
                repo=name,
                path=path,
                hint="never removed; remove it by hand if it is a mirror the meter left, or set "
                "embry0.slopMeter=true in its git config to let the meter remove it",
            )
            await self.audit(action="slop_meter.prune.refused", details={"repo": name, "path": path, "reason": reason})
        return marked

    async def _floor(self, held: int, on_disk: int) -> PruneOutcome:
        reason = "the registry read listed no repository while the meter keeps some: nothing is removed"
        logger.error(
            "slop_meter_prune_refused",
            reason=reason,
            held=held,
            mirrors=on_disk,
            hint="an empty registry read must not remove every repository's data; the next read that lists any "
            "repository resumes the removal of what left the meter's scope",
        )
        if not self._floor_audited:
            self._floor_audited = True
            await self.audit(
                action="slop_meter.prune.refused", details={"reason": reason, "held": held, "mirrors": on_disk}
            )
        return PruneOutcome(refused=reason)

    async def _remove(self, name: str, spellings: list[str], since: datetime) -> bool:
        """Remove what is kept of ``name`` (lowercased): its mirror, then the rows under each of ``spellings``.
        False when the mirror could not be deleted for now: everything is kept and tried again next tick."""
        mirror: str
        try:
            mirror = "removed" if await self.mirror.remove(name) else "absent"
        except MirrorRefused as exc:
            mirror = "refused"
            path = self._path(name)
            logger.error(
                "slop_meter_mirror_prune_refused",
                repo=name,
                path=path,
                reason=str(exc),
                hint="nothing on disk was deleted; remove it by hand. Its rows are removed",
            )
            await self.audit(
                action="slop_meter.prune.refused",
                details={"repo": name, "path": path, "reason": str(exc), "rows": "removed"},
            )
        except OSError as exc:
            logger.warning(
                "slop_meter_mirror_prune_failed",
                repo=name,
                error=type(exc).__name__,
                hint="its rows are kept and the next tick tries again",
            )
            return False
        counts: dict[str, int] = {}
        for spelled in spellings:
            for key, value in (await self.store.forget(spelled)).items():
                counts[key] = counts.get(key, 0) + value
        await self.exits.clear(FEATURE, [name])
        self._announced.discard(name)
        logger.info(
            "slop_meter_pruned",
            repo=name,
            mirror=mirror,
            left_at=since.isoformat(),
            reason="left the meter's scope",
            **counts,
        )
        await self.audit(
            action="slop_meter.pruned",
            details={"repo": name, "mirror": mirror, "left_at": since.isoformat(), "rows": counts},
        )
        return True

    def _path(self, name: str) -> str:
        try:
            return str(self.mirror.path(name))
        except ValueError:
            return ""


def audit_writer(write: Callable[..., Awaitable[None]], db: Any, *, actor: str, audit_log_path: Any) -> Audit:
    """The meter's :class:`Audit` over ``write`` (``emit_audit``), as the actor ``actor``."""

    async def audit(*, action: AuditAction, details: dict[str, Any]) -> None:
        await write(db, action, actor=actor, details=details, audit_log_path=audit_log_path)

    return audit


__all__ = ["FEATURE", "Audit", "PruneOutcome", "Pruner", "audit_writer"]
