"""Settings snapshots holding the ``scout`` section, for tests of the scout loop without a database.

Built by :func:`embry0.settings.store.build_snapshot` from rows shaped as the
store reads them, so each organization's layer is resolved exactly as in
production.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from embry0.services.background_scope import Enrollment, enrollment, ledger_key
from embry0.settings.sections import CATALOG
from embry0.settings.sections.scout import SCOUT, ScoutSettings
from embry0.settings.store import Scoped, Snapshot, build_snapshot


def scout_snapshot(
    deployment: Mapping[str, Any] | ScoutSettings,
    organizations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Snapshot:
    """A snapshot of the deployment's ``scout`` values and each organization's layer (owner to its values)."""
    values = (
        deployment.model_dump(mode="json", exclude_unset=True)
        if isinstance(deployment, ScoutSettings)
        else dict(deployment)
    )
    rows: list[dict[str, Any]] = [
        {"scope": "deployment", "data": {"scout": values}, "generation": 1, "updated_at": None}
    ]
    for owner, layer in (organizations or {}).items():
        rows.append(
            {"scope": f"organization:{owner}", "data": {"scout": dict(layer)}, "generation": 1, "updated_at": None}
        )
    return build_snapshot(CATALOG, rows)


def scout_read(
    deployment: Mapping[str, Any] | ScoutSettings,
    organizations: Mapping[str, Mapping[str, Any]] | None = None,
) -> Scoped[ScoutSettings]:
    """The ``scout`` section of :func:`scout_snapshot`, as a loop built with ``scoped_reader`` reads it."""
    return scout_snapshot(deployment, organizations).scoped(SCOUT)


def scout_enrollment(
    deployment: bool = True, layers: Mapping[str, Mapping[str, Any]] | None = None, *, settled: bool = True
) -> Enrollment:
    """``background.scout`` as a tick reads it: the deployment's value and each layer's ``background``
    values (stored scope string to them), with the enrollment import run unless ``settled`` is False."""
    rows: list[dict[str, Any]] = [
        {
            "scope": "deployment",
            "data": {"background": {"scout": deployment}},
            "generation": 1,
            "updated_at": None,
            "bootstrapped_keys": [ledger_key("scout")] if settled else [],
        }
    ]
    for scope, values in (layers or {}).items():
        rows.append({"scope": scope, "data": {"background": dict(values)}, "generation": 1, "updated_at": None})
    return enrollment(build_snapshot(CATALOG, rows))
