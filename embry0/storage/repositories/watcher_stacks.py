"""watcher_stacks — the rows behind "what does embry0 watch" (#378, migration 83)."""

from __future__ import annotations

from typing import Any

from embry0.services.watcher_stacks import SETTING_KEYS, WatcherStack
from embry0.storage.database import DatabasePool

_COLUMNS = "repo, enabled, logql, tracker, tracker_target, settings, created_at, updated_at"

VALID_TRACKERS = frozenset({"linear", "github", "gitlab"})


def _stack(row: Any) -> WatcherStack:
    return WatcherStack(
        repo=row["repo"],
        logql=row["logql"],
        tracker=row["tracker"],
        tracker_target=dict(row["tracker_target"] or {}),
        settings=dict(row["settings"] or {}),
        enabled=bool(row["enabled"]),
    )


class WatcherStacksRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def count(self) -> int:
        return int(await self._db.fetchval("SELECT count(*) FROM watcher_stacks") or 0)

    async def list_all(self, *, enabled_only: bool = False) -> list[WatcherStack]:
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM watcher_stacks WHERE (NOT $1::boolean OR enabled) ORDER BY repo",
            enabled_only,
        )
        return [_stack(r) for r in rows]

    async def get(self, repo: str) -> WatcherStack | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM watcher_stacks WHERE repo = $1", repo)
        return _stack(row) if row else None

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        """Create or replace one stack. Unknown setting keys are dropped —
        a typo must not become a setting nothing reads (SETTING_KEYS)."""
        if stack.tracker not in VALID_TRACKERS:
            raise ValueError(f"unknown tracker {stack.tracker!r}")
        if not stack.repo.strip() or not stack.logql.strip():
            raise ValueError("a stack needs a repo and a LogQL selector")
        settings = {k: v for k, v in (stack.settings or {}).items() if k in SETTING_KEYS}
        row = await self._db.fetchrow(
            f"""
            INSERT INTO watcher_stacks (repo, enabled, logql, tracker, tracker_target, settings)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            ON CONFLICT (repo) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                logql = EXCLUDED.logql,
                tracker = EXCLUDED.tracker,
                tracker_target = EXCLUDED.tracker_target,
                settings = EXCLUDED.settings,
                updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            stack.repo.strip(),
            stack.enabled,
            stack.logql.strip(),
            stack.tracker,
            dict(stack.tracker_target or {}),
            settings,
        )
        return _stack(row)

    async def set_enabled(self, repo: str, enabled: bool) -> WatcherStack | None:
        row = await self._db.fetchrow(
            f"UPDATE watcher_stacks SET enabled = $2, updated_at = NOW() WHERE repo = $1 RETURNING {_COLUMNS}",
            repo,
            enabled,
        )
        return _stack(row) if row else None

    async def delete(self, repo: str) -> bool:
        """Stop watching a stack. Its runs, signatures and cases stay — they
        are the record of what it found."""
        result = await self._db.execute("DELETE FROM watcher_stacks WHERE repo = $1", repo)
        return bool(result) and str(result).split()[-1] == "1"
