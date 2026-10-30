"""The settings store: writes with history and undo, reads through a cached snapshot.

**Documents.** One for the deployment and, since spec 2026-10-29, one layer
per organization and per repository that set anything of their own
(:mod:`embry0.settings.scope`). A key takes the value of the most specific
layer that sets it, where its section lets that layer set it
(:mod:`embry0.settings.layers`).

**Reading.** A loop calls :meth:`SettingsStore.snapshot` once at the top of a
tick and reads every section it needs from that one snapshot, so a tick
never mixes two versions of any document. The snapshot holds every layer, so
a loop that works across many repositories resolves each one in memory:
``snapshot.get(SECTION, repo=repo)``. The call is one query, an aggregate
over the documents' write counters and write times; every layer is read
again, in one statement, whenever it differs from the snapshot's, in either
direction, so a database restored to an older copy is read again too. The
counters go up in the transaction of every write, so a reader that sees a
new token also sees its values, in this process or any other: there is
nothing to notify. When the check fails, the last snapshot is used and a
warning logged; a tick never fails because of it, nor because of a stored
value: one the code refuses reads as its default, or as the inherited value
for a layer.

**Writing.** Every write locks its document, checks the whole document
(:func:`embry0.settings.document.check`), a layer together with the values
it inherits, read under the locks of the documents it inherits from so a
concurrent write to them commits first, stores the text and its values,
and appends a
``config_revisions`` row in the same transaction when the text changed. A
write of some keys of one section (:meth:`SettingsStore.set_keys`) is made
to the text it finds under that lock, so it keeps the text's comments and
loses no write made in between, and is checked as a whole document. A
write that cannot be recorded does not happen. Undo reverses only the newest
change of a document, and only while its stored text is still what that
change wrote (ADR 0019's rule); it is recorded too, with source ``restore``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final, cast

import asyncpg
import structlog

from embry0.settings.document import (
    Change,
    Checked,
    DocumentInvalid,
    DocumentTooLarge,
    Problem,
    Sections,
    changes,
    check,
    dump_yaml,
    load_yaml,
    patch_section,
    read_sections,
    text_diff,
)
from embry0.settings.layers import LayerRefusal, Resolved, resolve, settable_values
from embry0.settings.registry import DOCUMENT_KEYS, MANAGED_BY_KEY, SCHEMA_VERSION, Catalog, Section, SectionModel
from embry0.settings.scope import DEPLOYMENT, Scope, ScopeError, current, owner_of, stored_rows
from embry0.storage import repo_renames
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.config_revisions import ConfigRevisionsRepository, RevisionSource
from embry0.storage.repositories.settings_documents import SettingsDocumentsRepository, token_of_rows

logger = structlog.get_logger(__name__)


#: The ``config_revisions`` surface a settings document is recorded under.
SURFACE: Final = "settings"
#: Why an older change cannot be undone; the same words as a repository's history.
WHY_NOT: Final = "This changed again later. Undo the newer change first."
#: The most layer values one answer names in ``layers_refused``; ``layers_refused_total`` counts them all.
MAX_LAYERS_REFUSED: Final = 100


class GenerationMismatch(Exception):
    """The document changed since the caller read it (``If-Match``)."""

    def __init__(self, current: int) -> None:
        super().__init__(f"the settings document is at generation {current}")
        self.current = current


class RevisionNotFound(LookupError):
    """No revision of this document has that id."""


class NotUndoable(Exception):
    """Only the newest change can be undone, while it is still what is stored."""


class RevisionInvalid(DocumentInvalid):
    """The text an undo would put back is refused by today's sections."""


def _layer_scopes(owner: str | None, repo: str | None) -> tuple[Scope | None, Scope | None]:
    """The organization and repository layers a read names; a name no layer can have names none."""
    if owner and repo:
        raise ValueError("pass repo= or owner=, not both: the owner of a repository is derived from it")
    repo_scope: Scope | None = None
    if repo:
        try:
            repo_scope = Scope.repository(repo)
        except ScopeError:
            repo_scope = None
        owner = repo_scope.owner if repo_scope is not None else owner_of(repo)
    org_scope: Scope | None = None
    if owner:
        try:
            org_scope = Scope.organization(owner)
        except ScopeError:
            org_scope = None
    return org_scope, repo_scope


