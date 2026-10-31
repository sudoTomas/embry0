"""When a repository left a background feature's scope (migration 136, #165).

A feature that removes what it kept of a repository that left its scope
waits ``background.prune_after_days`` first, counted from the first tick that
found it out. Each row is one ``(feature, repo)`` with the time it left; a
repository that comes back has its row deleted. ``repo`` is the slug
lowercased.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


class ScopeExitsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def left(self, feature: str) -> dict[str, datetime]:
        """Each repository that left ``feature``'s scope (lowercased) to when it left."""
        rows = await self._db.fetch("SELECT repo, left_at FROM background_scope_exits WHERE feature = $1", feature)
        return {str(r["repo"]): r["left_at"] for r in rows}

    async def mark(self, feature: str, repos: Iterable[str], *, at: datetime) -> list[str]:
        """Record that ``repos`` left ``feature``'s scope ``at``; one already recorded keeps its time. The ones
        recorded now, lowercased."""
        names = sorted({repo.lower() for repo in repos})
        if not names:
            return []
        rows = await self._db.fetch(
            "INSERT INTO background_scope_exits (feature, repo, left_at) SELECT $1, unnest($2::text[]), $3 "
            "ON CONFLICT (feature, repo) DO NOTHING RETURNING repo",
            feature,
            names,
            at,
        )
        return sorted(str(r["repo"]) for r in rows)

    async def clear(self, feature: str, repos: Iterable[str]) -> list[str]:
        """Forget that ``repos`` left ``feature``'s scope: they came back, or nothing of them is kept any more.
        The ones forgotten, lowercased."""
        names = sorted({repo.lower() for repo in repos})
        if not names:
            return []
        rows: Any = await self._db.fetch(
            "DELETE FROM background_scope_exits WHERE feature = $1 AND repo = ANY($2::text[]) RETURNING repo",
            feature,
            names,
        )
        return sorted(str(r["repo"]) for r in rows)
