"""Repository for qa_image_tags — pre-baked sandbox image registry per repo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


@dataclass(frozen=True, slots=True)
class QAImageTagRow:
    repo: str
    image_tag: str
    lockfile_sha: str
    built_at: datetime
    built_by: str
    is_active: bool
    metadata: dict[str, Any]


class QAImageTagsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    async def record(
        self,
        *,
        repo: str,
        image_tag: str,
        lockfile_sha: str,
        built_by: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a freshly-built image tag. Idempotent on (repo, lockfile_sha):
        re-recording the same lockfile bumps built_at without duplicating.

        ``metadata`` is JSONB — pass the Python dict directly; the asyncpg
        JSONB codec handles encoding (see ``embry0/storage/database.py``).
        """
        sql = """
        INSERT INTO qa_image_tags (
            repo, image_tag, lockfile_sha, built_by, metadata, is_active
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, true)
        ON CONFLICT (repo, lockfile_sha) DO UPDATE SET
            image_tag  = EXCLUDED.image_tag,
            built_at   = NOW(),
            built_by   = EXCLUDED.built_by,
            metadata   = EXCLUDED.metadata,
            is_active  = true
        """
        await self.db.execute(sql, repo, image_tag, lockfile_sha, built_by, metadata or {})

    async def get_active(self, repo: str) -> QAImageTagRow | None:
        """Return the most recently built active image tag for the repo, or None."""
        sql = """
        SELECT repo, image_tag, lockfile_sha, built_at, built_by, is_active, metadata
        FROM qa_image_tags
        WHERE repo = $1 AND is_active = true
        ORDER BY built_at DESC
        LIMIT 1
        """
        row = await self.db.fetchrow(sql, repo)
        if row is None:
            return None
        # metadata is JSONB — the asyncpg codec returns a Python dict.
        meta = row["metadata"] or {}
        return QAImageTagRow(
            repo=row["repo"],
            image_tag=row["image_tag"],
            lockfile_sha=row["lockfile_sha"],
            built_at=row["built_at"],
            built_by=row["built_by"],
            is_active=row["is_active"],
            metadata=meta,
        )

    async def list_for_repo(self, repo: str) -> list[QAImageTagRow]:
        sql = """
        SELECT repo, image_tag, lockfile_sha, built_at, built_by, is_active, metadata
        FROM qa_image_tags
        WHERE repo = $1
        ORDER BY built_at DESC
        """
        rows = await self.db.fetch(sql, repo)
        out: list[QAImageTagRow] = []
        for r in rows:
            # metadata is JSONB — the asyncpg codec returns a Python dict.
            meta = r["metadata"] or {}
            out.append(
                QAImageTagRow(
                    repo=r["repo"],
                    image_tag=r["image_tag"],
                    lockfile_sha=r["lockfile_sha"],
                    built_at=r["built_at"],
                    built_by=r["built_by"],
                    is_active=r["is_active"],
                    metadata=meta,
                )
            )
        return out

    async def deactivate(self, *, repo: str, image_tag: str) -> None:
        await self.db.execute(
            "UPDATE qa_image_tags SET is_active = false WHERE repo = $1 AND image_tag = $2",
            repo,
            image_tag,
        )
