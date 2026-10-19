"""Repository for qa_image_tags — pre-baked sandbox image registry per repo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

_COLUMNS = "repo, image_tag, lockfile_sha, built_at, built_by, is_active, metadata, base_image, base_image_digest"


@dataclass(frozen=True, slots=True)
class QAImageTagRow:
    repo: str
    image_tag: str
    lockfile_sha: str
    built_at: datetime
    built_by: str
    is_active: bool
    metadata: dict[str, Any]
    # The image the builder sandbox was started from and its ID at build
    # time (#334). Both None on rows built before the digest was recorded —
    # "unknown", which is not the same as stale.
    base_image: str | None = None
    base_image_digest: str | None = None


def _to_row(r: Any) -> QAImageTagRow:
    return QAImageTagRow(
        repo=r["repo"],
        image_tag=r["image_tag"],
        lockfile_sha=r["lockfile_sha"],
        built_at=r["built_at"],
        built_by=r["built_by"],
        is_active=r["is_active"],
        # metadata is JSONB — the asyncpg codec returns a Python dict.
        metadata=r["metadata"] or {},
        base_image=r["base_image"],
        base_image_digest=r["base_image_digest"],
    )


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
        base_image: str | None = None,
        base_image_digest: str | None = None,
    ) -> None:
        """Record a freshly-built image tag as the repo's ONE active image.

        Idempotent on (repo, lockfile_sha): re-recording the same lockfile
        replaces that row (new tag, new base digest, built_at = now) without
        duplicating. Every other row of the repo is deactivated in the same
        transaction — the build being recorded is by definition the newest,
        including when it reuses the row of a lockfile that was reverted to.
        A partial unique index holds the invariant; the per-repo advisory
        lock makes two concurrent builds serialise instead of one of them
        failing on that index.

        ``base_image`` / ``base_image_digest`` go together or not at all.

        ``metadata`` is JSONB — pass the Python dict directly; the asyncpg
        JSONB codec handles encoding (see ``embry0/storage/database.py``).
        """
        if (base_image is None) != (base_image_digest is None):
            raise ValueError("base_image and base_image_digest must be given together")

        async with self.db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"qa_image_tags:{repo}")
            await conn.execute(
                "UPDATE qa_image_tags SET is_active = false WHERE repo = $1 AND is_active AND lockfile_sha <> $2",
                repo,
                lockfile_sha,
            )
            await conn.execute(
                """
                INSERT INTO qa_image_tags (
                    repo, image_tag, lockfile_sha, built_by, metadata,
                    base_image, base_image_digest, is_active
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, true)
                ON CONFLICT (repo, lockfile_sha) DO UPDATE SET
                    image_tag         = EXCLUDED.image_tag,
                    built_at          = NOW(),
                    built_by          = EXCLUDED.built_by,
                    metadata          = EXCLUDED.metadata,
                    base_image        = EXCLUDED.base_image,
                    base_image_digest = EXCLUDED.base_image_digest,
                    is_active         = true
                """,
                repo,
                image_tag,
                lockfile_sha,
                built_by,
                metadata or {},
                base_image,
                base_image_digest,
            )

    async def get_active(self, repo: str) -> QAImageTagRow | None:
        """Return the repo's active image tag, or None."""
        # At most one row matches (uq_qa_image_tags_one_active_per_repo).
        row = await self.db.fetchrow(f"SELECT {_COLUMNS} FROM qa_image_tags WHERE repo = $1 AND is_active", repo)
        return _to_row(row) if row is not None else None

    async def list_active(self) -> list[QAImageTagRow]:
        """Every repo's active image, ordered by repo."""
        rows = await self.db.fetch(f"SELECT {_COLUMNS} FROM qa_image_tags WHERE is_active ORDER BY repo")
        return [_to_row(r) for r in rows]

    async def list_all(self) -> list[QAImageTagRow]:
        """Every row, active or not, oldest build first."""
        rows = await self.db.fetch(f"SELECT {_COLUMNS} FROM qa_image_tags ORDER BY built_at")
        return [_to_row(r) for r in rows]

    async def list_for_repo(self, repo: str) -> list[QAImageTagRow]:
        rows = await self.db.fetch(
            f"SELECT {_COLUMNS} FROM qa_image_tags WHERE repo = $1 ORDER BY built_at DESC",
            repo,
        )
        return [_to_row(r) for r in rows]

    async def deactivate(self, *, repo: str, image_tag: str) -> None:
        await self.db.execute(
            "UPDATE qa_image_tags SET is_active = false WHERE repo = $1 AND image_tag = $2",
            repo,
            image_tag,
        )
