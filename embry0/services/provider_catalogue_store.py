"""The stored provider catalogue (#173, ADR 0031): a deployment's overlay of the catalogue embry0 ships.

One document per deployment (``provider_catalogue``), its YAML kept as
written, comments included, beside its values. Empty, it is the seed,
``embry0/agents/providers.yaml``, which embry0 ships and moves each release;
the overlay is merged with it field by field (``embry0.agents.catalogue.merge``).

**Writing.** A write is checked as a whole and refused naming every problem
(``CatalogueInvalid``), never a value: the text's own shape, what an overlay
may not set, the catalogue it makes, and that it takes out, leaves unpriced
or turns off no model a tier, the fallback order, an agent definition or a
``background_models`` value names while the catalogue offers it now. Every
write that changes the text appends a ``config_revisions`` row (surface
``provider_catalogue``) in its own transaction, so a write that cannot be
recorded does not happen. ``If-Match`` names the generation a write expects to
replace. Undo puts back what the newest change replaced, and only while the
stored text is still what that change wrote.

**Writes that name a model.** A tier, the fallback order, an agent
definition and a ``background_models`` value are checked against the stored
catalogue with its row held ``FOR SHARE`` (:meth:`CatalogueStore.held`,
:func:`held_catalogue`), and a change of the catalogue takes the row ``FOR
UPDATE`` before it reads what names a model: of a write naming a model and a
change of the catalogue taking it out, one waits for the other and is checked
against what it wrote.

**Reading.** The merged catalogue is installed in this process
(``embry0.agents.catalogue.install``), and every function of
``embry0.agents.providers`` answers from it. :meth:`CatalogueStore.refresh`
asks the database for the generation and reads the document only when it
moved: the lifespan calls it every few seconds (:data:`REFRESH_SECONDS`), a
write here at once, and the catalogue routes before they answer. A database
error keeps what is installed.

**Seed drift.** A stored overlay meets the seed of whatever release reads it,
which may have taken out a model or a provider it names. At a process's start
every part that still applies is installed and the rest left out
(``catalogue.merge_stored``); a running process that reads a later overlay
this release refuses part of keeps the catalogue it has (:meth:`CatalogueStore._install`).
Either way each refused part is named by ``GET /config/providers/catalog``,
counted by ``embry0_provider_catalogue_refused_parts`` (alert
``Embry0ProviderCatalogueRefused``), logged, and audited as
``provider_catalogue.refused``.

**The environment, once.** ``OPENAI_COMPATIBLE_PROVIDERS`` and
``OPENROUTER_CATALOG_REFRESH_HOURS`` were read from the environment before the
document held what they decide. At the first start of this release each set
variable is imported into the overlay (:data:`ENV_IMPORTS`), its key recorded
in the row's ``imported`` ledger whether or not it was set, and the variable
is ignored after that (:func:`log_ignored_env` says so at every start).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

import structlog
import yaml

from embry0.agents.catalogue import (
    Catalogue,
    CatalogueInvalid,
    Problem,
    StoredRead,
    current,
    install,
    merge,
    merge_stored,
    restored,
    seed,
    shipped_entries,
    stored_form,
)
from embry0.agents.providers import offered_problem
from embry0.audit.helpers import emit_audit
from embry0.metrics.loop import NULL_METRICS
from embry0.settings.env import env_name, is_set
from embry0.settings.registry import EnvImport
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.config_revisions import ConfigRevisionsRepository, RevisionSource
from embry0.text_documents import structure_problems, text_diff

logger = structlog.get_logger(__name__)

#: The ``config_revisions`` surface the catalogue's changes are recorded under, and its one scope.
SURFACE: Final = "provider_catalogue"
SCOPE: Final = "deployment"
#: The largest overlay a write takes, as UTF-8.
MAX_TEXT_BYTES: Final = 256 * 1024
#: Seconds between the lifespan's looks at the stored generation.
REFRESH_SECONDS: Final = 10.0
#: Why an older change cannot be undone; the settings document's words.
WHY_NOT: Final = "This changed again later. Undo the newer change first."


class CatalogueTooLarge(ValueError):
    """The text is longer than :data:`MAX_TEXT_BYTES`."""


class GenerationMismatch(Exception):
    """A write named a generation that is not the stored one."""

    def __init__(self, current: int) -> None:
        super().__init__(f"the stored generation is {current}")
        self.current = current


class RevisionNotFound(LookupError):
    """No change of the catalogue has that id."""


class NotUndoable(Exception):
    """The change is not the newest, or the stored text is no longer what it wrote."""


# ---- reading a text ----


def load_text(text: str) -> dict[str, Any]:
    """The overlay ``text`` holds, as a mapping; an empty or comment-only text is ``{}``.

    Refuses what every document an operator writes refuses
    (:mod:`embry0.text_documents`: an alias, a merge key, a key that is not
    text, a key written twice), a syntax error, a document nested too
    deeply, and a top level that is not a mapping: :class:`CatalogueInvalid`.
    Past :data:`MAX_TEXT_BYTES`, :class:`CatalogueTooLarge`.
    """
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise CatalogueTooLarge(f"the provider catalogue is larger than {MAX_TEXT_BYTES} bytes")
    if "\x00" in text:
        raise CatalogueInvalid([Problem((), "nul_character", "the catalogue cannot hold the NUL character")])
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        structure = structure_problems(root, what="the provider catalogue")
        if structure:
            raise CatalogueInvalid([Problem(p.loc, p.type, p.msg) for p in structure])
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise CatalogueInvalid([Problem((), "yaml_syntax", f"the YAML cannot be read{where}")]) from None
    except RecursionError:
        raise CatalogueInvalid([Problem((), "too_deep", "the catalogue nests too deeply")]) from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise CatalogueInvalid([Problem((), "not_a_mapping", "the catalogue must be a mapping")])
    return loaded


# ---- what names a model ----


@dataclass(frozen=True)
class Reference:
    """A model something embry0 runs on names: ``loc`` says where, never the model."""

    loc: tuple[str | int, ...]
    model: str


ReferenceReader = Callable[[Any], Awaitable[list[Reference]]]
"""``(conn) -> references``: what names a model, read on a write's own connection."""


