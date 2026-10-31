"""Import a section's environment values into the settings document, once per key.

Before a feature's policy moved into the document, it was read from
environment variables. At every boot, after migrations and before any loop
starts, :func:`import_env_once` takes each key a section declares in ``env``
that is not yet in the document's ``bootstrapped_keys`` ledger
(``section.key``), and:

- imports the variable when an operator set it (in the environment or
  ``.env``) and its value differs from the key's default. A code default is
  never imported: a section may change a default on purpose, and the old one
  must not be frozen into every existing document. The one exception is a
  key whose section declares a value deployments relied on
  (``EnvImport.relied_on``): while the variable is not set, the section's
  check asks this deployment's records (``DeploymentHistory``, on the
  import's connection, in a savepoint) whether it existed before, and when
  it did, that value is imported under a comment saying why
  (``settings_default_kept``). Only records older than the moment this
  release first asked about the key count: the import records that moment
  once in the ledger (``section.key@<time>``, see :func:`_first_asked`), so
  a record made while a check waits for its retry, or before the next
  start, is never taken for the deployment's past. A check that raises
  holds its key, logs ``settings_bootstrap_check_failed`` at error level,
  and is asked again in the same run by :func:`settle_unchecked`, which the
  lifespan starts, and at the next boot if the run ends first;
- names in a warning each part of a variable the section's conversion
  leaves out, such as a name the key does not know
  (``settings_env_value_dropped``);
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

**Values kept in the database before the document.** Some keys were not
variables alone: the five fleet budget keys lived in the ``budget_config``
row, which won over their variables once someone saved it. An
:class:`EarlierSource` reads such a store inside the import's transaction.
When it holds a record, each key it answers is imported from it, by the same
rules (a value equal to its default is not written, a refused one is logged
and skipped), and never from its variable; when it holds none, those keys are
read from their variables as above. Either way the key is marked, so the
record is read once.

The ledger is per key, so a key a later release adds to a section that was
imported before still gets its variable. All of it runs in one transaction
holding the document's row lock, so two processes booting together import
once. After that, a variable that is still set is ignored:
:func:`log_ignored_env` says so at every boot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog

from embry0.audit.helpers import emit_audit
from embry0.orchestration import dispatch_actors
from embry0.settings.document import mapping, read_sections, with_section
from embry0.settings.env import GovernedVariable, env_name, governed_variables, is_set
from embry0.settings.registry import (
    MANAGED_BY_FILE,
    MANAGED_BY_KEY,
    Catalog,
    EnvImport,
    ReliedOn,
    Section,
    SectionRefused,
)
from embry0.settings.store import SettingsStore
from embry0.storage.repositories.deployment_history import DeploymentHistory

logger = structlog.get_logger(__name__)

#: The actor of every revision and audit row the import writes.
BOOTSTRAP_ACTOR: Final = dispatch_actors.SETTINGS_BOOTSTRAP
#: Seconds :func:`settle_unchecked` waits before each new try, the last repeated: a minute, doubling to fifteen.
SETTLE_DELAYS: Final[tuple[float, ...]] = (60.0, 120.0, 240.0, 480.0, 900.0)
#: Joins a ``ReliedOn`` key to the moment this release first asked about it, in a ledger entry:
#: ``leads.enabled@2026-10-30T12:00:00+00:00``. Never a key of its own: no key holds the character.
ASKED_AT: Final = "@"


@dataclass(frozen=True)
class RefusedImport:
    section: str
    key: str
    variable: str
    reason: str


@dataclass(frozen=True)
class EarlierSource:
    """Where some keys of one section were stored in the database before the settings document.

    ``read`` is given the import's own connection, inside its transaction,
    and the running config. It answers each key's value as that key's reader
    read it, already in the section's form, or None when nothing is stored
    there: those keys are then read from their variables. Each key it answers
    is imported from it and never from its variable.
    """

    section: str
    #: How the comment above the imported keys, the revision and the logs name it.
    label: str
    read: Callable[[Any, Any], Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True)
class DroppedParts:
    """Parts of a variable's value the section's conversion leaves out (``EnvImport.dropped``)."""

    section: str
    key: str
    variable: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class KeptValue:
    """A key whose variable is not set and whose section declares a value deployments relied on."""

    section: str
    key: str
    variable: str
    value: Any
    evidence: str


@dataclass(frozen=True)
class BootstrapResult:
    #: Section name to the keys and values imported into it.
    imported: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The part of ``imported`` read from an :class:`EarlierSource`, not from the environment.
    earlier: dict[str, dict[str, Any]] = field(default_factory=dict)
    refused: tuple[RefusedImport, ...] = ()
    #: Values accepted on their own and refused with the section's other values: not marked, tried again next boot.
    held: tuple[RefusedImport, ...] = ()
    #: The keys (``section.key``) added to the ledger by this run.
    marked: tuple[str, ...] = ()
    revision_id: int | None = None
    #: The imported keys (``section.key``) whose variable is not set: its default, which this deployment relied on.
    kept: tuple[str, ...] = ()
    #: Keys whose check of whether the deployment relied on the default raised: not marked, asked again.
    unchecked: tuple[RefusedImport, ...] = ()
    dropped: tuple[DroppedParts, ...] = ()


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


def env_value(section: Section[Any], key: str, config: Any) -> Any:
    """``key``'s value as the import would read it from ``config`` now, or None when its variable is not set
    or the section refuses it: what a reader of a key not imported yet reads in its place."""
    imported = section.imports().get(key)
    if imported is None or not is_set(config, imported.attr):
        return None
    try:
        return _env_value(section, key, imported, config)
    except SectionRefused:
        return None


def _still_accepted(section: Section[Any], stored: dict[str, Any]) -> dict[str, Any]:
    """The section's stored values this release still accepts; one it refuses reads as its default,
    and must not stand in the way of a value the import brings."""
    known = {k: v for k, v in stored.items() if k in section.model.model_fields}
    refused = {r.key for r in read_sections(Catalog((section,)), {section.name: known}).refused}
    return {} if "" in refused else {k: v for k, v in known.items() if k not in refused}


def _asked_at(bootstrapped: list[str], path: str) -> datetime | None:
    """The earliest moment the ledger records for ``path`` (``path@<time>``); None when it records none."""
    found: list[datetime] = []
    for entry in bootstrapped:
        key, sep, at = entry.partition(ASKED_AT)
        if sep and key == path:
            try:
                found.append(datetime.fromisoformat(at))
            except ValueError:
                continue
    return min(found) if found else None


async def _first_asked(store: SettingsStore, started: datetime | None) -> dict[str, datetime]:
    """When this release first asked about each ``ReliedOn`` key the ledger does not mark yet.

    The first time a key is asked, the moment is written to the ledger as
    ``section.key@<time>``: ``started``, when the process that asks began
    (the lifespan reads the database's clock right after connecting, before
    the migrations run), else the database's clock now. It is written in a
    transaction of its own, before the import's, so it stays even when the
    import then fails; every later try, in this run or after a restart,
    reads the earliest one. So only what the deployment held before this
    release first started counts as its past, and a new deployment, whose
    tables are created after that moment, can never show any.
    """
    relied = [
        f"{section.name}.{key}"
        for section in store.catalog
        for key, imported in section.imports().items()
        if imported.relied_on is not None
    ]
    if not relied:
        return {}
    found: dict[str, datetime] = {}
    async with store.edit(actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="first asked") as draft:
        now = started
        for path in relied:
            if path in draft.bootstrapped:
                continue
            at = _asked_at(draft.bootstrapped, path)
            if at is None:
                if now is None:
                    now = await draft.conn.fetchval("SELECT now()")
                at = now
                draft.mark_bootstrapped([f"{path}{ASKED_AT}{at.isoformat()}"])
            found[path] = at
    return found


async def _relied_on(
    section: Section[Any],
    keys: list[str],
    config: Any,
    stored: dict[str, Any],
    conn: Any,
    asked: dict[str, datetime],
    unchecked: list[RefusedImport],
) -> dict[str, ReliedOn]:
    """The keys among ``keys`` whose variable is not set and whose ``ReliedOn`` check finds this
    deployment relied on them, each with its declaration (:class:`~embry0.settings.registry.ReliedOn`).

    Each check reads a :class:`DeploymentHistory` of what the deployment held
    before the key was first asked about (``asked``, :func:`_first_asked`),
    on the import's own connection, in a savepoint of its transaction, so one
    that raises leaves the rest of the import standing; its key goes to
    ``unchecked``, is not marked, and is asked again (:func:`settle_unchecked`,
    then the next boot). A key the operator has set in the document is never
    asked: their value stands.
    """
    found: dict[str, ReliedOn] = {}
    imports = section.imports()
    for key in keys:
        relied = imports[key].relied_on
        if relied is None or key in stored or is_set(config, imports[key].attr):
            continue
        try:
            async with conn.transaction():
                before = asked.get(f"{section.name}.{key}") or await conn.fetchval("SELECT now()")
                answer = await relied.check(DeploymentHistory(conn, before=before))
        except Exception as exc:  # noqa: BLE001 - a failed check never stops a boot
            reason = f"the check whether this deployment relied on it failed ({type(exc).__name__})"
            unchecked.append(RefusedImport(section.name, key, env_name(imports[key].attr), reason))
            continue
        if answer:
            found[key] = relied
    return found


def _dropped_parts(imported: EnvImport, config: Any) -> tuple[str, ...]:
    """What ``imported.dropped`` names of the variable's value; nothing when it has none or it raises."""
    if imported.dropped is None:
        return ()
    try:
        return tuple(str(part) for part in imported.dropped(getattr(config, imported.attr)))
    except Exception:  # noqa: BLE001 - the conversion's own check reports a value it cannot read
        return ()


