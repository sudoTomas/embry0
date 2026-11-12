"""Each repository's areas as embry0 keeps them (spec 2026-11-10): the one place they are read and written.

Every reader (owner proposals, decisions, the world, the pull requests
embry0 opens, the leads, the scout and the Slop-O-Meter) is given this
store and reads the stored values, one query per call, with no cache: a
change applies at the next read. A write checks the document
(:mod:`embry0.services.areas_document`) and stores it with a revision, in
one transaction (:class:`~embry0.storage.repositories.repo_areas.RepoAreasRepository`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from embry0.services.areas import Area
from embry0.services.areas_document import areas_of as read_areas
from embry0.services.areas_document import changes, check, dump, load_yaml, text_diff
from embry0.storage.database import DatabasePool
from embry0.storage.repo_renames import repo_slug_variants
from embry0.storage.repositories.repo_areas import AreasChanged, RepoAreasRepository, snapshot
from embry0.storage.repositories.repo_config_revisions import RevisionSource

#: What a registered repository with no document reads.
EMPTY: dict[str, Any] = {"areas": []}


@dataclass(frozen=True)
class StoredAreas:
    """A repository's document as stored; generation 0 and no text when it has none."""

    repo: str
    yaml_text: str = ""
    data: dict[str, Any] = field(default_factory=lambda: dict(EMPTY))
    generation: int = 0
    updated_by: str = ""
    updated_at: datetime | None = None

    @property
    def areas(self) -> list[Area]:
        return read_areas(self.data)

    @property
    def stored(self) -> bool:
        return self.generation > 0


@dataclass(frozen=True)
class SaveResult:
    generation: int
    revision_id: int | None
    would_change: bool
    changes: list[dict[str, Any]]
    text_diff: str


class AreasStore:
    """Reads and writes each repository's areas document."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self._rows = RepoAreasRepository(db)

    async def get(self, repo: str) -> StoredAreas:
        """``repo``'s document; ``repo`` is the registry's spelling."""
        row = await self._rows.get(repo)
        if row is None:
            return StoredAreas(repo)
        return StoredAreas(
            repo=repo,
            yaml_text=str(row["yaml_text"]),
            data=dict(row["data"] or EMPTY),
            generation=int(row["generation"]),
            updated_by=str(row["updated_by"] or ""),
            updated_at=row["updated_at"],
        )

    async def areas_of(self, repos: Iterable[str]) -> dict[str, list[Area]]:
        """Each of ``repos``' areas, keyed by the name asked for, lower-cased; [] for one with none. One query.

        A repository asked for under an old name (``REPO_RENAMES``) reads the
        document stored under its current one, and the other way round.
        """
        asked = {r.lower() for r in repos if r}
        spellings = {name: {v.lower() for v in repo_slug_variants(name)} | {name} for name in asked}
        rows = await self._rows.rows_for(s for names in spellings.values() for s in names)
        by_slug = {str(r["repo"]).lower(): read_areas(r["data"]) for r in rows}
        out: dict[str, list[Area]] = {}
        for name, names in spellings.items():
            found = by_slug.get(name)
            if found is None:
                found = next((by_slug[s] for s in sorted(names) if s in by_slug), [])
            out[name] = found
        return out

    async def areas_for(self, repo: str) -> list[Area]:
        """``repo``'s areas; [] when it names none."""
        return (await self.areas_of([repo])).get(repo.lower(), [])

    async def save(
        self,
        repo: str,
        text: str | None,
        values: Any,
        *,
        expected: Collection[int] | None,
        actor: str,
        source: RevisionSource,
        reason: str = "",
        dry_run: bool = False,
        also: Callable[[Any], Awaitable[object]] | None = None,
    ) -> SaveResult:
        """Check and store ``repo``'s document; ``text`` None (a JSON body) stores the YAML ``values`` dump to.

        Raises :class:`~embry0.services.areas_document.DocumentInvalid` naming every problem, and
        :class:`~embry0.storage.repositories.repo_areas.AreasChanged` when the stored generation is not one of
        ``expected``. A dry run checks the same and stores nothing. ``also`` runs on the write's connection, in
        its transaction, when the document is stored: what must commit with it, or not at all.
        """
        data = check(values)
        if text is None:
            text = dump(data)
        async with self._db.transaction() as conn:
            stored = await self._rows.locked(conn, repo)
            current = int(stored["generation"]) if stored is not None else 0
            if expected is not None and current not in expected:
                raise AreasChanged(current)
            before_text = str(stored["yaml_text"]) if stored is not None else ""
            before_data = stored["data"] if stored is not None else EMPTY
            would_change = before_text != text
            found = changes(before_data, data)
            diff = text_diff(before_text, text) if would_change else ""
            if dry_run or not would_change:
                return SaveResult(current, None, would_change, found, diff)
            generation, revision_id = await self._rows.write(
                conn,
                repo,
                yaml_text=text,
                data=data,
                expected=expected,
                actor=actor,
                source=source,
                reason=reason,
            )
            if also is not None:
                await also(conn)
        return SaveResult(generation, revision_id, True, found, diff)

    async def write_on(
        self,
        conn: Any,
        repo: str,
        text: str,
        data: dict[str, Any],
        *,
        expected: Collection[int] | None,
        actor: str,
        source: RevisionSource,
        reason: str,
    ) -> tuple[int, int | None]:
        """Store checked ``data`` and its ``text`` on the caller's connection, inside its transaction:
        ``(generation, revision_id)``. :class:`AreasChanged` when the stored generation is not one of ``expected``."""
        return await self._rows.write(
            conn, repo, yaml_text=text, data=data, expected=expected, actor=actor, source=source, reason=reason
        )

    async def current_snapshot(self, repo: str) -> dict[str, Any] | None:
        """The stored document as a revision's ``after`` holds it, or None: what undo compares with."""
        return snapshot(await self._rows.get(repo))

    async def restore(
        self,
        repo: str,
        before: Mapping[str, Any] | None,
        *,
        after: Mapping[str, Any] | None,
        actor: str,
        reason: str,
    ) -> int | None:
        """Put back ``before`` while the stored document still equals ``after``; the revision.

        ``before`` None (the change created the document) stores the empty
        document, so the generation keeps going up: a version read before
        the undo never matches what is written after it.
        Raises :class:`AreasChanged` when the stored document is not
        ``after``, and :class:`~embry0.services.areas_document.DocumentInvalid`
        when ``before`` is no longer valid.
        """
        text = str(before["yaml_text"]) if before is not None else ""
        data = check(load_yaml(text))
        async with self._db.transaction() as conn:
            stored = await self._rows.locked(conn, repo)
            if snapshot(stored) != (dict(after) if after is not None else None):
                raise AreasChanged(int(stored["generation"]) if stored is not None else 0)
            _, revision_id = await self._rows.write(
                conn,
                repo,
                yaml_text=text,
                data=data,
                expected=None,
                actor=actor,
                source="restore",
                reason=reason,
            )
            return revision_id