async def read_references(
    conn: Any, tiers: Callable[[], Awaitable[Mapping[str, Any]]] | None = None
) -> list[Reference]:
    """The models the tiers and the fallback order, the agent definitions and the stored ``background_models``
    values name. ``tiers`` reads the deployment's provider configuration (its row, or the environment's defaults
    without one); without it, the row alone."""
    out: list[Reference] = []
    config: Mapping[str, Any]
    if tiers is not None:
        config = await tiers()
    else:
        row = await conn.fetchrow("SELECT * FROM provider_config WHERE id = 'default'")
        config = dict(row) if row is not None else {}
    for key in ("model_heavy", "model_medium", "model_light", "default_model"):
        value = config.get(key)
        if isinstance(value, str) and value:
            out.append(Reference(("tiers", key), value))
    fallback = config.get("fallback_models")
    if isinstance(fallback, str):
        try:
            fallback = json.loads(fallback)
        except ValueError:
            fallback = []
    for index, value in enumerate(fallback or []):
        if isinstance(value, str) and value:
            out.append(Reference(("fallback_models", index), value))
    for row in await conn.fetch("SELECT type, model FROM agent_definitions WHERE model <> '' ORDER BY type"):
        out.append(Reference(("agent_definitions", str(row["type"])), str(row["model"])))
    rows = await conn.fetch(
        "SELECT scope, data -> 'background_models' AS models FROM settings_documents "
        "WHERE data ? 'background_models' ORDER BY scope"
    )
    for row in rows:
        values = row["models"]
        if isinstance(values, str):
            values = json.loads(values)
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():
            if isinstance(value, str) and value:
                out.append(Reference(("background_models", str(row["scope"]), str(key)), value))
    return out