def _env_values(
    section: Section[Any],
    keys: list[str],
    config: Any,
    stored: dict[str, Any],
    refused: list[RefusedImport],
    held: list[RefusedImport],
    relied: Mapping[str, ReliedOn] | None = None,
    dropped: list[DroppedParts] | None = None,
) -> dict[str, Any]:
    """The values of ``keys`` that ``config`` was given, differ from their defaults and the section accepts.

    A key in ``relied`` counts as given although its variable is not set: its
    ``ReliedOn.value`` is the value (:func:`_relied_on`). A part of a set
    variable that the conversion drops goes to ``dropped``. A value the
    section refuses on its own goes to ``refused``: it is marked imported and
    never read again. A value accepted on its own but refused together with
    the section's other values goes to ``held``: its key is not marked, so
    the next boot tries it again, once the other values may have changed.
    """
    defaults = section.defaults().model_dump(mode="json")
    imports = section.imports()
    accepted: dict[str, Any] = {}
    for key in keys:
        if key in stored:
            continue
        imported = imports[key]
        kept = (relied or {}).get(key)
        try:
            if kept is not None:
                clean = section.validate({key: kept.value}).model_dump(mode="json", exclude_unset=True)[key]
            elif is_set(config, imported.attr):
                parts = _dropped_parts(imported, config)
                if parts and dropped is not None:
                    dropped.append(DroppedParts(section.name, key, env_name(imported.attr), parts))
                clean = _env_value(section, key, imported, config)
            else:
                continue
        except SectionRefused as exc:
            refused.append(RefusedImport(section.name, key, env_name(imported.attr), _reason(exc)))
            continue
        if clean != defaults.get(key):
            accepted[key] = clean
    return _together(section, accepted, stored, held, lambda key: env_name(imports[key].attr))


