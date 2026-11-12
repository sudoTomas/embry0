"""A repository's areas, stored in embry0 (migration 145, spec 2026-11-10).

One row per repository that has had a document: the YAML as written
(``yaml_text``) and its checked values (``data``), which every reader
reads, never the YAML. ``generation`` is the next value of the
``repo_areas_generation`` sequence in each write's transaction, for
``If-Match`` and a map's compare-and-set: it only goes up and is never used
twice, not after an undo and not after the repository was removed and
registered again. A registered repository without a row names no areas,
at generation 0; nor does a row holding the empty text, which an undo of a
first write stores, as a row is deleted only with its repository. Every
write that changes the text appends a
``repo_config_revisions`` row (surface ``areas``) in the same transaction
(ADR 0019), so a write whose revision cannot be stored does not happen
either. Callers check the document (:mod:`embry0.services.areas_document`)
before writing: this layer stores what it is given.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

import asyncpg

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository, RevisionSource

_COLUMNS = "repo, yaml_text, data, schema_version, generation, updated_by, created_at, updated_at"


class AreasChanged(Exception):
    """The stored document is not the version a write expected; ``generation`` is the one stored (0: none)."""

    def __init__(self, generation: int) -> None:
        super().__init__(f"the areas document is at generation {generation}")
        self.generation = generation


def snapshot(row: Any) -> dict[str, Any] | None:
    """A stored row as a revision's ``before`` or ``after`` holds it; None for no row."""
    return None if row is None else {"yaml_text": row["yaml_text"], "data": row["data"]}


class RepoAreasRepository:
    """Reads and writes the ``repo_areas`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self._revisions = RepoConfigRevisionsRepository(db)

    async def get(self, repo: str) -> dict[str, Any] | None:
        """The row of ``repo`` as registered, or None."""
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repo_areas WHERE repo = $1", repo)
        return dict(row) if row else None

    async def rows_for(self, slugs: Iterable[str]) -> list[dict[str, Any]]:
        """The rows whose repository is one of ``slugs``, without regard to case. One query."""
        wanted = sorted({s.lower() for s in slugs})
        if not wanted:
            return []
        rows = await self._db.fetch(
            "SELECT repo, data, generation FROM repo_areas WHERE lower(repo) = ANY($1::text[])", wanted
        )
        return [dict(r) for r in rows]

    async def write(
        self,
        conn: asyncpg.Connection,
        repo: str,
        *,
        yaml_text: str,
        data: dict[str, Any],
        expected: Collection[int] | None,
        actor: str,
        source: RevisionSource,
        reason: str,
    ) -> tuple[int, int | None]:
        """Store the document on the caller's connection, inside its transaction; ``(generation, revision_id)``.

        ``expected`` holds the generations the write may replace (None: any);
        another raises :class:`AreasChanged` and nothing is written. A text
        equal to the stored one (no row reads as the empty text) writes and
        records nothing (``revision_id`` None). ``repo`` is the registry's
        spelling: the row needs a ``repos`` row.
        """
        stored = await conn.fetchrow(f"SELECT {_COLUMNS} FROM repo_areas WHERE repo = $1 FOR UPDATE", repo)
        current = int(stored["generation"]) if stored is not None else 0
        if expected is not None and current not in expected:
            raise AreasChanged(current)
        if (str(stored["yaml_text"]) if stored is not None else "") == yaml_text:
            return current, None
        if stored is None:
            # Nothing was there to lock: another first write may be uncommitted. The insert waits for it, and
            # then writes nothing, which is a change made meanwhile.
            row = await conn.fetchrow(
                f"INSERT INTO repo_areas (repo, yaml_text, data, updated_by, generation) "
                f"VALUES ($1, $2, $3::jsonb, $4, nextval('repo_areas_generation')) "
                f"ON CONFLICT (repo) DO NOTHING RETURNING {_COLUMNS}",
                repo,
                yaml_text,
                data,
                actor,
            )
            if row is None:
                raise AreasChanged(int(await conn.fetchval("SELECT generation FROM repo_areas WHERE repo = $1", repo)))
        else:
            row = await conn.fetchrow(
                f"UPDATE repo_areas SET yaml_text = $2, data = $3::jsonb, updated_by = $4, "
                f"generation = nextval('repo_areas_generation'), updated_at = NOW() WHERE repo = $1 "
                f"RETURNING {_COLUMNS}",
                repo,
                yaml_text,
                data,
                actor,
            )
        revision_id = await self._revisions.append(
            conn,
            repo=repo,
            surface="areas",
            before=snapshot(stored),
            after=snapshot(row),
            actor=actor,
            source=source,
            reason=reason,
        )
        return int(row["generation"]), revision_id

    async def locked(self, conn: asyncpg.Connection, repo: str) -> dict[str, Any] | None:
        """The row of ``repo``, locked for the caller's transaction, or None."""
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM repo_areas WHERE repo = $1 FOR UPDATE", repo)
        return dict(row) if row else None