def broken_references(references: list[Reference], before: Catalogue, after: Catalogue) -> list[Problem]:
    """A problem for each reference ``before`` offers priced and ``after`` does not: what a write may not take
    out, leave unpriced or turn off."""
    problems: list[Problem] = []
    for reference in references:
        if offered_problem(reference.model, before) is not None:
            continue
        why = offered_problem(reference.model, after)
        if why is not None:
            problems.append(
                Problem(
                    ("references", *reference.loc),
                    "in_use",
                    f"names a model this change makes unusable ({why}); change what names it first",
                )
            )
    return problems


# ---- the environment, once ----


def _enabled_from_env(value: Any) -> list[str]:
    """``OPENAI_COMPATIBLE_PROVIDERS`` as the providers it turns on: shipped OpenAI-compatible names only."""
    family = {p.name for p in seed().chat_completions}
    names = [part.strip().lower() for part in str(value or "").split(",") if part.strip()]
    return [name for name in dict.fromkeys(names) if name in family]


def _enabled_dropped(value: Any) -> list[str]:
    family = {p.name for p in seed().chat_completions}
    return sorted({part.strip().lower() for part in str(value or "").split(",") if part.strip()} - family)


def _hours_from_env(value: Any) -> float:
    """``OPENROUTER_CATALOG_REFRESH_HOURS`` as the reader read it: a number, at least 0 (0 stops the loop)."""
    hours = float(str(value).strip())
    if hours != hours or hours in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    return min(max(0.0, hours), 720.0)


#: The variables the catalogue replaces, imported once each (the ``imported`` ledger), then ignored. Each key
#: names what it fills; the retirement of these variables removes its entry.
ENV_IMPORTS: Final[Mapping[str, EnvImport]] = {
    "providers.enabled": EnvImport("openai_compatible_providers", _enabled_from_env, _enabled_dropped),
    "openrouter.refresh_hours": EnvImport("openrouter_catalog_refresh_hours", _hours_from_env),
}


def _env_overlay(key: str, value: Any) -> dict[str, Any]:
    """The part of an overlay a variable's converted ``value`` writes."""
    if key == "providers.enabled":
        return {name: {"enabled": True} for name in value}
    if key == "openrouter.refresh_hours":
        shipped = seed().document.providers["openrouter"].refresh_hours
        return {} if value == shipped else {"openrouter": {"refresh_hours": value}}
    raise KeyError(key)


# ---- the store ----


@dataclass
class SaveResult:
    generation: int
    revision_id: int | None
    changed: bool
    diff: str = ""


@dataclass
class Preview:
    generation: int
    changed: bool
    diff: str = ""


@dataclass
class _Row:
    yaml_text: str
    data: dict[str, Any]
    generation: int
    imported: list[str]
    updated_by: str
    updated_at: datetime | None
    #: Of each model the overlay names, its shipped entry as the writes saw it (``catalogue.shipped_entries``).
    shipped: dict[str, Any] = field(default_factory=dict)


_COLUMNS: Final = "yaml_text, data, generation, imported, updated_by, updated_at, shipped"


def _row(row: Any) -> _Row:
    def _json(value: Any) -> dict[str, Any]:
        value = json.loads(value) if isinstance(value, str) else value
        return dict(value) if isinstance(value, Mapping) else {}

    return _Row(
        str(row["yaml_text"]),
        _json(row["data"]),
        int(row["generation"]),
        list(row["imported"] or []),
        str(row["updated_by"]),
        row["updated_at"],
        _json(row["shipped"]),
    )


InForce = Literal["whole", "in_part", "earlier"]
"""How much of the stored overlay a process runs on: all of it; at its start, every part this release takes (the
rest is :attr:`CatalogueStore.refused`); or, for an overlay this release refuses part of once it runs, the catalogue
it ran on before."""


