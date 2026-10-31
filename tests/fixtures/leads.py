"""The ``leads`` section of the settings document as its readers take it, from documents held in memory.

Built through the real snapshot, so a test reads its values the way the loop
and the API do: the deployment's document over the defaults, then an
organization's layer for the keys the section lets it set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from embry0.settings.registry import Section
from embry0.settings.scope import DEPLOYMENT, Scope
from embry0.settings.sections import CATALOG
from embry0.settings.store import Scoped, Snapshot, build_snapshot


def leads_section() -> Section[Any]:
    """The application's ``leads`` section, taken from the catalog by name, so a test fails with this
    message, not an import error, on a tree that has none."""
    section = CATALOG.get("leads")
    assert section is not None, "leads is a section of the settings document"
    return section


def _rows(deployment: Mapping[str, Any], organizations: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [{"scope": DEPLOYMENT, "data": {"leads": dict(deployment)}, "generation": 1, "updated_at": None}]
    for owner, values in organizations.items():
        scope = str(Scope.organization(owner))
        rows.append({"scope": scope, "data": {"leads": dict(values)}, "generation": 1, "updated_at": None})
    return rows


def leads_snapshot(
    deployment: Mapping[str, Any] | None = None, organizations: Mapping[str, Mapping[str, Any]] | None = None
) -> Snapshot:
    """A snapshot whose deployment document holds ``leads: deployment``, and each organization ``leads: values``.

    A value the section refuses fails the test here: what a test writes is what its readers must read.
    """
    leads_section()
    snapshot = build_snapshot(CATALOG, _rows(deployment or {}, organizations or {}), token=(), log=False)
    assert not snapshot.sections.refused, f"a test writes only values the section accepts: {snapshot.sections.refused}"
    return snapshot


def leads_policy(
    deployment: Mapping[str, Any] | None = None, organizations: Mapping[str, Mapping[str, Any]] | None = None
) -> Scoped[Any]:
    """The ``leads`` section of :func:`leads_snapshot`, read per repository."""
    return leads_snapshot(deployment, organizations).scoped(leads_section())


class MemorySettings:
    """The settings store as its readers use it: ``snapshot()`` of documents a test changes in place."""

    def __init__(
        self, deployment: Mapping[str, Any] | None = None, organizations: Mapping[str, Mapping[str, Any]] | None = None
    ) -> None:
        self.deployment = dict(deployment or {})
        self.organizations = {owner: dict(values) for owner, values in (organizations or {}).items()}
        self.reads = 0

    async def snapshot(self) -> Snapshot:
        self.reads += 1
        return leads_snapshot(self.deployment, self.organizations)

    async def leads(self) -> Scoped[Any]:
        """What ``scoped_reader(store, LEADS)`` returns: the section of the snapshot read now."""
        return (await self.snapshot()).scoped(leads_section())


def brief_reader(models: Any, leads: MemorySettings | None = None) -> Any:
    """The proposal brief writer's settings reader for a test: ``models()`` as its model lookup and the
    ``leads`` section of ``leads`` (else the defaults), both read at each call, as one snapshot holds them."""
    from embry0.services import proposal_briefs

    settings_type = getattr(proposal_briefs, "BriefSettings", None)
    assert settings_type is not None, "the proposal brief writer reads its settings from one snapshot per tick"
    source = leads or MemorySettings()

    async def read() -> Any:
        return settings_type(models=await models(), leads=await source.leads())

    return read