def _together(
    section: Section[Any],
    accepted: dict[str, Any],
    stored: dict[str, Any],
    held: list[RefusedImport],
    named: Callable[[str], str],
) -> dict[str, Any]:
    """Of ``accepted``, the values the section takes together with the stored values it still accepts; each
    key blamed for a refusal goes to ``held`` under the name ``named`` gives it."""
    base = _still_accepted(section, stored) if accepted else {}
    while accepted:
        try:
            section.validate({**base, **accepted})
            break
        except SectionRefused as exc:
            blamed_keys = {str(e["loc"][0]) for e in exc.errors if e.get("loc") and str(e["loc"][0]) in accepted}
            blamed = blamed_keys or set(accepted)
            reason = _reason(exc)
            held.extend(RefusedImport(section.name, key, named(key), reason) for key in sorted(blamed))
            accepted = {k: v for k, v in accepted.items() if k not in blamed}
    return accepted


def _earlier_values(
    section: Section[Any],
    source: EarlierSource,
    found: Mapping[str, Any],
    keys: list[str],
    stored: dict[str, Any],
    refused: list[RefusedImport],
    held: list[RefusedImport],
) -> dict[str, Any]:
    """The values of ``keys`` that ``found`` holds, differ from their defaults and the section accepts.

    The rules of :func:`_env_values`, for values an :class:`EarlierSource`
    read: a key already stored keeps its stored value, a value refused on its
    own is skipped and logged, and one refused only with the section's other
    values is held.
    """
    defaults = section.defaults().model_dump(mode="json")
    accepted: dict[str, Any] = {}
    for key in keys:
        if key in stored:
            continue
        try:
            clean = section.validate({key: found[key]}).model_dump(mode="json", exclude_unset=True)[key]
        except SectionRefused as exc:
            refused.append(RefusedImport(section.name, key, f"{source.label}: {key}", _reason(exc)))
            continue
        if clean != defaults.get(key):
            accepted[key] = clean
    return _together(section, accepted, stored, held, lambda key: f"{source.label}: {key}")