@dataclass
class CatalogueStore:
    """The deployment's catalogue overlay: read, checked writes with history and undo, and what is installed."""

    db: DatabasePool
    references: ReferenceReader = field(default=read_references)
    audit_log_path: str | None = None
    #: ``LoopMetrics`` (``embry0_provider_catalogue_refused_parts``); the null one records nothing.
    metrics: Any = NULL_METRICS
    _installed_generation: int | None = None
    _refused: list[Problem] = field(default_factory=list)
    _notes: list[Problem] = field(default_factory=list)
    _in_force: InForce = "whole"
    _in_force_generation: int | None = None

    @property
    def refused(self) -> list[Problem]:
        """The problems of each part of the stored overlay this process does not run on: the release refuses it."""
        return list(self._refused)

    @property
    def notes(self) -> list[Problem]:
        """Models a release dropped that the stored overlay keeps as the deployment's own, at its own prices."""
        return list(self._notes)

    @property
    def in_force(self) -> InForce:
        return self._in_force

    @property
    def in_force_generation(self) -> int | None:
        """The generation of the stored overlay this process runs on (all of it, or the parts it takes)."""
        return self._in_force_generation

    async def document(self) -> _Row:
        row = await self.db.fetchrow(f"SELECT {_COLUMNS} FROM provider_catalogue WHERE scope = $1", SCOPE)
        if row is None:  # migration 152 inserts it; a test database may not have
            return _Row("", {}, 0, [], "", None)
        return _row(row)

    async def refresh(self) -> Catalogue:
        """Install the stored catalogue when its generation moved since the last look; answer what is installed."""
        try:
            generation = await self.db.fetchval("SELECT generation FROM provider_catalogue WHERE scope = $1", SCOPE)
        except Exception:
            logger.warning("provider_catalogue_read_failed", exc_info=True)
            return current()
        if generation is None or int(generation) == self._installed_generation:
            return current()
        stored = await self.document()
        await self._take(stored)
        return current()

    async def _take(self, stored: _Row) -> None:
        """Install what ``stored`` makes (:meth:`_install`); a part this release refuses is audited, once a
        generation."""
        if self._install(stored):
            await emit_audit(
                self.db,
                "provider_catalogue.refused",
                actor="embry0:provider-catalogue",
                details={
                    "generation": stored.generation,
                    "in_force": self._in_force,
                    "in_force_generation": self._in_force_generation,
                    "parts": [".".join(str(part) for part in problem.loc) for problem in self._refused[:50]],
                    "count": len(self._refused),
                },
                audit_log_path=self.audit_log_path,
            )

    def _install(self, stored: _Row) -> bool:
        """Install the catalogue ``stored`` makes with this release's seed; True when the release refuses a part.

        All of it applies: installed. At this store's first install (the
        process's start), the parts that apply are installed and the rest
        left out (``catalogue.merge_stored``), so a seed that moved never
        turns off what the deployment turned on; a price that is left out
        leaves what it priced unpriced, never priced at a value the deployment
        did not set. Once the process runs on a catalogue, a later overlay this
        release refuses part of changes nothing: it keeps the catalogue it has.
        Either way the refused parts are :attr:`refused`, the gauge counts them
        and a warning is logged; :meth:`_take` audits them. Models kept as the
        deployment's own are :attr:`notes`, logged.
        """
        candidate, read = built(stored)
        refused = read.refused
        if not refused or self._installed_generation is None:
            install(candidate)
            self._in_force = "in_part" if refused else "whole"
            self._in_force_generation = stored.generation
            self._notes = read.notes
            if read.notes:
                logger.warning(
                    "provider_catalogue_models_kept_as_own",
                    generation=stored.generation,
                    models=[".".join(str(part) for part in note.loc) for note in read.notes[:20]],
                    msg="this release no longer ships these models: the catalogue keeps them as the deployment's own",
                )
        else:
            self._in_force = "earlier"
        self._refused = refused
        self._installed_generation = stored.generation
        self.metrics.provider_catalogue_refused(len(refused))
        if refused:
            logger.warning(
                "provider_catalogue_refused",
                generation=stored.generation,
                in_force=self._in_force,
                in_force_generation=self._in_force_generation,
                problems=[str(problem) for problem in refused[:10]],
                msg="this release refuses part of the stored provider catalogue; that part is not in force",
            )
        return bool(refused)

    async def _checked(self, conn: Any, text: str, stored: _Row) -> tuple[dict[str, Any], Catalogue]:
        """``text``'s overlay and the catalogue it makes, refused naming every problem: its own, and each model
        something names that the catalogue ``stored`` makes offers and the new one does not."""
        data = load_text(text)
        after = Catalogue.build(merge(seed().document, data or None, stored.shipped))
        problems = broken_references(await self.references(conn), built(stored)[0], after)
        if problems:
            raise CatalogueInvalid(problems)
        return data, after

    async def preview(self, text: str, *, if_generation: int | Collection[int] | None = None) -> Preview:
        """What a write of ``text`` would do; nothing is stored. Refused as the write would be."""
        await self.refresh()
        async with self.db.transaction() as conn:
            stored = await self._locked(conn, for_update=False)
            if not _matches(if_generation, stored.generation):
                raise GenerationMismatch(stored.generation)
            await self._checked(conn, text, stored)
        changed = text != stored.yaml_text
        return Preview(stored.generation, changed, _diff(stored.yaml_text, text) if changed else "")

    async def save(
        self,
        text: str,
        *,
        actor: str,
        source: RevisionSource = "person",
        reason: str = "",
        if_generation: int | Collection[int] | None = None,
    ) -> SaveResult:
        """Store ``text`` as the overlay and install the catalogue it makes; a text equal to the stored one records
        nothing. Refused (:class:`CatalogueInvalid`, :class:`CatalogueTooLarge`, :class:`GenerationMismatch`) with
        nothing stored."""
        await self.refresh()
        async with self.db.transaction() as conn:
            stored = await self._locked(conn)
            if not _matches(if_generation, stored.generation):
                raise GenerationMismatch(stored.generation)
            data, _after = await self._checked(conn, text, stored)
            if text == stored.yaml_text:
                return SaveResult(stored.generation, None, False)
            generation = await self._write(conn, text, data, actor=actor, previous=stored.shipped)
            revision_id = await ConfigRevisionsRepository(self.db).append(
                conn,
                scope=SCOPE,
                surface=SURFACE,
                before=_document(stored.yaml_text, stored.data),
                after=_document(text, data),
                actor=actor,
                source=source,
                reason=reason,
            )
        await self.refresh()
        return SaveResult(generation, revision_id, True, _diff(stored.yaml_text, text))

    async def revisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return await ConfigRevisionsRepository(self.db).history(SCOPE, SURFACE, limit=limit)

    async def revision(self, revision_id: int) -> dict[str, Any] | None:
        row = await ConfigRevisionsRepository(self.db).get(revision_id)
        return row if row is not None and row["surface"] == SURFACE else None

    async def undo(self, revision_id: int, *, actor: str) -> SaveResult:
        """Put back what change ``revision_id`` replaced: only the newest, while the stored text is what it wrote
        (:class:`NotUndoable`), and only when that earlier text still makes a catalogue (:class:`CatalogueInvalid`).
        Recorded with source ``restore``."""
        revision = await self.revision(revision_id)
        if revision is None:
            raise RevisionNotFound(revision_id)
        before_doc = _mapping(revision.get("before"))
        after_doc = _mapping(revision.get("after"))
        await self.refresh()
        async with self.db.transaction() as conn:
            stored = await self._locked(conn)
            latest = await ConfigRevisionsRepository(self.db).latest_on(conn, SCOPE, SURFACE)
            if latest is None or latest["id"] != revision_id or after_doc.get("yaml_text") != stored.yaml_text:
                raise NotUndoable(WHY_NOT)
            text = str(before_doc.get("yaml_text") or "")
            data, _after = await self._checked(conn, text, stored)
            generation = await self._write(conn, text, data, actor=actor, previous=stored.shipped)
            new_revision = await ConfigRevisionsRepository(self.db).append(
                conn,
                scope=SCOPE,
                surface=SURFACE,
                before=_document(stored.yaml_text, stored.data),
                after=_document(text, data),
                actor=actor,
                source="restore",
                reason=f"undo of change {revision_id}",
            )
        await self.refresh()
        return SaveResult(generation, new_revision, True, _diff(stored.yaml_text, text))

    async def import_env(self, config: Any, *, actor: str = "embry0:bootstrap") -> list[str]:
        """Import each variable of :data:`ENV_IMPORTS` not yet in the ledger, once; answer the keys imported.

        A set variable whose value the catalogue takes is merged into the
        overlay (the document is written again from its values, under a
        comment naming the variables and the day); one it refuses is logged
        and skipped. Every key is marked, set or not, so it is never read again.
        """
        imported: list[str] = []
        async with self.db.transaction() as conn:
            stored = await self._locked(conn)
            pending = [key for key in ENV_IMPORTS if key not in stored.imported]
            if not pending:
                return []
            overlay = restored(json.loads(json.dumps(stored.data or {})))
            providers = overlay.setdefault("providers", {})
            variables: list[str] = []
            for key in pending:
                spec = ENV_IMPORTS[key]
                if not is_set(config, spec.attr):
                    continue
                raw = getattr(config, spec.attr)
                if spec.dropped is not None and spec.dropped(raw):
                    logger.warning(
                        "provider_catalogue_env_value_dropped", variable=env_name(spec.attr), dropped=spec.dropped(raw)
                    )
                try:
                    value = spec.convert(raw) if spec.convert is not None else raw
                    part = _env_overlay(key, value)
                except (TypeError, ValueError):
                    logger.warning("provider_catalogue_env_refused", variable=env_name(spec.attr))
                    continue
                for name, fields in part.items():
                    providers.setdefault(name, {}).update(fields)
                if part:
                    imported.append(key)
                    variables.append(env_name(spec.attr))
            if imported:
                try:
                    merge(seed().document, overlay, stored.shipped)
                except CatalogueInvalid as invalid:
                    logger.warning("provider_catalogue_env_refused", problems=[str(p) for p in invalid.problems[:10]])
                    imported = []
            ledger = sorted({*stored.imported, *pending})
            if imported:
                day = datetime.now(UTC).date().isoformat()
                text = f"# Imported once from {', '.join(variables)} on {day}; the variables are ignored now.\n"
                text += yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False)
                generation = await self._write(
                    conn, text, overlay, actor=actor, imported=ledger, previous=stored.shipped
                )
                await ConfigRevisionsRepository(self.db).append(
                    conn,
                    scope=SCOPE,
                    surface=SURFACE,
                    before=_document(stored.yaml_text, stored.data),
                    after=_document(text, overlay),
                    actor=actor,
                    source="bootstrap",
                    reason=f"imported once from {', '.join(variables)}",
                )
                logger.info("provider_catalogue_env_imported", variables=variables, generation=generation)
            else:
                await conn.execute("UPDATE provider_catalogue SET imported = $2 WHERE scope = $1", SCOPE, ledger)
        if imported:
            await emit_audit(
                self.db,
                "provider_catalogue.bootstrapped",
                actor=actor,
                details={"variables": variables, "keys": imported},
                audit_log_path=self.audit_log_path,
            )
        await self.refresh()
        return imported

    async def _locked(self, conn: Any, *, for_update: bool = True) -> _Row:
        return await _held_row(conn, " FOR UPDATE" if for_update else "")

    @asynccontextmanager
    async def held(self) -> AsyncIterator[Catalogue]:
        """The stored catalogue, installed in this process, its row held ``FOR SHARE`` until the block ends.

        A write of something that names a model checks it, and stores it,
        inside the block: a change of the catalogue waits for it, then reads
        what it wrote among what names a model; and it waits for a change of
        the catalogue under way, then checks against what that wrote.
        """
        async with self.db.transaction() as conn:
            stored = await _held_row(conn, " FOR SHARE")
            if stored.generation != self._installed_generation:
                await self._take(stored)
            yield current()

    async def _write(
        self,
        conn: Any,
        text: str,
        data: Mapping[str, Any],
        *,
        actor: str,
        previous: Mapping[str, Any],
        imported: list[str] | None = None,
    ) -> int:
        """Store ``text`` and its values in JSON form (a ``from`` day as its ISO text), with the shipped entry of
        each model they name (``catalogue.shipped_entries``: this release's, or ``previous``'s for one it dropped)."""
        generation = await conn.fetchval(
            "UPDATE provider_catalogue SET yaml_text = $2, data = $3::jsonb, generation = generation + 1, "
            "imported = COALESCE($5, imported), updated_by = $4, updated_at = NOW(), shipped = $6::jsonb "
            "WHERE scope = $1 RETURNING generation",
            SCOPE,
            text,
            stored_form(data),
            actor,
            imported,
            shipped_entries(seed().document, data, previous),
        )
        return int(generation)


