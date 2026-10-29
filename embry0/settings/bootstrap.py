"""Import a section's environment values into the settings document, once per key.

Before a feature's policy moved into the document, it was read from
environment variables. At every boot, after migrations and before any loop
starts, :func:`import_env_once` takes each key a section declares in ``env``
that is not yet in the document's ``bootstrapped_keys`` ledger
(``section.key``), and:

- imports the variable when an operator set it (in the environment or
  ``.env``) and its value differs from the key's default. A code default is
  never imported: a section may change a default on purpose, and the old one
  must not be frozen into every existing document;
- skips a value the section refuses, whatever its check raises, with a
  warning, and goes on: a bad variable never stops a boot. The value is
  checked against the stored values this release still accepts, so a stored
  value it refuses never blocks the import. A value accepted on its own but
  refused together with the section's other values is held: its key is not
  marked, and the next boot tries it again;
- appends what it imported to the text under a comment naming the variables
  and the date, records one revision with source ``bootstrap``, and writes a
  ``settings.bootstrapped`` audit row through the store's own pool;
- adds the key to the ledger whether or not anything was imported (a held
  key aside), so it never runs again for that key.

A document kept in a file (``managed_by: file``) is written all the same,
since the variable is ignored from then on, but the import goes into the
stored document, not into the operator's file: the next ``embry0 settings
apply`` of the file would put each key back to its default. So it warns
(``settings_env_imported_into_file_document``), naming each key to copy into
the file.

The ledger is per key, so a key a later release adds to a section that was
imported before still gets its variable. All of it runs in one transaction
holding the document's row lock, so two processes booting together import
once. After that, a variable that is still set is ignored:
:func:`log_ignored_env` says so at every boot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog

from embry0.audit.helpers import emit_audit
from embry0.orchestration import dispatch_actors
from embry0.settings.document import mapping, read_sections, with_section
from embry0.settings.env import GovernedVariable, env_name, governed_variables, is_set
from embry0.settings.registry import MANAGED_BY_FILE, MANAGED_BY_KEY, Catalog, EnvImport, Section, SectionRefused
from embry0.settings.store import SettingsStore

logger = structlog.get_logger(__name__)

#: The actor of every revision and audit row the import writes.
BOOTSTRAP_ACTOR: Final = dispatch_actors.SETTINGS_BOOTSTRAP


@dataclass(frozen=True)
class RefusedImport:
    section: str
    key: str
    variable: str
    reason: str


@dataclass(frozen=True)
class BootstrapResult:
    #: Section name to the keys and values imported into it.
    imported: dict[str, dict[str, Any]] = field(default_factory=dict)
    refused: tuple[RefusedImport, ...] = ()
    #: Values accepted on their own and refused with the section's other values: not marked, tried again next boot.
    held: tuple[RefusedImport, ...] = ()
    #: The keys (``section.key``) added to the ledger by this run.
    marked: tuple[str, ...] = ()
    revision_id: int | None = None


def _reason(exc: SectionRefused) -> str:
    return "; ".join(str(e.get("msg") or "invalid").removeprefix("Value error, ") for e in exc.errors)


def _env_value(section: Section[Any], key: str, imported: EnvImport, config: Any) -> Any:
    """``key``'s value from ``config``, as the section stores it; :class:`SectionRefused` when refused."""
    try:
        value = getattr(config, imported.attr)
        if imported.convert is not None:
            value = imported.convert(value)
    except Exception as exc:  # noqa: BLE001 - a converter that raised is a refused value
        raise SectionRefused([{"loc": (key,), "msg": f"its conversion failed ({type(exc).__name__})"}]) from None
    return section.validate({key: value}).model_dump(mode="json", exclude_unset=True)[key]


def _still_accepted(section: Section[Any], stored: dict[str, Any]) -> dict[str, Any]:
    """The section's stored values this release still accepts; one it refuses reads as its default,
    and must not stand in the way of a value the import brings."""
    known = {k: v for k, v in stored.items() if k in section.model.model_fields}
    refused = {r.key for r in read_sections(Catalog((section,)), {section.name: known}).refused}
    return {} if "" in refused else {k: v for k, v in known.items() if k not in refused}


def _env_values(
    section: Section[Any],
    keys: list[str],
    config: Any,
    stored: dict[str, Any],
    refused: list[RefusedImport],
    held: list[RefusedImport],
) -> dict[str, Any]:
    """The values of ``keys`` that ``config`` was given, differ from their defaults and the section accepts.

    A value the section refuses on its own goes to ``refused``: it is marked
    imported and never read again. A value accepted on its own but refused
    together with the section's other values goes to ``held``: its key is not
    marked, so the next boot tries it again, once the other values may have
    changed.
    """
    defaults = section.defaults().model_dump(mode="json")
    imports = section.imports()
    accepted: dict[str, Any] = {}
    for key in keys:
        if key in stored or not is_set(config, imports[key].attr):
            continue
        try:
            clean = _env_value(section, key, imports[key], config)
        except SectionRefused as exc:
            refused.append(RefusedImport(section.name, key, env_name(imports[key].attr), _reason(exc)))
            continue
        if clean != defaults.get(key):
            accepted[key] = clean
    base = _still_accepted(section, stored) if accepted else {}
    while accepted:
        try:
            section.validate({**base, **accepted})
            break
        except SectionRefused as exc:
            named = {str(e["loc"][0]) for e in exc.errors if e.get("loc") and str(e["loc"][0]) in accepted}
            blamed = named or set(accepted)
            reason = _reason(exc)
            held.extend(RefusedImport(section.name, key, env_name(imports[key].attr), reason) for key in sorted(blamed))
            accepted = {k: v for k, v in accepted.items() if k not in blamed}
    return accepted