@dataclass(frozen=True)
class EnvValues:
    """What the environment says now, section by section, as the import would read it into an empty document."""

    #: Section name to the keys and values the environment holds for it.
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    refused: tuple[RefusedImport, ...] = ()
    #: Values accepted on their own and refused together with the section's other values.
    held: tuple[RefusedImport, ...] = ()
    #: Parts of a set variable the section's conversion leaves out.
    dropped: tuple[DroppedParts, ...] = ()
    #: Keys whose variable is not set and whose section declares a value a deployment that relied on it keeps:
    #: what the running deployment's first start of the release keeps, which this environment alone cannot say.
    kept: tuple[KeptValue, ...] = ()

    def variables(self, catalog: Catalog, section: str) -> list[str]:
        """The variables the values of ``section`` were read from, in the section's order, each named once."""
        found = catalog.get(section)
        if found is None:
            return []
        imports = found.imports()
        return list(
            dict.fromkeys(env_name(imports[key].attr) for key in self.values.get(section, {}) if key in imports)
        )


def env_values(catalog: Catalog, config: Any) -> EnvValues:
    """Each section key's value from ``config``, by the import's own rules, storing nothing.

    The rules of :func:`import_env_once`: only a variable an operator set,
    whose value differs from its key's default and which the section
    accepts. Neither the document nor the import's ledger is read, so this
    is what the environment says now, whatever was imported before: what
    ``embry0 settings import-env`` writes out. A key whose variable is not set
    and that declares a ``ReliedOn`` is listed in ``kept``: whether the
    running deployment keeps its value depends on its records, not on the
    environment.
    """
    values: dict[str, dict[str, Any]] = {}
    refused: list[RefusedImport] = []
    held: list[RefusedImport] = []
    dropped: list[DroppedParts] = []
    kept: list[KeptValue] = []
    for section in catalog:
        found = _env_values(section, list(section.imports()), config, {}, refused, held, dropped=dropped)
        if found:
            values[section.name] = found
        for key, imported in section.imports().items():
            relied = imported.relied_on
            if relied is not None and not is_set(config, imported.attr):
                variable = env_name(imported.attr)
                kept.append(KeptValue(section.name, key, variable, relied.value, relied.evidence))
    return EnvValues(values=values, refused=tuple(refused), held=tuple(held), dropped=tuple(dropped), kept=tuple(kept))