async def _held_row(conn: Any, lock: str) -> _Row:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM provider_catalogue WHERE scope = $1{lock}", SCOPE)
    if row is None:  # migration 152 inserts it
        await conn.execute("INSERT INTO provider_catalogue (scope) VALUES ($1) ON CONFLICT DO NOTHING", SCOPE)
        return await _held_row(conn, lock)
    return _row(row)


def built(stored: _Row) -> tuple[Catalogue, StoredRead]:
    """The catalogue a stored overlay makes with this release's seed: every part of it that applies, its models
    whose price is left out unpriced, and why (``catalogue.merge_stored``)."""
    read = merge_stored(seed().document, dict(stored.data) or None, stored.shipped)
    return Catalogue.build(read.document, withheld=read.withheld), read


async def held_catalogue(conn: Any) -> Catalogue:
    """The stored catalogue, its row read ``FOR SHARE`` on ``conn``: the lock lasts as long as ``conn``'s
    transaction (a settings write's, ``embry0.settings.write_context``)."""
    return built(await _held_row(conn, " FOR SHARE"))[0]


def _matches(expected: int | Collection[int] | None, generation: int) -> bool:
    if expected is None:
        return True
    return generation == expected if isinstance(expected, int) else generation in expected


def _document(text: str, data: Mapping[str, Any]) -> dict[str, Any]:
    return {"yaml_text": text, "data": stored_form(data)}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, Mapping) else {}


def _diff(before: str, after: str) -> str:
    return text_diff(before, after, before_name="stored", after_name="proposed")


async def log_ignored_env(store: CatalogueStore, config: Any) -> None:
    """Say, at every start, which variable the catalogue imported is still set: it is ignored."""
    stored = await store.document()
    for key, spec in ENV_IMPORTS.items():
        if key in stored.imported and is_set(config, spec.attr):
            logger.warning(
                "provider_catalogue_env_ignored",
                variable=env_name(spec.attr),
                msg="imported once into the provider catalogue; change the catalogue instead (PUT /config/providers/catalog)",
            )


__all__ = [
    "ENV_IMPORTS",
    "CatalogueStore",
    "CatalogueTooLarge",
    "GenerationMismatch",
    "NotUndoable",
    "Preview",
    "Reference",
    "RevisionNotFound",
    "SaveResult",
    "broken_references",
    "load_text",
    "log_ignored_env",
    "read_references",
]
