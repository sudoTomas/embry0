"""watcher_stacks — the rows behind "what does embry0 watch" (#378, migration 83)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from embry0.services.watcher_stacks import SETTING_KEYS, WatcherStack
from embry0.storage.database import DatabasePool, StoredValueTooDeep

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

    async def list_all(self, *, enabled_only: bool = False, repos: Sequence[str] | None = None) -> list[WatcherStack]:
        """Every watched stack, only the enabled with ``enabled_only``. ``repos`` (lower-cased slugs) keeps only
        those repositories' stacks; None keeps every one."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM watcher_stacks WHERE (NOT $1::boolean OR enabled) "
            "AND ($2::text[] IS NULL OR lower(repo) = ANY($2::text[])) ORDER BY repo",
            enabled_only,
            None if repos is None else list(repos),
        )
        return [_stack(r) for r in rows]

    async def get(self, repo: str) -> WatcherStack | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM watcher_stacks WHERE repo = $1", repo)
        return _stack(row) if row else None

    async def list_readable(self, *, enabled_only: bool = False) -> tuple[list[WatcherStack], list[str]]:
        """:meth:`list_all` after it raised ``StoredValueTooDeep`` (#527): each stack read alone, so one row too
        deep to read leaves the others; and the repositories of those that could not be read."""
        repos = await self._db.fetch(
            "SELECT repo FROM watcher_stacks WHERE (NOT $1::boolean OR enabled) ORDER BY repo", enabled_only
        )
        stacks: list[WatcherStack] = []
        unreadable: list[str] = []
        for row in repos:
            try:
                stack = await self.get(str(row["repo"]))
            except StoredValueTooDeep:
                unreadable.append(str(row["repo"]))
                continue
            if stack is not None:
                stacks.append(stack)
        return stacks, unreadable

    async def upsert(
        self, stack: WatcherStack, *, check: Callable[[WatcherStack | None], None] | None = None
    ) -> WatcherStack:
        """Create or replace one stack. Unknown setting keys are dropped —
        a typo must not become a setting nothing reads (SETTING_KEYS).

        ``check``, when given, is called with the stored stack (None when
        none is stored) while its row is held, before anything is written;
        whatever it raises rolls the write back."""
        if stack.tracker not in VALID_TRACKERS:
            raise ValueError(f"unknown tracker {stack.tracker!r}")
        if not stack.repo.strip() or not stack.logql.strip():
            raise ValueError("a stack needs a repo and a LogQL selector")
        settings = {k: v for k, v in (stack.settings or {}).items() if k in SETTING_KEYS}
        repo = stack.repo.strip()
        async with self._db.transaction() as conn:
            if check is not None:
                held = await conn.fetchrow(f"SELECT {_COLUMNS} FROM watcher_stacks WHERE repo = $1 FOR UPDATE", repo)
                check(_stack(held) if held else None)
            row = await conn.fetchrow(
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
                repo,
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