@dataclass(frozen=True)
class Snapshot:
    """Every settings document as one tick reads it.

    ``generation``, ``data``, ``sections`` and ``updated_at`` are the
    deployment's document; ``layers`` maps each organization's and
    repository's stored scope string to its stored values. Which stored
    repository layer a repository reads is worked out when it is read, with
    ``REPO_RENAMES`` as installed then (:func:`embry0.settings.scope.stored_rows`).
    Resolutions are worked out the first time they are asked for and kept
    for the snapshot's life.
    """

    generation: int
    data: Mapping[str, Any]
    sections: Sections
    #: When the deployment's row was last written.
    updated_at: Any = None
    #: What the freshness check compares (``SettingsDocumentsRepository.token``).
    token: tuple[Any, ...] = ()
    layers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: False for a snapshot a write only imagines (a dry run): it logs nothing.
    log: bool = True
    #: The sections this snapshot was read with; None reads only the deployment's (``inherited`` needs it).
    catalog: Catalog | None = field(default=None, compare=False, repr=False)
    _memo: dict[tuple[str, str, str], Resolved] = field(default_factory=dict, compare=False, repr=False)
    _logged: set[tuple[str, str, str]] = field(default_factory=set, compare=False, repr=False)
    #: ``[the REPO_RENAMES it was built with, current repository scope -> stored scope]``.
    _rows: list[Any] = field(default_factory=list, compare=False, repr=False)

    def get[M: SectionModel](self, section: Section[M], *, owner: str | None = None, repo: str | None = None) -> M:
        """``section``'s settings: the deployment's stored values over its defaults, then those of the
        organization (``owner``, or the owner of ``repo``) and of the repository ``repo`` where the
        section lets them set a key. Neither named reads the deployment's."""
        return cast(M, self.resolve(section, owner=owner, repo=repo).model)

    def scoped[M: SectionModel](self, section: Section[M]) -> Scoped[M]:
        """``section`` of this snapshot, for a loop that reads it per organization or per repository."""
        return Scoped(self, section)

    def _repository_rows(self) -> dict[str, str]:
        """Each repository's current scope string to the stored layer it reads, for the renames installed now."""
        renames = repo_renames.REPO_RENAMES
        if self._rows and self._rows[0] is renames:
            return cast(dict[str, str], self._rows[1])
        index, shadowed = stored_rows(self.layers)
        if self._rows:
            # Installed after the snapshot was first read: what was resolved before read other rows.
            self._memo.clear()
        self._rows[:] = [renames, index]
        if self.log:
            for stored, used in shadowed:
                logger.warning("settings_layer_shadowed", scope=stored, by=used)
        return index

    def layer_data(self, scope: Scope) -> Mapping[str, Any]:
        """The stored values of the layer at ``scope``: for a repository, the row it reads under its current slug."""
        if scope.layer == "repository":
            stored = self._repository_rows().get(str(current(scope)))
            return self.layers.get(stored, {}) if stored is not None else {}
        return self.layers.get(str(scope), {})

    def resolve(self, section: Section[Any], *, owner: str | None = None, repo: str | None = None) -> Resolved:
        """:meth:`get`, with which layer supplied what and which layer values are refused."""
        try:
            base = self.sections.models[section.name]
        except KeyError:
            raise KeyError(f"section {section.name!r} is not in this store's catalog") from None
        org_scope, repo_scope = _layer_scopes(owner, repo)
        if not (section.org_keys or section.repo_keys) or (org_scope is None and repo_scope is None):
            return Resolved(base)
        org_values = settable_values(section, "organization", self.layer_data(org_scope)) if org_scope else {}
        repo_values = settable_values(section, "repository", self.layer_data(repo_scope)) if repo_scope else {}
        if not org_values and not repo_values:
            return Resolved(base)
        memo_key = (
            section.name,
            str(org_scope) if org_values else "",
            str(repo_scope) if repo_values else "",
        )
        found = self._memo.get(memo_key)
        if found is not None:
            return found
        layered = [(str(org_scope), org_values), (str(repo_scope), repo_values)]
        result = resolve(
            section,
            base.model_dump(mode="json", exclude_unset=True),
            [(scope, values) for scope, values in layered if values],
            base_model=base,
        )
        self._memo[memo_key] = result
        if self.log:
            for refusal in result.refused:
                once = (refusal.scope, refusal.section, refusal.key)
                if once not in self._logged:
                    self._logged.add(once)
                    logger.warning(
                        "settings_layer_value_refused",
                        scope=refusal.scope,
                        section=refusal.section,
                        key=refusal.key,
                        reason=refusal.reason,
                        hint="refused together with the values it inherits; the inherited value is read",
                    )
        return result

    def inherited(self, scope: Scope) -> dict[str, dict[str, Any]] | None:
        """The values a document at ``scope`` inherits now, per section, without defaults; None for the deployment."""
        if scope.layer == DEPLOYMENT:
            return None
        out: dict[str, dict[str, Any]] = {}
        for section in self.catalog or ():
            owner = current(scope).owner if scope.layer == "repository" else None
            model = self.resolve(section, owner=owner).model
            out[section.name] = model.model_dump(mode="json", exclude_unset=True)
        return out


