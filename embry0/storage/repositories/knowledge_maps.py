"""Knowledge maps (migration 124, spec 2026-10-27): the uploads, people's marks, and what was sent.

A map is kept as its last upload: the parts and links as JSON text (served
as they are, never re-encoded) and one detail record per part (JSONB, read
one part at a time). A mark is each person's current verdict on a part;
clearing it deletes the row. A send is every part handed to an agent, with
the lead and job it started or the error it met; the work view joins the
newest send of each part to its job.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

_META = "slug, title, repo, commit_sha, source, generated_at, uploaded_at, uploaded_by, node_count, edge_count"


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
        """Store ``slug``'s upload, replacing the one before; marks and sends stay."""
        await self._db.execute(
            """
            INSERT INTO knowledge_maps (slug, title, repo, commit_sha, source, generated_at, uploaded_at, uploaded_by,
                                        node_count, edge_count, graph, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (slug) DO UPDATE SET
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
        )

    async def summaries(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_META} FROM knowledge_maps ORDER BY title")
        return [dict(r) for r in rows]

    async def meta(self, slug: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_META} FROM knowledge_maps WHERE slug = $1", slug)
        return dict(row) if row else None

    async def graph_text(self, slug: str) -> tuple[str, datetime] | None:
        """The parts and links as stored JSON text, and when they were uploaded."""
        row = await self._db.fetchrow("SELECT graph, uploaded_at FROM knowledge_maps WHERE slug = $1", slug)
        return (str(row["graph"]), row["uploaded_at"]) if row else None

    async def detail(self, slug: str, node_id: str) -> dict[str, Any] | None:
        value = await self._db.fetchval("SELECT details -> $2 FROM knowledge_maps WHERE slug = $1", slug, node_id)
        out = _json(value)
        return dict(out) if isinstance(out, Mapping) else None

    async def delete(self, slug: str) -> bool:
        """Remove a map; its marks and sends stay, and come back with a new upload under the same slug."""
        result = await self._db.execute("DELETE FROM knowledge_maps WHERE slug = $1", slug)
        return str(result).endswith(" 1")

    # ---- marks ----

    async def set_mark(self, slug: str, node_id: str, by: str, verdict: str, note: str, *, now: datetime) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_map_marks (map_slug, node_id, marked_by, verdict, note, marked_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (map_slug, node_id, marked_by) DO UPDATE SET
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
        )

    async def clear_mark(self, slug: str, node_id: str, by: str) -> None:
        await self._db.execute(
            "DELETE FROM knowledge_map_marks WHERE map_slug = $1 AND node_id = $2 AND marked_by = $3", slug, node_id, by
        )

    async def marks(self, slug: str, node_id: str | None = None) -> list[dict[str, Any]]:
        """A map's current marks (one part's, given ``node_id``), oldest first."""
        if node_id is None:
            rows = await self._db.fetch(
                "SELECT node_id, marked_by, verdict, note, marked_at FROM knowledge_map_marks"
                " WHERE map_slug = $1 ORDER BY marked_at",
                slug,
            )
        else:
            rows = await self._db.fetch(
                "SELECT node_id, marked_by, verdict, note, marked_at FROM knowledge_map_marks"
                " WHERE map_slug = $1 AND node_id = $2 ORDER BY marked_at",
                slug,
                node_id,
            )
        return [dict(r) for r in rows]

    # ---- sends ----

    async def add_send(
        self,
        *,
        slug: str,
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
                                                 sent_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id
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
            )
        )

    async def work(self, slug: str, node_id: str | None = None) -> list[dict[str, Any]]:
        """The newest send of each part (of one part, given ``node_id``), with its job's state."""
        where, args = (
            ("s.map_slug = $1", [slug]) if node_id is None else ("s.map_slug = $1 AND s.node_id = $2", [slug, node_id])
        )
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
