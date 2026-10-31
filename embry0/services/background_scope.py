"""The one rule for which repositories a background feature reads (#165).

A background feature (the leads finders, the scout, the Slop-O-Meter) reads
a repository when it is registered, active, and enrolled in that feature:
the ``background`` section of the settings document resolves to true for it,
through its organization's layer and its own
(:mod:`embry0.settings.sections.background`). Nothing else enrolls a
repository: not a job run on it, not the world map showing it.

A loop takes one snapshot of the settings documents at the top of a tick and
reads its enrollment from it with :func:`enrollment`, so a change applies on
the next tick with no restart, and one tick never mixes two versions:

    read = enrollment(await store.snapshot())
    repos = read.scope("slop_meter", await registered())

A feature can narrow the result to the repositories its own rule allows
(``among``): the leads finders read only where someone answers for the
repository.

**Settled.** A release that turned a feature's enrollment explicit imported,
once, the repositories that feature read before
(:mod:`embry0.services.background_enrollment`). Until that import has run,
the feature reads its defaults and may read fewer repositories than it
should; ``settled`` says when what it does not read is truly out, which is
what a feature that removes what it kept must wait for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from embry0.settings.layers import PerRepository
from embry0.settings.sections.background import BACKGROUND, FEATURES, BackgroundSettings, Feature

#: The features whose repositories a release imported once, when their enrollment became explicit. A feature
#: not listed here imported nothing, so what it reads is final from the start.
IMPORTED: Final[tuple[Feature, ...]] = ("scout", "slop_meter")


def ledger_key(feature: Feature) -> str:
    """The import ledger entry (``settings_documents.bootstrapped_keys``) saying ``feature``'s import ran."""
    return f"{BACKGROUND.name}.{feature}"


def _feature(feature: str) -> Feature:
    if feature not in FEATURES:
        raise ValueError(f"{feature!r} is not a background feature: one of {', '.join(FEATURES)}")
    return feature


class _Snapshot(Protocol):
    def scoped(self, section: Any) -> Any: ...

    def imported(self, path: str, scope: Any = None) -> bool: ...


@dataclass(frozen=True)
class Enrollment:
    """The ``background`` section of one snapshot of the settings documents."""

    section: PerRepository[BackgroundSettings]
    #: The features whose import has run, or that import nothing.
    settled_features: frozenset[str]

    def enrolled(self, feature: str, repo: str) -> bool:
        """Whether ``repo`` is enrolled in ``feature``, through its organization's layer and its own."""
        return bool(getattr(self.section.repo(repo), _feature(feature)))

    @property
    def prune_after(self) -> timedelta:
        """How long the meter waits, after a repository leaves its scope, before it removes what it kept of it."""
        return timedelta(days=self.section.deployment().prune_after_days)

    def settled(self, feature: str) -> bool:
        """Whether what ``feature`` does not read is out of its scope for good, not only until the import runs."""
        return _feature(feature) in self.settled_features

    def scope(self, feature: str, registered: Iterable[str], *, among: Iterable[str] | None = None) -> list[str]:
        """The repositories ``feature`` reads: those of ``registered`` (registered and active) that are enrolled
        in it, and in ``among`` when given, compared without regard to case. Each named once, as ``registered``
        spells it, sorted without regard to case."""
        _feature(feature)
        wanted = None if among is None else {name.lower() for name in among}
        out: dict[str, str] = {}
        for name in registered:
            key = name.lower()
            if key in out or (wanted is not None and key not in wanted):
                continue
            if self.enrolled(feature, name):
                out[key] = name
        return sorted(out.values(), key=str.lower)


def enrollment(snapshot: _Snapshot) -> Enrollment:
    """The enrollment ``snapshot`` holds: what one tick reads, from the snapshot the tick took."""
    settled = frozenset(
        feature for feature in FEATURES if feature not in IMPORTED or snapshot.imported(ledger_key(feature))
    )
    return Enrollment(section=snapshot.scoped(BACKGROUND), settled_features=settled)


class _Registry(Protocol):
    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]: ...


def spellings(rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, bool]]:
    """The registry's rows, one per repository: lowercased slug to ``(spelling, active)``.

    The registry can hold two rows that differ only by case (``Acme/Widgets``
    and ``acme/widgets``); every reader must take the same one, or the meter
    reads under one spelling and moves its rows to the other every tick. The
    rule, whatever order the database answers in: an active row before an
    inactive one, since an active row is the one the repository's
    preferences, environment and QA config are kept under; then the oldest
    (``created_at``), the registration the rest was made for; then the
    spelling that sorts first by code point. Active rows sort first, so the
    repository is active when any of its rows is.
    """
    out: dict[str, tuple[str, bool]] = {}
    for row in sorted(rows, key=_preference):
        name = str(row["repo"])
        out.setdefault(name.lower(), (name, bool(row.get("active", True))))
    return out


def _preference(row: Mapping[str, Any]) -> tuple[bool, float, str]:
    """The order :func:`spellings` keeps a row in: active first, then the oldest, then by code point."""
    created = row.get("created_at")
    at = created.timestamp() if isinstance(created, datetime) else float("inf")
    return (not bool(row.get("active", True)), at, str(row["repo"]))


def registered_reader(registry: _Registry) -> Callable[[], Awaitable[list[str]]]:
    """What a loop lists its candidates with: every registered, active repository, one spelling each
    (:func:`spellings`).

    An inactive repository is read by no background feature; one that is
    unregistered has no row at all.
    """

    async def read() -> list[str]:
        found = spellings(await registry.list_all(include_inactive=True))
        return sorted((name for name, active in found.values() if active), key=str.lower)

    return read


@dataclass(frozen=True)
class Registered:
    """The registry as a feature that removes what it kept reads it: what it reads, and what it keeps.

    ``active`` is read; ``inactive`` is not read, and keeps what was read of
    it: making a repository inactive hides it, it does not delete anything.
    Only a repository whose key resolves to false, or one unregistered, is
    removed (:mod:`embry0.services.slop_meter.prune`).
    """

    active: tuple[str, ...] = ()
    inactive: tuple[str, ...] = ()

    @property
    def every(self) -> tuple[str, ...]:
        """Every registered repository, active or not."""
        return self.active + self.inactive


def registry_reader(registry: _Registry) -> Callable[[], Awaitable[Registered]]:
    """What the meter lists its candidates with: every registered repository, one spelling each
    (:func:`spellings`), split by whether it is active. One read of the registry."""

    async def read() -> Registered:
        found = sorted(spellings(await registry.list_all(include_inactive=True)).values(), key=lambda v: v[0].lower())
        return Registered(
            active=tuple(name for name, active in found if active),
            inactive=tuple(name for name, active in found if not active),
        )

    return read


__all__ = [
    "IMPORTED",
    "Enrollment",
    "Registered",
    "enrollment",
    "ledger_key",
    "registered_reader",
    "registry_reader",
    "spellings",
]
