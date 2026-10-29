"""The settings store: writes with history and undo, reads through a cached snapshot.

**Reading.** A loop calls :meth:`SettingsStore.snapshot` once at the top of a
tick and reads every section it needs from that one snapshot, so a tick
never mixes two versions of the document. The call is one primary-key query
for the row's ``generation`` and ``updated_at``; the values are read again
whenever either differs from the snapshot's, in either direction, so a
database restored to an older copy is read again too. The counter goes up in
the transaction of every write, so a reader that sees a new generation also
sees its values, in this process or any other: there is nothing to notify.
This process's own writes replace its snapshot at once. When the check
fails, the last snapshot is used and a warning logged; a tick never fails
because of it, nor because of a stored value: one the code refuses reads as
its default.

**Writing.** Every write locks the row, checks the whole document
(:func:`embry0.settings.document.check`), stores the text and its values,
and appends a ``config_revisions`` row in the same transaction when the text
changed. A write that cannot be recorded does not happen. Undo reverses only
the newest change, and only while the stored text is still what that change
wrote (ADR 0019's rule); it is recorded too, with source ``restore``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, cast

import asyncpg
import structlog

from embry0.settings.document import (
    Change,
    DocumentInvalid,
    DocumentTooLarge,
    Problem,
    Sections,
    changes,
    check,
    dump_yaml,
    load_yaml,
    read_sections,
    text_diff,
)
from embry0.settings.registry import SCHEMA_VERSION, Catalog, Section, SectionModel
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.config_revisions import ConfigRevisionsRepository, RevisionSource
from embry0.storage.repositories.settings_documents import DEPLOYMENT, SettingsDocumentsRepository

logger = structlog.get_logger(__name__)


#: The ``config_revisions`` surface a settings document is recorded under.
SURFACE: Final = "settings"
#: Why an older change cannot be undone; the same words as a repository's history.
WHY_NOT: Final = "This changed again later. Undo the newer change first."


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


@dataclass(frozen=True)
class Snapshot:
    """The document as one tick reads it: every section of the catalog as its model."""

    generation: int
    data: Mapping[str, Any]
    sections: Sections
    #: When the row was last written; with ``generation``, what the freshness check compares.
    updated_at: Any = None

    def get[M: SectionModel](self, section: Section[M]) -> M:
        """``section``'s settings: its stored values over its defaults."""
        try:
            model = self.sections.models[section.name]
        except KeyError:
            raise KeyError(f"section {section.name!r} is not in this store's catalog") from None
        return cast(M, model)


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


@dataclass(frozen=True)
class Preview:
    would_change: bool
    text_diff: str
    changes: list[dict[str, Any]]
    unknown_sections: tuple[str, ...]
    unknown_keys: tuple[str, ...] = ()


def _matches(expected: int | Collection[int] | None, generation: int) -> bool:
    if expected is None:
        return True
    return generation == expected if isinstance(expected, int) else generation in expected


