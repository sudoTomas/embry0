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
definition, a pipeline template's steps, a repository's review model and a
``background_models`` value are checked against the stored
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
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import structlog
import yaml

from embry0.agents.catalogue import (
    Catalogue,
    CatalogueInvalid,
    CatalogueLoader,
    CatalogueOverlay,
    Problem,
    StoredRead,
    current,
    install,
    merge,
    merge_stored,
    records,
    seed,
    shipped_entries,
    stored_form,
)
from embry0.agents.providers import offered_problem
from embry0.audit.helpers import emit_audit
from embry0.metrics.loop import NULL_METRICS
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.config_revisions import ConfigRevisionsRepository, RevisionSource
from embry0.text_documents import StructureProblem, structure_problems, text_diff

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


def _structure_placed(found: Iterable[StructureProblem]) -> list[Problem]:
    """Each problem of the text's structure (:mod:`embry0.text_documents`) cut where the catalogue's own words end:
    at the top-level key it is under when the overlay takes that key (``version``, ``providers``), else at the
    document. Below ``providers`` every key is a provider's or a model's id, which the operator wrote, so a number
    too long or a key written twice inside a model is placed at ``providers``; its line says where."""
    return [
        Problem(
            problem.loc[:1] if problem.loc and problem.loc[0] in CatalogueOverlay.model_fields else (),
            problem.type,
            problem.msg,
        )
        for problem in found
    ]


def load_text(text: str) -> dict[str, Any]:
    """The overlay ``text`` holds, as a mapping; an empty or comment-only text is ``{}``.

    Refuses what every document an operator writes refuses
    (:mod:`embry0.text_documents`: an alias, a merge key, a key that is not
    text, a key written twice), a syntax error, a document nested too
    deeply, and a top level that is not a mapping: :class:`CatalogueInvalid`,
    the structure's problems placed by :func:`_structure_placed`, never at a
    provider's or a model's id.
    Past :data:`MAX_TEXT_BYTES`, :class:`CatalogueTooLarge`.
    """
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise CatalogueTooLarge(f"the provider catalogue is larger than {MAX_TEXT_BYTES} bytes")
    if "\x00" in text:
        raise CatalogueInvalid([Problem((), "nul_character", "the catalogue cannot hold the NUL character")])
    try:
        root = yaml.compose(text, Loader=CatalogueLoader)
        structure = structure_problems(root, what="the provider catalogue")
        if structure:
            raise CatalogueInvalid(_structure_placed(structure))
        loaded = yaml.load(text, Loader=CatalogueLoader)  # noqa: S506 - a safe loader
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
    """The models the tiers and the fallback order, the agent definitions, the pipeline templates' steps (a step's
    ``config.model``, or a legacy top-level ``model``), the repositories' review model (``pr_review.model``) and the
    stored ``background_models`` values name. ``tiers`` reads the deployment's provider configuration (its row, or
    the environment's defaults without one); without it, the row alone."""
    from embry0.workflows.issue_to_pr.route_plan import node_config

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
    for row in await conn.fetch("SELECT name, graph_definition FROM pipeline_templates ORDER BY name"):
        graph = row["graph_definition"]
        if isinstance(graph, str):
            graph = json.loads(graph)
        nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
        for index, node in enumerate(nodes if isinstance(nodes, list) else []):
            if not isinstance(node, Mapping):
                continue
            value = node_config(node).get("model")
            if isinstance(value, str) and value:
                step = str(node.get("id") or index)
                out.append(Reference(("pipeline_templates", str(row["name"]), step), value))
    rows = await conn.fetch(
        "SELECT repo, pr_review ->> 'model' AS model FROM repo_preferences "
        "WHERE jsonb_typeof(pr_review) = 'object' AND COALESCE(pr_review ->> 'model', '') <> '' ORDER BY repo"
    )
    for row in rows:
        out.append(Reference(("repo_preferences", str(row["repo"]), "pr_review"), str(row["model"])))
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


# ---- the custom provider's old defaults, kept once where a deployment relied on them ----

#: The ``imported`` ledger key of the one-time import below.
CUSTOM_IMPORT: Final = "custom.relied_on"
#: The custom provider's turn and time factors before its defaults changed (#173), kept where it was used.
RELIED_ON_FACTORS: Final[Mapping[str, float]] = {"turns": 2.0, "timeout": 2.0}
#: What each custom model a deployment used had before: run at $0, and, on a background call, held to the call's
#: JSON schema with the server's thinking turned off. The switch is a background-only extra: an agent's run never
#: sent it, so it is not a request extra, which goes with every call of the model.
RELIED_ON_MODEL: Final[Mapping[str, Any]] = {
    "free": True,
    "structured_output": "json_schema",
    "background_request_extras": {"chat_template_kwargs": {"enable_thinking": False}},
}
#: What the import adds to a custom model the overlay already lists, where the overlay does not set it: the fields
#: a background call relied on, never a price.
RELIED_ON_CALL_FIELDS: Final = ("structured_output", "background_request_extras")