async def import_env_once(
    store: SettingsStore,
    config: Any,
    *,
    today: date | None = None,
    earlier: Sequence[EarlierSource] = (),
    started: datetime | None = None,
) -> BootstrapResult:
    """Import the environment value of every section key not imported yet, and what each of ``earlier``
    stored. See the module docstring.

    ``started`` is when this process began, by the database's clock (else the
    store's ``started``): the moment a ``ReliedOn`` key is recorded as first
    asked, when it has not been before (:func:`_first_asked`).
    """
    sources = {source.section: source for source in earlier if store.catalog.get(source.section) is not None}
    pending = [f"{section.name}.{key}" for section in store.catalog for key in section.imports()]
    if not pending and not sources:
        return BootstrapResult()
    asked = await _first_asked(store, started if started is not None else store.started)
    day = (today or datetime.now(UTC).date()).isoformat()
    imported: dict[str, dict[str, Any]] = {}
    from_earlier: dict[str, dict[str, Any]] = {}
    refused: list[RefusedImport] = []
    held: list[RefusedImport] = []
    unchecked: list[RefusedImport] = []
    dropped: list[DroppedParts] = []
    kept: list[tuple[str, str, str, str]] = []
    async with store.edit(actor=BOOTSTRAP_ACTOR, source="bootstrap", reason="imported from the environment") as draft:
        todo = [path for path in pending if path not in draft.bootstrapped]
        text, data = draft.yaml_text, dict(draft.data)
        for section in store.catalog:
            keys = [key for key in section.imports() if f"{section.name}.{key}" in todo]
            source = sources.get(section.name)
            found: Mapping[str, Any] | None = None
            if source is not None:
                fields = section.model.model_fields
                found = await source.read(draft.conn, config)
                if found is not None:
                    # What the source holds comes from it, never from a variable.
                    earlier_keys = [
                        key for key in found if key in fields and f"{section.name}.{key}" not in draft.bootstrapped
                    ]
                    todo.extend(f"{section.name}.{key}" for key in earlier_keys if f"{section.name}.{key}" not in todo)
                    keys = [key for key in keys if key not in found]
                    stored = mapping(data.get(section.name))
                    values = _earlier_values(section, source, found, earlier_keys, stored, refused, held)
                    if values:
                        comment = f"Imported from {source.label} on {day}: {', '.join(values)}."
                        text = with_section(text, data, section.name, values, comment)
                        data = {**data, section.name: {**stored, **values}}
                        from_earlier[section.name] = values
                        imported[section.name] = dict(values)
            if not keys:
                continue
            stored = mapping(data.get(section.name))
            relied = await _relied_on(section, keys, config, stored, draft.conn, asked, unchecked)
            values = _env_values(section, keys, config, stored, refused, held, relied, dropped)
            if not values:
                continue
            imports = section.imports()
            given = {key: value for key, value in values.items() if key not in relied}
            if given:
                # One variable may feed several keys (CONTEXT_AUDIT_JOB_BUDGET_USD fed three): named once.
                variables = ", ".join(dict.fromkeys(env_name(imports[key].attr) for key in given))
                comment = f"Imported from the environment on {day}: {variables}."
                text = with_section(text, data, section.name, given, comment)
                data = {**data, section.name: {**mapping(data.get(section.name)), **given}}
            for key in (key for key in values if key in relied):
                variable = env_name(imports[key].attr)
                evidence = relied[key].evidence
                comment = f"Kept on {day}: {variable} is not set, and {evidence}."
                text = with_section(text, data, section.name, {key: values[key]}, comment)
                data = {**data, section.name: {**mapping(data.get(section.name)), key: values[key]}}
                kept.append((section.name, key, variable, evidence))
            imported[section.name] = {**imported.get(section.name, {}), **values}
        in_file = data.get(MANAGED_BY_KEY) == MANAGED_BY_FILE
        if imported:
            # Each value was checked as its section when it was read; the rest
            # of the document is what is stored, checked when it was written.
            draft.replace(text, data, draft.schema_version)
            draft.reason = _import_reason(sources, from_earlier, imported)
        waiting = {f"{item.section}.{item.key}" for item in (*held, *unchecked)}
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
    for item in unchecked:
        logger.error(
            "settings_bootstrap_check_failed",
            variable=item.variable,
            key=f"{item.section}.{item.key}",
            reason=item.reason,
            hint="the key reads its default until a later try in this run, or the next start, answers",
        )
    for part in dropped:
        logger.warning(
            "settings_env_value_dropped",
            variable=part.variable,
            key=f"{part.section}.{part.key}",
            dropped=list(part.parts),
            hint="the key does not know these, so they are left out of what is imported",
        )
    for name, values in imported.items():
        logger.info("settings_env_imported", section=name, keys=sorted(values), revision_id=draft.revision_id)
    for name, key, variable, evidence in kept:
        logger.info(
            "settings_default_kept",
            variable=variable,
            key=f"{name}.{key}",
            evidence=evidence,
            revision_id=draft.revision_id,
            hint="the variable is not set and this deployment relied on its default, so the document keeps it",
        )
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
        details: dict[str, Any] = {
            "sections": {name: sorted(values) for name, values in imported.items()},
            "revision_id": draft.revision_id,
        }
        if from_earlier:
            details["earlier"] = {name: sources[name].label for name in from_earlier}
        if kept:
            details["kept"] = [f"{name}.{key}" for name, key, _, _ in kept]
        await emit_audit(
            store.db,
            "settings.bootstrapped",
            actor=BOOTSTRAP_ACTOR,
            details=details,
            audit_log_path=getattr(config, "audit_log_path", None),
        )
    return BootstrapResult(
        imported=imported,
        earlier=from_earlier,
        refused=tuple(refused),
        held=tuple(held),
        marked=tuple(marked),
        revision_id=draft.revision_id,
        kept=tuple(f"{name}.{key}" for name, key, _, _ in kept),
        unchecked=tuple(unchecked),
        dropped=tuple(dropped),
    )


