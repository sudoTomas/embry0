"""Helpers for tests of the asks.

- Settings snapshots holding the ``asks`` section, for tests of the ask loop
  and the feed without a database. Built by
  :func:`embry0.settings.store.build_snapshot` from rows shaped as the store
  reads them, so each organization's layer is resolved exactly as in
  production.
- :class:`CountingDb`, a database pool that counts the statements sent
  through it, for an ``AsksRepository`` whose round trips a test counts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from embry0.settings.sections import CATALOG
from embry0.settings.sections.asks import ASKS, AsksSettings
from embry0.settings.store import Scoped, Snapshot, build_snapshot


def asks_snapshot(
    deployment: Mapping[str, Any] | AsksSettings,
    organizations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Snapshot:
    """A snapshot of the deployment's ``asks`` values and each organization's layer (owner to its values)."""
    values = (
        deployment.model_dump(mode="json", exclude_unset=True)
        if isinstance(deployment, AsksSettings)
        else dict(deployment)
    )
    rows: list[dict[str, Any]] = [
        {"scope": "deployment", "data": {"asks": values}, "generation": 1, "updated_at": None}
    ]
    for owner, layer in (organizations or {}).items():
        rows.append(
            {"scope": f"organization:{owner}", "data": {"asks": dict(layer)}, "generation": 1, "updated_at": None}
        )
    return build_snapshot(CATALOG, rows)


def asks_read(
    deployment: Mapping[str, Any] | AsksSettings,
    organizations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Scoped[AsksSettings]:
    """The ``asks`` section of :func:`asks_snapshot`, as a loop built with ``scoped_reader`` reads it."""
    return asks_snapshot(deployment, organizations).scoped(ASKS)


class CountingDb:
    """A database pool that records each statement sent through it, one entry per round trip.

    Give it to the one repository whose round trips are counted, so the other
    statements of the same pool, such as a background loop's, never count.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self.statements: list[str] = []
        #: Each statement's arguments, in the order sent.
        self.arguments: list[tuple[Any, ...]] = []

    def _send(self, name: str) -> Any:
        real = getattr(self._db, name)

        async def send(query: str, *args: Any) -> Any:
            self.statements.append(" ".join(query.split()))
            self.arguments.append(args)
            return await real(query, *args)

        return send

    def __getattr__(self, name: str) -> Any:
        if name in ("execute", "fetch", "fetchrow", "fetchval"):
            return self._send(name)
        return getattr(self._db, name)
