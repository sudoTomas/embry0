"""Copy an organization's settings that moved into a section into its layer, once (spec 2026-10-29, section 9).

Before layers, an organization kept some policy in ``organizations.settings``
(``PATCH /orgs/{owner}``). When a feature's section takes such a key over, as
a key an organization may set in its own layer, every organization's value
is copied into its layer at the first start of that release, after the
environment import:

- only a value the old reader would have used: a key it read as the
  deployment's default (null, or out of the range it accepted, which it
  ignored without a word) is nothing to copy, since a layer that does not set
  the key inherits the deployment's, and leaves no layer behind;
- checked as the layer's own value, together with what the layer inherits.
  A value refused is not copied, and is logged (``settings_org_value_refused``);
- written into the layer's text under a comment naming where it came from,
  recorded as a revision with source ``bootstrap``, audited, and added to the
  layer's ``bootstrapped_keys``, in one transaction per organization holding
  that layer's lock.

A key is marked once the organization's value was copied, refused, or found
already set in the layer (written there since, which wins), so a value taken
out of the layer later is never copied back. Nothing to copy marks nothing.
One organization's failure is logged at error level and stops no other: its
transaction rolls back, nothing of it is marked, and the next attempt
(:class:`embry0.services.budget_policy.BudgetPolicy` tries again while it
runs) copies it; meanwhile its readers read its own settings. The old key is left in
``organizations.settings``, read by nothing: a rollback to the release before
the move finds it as it was.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

import structlog

from embry0.audit.helpers import emit_audit
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR
from embry0.settings.document import DocumentInvalid, check, mapping, with_section
from embry0.settings.scope import Scope, ScopeError
from embry0.settings.store import SettingsStore

logger = structlog.get_logger(__name__)


class Moved(Protocol):
    """A key of ``organizations.settings`` that a section's key took over
    (:class:`embry0.services.organizations.MovedSetting`)."""

    @property
    def legacy(self) -> str: ...

    @property
    def section(self) -> str: ...

    @property
    def key(self) -> str: ...

    @property
    def read(self) -> Callable[[Mapping[str, Any]], Any]: ...


@dataclass(frozen=True)
class OrgImportResult:
    #: Owner to section to the keys and values copied into its layer.
    imported: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: ``(owner, section.key, reason)`` of each value not copied because it was refused.
    refused: tuple[tuple[str, str, str], ...] = ()
    #: The organizations whose copy failed: nothing of theirs is marked, so they are tried again.
    failed: tuple[str, ...] = ()


async def import_org_settings_once(
    store: SettingsStore,
    organizations: Iterable[tuple[str, Mapping[str, Any]]],
    moved: Sequence[Moved],
    *,
    audit_log_path: Any = None,
    today: date | None = None,
) -> OrgImportResult:
    """Copy each organization's moved settings into its layer, once per key. See the module docstring."""
    day = (today or datetime.now(UTC).date()).isoformat()
    imported: dict[str, dict[str, dict[str, Any]]] = {}
    refused: list[tuple[str, str, str]] = []
    failed: list[str] = []
    for owner, settings in organizations:
        # What the old reader used: anything it read as the deployment's is nothing to copy.
        present = [m for m in moved if m.read(settings) is not None and store.catalog.get(m.section) is not None]
        if not present:
            continue
        try:
            scope = Scope.organization(owner)
        except ScopeError:
            logger.warning("settings_org_import_skipped", owner=owner, reason="not a name a layer can have")
            continue
        try:
            copied, not_copied = await _import_one(store, scope, settings, present, day)
        except Exception:
            # One organization's failure (its transaction rolled back, nothing of it marked) is its own: the
            # others go on, and its readers read its own settings until a retry copies them.
            logger.error("settings_org_import_failed", owner=scope.name, exc_info=True)
            failed.append(scope.name)
            continue
        refused.extend((owner, path, reason) for path, reason in not_copied)
        for path, reason in not_copied:
            logger.warning(
                "settings_org_value_refused",
                owner=scope.name,
                key=path,
                reason=reason,
                hint="not copied into the organization's layer: set it there with PATCH /api/v1/config/settings/"
                f"{path.split('.', 1)[0]}?owner={scope.name}",
            )
        if copied:
            imported[scope.name] = copied
            logger.info(
                "settings_org_imported",
                owner=scope.name,
                keys=sorted(f"{name}.{key}" for name, values in copied.items() for key in values),
            )
            await emit_audit(
                store.db,
                "settings.bootstrapped",
                actor=BOOTSTRAP_ACTOR,
                details={"scope": str(scope), "sections": {name: sorted(values) for name, values in copied.items()}},
                audit_log_path=audit_log_path,
            )
    return OrgImportResult(imported=imported, refused=tuple(refused), failed=tuple(failed))


async def _import_one(
    store: SettingsStore, scope: Scope, settings: Mapping[str, Any], present: Sequence[Moved], day: str
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    """One organization's copy, in its own transaction: what was copied, and what was refused and why."""
    copied: dict[str, dict[str, Any]] = {}
    not_copied: list[tuple[str, str]] = []
    async with store.edit(
        actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="imported from the organization's settings", scope=scope
    ) as draft:
        todo = [m for m in present if f"{m.section}.{m.key}" not in draft.bootstrapped]
        if not todo:
            return copied, not_copied
        inherited = await store.inherited_now(draft)
        text, data = draft.yaml_text, dict(draft.data)
        marked: list[str] = []
        for name in dict.fromkeys(m.section for m in todo):
            stored = mapping(data.get(name))
            values: dict[str, Any] = {}
            legacy_of: dict[str, str] = {}
            for m in (m for m in todo if m.section == name):
                marked.append(f"{name}.{m.key}")
                if m.key in stored:
                    continue  # set in the layer since: that wins
                values[m.key] = m.read(settings)
                legacy_of[m.key] = m.legacy
            while values:
                try:
                    checked = check(
                        store.catalog,
                        {**data, name: {**stored, **values}},
                        stored=data,
                        stored_text=text,
                        layer=scope.layer,
                        inherited=inherited,
                    )
                except DocumentInvalid as exc:
                    blamed = {str(p.loc[1]) for p in exc.problems if len(p.loc) > 1 and str(p.loc[1]) in values}
                    for key in sorted(blamed or values):
                        reason = "; ".join(p.msg for p in exc.problems if len(p.loc) < 2 or str(p.loc[1]) == key)
                        not_copied.append((f"{name}.{key}", reason or "refused"))
                        values.pop(key, None)
                    continue
                written = mapping(checked.data.get(name))
                values = {key: written[key] for key in values}
                comment = f"Imported from the organization's settings on {day}: " + ", ".join(
                    legacy_of[key] for key in values
                )
                text = with_section(text, data, name, values, comment + ".")
                data = {**data, name: {**stored, **values}}
                copied[name] = values
                break
        if copied:
            draft.replace(text, data, draft.schema_version)
        draft.mark_bootstrapped(marked)
    return copied, not_copied
