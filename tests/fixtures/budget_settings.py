"""The settings document's ``budget`` section as a test hands it to a reader, with no database.

The snapshot is built by the store's own reading (:func:`embry0.settings.store.build_snapshot`),
so a value the section refuses reads as its default here as it does in production.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from embry0.services.budget_policy import BudgetView
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot


def budget_snapshot(deployment: Mapping[str, Any] | None = None, **orgs: Mapping[str, Any]) -> Snapshot:
    """A snapshot whose deployment document sets ``deployment`` in ``budget``, and each organization of
    ``orgs`` (by owner) its own layer's ``budget`` keys."""
    rows: list[dict[str, Any]] = [
        {"scope": "deployment", "data": {"budget": dict(deployment or {})}, "generation": 1, "updated_at": None}
    ]
    rows += [
        {"scope": f"organization:{owner}", "data": {"budget": dict(values)}, "generation": 1, "updated_at": None}
        for owner, values in orgs.items()
    ]
    return build_snapshot(CATALOG, rows, token=(), log=False)


def budget_reader(
    deployment: Mapping[str, Any] | None = None, **orgs: Mapping[str, Any]
) -> Callable[..., Awaitable[BudgetView]]:
    """A reader as :meth:`embry0.services.budget_policy.BudgetPolicy.view` is one, over :func:`budget_snapshot`,
    with every key imported."""
    snapshot = budget_snapshot(deployment, **orgs)

    async def read(organizations: Any = ()) -> BudgetView:
        return BudgetView(snapshot)

    return read


class BudgetStore:
    """A settings store as a route reads it (``app.state.settings``): only its snapshot, of these budget values."""

    def __init__(self, deployment: Mapping[str, Any] | None = None, **orgs: Mapping[str, Any]) -> None:
        self.deployment = dict(deployment or {})
        self.orgs = {owner: dict(values) for owner, values in orgs.items()}

    async def snapshot(self) -> Snapshot:
        return budget_snapshot(self.deployment, **self.orgs)
