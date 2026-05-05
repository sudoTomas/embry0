"""Repository for qa_volume_state — shared-volume warmth tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from athanor.storage.database import DatabasePool


@dataclass(frozen=True, slots=True)
class VolumeState:
    scope: str
    scope_key: str
    volume_name: str
    last_warmed_sha: str | None
    last_warmed_at: datetime | None


class QAVolumeStateRepository:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        scope: str,
        scope_key: str,
        volume_name: str,
        last_warmed_sha: str | None,
    ) -> None:
        sql = """
        INSERT INTO qa_volume_state (
            scope, scope_key, volume_name, last_warmed_sha, last_warmed_at
        )
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (scope, scope_key) DO UPDATE SET
            volume_name      = EXCLUDED.volume_name,
            last_warmed_sha  = COALESCE(EXCLUDED.last_warmed_sha, qa_volume_state.last_warmed_sha),
            last_warmed_at   = NOW()
        """
        await self.db.execute(sql, scope, scope_key, volume_name, last_warmed_sha)

    async def get(self, *, scope: str, scope_key: str) -> VolumeState | None:
        sql = """
        SELECT scope, scope_key, volume_name, last_warmed_sha, last_warmed_at
        FROM qa_volume_state
        WHERE scope = $1 AND scope_key = $2
        """
        row = await self.db.fetchrow(sql, scope, scope_key)
        if row is None:
            return None
        return VolumeState(
            scope=row["scope"],
            scope_key=row["scope_key"],
            volume_name=row["volume_name"],
            last_warmed_sha=row["last_warmed_sha"],
            last_warmed_at=row["last_warmed_at"],
        )