@dataclass(frozen=True)
class Scoped[M: SectionModel]:
    """One section of one snapshot, read per organization or per repository."""

    snapshot: Snapshot
    section: Section[M]

    def deployment(self) -> M:
        return self.snapshot.get(self.section)

    def owner(self, owner: str | None) -> M:
        """The deployment's values and the organization ``owner``'s."""
        return self.snapshot.get(self.section, owner=owner)

    def repo(self, repo: str | None) -> M:
        """The deployment's values, those of ``repo``'s organization and ``repo``'s own; None reads the deployment's."""
        return self.snapshot.get(self.section, repo=repo)


def build_snapshot(
    catalog: Catalog,
    rows: Sequence[Mapping[str, Any]],
    *,
    token: tuple[Any, ...] | None = None,
    log: bool = True,
) -> Snapshot:
    """A snapshot of ``rows`` (``SettingsDocumentsRepository.all``), each layer under its stored scope string."""
    deployment = next((row for row in rows if row["scope"] == DEPLOYMENT), None)
    data: dict[str, Any] = dict(deployment["data"]) if deployment is not None else {}
    generation = int(deployment["generation"]) if deployment is not None else 0
    sections = read_sections(catalog, data)
    if log:
        for refusal in sections.refused:
            logger.warning(
                "settings_stored_value_invalid",
                section=refusal.section,
                key=refusal.key,
                reason=refusal.reason,
                generation=generation,
            )
        if sections.unknown or sections.unknown_keys:
            logger.info(
                "settings_sections_unknown",
                sections=list(sections.unknown),
                keys=list(sections.unknown_keys),
                generation=generation,
            )
    layers: dict[str, Mapping[str, Any]] = {}
    for row in sorted((r for r in rows if r["scope"] != DEPLOYMENT), key=lambda r: str(r["scope"])):
        try:
            stored = Scope.parse(str(row["scope"]))
        except ScopeError:
            if log:
                logger.warning("settings_layer_scope_unknown", scope=str(row["scope"]))
            continue
        values = dict(row["data"] or {})
        layers[str(stored)] = values
        if log:
            _log_ignored(catalog, stored, values)
    return Snapshot(
        generation=generation,
        data=data,
        sections=sections,
        updated_at=deployment["updated_at"] if deployment is not None else None,
        token=token if token is not None else token_of_rows([dict(r) for r in rows]),
        layers=layers,
        log=log,
        catalog=catalog,
    )


def _log_ignored(catalog: Catalog, scope: Scope, data: Mapping[str, Any]) -> None:
    """Log the sections and keys a layer stores that this release does not let it set."""
    ignored: list[str] = []
    for name, raw in data.items():
        if name in DOCUMENT_KEYS:
            continue  # a key of the document itself (managed_by), not a section
        section = catalog.get(name)
        if section is None:
            ignored.append(name)
            continue
        if isinstance(raw, dict):
            allowed = section.settable(scope.layer)
            ignored.extend(f"{name}.{key}" for key in raw if key not in allowed)
    if ignored:
        logger.info("settings_layer_keys_ignored", scope=str(scope), keys=sorted(ignored))


def replaced(catalog: Catalog, snapshot: Snapshot, stored: Scope, data: Mapping[str, Any]) -> Snapshot:
    """``snapshot`` with the document stored at ``stored`` holding ``data``: what a write makes, or made. Logs nothing."""
    rows: list[dict[str, Any]] = [
        {"scope": DEPLOYMENT, "data": dict(snapshot.data), "generation": snapshot.generation, "updated_at": None}
    ]
    rows.extend(
        {"scope": key, "data": dict(values), "generation": 0, "updated_at": None}
        for key, values in snapshot.layers.items()
    )
    key = str(stored)
    rows = [row for row in rows if row["scope"] != key]
    rows.append({"scope": key, "data": dict(data), "generation": 0, "updated_at": None})
    return build_snapshot(catalog, rows, token=(), log=False)


def layer_refusals(
    catalog: Catalog, snapshot: Snapshot, under: Scope, *, only: Collection[str] | None = None
) -> dict[tuple[str, str, str], LayerRefusal]:
    """Every refused value of the layers that inherit from ``under``: all of them for the deployment,
    an organization's repositories for an organization, none for a repository. ``only`` limits it to
    those layers' current scope strings."""
    out: dict[tuple[str, str, str], LayerRefusal] = {}
    if under.layer == "repository":
        return out
    seen: set[str] = set()
    for key in snapshot.layers:
        try:
            scope = current(Scope.parse(key))
        except ScopeError:
            continue
        name = str(scope)
        if name in seen or (only is not None and name not in only):
            continue
        seen.add(name)
        if under.layer == "organization" and not (scope.layer == "repository" and scope.owner == under.name):
            continue
        for section in catalog:
            if not section.settable(scope.layer):
                continue
            result = (
                snapshot.resolve(section, owner=scope.name)
                if scope.layer == "organization"
                else snapshot.resolve(section, repo=scope.name)
            )
            for refusal in result.refused:
                if refusal.scope == name:
                    out[(refusal.scope, refusal.section, refusal.key)] = refusal
    return out


