"""The budget every reader enforces: the settings document's ``budget`` section, and the old readers until it is imported.

The dispatch gate, every job's cap, the context audit and the routes that
start unattended jobs read the budget through :class:`BudgetPolicy`, never
the section alone. A key of the section that the import at startup has not
imported yet is read as the release before read it, never as a code default
it did not have:

- a deployment key whose ``budget.<key>`` is not in the deployment
  document's import ledger reads the saved ``budget_config`` row when one was
  ever saved (the fleet limits), else its variable, else its default. A key
  written into the document since wins, as it would win over the import;
- an organization's audit caps read its ``organizations.settings``
  (:data:`embry0.services.organizations.MOVED_SETTINGS`) until its layer's
  ledger names them, unless its layer sets them already.

That happens when the import failed, as after a database that dropped the
connection at the first start of the release. Every read that finds a key
not imported tries the import again in this process, at most once every
:data:`RETRY_SECONDS`, and logs at error level until it succeeds. When no
snapshot of the documents can be read at all, every key is read the old way.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, cast

import structlog

from embry0.settings.bootstrap import EarlierSource, env_value, import_env_once
from embry0.settings.document import mapping
from embry0.settings.effective import NotImported
from embry0.settings.env import env_name
from embry0.settings.org_import import Moved, import_org_settings_once
from embry0.settings.registry import SectionRefused
from embry0.settings.scope import Scope, ScopeError
from embry0.settings.sections.budget import BUDGET, BudgetSettings
from embry0.settings.store import SettingsStore, Snapshot
from embry0.storage.repositories.budget_config import LABEL, read_row

logger = structlog.get_logger(__name__)

#: The least time between two attempts to import what the first start did not.
RETRY_SECONDS: Final = 30.0


class _Organization(Protocol):
    owner: str
    settings: Mapping[str, Any]


def _path(key: str) -> str:
    return f"{BUDGET.name}.{key}"


@dataclass(frozen=True)
class BudgetView:
    """The budget at one moment: one snapshot of the documents, and what the old readers read for each key
    of it not imported yet. ``snapshot`` None: no snapshot could be read, so ``legacy`` holds every key."""

    snapshot: Snapshot | None
    #: The deployment's keys not imported yet, as the release before read them.
    legacy: Mapping[str, Any] = field(default_factory=dict)
    #: The organization settings the section took over, read from the organization until its layer imported them.
    moved: Sequence[Moved] = ()
    #: Where each key of ``legacy`` was read from: ``row`` (the saved row), ``variable`` or ``default``.
    origins: Mapping[str, str] = field(default_factory=dict)

    def deployment(self) -> BudgetSettings:
        """The deployment's budget: the dispatch gate's limits and every job's ceiling."""
        base = self.snapshot.get(BUDGET) if self.snapshot is not None else BUDGET.defaults()
        return base.model_copy(update=dict(self.legacy)) if self.legacy else base

    def organization(self, owner: str, settings: Mapping[str, Any] | None = None) -> BudgetSettings:
        """The budget as the organization ``owner`` reads it; ``settings`` is its ``organizations.settings``,
        read for each moved key its layer has not imported yet."""
        if self.snapshot is None:
            return self._with_moved(self.deployment(), set(), owner, settings)
        try:
            scope: Scope | None = Scope.organization(owner)
        except ScopeError:
            scope = None
        resolved = self.snapshot.resolve(BUDGET, owner=owner)
        own = set(resolved.applied.get(str(scope), {})) if scope is not None else set()
        updates = {key: value for key, value in self.legacy.items() if key not in own}
        base = cast(BudgetSettings, resolved.model)
        model = base.model_copy(update=updates) if updates else base
        return self._with_moved(model, own, owner, settings, scope)

    def repository(self, repo: str) -> BudgetSettings:
        """The budget as the repository ``repo`` reads it, its organization's layer and its own over the deployment's."""
        if self.snapshot is None:
            return self.deployment()
        resolved = self.snapshot.resolve(BUDGET, repo=repo)
        own = {key for values in resolved.applied.values() for key in values}
        updates = {key: value for key, value in self.legacy.items() if key not in own}
        base = cast(BudgetSettings, resolved.model)
        return base.model_copy(update=updates) if updates else base

    def not_imported(
        self, owner: str | None = None, settings: Mapping[str, Any] | None = None
    ) -> dict[str, dict[tuple[str, str], NotImported]]:
        """Each key read the old way, by the scope of the document that has not imported it, for
        :func:`embry0.settings.effective.effective_view`: the deployment's keys, and with ``owner`` and its
        ``organizations.settings``, the moved settings its layer has not imported and does not set."""
        imports = BUDGET.imports()
        deployment: dict[tuple[str, str], NotImported] = {}
        for key, value in self.legacy.items():
            origin = self.origins.get(key, "default")
            imported = imports.get(key)
            if origin == "row":
                label = LABEL
            elif origin == "variable" and imported is not None:
                label = env_name(imported.attr)
            else:
                label = "the default"
            deployment[(BUDGET.name, key)] = NotImported(value=value, read_from=origin, label=label)
        out: dict[str, dict[tuple[str, str], NotImported]] = {"deployment": deployment} if deployment else {}
        if owner is None or settings is None:
            return out
        try:
            scope = Scope.organization(owner)
        except ScopeError:
            return out
        own: set[str] = set()
        if self.snapshot is not None:
            own = set(self.snapshot.resolve(BUDGET, owner=owner).applied.get(str(scope), {}))
        pending = self._moved_values(own, settings, scope)
        if pending:
            out[str(scope)] = {
                (BUDGET.name, key): NotImported(
                    value=value, read_from="organization", label=f"{scope.name}'s own settings"
                )
                for key, value in pending.items()
            }
        return out

    def _moved_values(self, own: set[str], settings: Mapping[str, Any], scope: Scope | None) -> dict[str, Any]:
        """The moved settings of an organization read from its ``settings``: not set in its layer, and not
        imported into it yet."""
        values: dict[str, Any] = {}
        for moved in self.moved:
            if moved.section != BUDGET.name or moved.key in own:
                continue
            path = f"{moved.section}.{moved.key}"
            if self.snapshot is not None and scope is not None and self.snapshot.imported(path, scope):
                continue
            value = moved.read(settings)
            if value is not None:
                values[moved.key] = value
        return values

    def _with_moved(
        self,
        model: BudgetSettings,
        own: set[str],
        owner: str,
        settings: Mapping[str, Any] | None,
        scope: Scope | None = None,
    ) -> BudgetSettings:
        if settings is None or not self.moved:
            return model
        updates = self._moved_values(own, settings, scope)
        return model.model_copy(update=updates) if updates else model