def _import_reason(
    sources: Mapping[str, EarlierSource],
    from_earlier: Mapping[str, Mapping[str, Any]],
    imported: Mapping[str, Mapping[str, Any]],
) -> str:
    """What the import's revision says it imported from."""
    labels = [sources[name].label for name in from_earlier]
    from_env = any(set(values) - set(from_earlier.get(name, {})) for name, values in imported.items())
    if not labels:
        return "imported from the environment"
    return "imported from " + " and ".join([*labels, *(["the environment"] if from_env else [])])


async def settle_unchecked(
    store: SettingsStore,
    config: Any,
    result: BootstrapResult | None,
    *,
    earlier: Sequence[EarlierSource] = (),
    started: datetime | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> BootstrapResult:
    """Import again, in this run, until no key's ``ReliedOn`` check fails and the import itself succeeds.

    The lifespan starts it when the start's import left keys unchecked, or
    raised (``result`` None). It waits :data:`SETTLE_DELAYS` before each try
    (the last repeated) and runs the import again with the same ``earlier``
    sources and the same start (``started``, else the store's), which touches
    only the keys the ledger lacks; each try that still fails logs at error
    level. Without it, such a key would read its default until the next
    start. A value an operator wrote in the meantime stands: a key the
    document holds is never asked or imported. Returns the last try's result,
    once nothing is unchecked.
    """
    tries = 0
    while result is None or result.unchecked:
        delays = SETTLE_DELAYS
        await sleep(delays[min(tries, len(delays) - 1)])
        tries += 1
        try:
            result = await import_env_once(store, config, earlier=earlier, started=started)
        except Exception:  # noqa: BLE001 - tried again after the next delay
            logger.error("settings_bootstrap_retry_failed", tries=tries, exc_info=True)
            result = None
    if tries:
        logger.info("settings_bootstrap_check_settled", tries=tries, kept=list(result.kept))
    return result


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