def newly_refused(
    catalog: Catalog, before: Snapshot, after: Snapshot, under: Scope
) -> tuple[list[dict[str, Any]], int]:
    """The values of the layers under ``under`` that ``after`` reads as inherited and ``before`` did not.

    Every layer under ``under`` is resolved in ``after``; in ``before`` only
    the few that ``after`` refuses something of, so a change to the
    deployment's document resolves each stored layer once, not twice.
    Answers at most :data:`MAX_LAYERS_REFUSED` of them, and how many there are.
    """
    new = layer_refusals(catalog, after, under)
    if not new:
        return [], 0
    old = layer_refusals(catalog, before, under, only={refusal.scope for refusal in new.values()})
    fresh = [refusal for triple, refusal in sorted(new.items()) if triple not in old]
    shown = [
        {"scope": Scope.parse(r.scope).view(), "key": f"{r.section}.{r.key}", "reason": r.reason}
        for r in fresh[:MAX_LAYERS_REFUSED]
    ]
    return shown, len(fresh)


@dataclass
class Draft:
    """The locked document inside :meth:`SettingsStore.edit`; change it with :meth:`replace`."""

    conn: asyncpg.Connection
    yaml_text: str
    data: dict[str, Any]
    schema_version: int
    #: ``section.key`` of every key whose environment value was imported (or found unset) already.
    bootstrapped: list[str]
    generation: int
    updated_by: str
    #: The document as callers name it: a repository under its current slug.
    scope: Scope = field(default_factory=Scope.deployment)
    #: Where it is stored: for a repository renamed since its layer was written, under the old slug.
    stored: Scope = field(default_factory=Scope.deployment)
    #: Set once the block has written: the new revision, or None when the text did not change.
    revision_id: int | None = None
    dirty: bool = False

    def replace(self, yaml_text: str, data: dict[str, Any], schema_version: int) -> None:
        self.yaml_text, self.data, self.schema_version = yaml_text, data, schema_version
        self.dirty = True

    def mark_bootstrapped(self, keys: Iterable[str]) -> None:
        """Add ``section.key`` entries to the environment import's ledger."""
        added = [key for key in keys if key not in self.bootstrapped]
        if added:
            self.bootstrapped = [*self.bootstrapped, *added]
            self.dirty = True


@dataclass(frozen=True)
class SaveResult:
    generation: int
    #: The new revision; None when the text was unchanged and nothing was recorded.
    revision_id: int | None
    unknown_sections: tuple[str, ...]
    changes: list[dict[str, Any]]
    unknown_keys: tuple[str, ...] = ()
    #: Values of the layers under this document that the change made read as inherited, at most
    #: :data:`MAX_LAYERS_REFUSED`; ``layers_refused_total`` counts them all.
    layers_refused: list[dict[str, Any]] = field(default_factory=list)
    layers_refused_total: int = 0
    #: ``"file"`` when the document, as stored now, says it is kept in a file; else None.
    managed_by: str | None = None


@dataclass(frozen=True)
class Preview:
    would_change: bool
    text_diff: str
    changes: list[dict[str, Any]]
    unknown_sections: tuple[str, ...]
    unknown_keys: tuple[str, ...] = ()
    layers_refused: list[dict[str, Any]] = field(default_factory=list)
    layers_refused_total: int = 0
    #: ``"file"`` when the document, as the change would store it, says it is kept in a file; else None.
    managed_by: str | None = None


#: What a write makes of the document it replaces: from the stored text, the stored values and what the
#: document inherits (None for the deployment's), the text to store and its checked values. Raises
#: :class:`DocumentInvalid` listing every problem.
Proposal = Callable[[str, Mapping[str, Any], Mapping[str, Mapping[str, Any]] | None], tuple[str, Checked]]


def _matches(expected: int | Collection[int] | None, generation: int) -> bool:
    if expected is None:
        return True
    return generation == expected if isinstance(expected, int) else generation in expected