def _document(row: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a row a revision keeps, before and after."""
    return {"yaml_text": row["yaml_text"], "data": row["data"], "schema_version": row["schema_version"]}


class SettingsStore:
    """The deployment's settings document, for every reader and writer in this process."""

    def __init__(self, db: DatabasePool, catalog: Catalog | None = None, *, scope: str = DEPLOYMENT) -> None:
        if catalog is None:
            from embry0.settings.sections import CATALOG

            catalog = CATALOG
        self._catalog = catalog
        self._scope = scope
        self._documents = SettingsDocumentsRepository(db)
        self._revisions = ConfigRevisionsRepository(db)
        self._db = db
        self._snapshot: Snapshot | None = None

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def db(self) -> DatabasePool:
        """The pool the document lives in, which the environment import writes its audit row to."""
        return self._db

    # ---- reading ------------------------------------------------------------

    async def snapshot(self) -> Snapshot:
        """The current document, read again only when its generation or write time moved."""
        try:
            head = await self._documents.head(self._scope)
            if self._snapshot is not None and head == (self._snapshot.generation, self._snapshot.updated_at):
                return self._snapshot
            return self._keep(await self._documents.get(self._scope))
        except Exception:
            if self._snapshot is None:
                raise
            logger.warning("settings_refresh_failed", generation=self._snapshot.generation, exc_info=True)
            return self._snapshot

    async def document(self) -> dict[str, Any]:
        """The stored row, read now: text, values, versions and who wrote it."""
        row = await self._documents.get(self._scope)
        if row is None:
            return {
                "yaml_text": "",
                "data": {},
                "schema_version": SCHEMA_VERSION,
                "generation": 0,
                "bootstrapped_keys": [],
                "updated_by": "",
                "updated_at": None,
            }
        self._keep(row)
        return row

    def _keep(self, row: Mapping[str, Any] | None) -> Snapshot:
        """``row`` as the snapshot: the cached one when it is the same write, else a new one."""
        generation = int(row["generation"]) if row is not None else 0
        updated_at = row["updated_at"] if row is not None else None
        if self._snapshot is not None and (self._snapshot.generation, self._snapshot.updated_at) == (
            generation,
            updated_at,
        ):
            return self._snapshot
        data: dict[str, Any] = dict(row["data"]) if row is not None else {}
        sections = read_sections(self._catalog, data)
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
        self._snapshot = Snapshot(generation=generation, data=data, sections=sections, updated_at=updated_at)
        return self._snapshot

    # ---- writing ------------------------------------------------------------

    @asynccontextmanager
    async def edit(self, *, actor: str, source: RevisionSource, reason: str = "") -> AsyncIterator[Draft]:
        """The locked document, written back when the block changed it.

        A revision is appended when the text changed. An exception in the
        block rolls everything back.
        """
        written: dict[str, Any] | None = None
        async with self._db.transaction() as conn:
            row = await self._documents.lock(conn, self._scope)
            draft = Draft(
                conn=conn,
                yaml_text=row["yaml_text"],
                data=dict(row["data"]),
                schema_version=int(row["schema_version"]),
                bootstrapped=list(row["bootstrapped_keys"]),
                generation=int(row["generation"]),
                updated_by=str(row["updated_by"]),
            )
            yield draft
            if draft.dirty:
                text_changed = draft.yaml_text != row["yaml_text"]
                written = await self._documents.write(
                    conn,
                    self._scope,
                    yaml_text=draft.yaml_text,
                    data=draft.data,
                    schema_version=draft.schema_version,
                    bootstrapped_keys=draft.bootstrapped,
                    updated_by=actor if text_changed else draft.updated_by,
                )
                if text_changed:
                    draft.revision_id = await self._revisions.append(
                        conn,
                        scope=self._scope,
                        surface=SURFACE,
                        before=_document(row),
                        after=_document(written),
                        actor=actor,
                        source=source,
                        reason=reason,
                    )
                draft.generation = int(written["generation"])
        if written is not None:
            self._keep(written)

    async def save(
        self,
        text: str | None,
        values: Mapping[str, Any],
        *,
        actor: str,
        source: RevisionSource = "person",
        reason: str = "",
        if_generation: int | Collection[int] | None = None,
    ) -> SaveResult:
        """Replace the document with ``values`` (parsed from ``text``, or a JSON object when ``text`` is None).

        Raises :class:`DocumentInvalid` listing every problem, or
        :class:`GenerationMismatch` when ``if_generation`` (one generation, or
        those an ``If-Match`` names) does not hold the stored one.
        An unchanged text stores nothing and records nothing.
        """
        async with self.edit(actor=actor, source=source, reason=reason) as draft:
            if not _matches(if_generation, draft.generation):
                raise GenerationMismatch(draft.generation)
            before = draft.data
            checked = check(self._catalog, values, stored=draft.data, stored_text=draft.yaml_text)
            new_text = text if text is not None else dump_yaml(values)
            if new_text != draft.yaml_text:
                draft.replace(new_text, checked.data, checked.schema_version)
        return SaveResult(
            generation=draft.generation,
            revision_id=draft.revision_id,
            unknown_sections=checked.unknown_sections,
            changes=describe_changes(self._catalog, before, draft.data),
            unknown_keys=checked.unknown_keys,
        )

    async def preview(
        self, text: str | None, values: Mapping[str, Any], *, if_generation: int | Collection[int] | None = None
    ) -> Preview:
        """What :meth:`save` would change, storing nothing. Raises as :meth:`save` does, ``If-Match`` included."""
        row = await self.document()
        if not _matches(if_generation, int(row["generation"])):
            raise GenerationMismatch(int(row["generation"]))
        checked = check(self._catalog, values, stored=row["data"], stored_text=row["yaml_text"])
        new_text = text if text is not None else dump_yaml(values)
        return Preview(
            would_change=new_text != row["yaml_text"],
            text_diff=text_diff(row["yaml_text"], new_text),
            changes=describe_changes(self._catalog, row["data"], checked.data),
            unknown_sections=checked.unknown_sections,
            unknown_keys=checked.unknown_keys,
        )

    # ---- history ------------------------------------------------------------

    async def revisions(self, limit: int = 50) -> list[dict[str, Any]]:
        """This document's revisions, newest first."""
        return await self._revisions.history(self._scope, SURFACE, limit=limit)

    async def revision(self, revision_id: int) -> dict[str, Any] | None:
        row = await self._revisions.get(revision_id)
        if row is None or row["scope"] != self._scope or row["surface"] != SURFACE:
            return None
        return row

    async def undo(self, revision_id: int, *, actor: str) -> SaveResult:
        """Put back what revision ``revision_id`` replaced.

        Raises :class:`RevisionNotFound`, :class:`NotUndoable` (not the newest
        change, or the stored text is no longer what it wrote) or
        :class:`RevisionInvalid` (today's sections refuse the old text).
        """
        revision = await self.revision(revision_id)
        if revision is None:
            raise RevisionNotFound(revision_id)
        before_doc: Mapping[str, Any] = revision["before"] or {}
        after_doc: Mapping[str, Any] = revision["after"] or {}
        async with self.edit(actor=actor, source="restore", reason=f"undo of change {revision_id}") as draft:
            latest = await self._revisions.latest_on(draft.conn, self._scope, SURFACE)
            if latest is None or latest["id"] != revision_id or after_doc.get("yaml_text") != draft.yaml_text:
                raise NotUndoable(WHY_NOT)
            before = draft.data
            text = str(before_doc.get("yaml_text") or "")
            problem = Problem((), "revision_invalid", "That earlier version is no longer valid.")
            try:
                checked = check(self._catalog, load_yaml(text), stored=draft.data, stored_text=draft.yaml_text)
            except DocumentInvalid as exc:
                raise RevisionInvalid([problem, *exc.problems]) from None
            except DocumentTooLarge as exc:
                raise RevisionInvalid([problem, Problem((), "too_large", str(exc))]) from None
            draft.replace(text, checked.data, checked.schema_version)
        return SaveResult(
            generation=draft.generation,
            revision_id=draft.revision_id,
            unknown_sections=checked.unknown_sections,
            changes=describe_changes(self._catalog, before, draft.data),
            unknown_keys=checked.unknown_keys,
        )


def describe_changes(catalog: Catalog, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each stored key that differs, with the value a reader gets before and after.

    ``before`` and ``after`` are the stored values, null when the key was not
    written; ``effective_*`` is what a loop reads, the default included. A
    key of a section this code does not know has no effective value, and
    ``managed_by``, which no loop reads, none at all.
    """
    old, new = read_sections(catalog, before), read_sections(catalog, after)
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


def _effective(sections: Sections, change: Change) -> Any:
    model = sections.models.get(change.section)
    if model is None:
        return None
    return model.model_dump(mode="json").get(change.key)


def section_reader[M: SectionModel](store: SettingsStore, section: Section[M]) -> Callable[[], Awaitable[M]]:
    """A callable a loop is built with: each call returns ``section`` from the current snapshot.

    For a loop that reads one section; a loop that reads several calls
    ``store.snapshot()`` once per tick and reads each from it.
    """

    async def read() -> M:
        return (await store.snapshot()).get(section)

    return read
