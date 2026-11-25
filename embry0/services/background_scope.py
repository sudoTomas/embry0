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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from embry0.settings.layers import PerRepository
from embry0.settings.sections.background import BACKGROUND, FEATURES, BackgroundSettings, Feature


def _feature(feature: str) -> Feature:
    if feature not in FEATURES:
        raise ValueError(f"{feature!r} is not a background feature: one of {', '.join(FEATURES)}")
    return feature


class _Snapshot(Protocol):
    def scoped(self, section: Any) -> Any: ...


@dataclass(frozen=True)
class Enrollment:
    """The ``background`` section of one snapshot of the settings documents."""

    section: PerRepository[BackgroundSettings]
    #: The features that remove what they kept whose enrollment nothing has decided yet: the import that wrote it
    #: never ran on this deployment and no document sets the key (``embry0.settings.upgrade_check``).
    undecided: frozenset[str] = frozenset()

    def prune_held(self, feature: str) -> bool:
        """Whether ``feature`` must keep what it kept of every repository for now: a deployment that skipped the
        release that enrolled what it read before reads nothing, and must not remove it all before an operator
        enrolls (``background.<feature>`` set in any document)."""
        return _feature(feature) in self.undecided

    def enrolled(self, feature: str, repo: str) -> bool:
        """Whether ``repo`` is enrolled in ``feature``, through its organization's layer and its own."""
        return bool(getattr(self.section.repo(repo), _feature(feature)))

    @property
    def prune_after(self) -> timedelta:
        """How long the meter waits, after a repository leaves its scope, before it removes what it kept of it."""
        return timedelta(days=self.section.deployment().prune_after_days)

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


#: The features that remove what they kept of a repository that left their scope.
_PRUNING: Final[tuple[Feature, ...]] = ("slop_meter",)


def _set_anywhere(snapshot: Any, feature: str) -> bool:
    """Whether any document of ``snapshot``, the deployment's or a layer, sets ``background.<feature>``."""
    documents = [getattr(snapshot, "data", None) or {}, *(getattr(snapshot, "layers", None) or {}).values()]
    return any(feature in (doc.get(BACKGROUND.name) or {}) for doc in documents if isinstance(doc, Mapping))


def enrollment(snapshot: _Snapshot) -> Enrollment:
    """The enrollment ``snapshot`` holds: what one tick reads, from the snapshot the tick took."""
    imported = getattr(snapshot, "imported", None)
    undecided = frozenset(
        feature
        for feature in _PRUNING
        if callable(imported) and not imported(f"{BACKGROUND.name}.{feature}") and not _set_anywhere(snapshot, feature)
    )
    return Enrollment(section=snapshot.scoped(BACKGROUND), undecided=undecided)


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
    "Enrollment",
    "Registered",
    "enrollment",
    "registered_reader",
    "registry_reader",
    "spellings",
]
