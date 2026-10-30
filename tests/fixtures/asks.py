"""Settings snapshots holding the ``asks`` section, for tests of the ask loop and the feed without a database.

Built by :func:`embry0.settings.store.build_snapshot` from rows shaped as the
store reads them, so each organization's layer is resolved exactly as in
production.
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