class BudgetPolicy:
    """The budget as every reader enforces it: see the module docstring.

    ``earlier`` and ``moved`` are what the import at startup reads (the
    application's :data:`embry0.settings.earlier.EARLIER_SOURCES` and
    :data:`~embry0.services.organizations.MOVED_SETTINGS`). ``retry_seconds``
    None never tries the import again: a policy a caller builds for itself,
    where the application's own one retries.
    """

    def __init__(
        self,
        store: SettingsStore,
        config: Any,
        *,
        earlier: Sequence[EarlierSource] = (),
        moved: Sequence[Moved] = (),
        retry_seconds: float | None = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._config = config
        self._earlier = tuple(earlier)
        self._moved = tuple(moved)
        self._retry_seconds = retry_seconds
        self._clock = clock
        # The first read that finds something not imported tries at once.
        self._next_try: float | None = None
        self._lock = asyncio.Lock()

    @property
    def store(self) -> SettingsStore:
        return self._store

    async def settings(self) -> BudgetSettings:
        """The deployment's budget now, for the dispatch gate and every job's cap. Raises what reading the
        saved row raises when no snapshot can be read either, so the gate's rule for a database that is down
        applies."""
        return (await self.view()).deployment()

    async def view(self, organizations: Iterable[_Organization] = ()) -> BudgetView:
        """The budget now; ``organizations`` are the ones the caller reads it for, so a moved setting of one
        whose layer has not imported it is imported again too."""
        orgs = list(organizations)
        snapshot = await self._snapshot()
        if snapshot is not None and self._retry_seconds is not None:
            pending, waiting = self._pending(snapshot), self._waiting(snapshot, orgs)
            if pending or waiting:
                if await self._retry(pending, waiting):
                    snapshot = await self._snapshot() or snapshot
        keys = self._legacy_keys(snapshot)
        legacy, origins = await self._legacy(keys) if keys else ({}, {})
        return BudgetView(snapshot=snapshot, legacy=legacy, moved=self._moved, origins=origins)

    async def _snapshot(self) -> Snapshot | None:
        try:
            return await self._store.snapshot()
        except Exception:
            logger.error(
                "budget_settings_unreadable",
                exc_info=True,
                hint="no snapshot of the settings documents: every budget key is read as the release before read it",
            )
            return None

    def _pending(self, snapshot: Snapshot) -> list[str]:
        """The deployment's keys the import has not imported yet."""
        return [key for key in BUDGET.imports() if not snapshot.imported(_path(key))]

    def _waiting(self, snapshot: Snapshot, organizations: Sequence[_Organization]) -> list[_Organization]:
        """The organizations with a moved setting their layer has not imported yet."""
        out = []
        for org in organizations:
            try:
                scope = Scope.organization(org.owner)
            except ScopeError:
                continue
            if any(
                moved.section == BUDGET.name
                and moved.read(org.settings) is not None
                and not snapshot.imported(f"{moved.section}.{moved.key}", scope)
                for moved in self._moved
            ):
                out.append(org)
        return out

    def _legacy_keys(self, snapshot: Snapshot | None) -> list[str]:
        """The deployment's keys read the old way: not imported, and not written into the document since."""
        if snapshot is None:
            return list(BUDGET.imports())
        stored = mapping(snapshot.data.get(BUDGET.name))
        return [key for key in self._pending(snapshot) if key not in stored]

    async def _legacy(self, keys: Sequence[str]) -> tuple[dict[str, Any], dict[str, str]]:
        """Each of ``keys`` as the release before read it: the saved row, else the variable, else the default;
        and where each was read from (``row``, ``variable``, ``default``)."""
        row = await read_row(self._store.db, self._config)
        defaults = BUDGET.defaults().model_dump(mode="json")
        out: dict[str, Any] = {}
        origins: dict[str, str] = {}
        for key in keys:
            value: Any = None
            origin = "default"
            if row is not None and key in row:
                # A saved row won over the variables: its five keys never read one.
                try:
                    value = BUDGET.validate({key: row[key]}).model_dump(mode="json", exclude_unset=True)[key]
                    origin = "row"
                except SectionRefused:
                    value = None
            else:
                value = env_value(BUDGET, key, self._config)
                origin = "variable" if value is not None else "default"
            out[key] = defaults[key] if value is None else value
            origins[key] = origin if value is not None else "default"
        return out, origins

    async def _retry(self, pending: Sequence[str], waiting: Sequence[_Organization]) -> bool:
        """Try the import again, at most every ``retry_seconds``; True when an attempt was made."""
        assert self._retry_seconds is not None
        if self._next_try is not None and self._clock() < self._next_try:
            return False
        async with self._lock:
            if self._next_try is not None and self._clock() < self._next_try:
                return False
            self._next_try = self._clock() + self._retry_seconds
            logger.error(
                "settings_budget_not_imported",
                keys=[_path(key) for key in pending],
                organizations=[org.owner for org in waiting],
                hint=(
                    "read as the release before read them (the saved budget_config row, else the variables; an "
                    "organization's own settings) until the import succeeds; trying it again now"
                ),
            )
            try:
                if pending:
                    await import_env_once(self._store, self._config, earlier=self._earlier)
                if waiting:
                    result = await import_org_settings_once(
                        self._store,
                        [(org.owner, org.settings) for org in waiting],
                        self._moved,
                        audit_log_path=getattr(self._config, "audit_log_path", None),
                    )
                    if result.failed:
                        return True
            except Exception:
                logger.error("settings_budget_import_retry_failed", exc_info=True, retry_in_seconds=self._retry_seconds)
                return True
            logger.info(
                "settings_budget_import_retried",
                keys=[_path(key) for key in pending],
                organizations=[org.owner for org in waiting],
            )
            return True


def env_budget(config: Any) -> BudgetSettings:
    """The deployment's budget as its variables give it, for a reader that cannot read the database at all."""
    updates: dict[str, Any] = {}
    for key in BUDGET.imports():
        value = env_value(BUDGET, key, config)
        if value is not None:
            updates[key] = value
    base = BUDGET.defaults()
    return base.model_copy(update=updates) if updates else base