def _document(row: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a row a revision keeps, before and after."""
    return {"yaml_text": row["yaml_text"], "data": row["data"], "schema_version": row["schema_version"]}


def _empty(scope: Scope) -> dict[str, Any]:
    return {
        "scope": str(scope),
        "yaml_text": "",
        "data": {},
        "schema_version": SCHEMA_VERSION,
        "generation": 0,
        "bootstrapped_keys": [],
        "updated_by": "",
        "updated_at": None,
    }


def _ancestors(scope: Scope) -> list[Scope]:
    """The documents ``scope`` inherits from, most general first."""
    if scope.layer == "organization":
        return [Scope.deployment()]
    if scope.layer == "repository":
        parent = current(scope).parent
        return [Scope.deployment(), *([parent] if parent is not None else [])]
    return []


class SettingsStore:
    """Every settings document of the deployment, for every reader and writer in this process."""

    def __init__(self, db: DatabasePool, catalog: Catalog | None = None) -> None:
        if catalog is None:
            from embry0.settings.sections import CATALOG

            catalog = CATALOG
        self._catalog = catalog
        self._documents = SettingsDocumentsRepository(db)
        self._revisions = ConfigRevisionsRepository(db)
        self._db = db
        self._snapshot: Snapshot | None = None

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def db(self) -> DatabasePool:
        """The pool the documents live in, which the environment import writes its audit row to."""
        return self._db

    # ---- reading ------------------------------------------------------------

    async def snapshot(self) -> Snapshot:
        """Every document, read again only when a write counter or write time moved."""
        try:
            token = await self._documents.token()
            if self._snapshot is not None and token == self._snapshot.token:
                return self._snapshot
            return self._keep(await self._documents.all())
        except Exception:
            if self._snapshot is None:
                raise
            logger.warning("settings_refresh_failed", generation=self._snapshot.generation, exc_info=True)
            return self._snapshot

    def _keep(self, rows: Sequence[Mapping[str, Any]]) -> Snapshot:
        """``rows`` as the snapshot: the cached one when they add up to its token, else a new one."""
        token = token_of_rows([dict(r) for r in rows])
        if self._snapshot is not None and self._snapshot.token == token:
            return self._snapshot
        self._snapshot = build_snapshot(self._catalog, rows, token=token)
        return self._snapshot

    async def _stored(self, scope: Scope, conn: asyncpg.Connection | None = None) -> Scope:
        """Where the document at ``scope`` is stored.

        A repository renamed since its layer was written (``REPO_RENAMES``)
        reads the row under its old slug while none is stored under the new
        one, as a snapshot does; that row is what is opened, written and
        undone, so a save never starts a second layer beside it.
        """
        if scope.layer != "repository":
            return scope
        name = str(current(scope))
        index, _ = stored_rows(await self._documents.repository_scopes(conn))
        found = index.get(name)
        return Scope.parse(found) if found is not None and found != name else current(scope)

    async def document(self, scope: Scope | None = None) -> dict[str, Any]:
        """The stored document at ``scope`` (the deployment's when None), read now: text, values, versions
        and who wrote it. A layer nobody wrote is an empty document at generation 0."""
        stored = await self._stored(scope or Scope.deployment())
        row = await self._documents.get(str(stored))
        return row if row is not None else _empty(stored)

    async def layers(self) -> list[dict[str, Any]]:
        """Every stored organization and repository layer: scope, values, generation and who wrote it."""
        return await self._documents.layers()

    # ---- writing ------------------------------------------------------------

    @asynccontextmanager
    async def edit(
        self, *, actor: str, source: RevisionSource, reason: str = "", scope: Scope | None = None
    ) -> AsyncIterator[Draft]:
        """The locked document at ``scope``, written back when the block changed it.

        A layer's write first queues behind every writer of the documents it
        inherits from (:meth:`SettingsDocumentsRepository.hold`), so what it
        reads of them inside the block is what they hold once those writers
        commit. A revision is appended when the text changed. An exception in
        the block rolls everything back.
        """
        scope = scope or Scope.deployment()
        async with self._db.transaction() as conn:
            for ancestor in _ancestors(scope):
                await self._documents.hold(conn, str(ancestor))
            await self._documents.hold(conn, str(current(scope)))
            stored = await self._stored(scope, conn)
            key = str(stored)
            row = await self._documents.lock(conn, key)
            draft = Draft(
                conn=conn,
                yaml_text=row["yaml_text"],
                data=dict(row["data"]),
                schema_version=int(row["schema_version"]),
                bootstrapped=list(row["bootstrapped_keys"]),
                generation=int(row["generation"]),
                updated_by=str(row["updated_by"]),
                scope=scope,
                stored=stored,
            )
            yield draft
            if draft.dirty:
                text_changed = draft.yaml_text != row["yaml_text"]
                target = current(scope)
                if text_changed and row.get("exists") and stored.layer == "repository" and key != str(target):
                    # A renamed repository's layer is written under its current slug from now on, with
                    # its history, so it does not depend on the rename entry staying in REPO_RENAMES.
                    await self._documents.move(conn, key, str(target))
                    await self._revisions.rescope(conn, key, str(target), SURFACE)
                    await self._revisions.append(
                        conn,
                        scope=str(target),
                        surface=SURFACE,
                        before=_document(row),
                        after=_document(row),
                        actor=actor,
                        source="upgrade",
                        reason=f"moved from {key}: REPO_RENAMES maps it to {target.name}",
                    )
                    logger.info("settings_layer_moved", scope=key, to=str(target))
                    key, draft.stored = str(target), target
                written = await self._documents.write(
                    conn,
                    key,
                    yaml_text=draft.yaml_text,
                    data=draft.data,
                    schema_version=draft.schema_version,
                    bootstrapped_keys=draft.bootstrapped,
                    updated_by=actor if text_changed else draft.updated_by,
                )
                if text_changed:
                    draft.revision_id = await self._revisions.append(
                        conn,
                        scope=key,
                        surface=SURFACE,
                        before=_document(row),
                        after=_document(written),
                        actor=actor,
                        source=source,
                        reason=reason,
                    )
                draft.generation = int(written["generation"])

    async def _inherited_now(self, conn: asyncpg.Connection, scope: Scope) -> dict[str, dict[str, Any]] | None:
        """What the document at ``scope`` inherits, read on ``conn`` inside :meth:`edit`, after its holds."""
        if scope.layer == DEPLOYMENT:
            return None
        rows = await self._documents.rows(conn, [str(ancestor) for ancestor in _ancestors(scope)])
        return build_snapshot(self._catalog, rows, token=(), log=False).inherited(scope)

    async def _refused_by(
        self, scope: Scope, stored: Scope, before: Mapping[str, Any], after: Snapshot
    ) -> tuple[list[dict[str, Any]], int]:
        """The layer values under ``scope`` that writing it, from ``before`` to what ``after`` holds, refused."""
        if scope.layer == "repository":
            return [], 0
        return newly_refused(self._catalog, replaced(self._catalog, after, stored, before), after, scope)

    async def save(
        self,
        text: str | None,
        values: Mapping[str, Any],
        *,
        actor: str,
        source: RevisionSource = "person",
        reason: str = "",
        if_generation: int | Collection[int] | None = None,
        scope: Scope | None = None,
    ) -> SaveResult:
        """Replace the document at ``scope`` with ``values`` (parsed from ``text``, or a JSON object when
        ``text`` is None).

        Raises :class:`DocumentInvalid` listing every problem, or
        :class:`GenerationMismatch` when ``if_generation`` (one generation, or
        those an ``If-Match`` names) does not hold the stored one.
        An unchanged text stores nothing and records nothing.
        """
        scope = scope or Scope.deployment()
        return await self._write(
            self._whole(text, values, scope),
            actor=actor,
            source=source,
            reason=reason,
            if_generation=if_generation,
            scope=scope,
        )

    async def set_keys(
        self,
        section: str,
        keys: Mapping[str, Any],
        *,
        actor: str,
        source: RevisionSource = "person",
        reason: str = "",
        if_generation: int | Collection[int] | None = None,
        scope: Scope | None = None,
    ) -> SaveResult:
        """Set ``keys`` of ``section`` in the document at ``scope``, a None value taking its key out, and
        leave the rest of the document as stored, its comments included (:func:`patch_section`).

        Checked, stored, recorded and refused as :meth:`save` is, the whole
        document it makes included; the change is made to the text stored
        under the document's lock, so a write in between is never lost, and
        ``if_generation`` refuses one made since the caller read it.
        """
        scope = scope or Scope.deployment()
        return await self._write(
            self._keys(section, keys, scope),
            actor=actor,
            source=source,
            reason=reason,
            if_generation=if_generation,
            scope=scope,
        )

    def _whole(self, text: str | None, values: Mapping[str, Any], scope: Scope) -> Proposal:
        """A write of the whole document: ``values``, kept as ``text`` (or the YAML a JSON object dumps to)."""

        def propose(
            stored_text: str, stored: Mapping[str, Any], inherited: Mapping[str, Mapping[str, Any]] | None
        ) -> tuple[str, Checked]:
            checked = check(
                self._catalog, values, stored=stored, stored_text=stored_text, layer=scope.layer, inherited=inherited
            )
            return (text if text is not None else dump_yaml(values)), checked

        return propose

    def _keys(self, section: str, keys: Mapping[str, Any], scope: Scope) -> Proposal:
        """A write of some keys of one section, into the stored text."""

        def propose(
            stored_text: str, stored: Mapping[str, Any], inherited: Mapping[str, Mapping[str, Any]] | None
        ) -> tuple[str, Checked]:
            return patch_section(
                self._catalog,
                section,
                keys,
                stored=stored,
                stored_text=stored_text,
                layer=scope.layer,
                inherited=inherited,
            )

        return propose

    async def _write(
        self,
        propose: Proposal,
        *,
        actor: str,
        source: RevisionSource,
        reason: str,
        if_generation: int | Collection[int] | None,
        scope: Scope,
    ) -> SaveResult:
        """Store what ``propose`` makes of the locked document, when its text changes."""
        async with self.edit(actor=actor, source=source, reason=reason, scope=scope) as draft:
            if not _matches(if_generation, draft.generation):
                raise GenerationMismatch(draft.generation)
            before = draft.data
            inherited = await self._inherited_now(draft.conn, scope)
            new_text, checked = propose(draft.yaml_text, draft.data, inherited)
            if new_text != draft.yaml_text:
                draft.replace(new_text, checked.data, checked.schema_version)
        refused: tuple[list[dict[str, Any]], int] = ([], 0)
        if draft.revision_id is not None:
            refused = await self._refused_by(scope, draft.stored, before, await self.snapshot())
        return SaveResult(
            generation=draft.generation,
            revision_id=draft.revision_id,
            unknown_sections=checked.unknown_sections,
            changes=describe_changes(self._catalog, before, draft.data, scope=scope, inherited=inherited),
            unknown_keys=checked.unknown_keys,
            layers_refused=refused[0],
            layers_refused_total=refused[1],
            managed_by=draft.data.get(MANAGED_BY_KEY),
        )

    async def preview(
        self,
        text: str | None,
        values: Mapping[str, Any],
        *,
        if_generation: int | Collection[int] | None = None,
        scope: Scope | None = None,
    ) -> Preview:
        """What :meth:`save` would change, storing nothing. Raises as :meth:`save` does, ``If-Match`` included."""
        scope = scope or Scope.deployment()
        return await self._dry_run(self._whole(text, values, scope), if_generation=if_generation, scope=scope)

    async def preview_keys(
        self,
        section: str,
        keys: Mapping[str, Any],
        *,
        if_generation: int | Collection[int] | None = None,
        scope: Scope | None = None,
    ) -> Preview:
        """What :meth:`set_keys` would change, storing nothing. Raises as it does, ``If-Match`` included."""
        scope = scope or Scope.deployment()
        return await self._dry_run(self._keys(section, keys, scope), if_generation=if_generation, scope=scope)

    async def _dry_run(
        self, propose: Proposal, *, if_generation: int | Collection[int] | None, scope: Scope
    ) -> Preview:
        """What ``propose`` would make of the stored document, storing nothing."""
        snapshot = await self.snapshot()
        inherited = snapshot.inherited(scope)
        stored = await self._stored(scope)
        row = await self._documents.get(str(stored)) or _empty(stored)
        if not _matches(if_generation, int(row["generation"])):
            raise GenerationMismatch(int(row["generation"]))
        new_text, checked = propose(row["yaml_text"], row["data"], inherited)
        would_change = new_text != row["yaml_text"]
        refused: tuple[list[dict[str, Any]], int] = ([], 0)
        if would_change and scope.layer != "repository":
            after = replaced(self._catalog, snapshot, stored, checked.data)
            refused = newly_refused(self._catalog, snapshot, after, scope)
        return Preview(
            would_change=would_change,
            text_diff=text_diff(row["yaml_text"], new_text),
            changes=describe_changes(self._catalog, row["data"], checked.data, scope=scope, inherited=inherited),
            unknown_sections=checked.unknown_sections,
            unknown_keys=checked.unknown_keys,
            layers_refused=refused[0],
            layers_refused_total=refused[1],
            managed_by=checked.data.get(MANAGED_BY_KEY),
        )

    # ---- history ------------------------------------------------------------

    async def revisions(self, limit: int = 50, scope: Scope | None = None) -> list[dict[str, Any]]:
        """The revisions of the document at ``scope`` (the deployment's when None), newest first."""
        stored = await self._stored(scope or Scope.deployment())
        return await self._revisions.history(str(stored), SURFACE, limit=limit)

    async def revision(self, revision_id: int) -> dict[str, Any] | None:
        """One revision of any document; its ``scope`` says where it is stored."""
        row = await self._revisions.get(revision_id)
        if row is None or row["surface"] != SURFACE:
            return None
        return row

    async def undo(self, revision_id: int, *, actor: str) -> SaveResult:
        """Put back what revision ``revision_id`` replaced, in the document it changed.

        Raises :class:`RevisionNotFound`, :class:`NotUndoable` (not the newest
        change of that document, or its stored text is no longer what the
        change wrote) or :class:`RevisionInvalid` (today's sections refuse the
        old text, or it no longer combines with what the document inherits).
        """
        revision = await self.revision(revision_id)
        if revision is None:
            raise RevisionNotFound(revision_id)
        try:
            scope = current(Scope.parse(str(revision["scope"])))
        except ScopeError:
            raise RevisionNotFound(revision_id) from None
        before_doc: Mapping[str, Any] = revision["before"] or {}
        after_doc: Mapping[str, Any] = revision["after"] or {}
        async with self.edit(
            actor=actor, source="restore", reason=f"undo of change {revision_id}", scope=scope
        ) as draft:
            latest = await self._revisions.latest_on(draft.conn, str(draft.stored), SURFACE)
            if (
                str(draft.stored) != str(revision["scope"])
                or latest is None
                or latest["id"] != revision_id
                or after_doc.get("yaml_text") != draft.yaml_text
            ):
                raise NotUndoable(WHY_NOT)
            before = draft.data
            inherited = await self._inherited_now(draft.conn, scope)
            text = str(before_doc.get("yaml_text") or "")
            problem = Problem((), "revision_invalid", "That earlier version is no longer valid.")
            try:
                checked = check(
                    self._catalog,
                    load_yaml(text),
                    stored=draft.data,
                    stored_text=draft.yaml_text,
                    layer=scope.layer,
                    inherited=inherited,
                )
            except DocumentInvalid as exc:
                raise RevisionInvalid([problem, *exc.problems]) from None
            except DocumentTooLarge as exc:
                raise RevisionInvalid([problem, Problem((), "too_large", str(exc))]) from None
            draft.replace(text, checked.data, checked.schema_version)
        refused = await self._refused_by(scope, draft.stored, before, await self.snapshot())
        return SaveResult(
            generation=draft.generation,
            revision_id=draft.revision_id,
            unknown_sections=checked.unknown_sections,
            changes=describe_changes(self._catalog, before, draft.data, scope=scope, inherited=inherited),
            unknown_keys=checked.unknown_keys,
            layers_refused=refused[0],
            layers_refused_total=refused[1],
        )


def _models(
    catalog: Catalog, data: Mapping[str, Any], scope: Scope, inherited: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, SectionModel]:
    """Each section as a reader of the document at ``scope`` holding ``data`` gets it."""
    if inherited is None:
        return dict(read_sections(catalog, data).models)
    out: dict[str, SectionModel] = {}
    for section in catalog:
        base = dict(inherited.get(section.name) or {})
        values = settable_values(section, scope.layer, data)
        out[section.name] = resolve(section, base, [(str(scope), values)] if values else []).model
    return out


def describe_changes(
    catalog: Catalog,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    scope: Scope | None = None,
    inherited: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Each stored key that differs, with the value a reader gets before and after.

    ``before`` and ``after`` are the stored values, null when the key was not
    written; ``effective_*`` is what a loop reads, the default included, and
    for a layer what it inherits. A key of a section this code does not know
    has no effective value, and ``managed_by``, which no loop reads, none at
    all.
    """
    scope = scope or Scope.deployment()
    old, new = _models(catalog, before, scope, inherited), _models(catalog, after, scope, inherited)
    out: list[dict[str, Any]] = []
    for change in changes(before, after):
        described: dict[str, Any] = {
            "key": change.path,
            "before": change.before,
            "after": change.after,
            "set_before": change.set_before,
            "set_after": change.set_after,
        }
        if change.key:  # a key of the document itself (managed_by) is read by no loop, so it has no effective value
            described["effective_before"] = _effective(old, change)
            described["effective_after"] = _effective(new, change)
        out.append(described)
    return out


def _effective(models: Mapping[str, SectionModel], change: Change) -> Any:
    model = models.get(change.section)
    if model is None:
        return None
    return model.model_dump(mode="json").get(change.key)


def section_reader[M: SectionModel](store: SettingsStore, section: Section[M]) -> Callable[[], Awaitable[M]]:
    """A callable a loop is built with: each call returns ``section`` from the current snapshot.

    For a loop that reads one section for the whole deployment; a loop that
    reads it per organization or per repository is built with
    :func:`scoped_reader`, and one that reads several calls
    ``store.snapshot()`` once per tick and reads each from it.
    """

    async def read() -> M:
        return (await store.snapshot()).get(section)

    return read


def scoped_reader[M: SectionModel](store: SettingsStore, section: Section[M]) -> Callable[[], Awaitable[Scoped[M]]]:
    """A callable a loop that works per organization or per repository is built with.

    Each call takes the current snapshot once; the loop then reads
    ``scoped.repo(repo)`` or ``scoped.owner(owner)`` for each one it handles,
    in memory.
    """

    async def read() -> Scoped[M]:
        return (await store.snapshot()).scoped(section)

    return read