@dataclass(frozen=True)
class EnvValues:
    """What the environment says now, section by section, as the import would read it into an empty document."""

    #: Section name to the keys and values the environment holds for it.
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    refused: tuple[RefusedImport, ...] = ()
    #: Values accepted on their own and refused together with the section's other values.
    held: tuple[RefusedImport, ...] = ()

    def variables(self, catalog: Catalog, section: str) -> list[str]:
        """The variables the values of ``section`` were read from, in the section's order."""
        found = catalog.get(section)
        if found is None:
            return []
        imports = found.imports()
        return [env_name(imports[key].attr) for key in self.values.get(section, {}) if key in imports]


def env_values(catalog: Catalog, config: Any) -> EnvValues:
    """Each section key's value from ``config``, by the import's own rules, storing nothing.

    The rules of :func:`import_env_once`: only a variable an operator set,
    whose value differs from its key's default and which the section
    accepts. Neither the document nor the import's ledger is read, so this
    is what the environment says now, whatever was imported before: what
    ``embry0 settings import-env`` writes out.
    """
    values: dict[str, dict[str, Any]] = {}
    refused: list[RefusedImport] = []
    held: list[RefusedImport] = []
    for section in catalog:
        found = _env_values(section, list(section.imports()), config, {}, refused, held)
        if found:
            values[section.name] = found
    return EnvValues(values=values, refused=tuple(refused), held=tuple(held))


async def import_env_once(store: SettingsStore, config: Any, *, today: date | None = None) -> BootstrapResult:
    """Import the environment value of every section key not imported yet. See the module docstring."""
    pending = [f"{section.name}.{key}" for section in store.catalog for key in section.imports()]
    if not pending:
        return BootstrapResult()
    day = (today or datetime.now(UTC).date()).isoformat()
    imported: dict[str, dict[str, Any]] = {}
    refused: list[RefusedImport] = []
    held: list[RefusedImport] = []
    async with store.edit(actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="imported from the environment") as draft:
        todo = [path for path in pending if path not in draft.bootstrapped]
        text, data = draft.yaml_text, dict(draft.data)
        for section in store.catalog:
            keys = [key for key in section.imports() if f"{section.name}.{key}" in todo]
            if not keys:
                continue
            stored = mapping(data.get(section.name))
            values = _env_values(section, keys, config, stored, refused, held)
            if not values:
                continue
            variables = ", ".join(env_name(section.imports()[key].attr) for key in values)
            comment = f"Imported from the environment on {day}: {variables}."
            text = with_section(text, data, section.name, values, comment)
            data = {**data, section.name: {**stored, **values}}
            imported[section.name] = values
        in_file = data.get(MANAGED_BY_KEY) == MANAGED_BY_FILE
        if imported:
            # Each value was checked as its section when it was read; the rest
            # of the document is what is stored, checked when it was written.
            draft.replace(text, data, draft.schema_version)
        waiting = {f"{item.section}.{item.key}" for item in held}
        marked = [path for path in todo if path not in waiting]
        draft.mark_bootstrapped(marked)
    for item in refused:
        logger.warning(
            "settings_bootstrap_value_refused",
            variable=item.variable,
            key=f"{item.section}.{item.key}",
            reason=item.reason,
            hint="not imported, and not read again: set it in the settings document",
        )
    for item in held:
        logger.warning(
            "settings_bootstrap_value_held",
            variable=item.variable,
            key=f"{item.section}.{item.key}",
            reason=item.reason,
            hint="refused together with the section's other values; tried again at the next start",
        )
    for name, values in imported.items():
        logger.info("settings_env_imported", section=name, keys=sorted(values), revision_id=draft.revision_id)
    if imported and in_file:
        logger.warning(
            "settings_env_imported_into_file_document",
            keys=[f"{name}.{key}" for name, values in imported.items() for key in values],
            revision_id=draft.revision_id,
            hint=(
                "the settings document is kept in a file (managed_by: file): copy these keys into it, as the "
                "revision shows them, or the next `embry0 settings apply` of the file puts them back to their defaults"
            ),
        )
    if imported:
        await emit_audit(
            store.db,
            "settings.bootstrapped",
            actor=BOOTSTRAP_ACTOR,
            details={
                "sections": {name: sorted(values) for name, values in imported.items()},
                "revision_id": draft.revision_id,
            },
            audit_log_path=getattr(config, "audit_log_path", None),
        )
    return BootstrapResult(
        imported=imported,
        refused=tuple(refused),
        held=tuple(held),
        marked=tuple(marked),
        revision_id=draft.revision_id,
    )


async def log_ignored_env(store: SettingsStore, config: Any) -> list[GovernedVariable]:
    """Warn once per boot about each variable that is set while the document governs it.

    Only for keys already imported: until then the variable is still the
    import's source. ``matches`` says whether the variable's value is what
    loops read now; the value itself is not logged.
    """
    document = await store.document()
    snapshot = await store.snapshot()
    done = set(document.get("bootstrapped_keys") or [])
    ignored = [v for v in governed_variables(store.catalog, config) if v.path in done]
    for variable in ignored:
        section = store.catalog.get(variable.section)
        matches: bool | None = None
        if section is not None:
            effective = snapshot.get(section).model_dump(mode="json").get(variable.key)
            try:
                matches = _env_value(section, variable.key, section.imports()[variable.key], config) == effective
            except SectionRefused:
                matches = False
        logger.warning(
            "settings_env_ignored",
            variable=variable.name,
            key=variable.path,
            matches=matches,
            hint="the settings document governs this; edit it with PUT /api/v1/config/settings",
        )
    return ignored
