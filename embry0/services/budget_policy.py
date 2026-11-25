"""The budget every reader enforces: the settings document's ``budget`` section, and an organization's moved settings.

The dispatch gate, every job's cap, the context audit and the routes that
start unattended jobs read the budget through :class:`BudgetPolicy`, never
the section alone. An organization's audit caps read its
``organizations.settings`` (:data:`embry0.services.organizations.MOVED_SETTINGS`)
until its layer's ledger names them, unless its layer sets them already. The
same holds for every organization setting another section took over
(``context_audit.days`` and ``open_prs``, #170): :meth:`BudgetView.moved_values`
answers what such a section's reader reads from the organization meanwhile,
and the retry below copies them with the budget's.

That happens when the copy failed, as after a database that dropped the
connection at the first start of the release. Every read that finds an
organization's setting not copied tries again in this process, at most once
every :data:`RETRY_SECONDS`, and logs at error level until it succeeds. A
read that finds no snapshot of the documents raises: the budget is what the
operator set, never a default in its place.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

import structlog

from embry0.settings.effective import NotImported
from embry0.settings.org_import import Moved, import_org_settings_once
from embry0.settings.scope import Scope, ScopeError
from embry0.settings.sections.budget import BUDGET, BudgetSettings
from embry0.settings.store import SettingsStore, Snapshot

logger = structlog.get_logger(__name__)

#: The least time between two attempts to copy what the first start did not.
RETRY_SECONDS: Final = 30.0


class _Organization(Protocol):
    owner: str
    settings: Mapping[str, Any]


@dataclass
class _Owner:
    owner: str
    settings: Mapping[str, Any]


@dataclass(frozen=True)
class BudgetView:
    """The budget at one moment: one snapshot of the documents. ``snapshot`` None reads every key at its
    default (a caller's own view; :class:`BudgetPolicy` never builds one)."""

    snapshot: Snapshot | None
    #: The organization settings the section took over, read from the organization until its layer imported them.
    moved: Sequence[Moved] = ()

    def deployment(self) -> BudgetSettings:
        """The deployment's budget: the dispatch gate's limits and every job's ceiling."""
        return self.snapshot.get(BUDGET) if self.snapshot is not None else BUDGET.defaults()

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
        return self._with_moved(cast(BudgetSettings, resolved.model), own, owner, settings, scope)

    def repository(self, repo: str) -> BudgetSettings:
        """The budget as the repository ``repo`` reads it, its organization's layer and its own over the deployment's."""
        if self.snapshot is None:
            return self.deployment()
        return cast(BudgetSettings, self.snapshot.resolve(BUDGET, repo=repo).model)

    def not_imported(
        self, owner: str | None = None, settings: Mapping[str, Any] | None = None
    ) -> dict[str, dict[tuple[str, str], NotImported]]:
        """Each setting read from the organization ``owner``'s ``organizations.settings``, by the scope of its
        layer, for :func:`embry0.settings.effective.effective_view`: the moved settings its layer has not imported
        and does not set. Empty without ``owner`` and its ``settings``."""
        out: dict[str, dict[tuple[str, str], NotImported]] = {}
        if owner is None or settings is None:
            return out
        try:
            scope = Scope.organization(owner)
        except ScopeError:
            return out
        org_pending: dict[tuple[str, str], NotImported] = {}
        for name in dict.fromkeys(m.section for m in self.moved):
            for key, value in self.moved_values(name, owner, settings).items():
                org_pending[(name, key)] = NotImported(
                    value=value, read_from="organization", label=f"{scope.name}'s own settings"
                )
        if org_pending:
            out[str(scope)] = org_pending
        return out

    def moved_values(self, section: str, owner: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        """The settings of ``section`` that moved off the organization row and that ``owner`` still reads from its
        ``settings``: its layer has not imported them, and does not set them. Empty once every one is imported, as
        it is after the first start of the release that moved them."""
        try:
            scope: Scope | None = Scope.organization(owner)
        except ScopeError:
            scope = None
        own: set[str] = set()
        if self.snapshot is not None and scope is not None:
            found = self.snapshot.catalog.get(section) if self.snapshot.catalog is not None else None
            if found is not None:
                own = set(self.snapshot.resolve(found, owner=owner).applied.get(str(scope), {}))
        return self._moved_values(own, settings, scope, section)

    def _moved_values(
        self, own: set[str], settings: Mapping[str, Any], scope: Scope | None, section: str = BUDGET.name
    ) -> dict[str, Any]:
        """The moved settings of ``section`` an organization reads from its ``settings``: not set in its layer,
        and not imported into it yet."""
        values: dict[str, Any] = {}
        for moved in self.moved:
            if moved.section != section or moved.key in own:
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

    ``moved`` is what the copy at startup reads
    (:data:`~embry0.services.organizations.MOVED_SETTINGS`). ``retry_seconds``
    None never tries the copy again: a policy a caller builds for itself,
    where the application's own one retries.
    """

    def __init__(
        self,
        store: SettingsStore,
        config: Any,
        *,
        moved: Sequence[Moved] = (),
        retry_seconds: float | None = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._config = config
        self._moved = tuple(moved)
        self._retry_seconds = retry_seconds
        self._clock = clock
        # The first read that finds something not copied tries at once.
        self._next_try: float | None = None
        self._lock = asyncio.Lock()

    @property
    def store(self) -> SettingsStore:
        return self._store

    async def settings(self) -> BudgetSettings:
        """The deployment's budget now, for the dispatch gate and every job's cap. Raises what reading the
        documents raises, so the gate's rule for a database that is down applies."""
        return (await self.view()).deployment()

    async def view(self, organizations: Iterable[_Organization] = ()) -> BudgetView:
        """The budget now; ``organizations`` are the ones the caller reads it for, so a moved setting of one
        whose layer has not imported it is imported again too."""
        orgs = list(organizations)
        snapshot = await self._store.snapshot()
        if self._retry_seconds is not None:
            waiting = self._waiting(snapshot, orgs)
            if waiting and await self._retry(waiting):
                snapshot = await self._store.snapshot()
        return BudgetView(snapshot=snapshot, moved=self._moved)

    async def moved_values_at(
        self, snapshot: Snapshot | None, section: str, owner: str, settings: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The moved settings of ``section`` that ``owner`` reads from its ``settings`` as of ``snapshot``, a reader's
        own (#575). The organization's copy into its layer is tried again first when it is pending (:meth:`view`),
        but the answer is read against ``snapshot``: a reader whose snapshot predates that copy has the values in
        neither its snapshot's layer nor, read against the snapshot after the copy, the organization's row."""
        await self.view([_Owner(owner=owner, settings=settings)])
        return BudgetView(snapshot=snapshot, moved=self._moved).moved_values(section, owner, settings)

    def _waiting(self, snapshot: Snapshot, organizations: Sequence[_Organization]) -> list[_Organization]:
        """The organizations with a moved setting their layer has not imported yet."""
        out = []
        for org in organizations:
            try:
                scope = Scope.organization(org.owner)
            except ScopeError:
                continue
            # Every section's moved settings, the budget's and the context audit's: one copy takes them all.
            if any(
                moved.read(org.settings) is not None and not snapshot.imported(f"{moved.section}.{moved.key}", scope)
                for moved in self._moved
            ):
                out.append(org)
        return out

    async def _retry(self, waiting: Sequence[_Organization]) -> bool:
        """Try the copy again, at most every ``retry_seconds``; True when an attempt was made."""
        assert self._retry_seconds is not None
        if self._next_try is not None and self._clock() < self._next_try:
            return False
        async with self._lock:
            if self._next_try is not None and self._clock() < self._next_try:
                return False
            self._next_try = self._clock() + self._retry_seconds
            logger.error(
                "settings_budget_not_imported",
                organizations=[org.owner for org in waiting],
                hint="read from the organization's own settings until the copy succeeds; trying it again now",
            )
            try:
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
            logger.info("settings_budget_import_retried", organizations=[org.owner for org in waiting])
            return True