CustomModelReader = Callable[[Any], Awaitable[list[str]]]
"""``(conn) -> models``: the custom models a deployment's stored configuration and runs name."""


def _custom(model: str) -> bool:
    provider = seed().named("custom")
    prefix = provider.model_prefixes[0] if provider is not None else "custom/"
    return model.startswith(prefix) and len(model) > len(prefix)


async def read_custom_models(conn: Any, references: ReferenceReader = read_references) -> list[str]:
    """Every ``custom/<model>`` the stored configuration names (:func:`read_references`: a tier or the default model,
    the fallback order, an agent definition, a template's step, a repository's review model, a
    ``background_models`` value at any layer) or a job's ``agent_models`` override names, and every one a run was
    traced on."""
    named = {reference.model for reference in await references(conn)}
    rows = await conn.fetch("SELECT DISTINCT model FROM traces WHERE model LIKE 'custom/%'")
    named |= {str(row["model"]) for row in rows}
    rows = await conn.fetch(
        "SELECT DISTINCT override.value AS model FROM jobs, jsonb_each_text("
        "CASE WHEN jsonb_typeof(jobs.pipeline_config -> 'agent_models_override') = 'object' "
        "THEN jobs.pipeline_config -> 'agent_models_override' ELSE '{}'::jsonb END) AS override "
        "WHERE override.value LIKE 'custom/%'"
    )
    named |= {str(row["model"]) for row in rows}
    return sorted(model for model in named if _custom(model))


def custom_import(data: Mapping[str, Any], models: Iterable[str]) -> tuple[dict[str, Any], list[str]] | None:
    """The overlay ``data`` with what the deployment relied on of the custom provider kept, and the models it wrote
    or completed; None when there is nothing to keep.

    The old factors fill each one the overlay does not set. Each of ``models`` the overlay does not list is
    written as :data:`RELIED_ON_MODEL`; one it lists (priced or free) keeps everything it sets and gains only the
    fields of :data:`RELIED_ON_CALL_FIELDS` it does not. (``null`` takes out a model a provider ships, so no valid
    overlay lists a custom model as ``null``: custom ships none. One that does anyway is left as it is, and the
    catalogue refuses the overlay as it refused it before.) Where the overlay
    sets the provider a ``default_price``, a model written is pinned at that price rather than ``free``: a model's
    entry needs prices of its own or ``free`` (a later change of the default does not move it).
    """
    models = sorted(set(models))
    if not models:
        return None
    overlay: dict[str, Any] = json.loads(json.dumps(dict(data or {})))
    providers = overlay.setdefault("providers", {})
    custom = providers.setdefault("custom", {})
    custom["factors"] = {**RELIED_ON_FACTORS, **(custom.get("factors") or {})}
    listed = custom.setdefault("models", {}) or {}
    custom["models"] = listed
    default_price = custom.get("default_price")
    kept: list[str] = []
    for model in models:
        if model in listed:
            entry = listed[model]
            if not isinstance(entry, dict):
                continue  # null: custom ships no model to take out, so the catalogue refuses this overlay anyway
            missing = [key for key in RELIED_ON_CALL_FIELDS if key not in entry]
            for key in missing:
                entry[key] = json.loads(json.dumps(RELIED_ON_MODEL[key]))
            if missing:
                kept.append(model)
            continue
        written = json.loads(json.dumps(dict(RELIED_ON_MODEL)))
        if isinstance(default_price, Mapping):
            del written["free"]
            written["prices"] = [dict(default_price)]
        listed[model] = written
        kept.append(model)
    return overlay, kept


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
    updated_by: str
    updated_at: datetime | None
    #: Of each model the overlay names, its shipped entry as the writes saw it (``catalogue.shipped_entries``).
    shipped: dict[str, Any] = field(default_factory=dict)


_COLUMNS: Final = "yaml_text, data, generation, updated_by, updated_at, shipped"


