"""Repository for qa_volume_state — shared-volume warmth tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from embry0.storage.database import DatabasePool


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
        reset_warmth: bool = False,
    ) -> None:
        """Record which volume backs ``(scope, scope_key)`` and its warmth.

        ``last_warmed_sha=None`` normally LEAVES any recorded sha in place
        (COALESCE), so a bare re-record does not lose warmth. ``reset_warmth``
        overrides that: the sha is written as given (``None`` clears it), which
        a freshly created — therefore empty — volume needs, so the warmer does
        not skip against a stale sha and mount an empty tree.
        """
        if reset_warmth:
            sql = """
            INSERT INTO qa_volume_state (
                scope, scope_key, volume_name, last_warmed_sha, last_warmed_at
            )
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (scope, scope_key) DO UPDATE SET
                volume_name      = EXCLUDED.volume_name,
                last_warmed_sha  = EXCLUDED.last_warmed_sha,
                last_warmed_at   = NOW()
            """
        else:
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

    async def delete_by_volume_name(self, volume_name: str) -> None:
        """Forget a volume that has been removed (#328).

        A row outliving its volume claims a warm tree that no longer exists.
        """
        await self.db.execute("DELETE FROM qa_volume_state WHERE volume_name = $1", volume_name)
