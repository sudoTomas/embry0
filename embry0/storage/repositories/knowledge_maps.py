"""Knowledge maps (migrations 124 and 133, spec 2026-10-27): the uploads, people's marks, and what was sent.

A map is kept as its last upload: the parts and links as JSON text
(served as they are, never re-encoded on a read) and one detail record per
part (JSONB, read one part at a time). A mark is each person's current
verdict on a part; clearing it deletes the row. A send is every part handed
to an agent, with the lead and job it started or the error it met; the work
view joins the newest send of each part to its job.

A map is named by its owner and its slug (migration 133): two owners may
each have a map called ``shop``. ``owner`` None is the map that belongs to
no one, and it is a value like any other here: every method takes it, and
matches it with ``IS NOT DISTINCT FROM``, so None never matches every map.
Marks and sends carry their map's owner the same way (``map_owner``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import asyncpg

from embry0.services.knowledge_maps import owner_query
from embry0.storage.database import DatabasePool

_META = "slug, owner, title, repo, commit_sha, source, generated_at, uploaded_at, uploaded_by, node_count, edge_count"


class OwnerTaken(Exception):
    """The owner a map would move to already has a map of that slug."""


def _json(value: Any) -> Any:
    # The pool decodes JSONB; a pool without the codec hands back its text.
    return json.loads(value) if isinstance(value, str) else value


class KnowledgeMapsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ---- maps ----

    async def put(
        self,
        *,
        slug: str,
        owner: str | None,
        title: str,
        repo: str | None,
        commit_sha: str | None,
        source: str,
        generated_at: datetime | None,
        uploaded_by: str,
        graph: Mapping[str, Any],
        details: Mapping[str, Any],
        now: datetime,
    ) -> None:
        """Store the upload of ``owner``'s map ``slug``, replacing the one before; marks and sends stay."""
        await self._db.execute(
            """
            INSERT INTO knowledge_maps (slug, title, repo, commit_sha, source, generated_at, uploaded_at, uploaded_by,
                                        node_count, edge_count, graph, details, owner)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (slug, owner) DO UPDATE SET
                title = EXCLUDED.title, repo = EXCLUDED.repo, commit_sha = EXCLUDED.commit_sha,
                source = EXCLUDED.source, generated_at = EXCLUDED.generated_at, uploaded_at = EXCLUDED.uploaded_at,
                uploaded_by = EXCLUDED.uploaded_by, node_count = EXCLUDED.node_count,
                edge_count = EXCLUDED.edge_count, graph = EXCLUDED.graph, details = EXCLUDED.details
            """,
            slug,
            title,
            repo,
            commit_sha,
            source,
            generated_at,
            now,
            uploaded_by,
            len(graph.get("nodes") or []),
            len(graph.get("edges") or []),
            json.dumps(graph, separators=(",", ":")),
            dict(details),
            owner,
        )

    async def summaries(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_META} FROM knowledge_maps ORDER BY title, slug, owner NULLS FIRST")
        return [dict(r) for r in rows]

    async def owners(self, slug: str) -> list[str | None]:
        """The owners that have a map called ``slug`` (None: the map that belongs to no one), owned ones first."""
        rows = await self._db.fetch("SELECT owner FROM knowledge_maps WHERE slug = $1 ORDER BY owner NULLS LAST", slug)
        return [r["owner"] for r in rows]

    async def meta(self, slug: str, *, owner: str | None) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_META} FROM knowledge_maps WHERE slug = $1 AND owner IS NOT DISTINCT FROM $2", slug, owner
        )
        return dict(row) if row else None

    async def graph_text(self, slug: str, *, owner: str | None) -> tuple[str, datetime] | None:
        """The parts and links as stored JSON text, and when they were uploaded."""
        row = await self._db.fetchrow(
            "SELECT graph, uploaded_at FROM knowledge_maps WHERE slug = $1 AND owner IS NOT DISTINCT FROM $2",
            slug,
            owner,
        )
        return (str(row["graph"]), row["uploaded_at"]) if row else None

    async def detail(self, slug: str, node_id: str, *, owner: str | None) -> dict[str, Any] | None:
        value = await self._db.fetchval(
            "SELECT details -> $2 FROM knowledge_maps WHERE slug = $1 AND owner IS NOT DISTINCT FROM $3",
            slug,
            node_id,
            owner,
        )
        out = _json(value)
        return dict(out) if isinstance(out, Mapping) else None

    async def delete(self, slug: str, *, owner: str | None) -> bool:
        """Remove a map; its marks and sends stay, and come back with a new upload of the same owner and slug."""
        result = await self._db.execute(
            "DELETE FROM knowledge_maps WHERE slug = $1 AND owner IS NOT DISTINCT FROM $2", slug, owner
        )
        return str(result).endswith(" 1")

    async def set_owner(self, slug: str, *, owner: str | None, to: str | None) -> bool:
        """Give ``owner``'s map ``slug`` to ``to``, with its marks, its sends and its leads; whether there was one.

        The stored graph text is not touched: the ``owner`` column is the map's owner, and a read adds it. A
        lead's link to its part is pointed at the new owner. Where both owners have a mark by one person on one
        part, the moving map's wins. Raises :class:`OwnerTaken` when ``to`` already has a map of this slug.
        """
        try:
            return await self._move(slug, owner, to)
        except asyncpg.UniqueViolationError as exc:
            # An upload of ``to``'s map of this slug that landed between the check and the move.
            raise OwnerTaken(slug) from exc

    async def _move(self, slug: str, owner: str | None, to: str | None) -> bool:
        exists = "SELECT 1 FROM knowledge_maps WHERE slug = $1 AND owner IS NOT DISTINCT FROM $2"
        async with self._db.transaction() as conn:
            if not await conn.fetchval(exists, slug, owner):
                return False
            if await conn.fetchval(exists, slug, to):
                raise OwnerTaken(slug)
            moved = await conn.execute(
                "UPDATE knowledge_maps SET owner = $3 WHERE slug = $1 AND owner IS NOT DISTINCT FROM $2",
                slug,
                owner,
                to,
            )
            if not str(moved).endswith(" 1"):
                return False
            await conn.execute(
                """
                DELETE FROM knowledge_map_marks t
                WHERE t.map_slug = $1 AND t.map_owner IS NOT DISTINCT FROM $3
                  AND EXISTS (SELECT 1 FROM knowledge_map_marks s
                              WHERE s.map_slug = $1 AND s.map_owner IS NOT DISTINCT FROM $2
                                AND s.node_id = t.node_id AND s.marked_by = t.marked_by)
                """,
                slug,
                owner,
                to,
            )
            for table in ("knowledge_map_marks", "knowledge_map_sends"):
                await conn.execute(
                    f"UPDATE {table} SET map_owner = $3 WHERE map_slug = $1 AND map_owner IS NOT DISTINCT FROM $2",
                    slug,
                    owner,
                    to,
                )
            # The map's weed leads say which owner's map made them (services.knowledge_maps.map_lead), and link
            # to the part with ?owner= (or, made before owners, with none).
            page = f"/maps/{slug}"
            await conn.execute(
                """
                UPDATE leads SET data = data || jsonb_build_object('map_owner', $3::text),
                    url = replace(replace(url, $4::text || $5::text, $4::text || $6::text),
                                  $4::text || '?part=', $4::text || $6::text || 'part=')
                WHERE kind = 'weed' AND source_key LIKE 'map:' || $1 || ':%'
                  AND data ->> 'map_owner' IS NOT DISTINCT FROM $2
                """,
                slug,
                owner,
                to,
                page,
                owner_query(owner),
                owner_query(to),
            )
        return True

    # ---- marks ----

    async def set_mark(
        self, slug: str, node_id: str, by: str, verdict: str, note: str, *, owner: str | None, now: datetime
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_map_marks (map_slug, node_id, marked_by, verdict, note, marked_at, map_owner)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (map_slug, node_id, marked_by, map_owner) DO UPDATE SET
                verdict = EXCLUDED.verdict, note = EXCLUDED.note,
                marked_at = CASE WHEN knowledge_map_marks.verdict = EXCLUDED.verdict
                                  AND knowledge_map_marks.note = EXCLUDED.note
                                 THEN knowledge_map_marks.marked_at ELSE EXCLUDED.marked_at END
            """,
            slug,
            node_id,
            by,
            verdict,
            note,
            now,
            owner,
        )

    async def clear_mark(self, slug: str, node_id: str, by: str, *, owner: str | None) -> None:
        await self._db.execute(
            "DELETE FROM knowledge_map_marks"
            " WHERE map_slug = $1 AND node_id = $2 AND marked_by = $3 AND map_owner IS NOT DISTINCT FROM $4",
            slug,
            node_id,
            by,
            owner,
        )

    async def marks(self, slug: str, node_id: str | None = None, *, owner: str | None) -> list[dict[str, Any]]:
        """A map's current marks (one part's, given ``node_id``), oldest first."""
        if node_id is None:
            rows = await self._db.fetch(
                "SELECT node_id, marked_by, verdict, note, marked_at FROM knowledge_map_marks"
                " WHERE map_slug = $1 AND map_owner IS NOT DISTINCT FROM $2 ORDER BY marked_at",
                slug,
                owner,
            )
        else:
            rows = await self._db.fetch(
                "SELECT node_id, marked_by, verdict, note, marked_at FROM knowledge_map_marks"
                " WHERE map_slug = $1 AND map_owner IS NOT DISTINCT FROM $2 AND node_id = $3 ORDER BY marked_at",
                slug,
                owner,
                node_id,
            )
        return [dict(r) for r in rows]

    # ---- sends ----

    async def add_send(
        self,
        *,
        slug: str,
        owner: str | None,
        node_id: str,
        kind: str,
        repo: str,
        sent_by: str,
        note: str,
        lead_id: str | None,
        job_id: str | None,
        error: str | None,
        now: datetime,
    ) -> int:
        return int(
            await self._db.fetchval(
                """
                INSERT INTO knowledge_map_sends (map_slug, node_id, kind, repo, lead_id, job_id, error, sent_by, note,
                                                 sent_at, map_owner)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id
                """,
                slug,
                node_id,
                kind,
                repo,
                lead_id,
                job_id,
                error,
                sent_by,
                note,
                now,
                owner,
            )
        )

    async def work(self, slug: str, node_id: str | None = None, *, owner: str | None) -> list[dict[str, Any]]:
        """The newest send of each part (of one part, given ``node_id``), with its job's state."""
        where = "s.map_slug = $1 AND s.map_owner IS NOT DISTINCT FROM $2"
        args: list[Any] = [slug, owner]
        if node_id is not None:
            where, args = f"{where} AND s.node_id = $3", [*args, node_id]
        rows = await self._db.fetch(
            f"""
            SELECT DISTINCT ON (s.node_id)
                   s.node_id, s.kind, s.repo, s.lead_id, s.job_id, s.error, s.sent_by, s.note, s.sent_at,
                   j.status AS job_status, j.pr_url, j.finished_at, j.total_cost_usd, j.error_message
            FROM knowledge_map_sends s LEFT JOIN jobs j ON j.job_id = s.job_id
            WHERE {where}
            ORDER BY s.node_id, s.sent_at DESC, s.id DESC
            """,
            *args,
        )
        return [dict(r) for r in rows]