def _row(row: Any) -> _Row:
    def _json(value: Any) -> dict[str, Any]:
        value = json.loads(value) if isinstance(value, str) else value
        return dict(value) if isinstance(value, Mapping) else {}

    return _Row(
        str(row["yaml_text"]),
        _json(row["data"]),
        int(row["generation"]),
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
    audit_log_path: Path | None = None
    #: What names a custom model, for the one-time import (:meth:`import_custom_defaults`); None: this store's
    #: ``references``, the traces and the jobs' ``agent_models``.
    custom_models: CustomModelReader | None = None
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
            return _Row("", {}, 0, "", None)
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
        stored, how = read_again(stored)
        if how != "same":
            # Once a generation: a process installs each generation once (at its start, or after a write).
            logger.warning(
                "provider_catalogue_read_again" if how == "read_again" else "provider_catalogue_text_not_read_again",
                generation=stored.generation,
                msg=(
                    "the stored overlay's text reads otherwise with this release's loader: the values read from it "
                    "now are in force, and a save of the text stores them"
                    if how == "read_again"
                    else "the stored overlay's text does not read again with this release's loader, or reads to an "
                    "overlay it takes less of: the values stored with it stay in force"
                ),
            )
        candidate, read = _built(stored)
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

    async def _checked(
        self, conn: Any, text: str, stored: _Row, recorded: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], Catalogue]:
        """``text``'s overlay and the catalogue it makes, refused naming every problem: its own, and each model
        something names that the catalogue ``stored`` makes offers and the new one does not. ``recorded`` is what
        the write will record of the shipped catalogue (``stored``'s by default)."""
        data = load_text(text)
        after = Catalogue.build(merge(seed().document, data or None, stored.shipped if recorded is None else recorded))
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
            shipped = shipped_entries(seed().document, data, stored.shipped)
            generation = await self._write(conn, text, data, actor=actor, shipped=shipped)
            revision_id = await ConfigRevisionsRepository(self.db).append(
                conn,
                scope=SCOPE,
                surface=SURFACE,
                before=_document(stored.yaml_text, stored.data, stored.shipped),
                after=_document(text, data, shipped),
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
            # What was recorded then, over what is recorded now: the text comes back as it was read.
            recorded = records(stored.shipped, _mapping(before_doc.get("shipped")))
            data, _after = await self._checked(conn, text, stored, recorded)
            shipped = shipped_entries(seed().document, data, recorded)
            generation = await self._write(conn, text, data, actor=actor, shipped=shipped)
            new_revision = await ConfigRevisionsRepository(self.db).append(
                conn,
                scope=SCOPE,
                surface=SURFACE,
                before=_document(stored.yaml_text, stored.data, stored.shipped),
                after=_document(text, data, shipped),
                actor=actor,
                source="restore",
                reason=f"undo of change {revision_id}",
            )
        await self.refresh()
        return SaveResult(generation, new_revision, True, _diff(stored.yaml_text, text))

    async def import_custom_defaults(self, *, actor: str = "embry0:upgrade") -> list[str]:
        """Keep, once, what the deployment relied on of the custom provider's old defaults; answer the models kept.

        The custom provider shipped a $0 price for any model and doubled turn and time budgets, and its background
        calls sent every model a JSON schema and turned its thinking off. Its defaults are now none of that
        (#173): a custom model runs only priced or ``free``. So at the first start of this release, the custom
        models the deployment names or ran (:func:`read_custom_models`) are kept in its overlay
        (:func:`custom_import`): written as :data:`RELIED_ON_MODEL`, or, where it lists them already, completed with
        the fields a background call relied on; with the old factors where it sets none. That is one revision
        (source ``upgrade``) and one ``provider_catalogue.custom_imported`` audit row. The ``imported`` ledger is
        marked once there is nothing left to keep or it is kept, so this never runs again; an overlay the catalogue
        would refuse with them is logged at error level, left as it is and not marked, so the start after the
        deployment fixes it keeps them then.
        """
        kept: list[str] = []
        written = False
        async with self.db.transaction() as conn:
            stored = await self._locked(conn)
            ledger = await conn.fetchval("SELECT imported FROM provider_catalogue WHERE scope = $1", SCOPE) or []
            if CUSTOM_IMPORT in ledger:
                return []
            reader = self.custom_models or (lambda c: read_custom_models(c, self.references))
            models = await reader(conn)
            imported = custom_import(stored.data, models)
            overlay, kept = imported if imported is not None else (None, [])
            if overlay == stored.data:
                overlay, kept = None, []  # it lists them all as they were, with factors of its own
            if overlay is not None:
                try:
                    merge(seed().document, overlay, stored.shipped)
                except CatalogueInvalid as invalid:
                    logger.error(
                        "provider_catalogue_custom_import_refused",
                        models=models[:20],
                        problems=[str(problem) for problem in invalid.problems[:10]],
                        hint="fix the provider catalogue (each custom model with its prices, or free: true); the "
                        "next start keeps them",
                    )
                    return []
            ledger = sorted({*ledger, CUSTOM_IMPORT})
            if overlay is not None:
                day = datetime.now(UTC).date().isoformat()
                text = (
                    f"# Kept once on {day}: the custom provider's old defaults, for the custom models this deployment\n"
                    "# used (free, a background call's JSON schema and thinking off) and its old factors.\n"
                )
                text += yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False)
                shipped = shipped_entries(seed().document, overlay, stored.shipped)
                generation = await self._write(conn, text, overlay, actor=actor, shipped=shipped)
                await conn.execute("UPDATE provider_catalogue SET imported = $2 WHERE scope = $1", SCOPE, ledger)
                await ConfigRevisionsRepository(self.db).append(
                    conn,
                    scope=SCOPE,
                    surface=SURFACE,
                    before=_document(stored.yaml_text, stored.data, stored.shipped),
                    after=_document(text, overlay, shipped),
                    actor=actor,
                    source="upgrade",
                    reason="the custom provider's old defaults, kept for the custom models this deployment used",
                )
                written = True
                logger.info(
                    "provider_catalogue_custom_imported", models=kept[:20], count=len(kept), generation=generation
                )
            else:
                await conn.execute("UPDATE provider_catalogue SET imported = $2 WHERE scope = $1", SCOPE, ledger)
        if written:
            count = len(kept)
            await emit_audit(
                self.db,
                "provider_catalogue.custom_imported",
                actor=actor,
                details={"models": kept[:50], "count": count},
                audit_log_path=self.audit_log_path,
            )
        await self.refresh()
        return kept

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
        shipped: Mapping[str, Any],
    ) -> int:
        """Store ``text`` and its values in JSON form (a ``from`` day as its ISO text), with what the write records
        of the shipped catalogue (``catalogue.shipped_entries``)."""
        generation = await conn.fetchval(
            "UPDATE provider_catalogue SET yaml_text = $2, data = $3::jsonb, generation = generation + 1, "
            "updated_by = $4, updated_at = NOW(), shipped = $5::jsonb "
            "WHERE scope = $1 RETURNING generation",
            SCOPE,
            text,
            stored_form(data),
            actor,
            dict(shipped),
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
    whose price is left out unpriced, and why (``catalogue.merge_stored``). Its values are read again from its text
    first (:func:`read_again`)."""
    return _built(read_again(stored)[0])


def _built(stored: _Row) -> tuple[Catalogue, StoredRead]:
    read = merge_stored(seed().document, dict(stored.data) or None, stored.shipped)
    return Catalogue.build(read.document, withheld=read.withheld), read


ReadAgain = Literal["same", "read_again", "kept"]


def read_again(stored: _Row) -> tuple[_Row, ReadAgain]:
    """``stored`` with its values read again from its text by this release's loader, and how that went.

    The values a row holds were read when the text was written, by that release's loader: an overlay stored before
    the loader read ``1e-7`` as a number (#756) holds the string, and a save of the same text records nothing. So
    the text is read again, and its values are in force (``read_again``) when they differ. The stored values stand
    (``kept``) when the text cannot be read, or reads to an overlay this release takes less of, so a deployment
    never loses its catalogue to a new reading; ``same`` when nothing differs (an empty text keeps its values)."""
    if not stored.yaml_text.strip():
        return stored, "same"
    try:
        data = stored_form(load_text(stored.yaml_text))
    except (CatalogueInvalid, CatalogueTooLarge):
        return stored, "kept"
    if data == stored.data:
        return stored, "same"
    again = replace(stored, data=data)
    refused_before = {problem.loc for problem in _built(stored)[1].refused}
    refused_after = {problem.loc for problem in _built(again)[1].refused}
    if not refused_after <= refused_before:
        return stored, "kept"
    return again, "read_again"


async def held_catalogue(conn: Any) -> Catalogue:
    """The stored catalogue, its row read ``FOR SHARE`` on ``conn``: the lock lasts as long as ``conn``'s
    transaction (a settings write's, ``embry0.settings.write_context``)."""
    return built(await _held_row(conn, " FOR SHARE"))[0]


def _matches(expected: int | Collection[int] | None, generation: int) -> bool:
    if expected is None:
        return True
    return generation == expected if isinstance(expected, int) else generation in expected


def _document(text: str, data: Mapping[str, Any], shipped: Mapping[str, Any]) -> dict[str, Any]:
    """A revision's side: the text, its values and what was recorded of the shipped catalogue with them, so an undo
    puts back the record too."""
    return {"yaml_text": text, "data": stored_form(data), "shipped": dict(shipped)}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, Mapping) else {}


def _diff(before: str, after: str) -> str:
    return text_diff(before, after, before_name="stored", after_name="proposed")


__all__ = [
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
    "read_references",
]
