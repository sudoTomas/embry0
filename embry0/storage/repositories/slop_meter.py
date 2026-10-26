"""The Slop-O-Meter's rows (migration 121, spec 2026-10-26, slop-o-meter).

A repository's history as read (commits, forge events, login emails, outlier
commit judgments), where reading it stands, and what is computed from it: the
weekly series and the current score per repository (area ``''``) and area.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

_STATE_FIELDS = frozenset(
    {
        "head_sha",
        "commits_read",
        "commits_total",
        "comments_since",
        "review_comments_since",
        "reviews_since",
        "mirrored_at",
        "computed_at",
        "error",
        "error_at",
        "history_version",
    }
)


class SlopMeterRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ---- where reading stands ----

    async def state(self, repo: str) -> dict[str, Any]:
        row = await self._db.fetchrow("SELECT * FROM slop_meter_repos WHERE repo = $1", repo)
        return dict(row) if row else {"repo": repo, "commits_read": 0, "commits_total": 0}

    async def set_state(self, repo: str, **fields: Any) -> None:
        unknown = set(fields) - _STATE_FIELDS
        if unknown:
            raise ValueError(f"unknown slop-meter state fields: {sorted(unknown)}")
        names = list(fields)
        columns = ", ".join(["repo", *names])
        values = ", ".join(f"${i}" for i in range(1, len(names) + 2))
        updates = ", ".join(f"{n} = EXCLUDED.{n}" for n in names) or "repo = EXCLUDED.repo"
        await self._db.execute(
            f"INSERT INTO slop_meter_repos ({columns}) VALUES ({values}) ON CONFLICT (repo) DO UPDATE SET {updates}",
            repo,
            *fields.values(),
        )

    # ---- the history ----

    async def add_commits(self, repo: str, first_seq: int, rows: Sequence[Mapping[str, Any]]) -> None:
        """Store commits read in history order, ``first_seq`` the first one's place; a commit already stored is kept."""
        if not rows:
            return
        async with self._db.transaction() as conn:
            await conn.executemany(
                """
            INSERT INTO slop_meter_commits (repo, sha, seq, authored_at, author_name, author_email, message,
                                            files, coauthors, ai_coauthor, bot, subcommits)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (repo, sha) DO NOTHING
            """,
                [
                    (
                        repo,
                        r["sha"],
                        first_seq + i,
                        r["authored_at"],
                        r["author_name"],
                        r["author_email"],
                        r["message"],
                        r["files"],
                        r["coauthors"],
                        r["ai_coauthor"],
                        r["bot"],
                        r["subcommits"],
                    )
                    for i, r in enumerate(rows)
                ],
            )

    async def commits(self, repo: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM slop_meter_commits WHERE repo = $1 ORDER BY seq", repo)
        return [dict(r) for r in rows]

    async def authors(self, repo: str) -> list[str]:
        """Every author email of the history as read."""
        rows = await self._db.fetch("SELECT DISTINCT author_email FROM slop_meter_commits WHERE repo = $1", repo)
        return [str(r["author_email"]) for r in rows]

    async def forget_history(self, repo: str) -> None:
        """A history rewritten under the stored one: read it again from the start."""
        await self._db.execute("DELETE FROM slop_meter_commits WHERE repo = $1", repo)
        await self.set_state(repo, commits_read=0, head_sha=None)

    async def add_events(self, repo: str, events: Iterable[tuple[str, str, str, datetime]]) -> None:
        """Store forge events ``(id, kind, login, at)``; one already stored is kept."""
        rows = [(repo, eid, kind, login, at) for eid, kind, login, at in events]
        if rows:
            async with self._db.transaction() as conn:
                await conn.executemany(
                    "INSERT INTO slop_meter_events (repo, id, kind, login, at) VALUES ($1, $2, $3, $4, $5) "
                    "ON CONFLICT (repo, id) DO NOTHING",
                    rows,
                )

    async def events(self, repo: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT kind, login, at FROM slop_meter_events WHERE repo = $1 ORDER BY at", repo)
        return [dict(r) for r in rows]

    async def logins(self) -> dict[str, str]:
        """Every login looked up, lower-cased, to its email ('' when none was found)."""
        rows = await self._db.fetch("SELECT login, email FROM slop_meter_logins")
        return {str(r["login"]): str(r["email"]) for r in rows}

    async def set_login(self, login: str, email: str) -> None:
        await self._db.execute(
            "INSERT INTO slop_meter_logins (login, email, checked_at) VALUES ($1, $2, NOW()) "
            "ON CONFLICT (login) DO UPDATE SET email = EXCLUDED.email, checked_at = NOW()",
            login.lower(),
            email,
        )

    async def judgments(self, repo: str) -> dict[str, bool]:
        rows = await self._db.fetch("SELECT sha, slop FROM slop_meter_judgments WHERE repo = $1", repo)
        return {str(r["sha"]): bool(r["slop"]) for r in rows}

    async def judge(self, repo: str, sha: str, *, slop: bool, reason: str, model: str) -> None:
        await self._db.execute(
            "INSERT INTO slop_meter_judgments (repo, sha, slop, reason, model, judged_at) "
            "VALUES ($1, $2, $3, $4, $5, NOW()) ON CONFLICT (repo, sha) DO UPDATE SET "
            "slop = EXCLUDED.slop, reason = EXCLUDED.reason, model = EXCLUDED.model, judged_at = NOW()",
            repo,
            sha,
            slop,
            reason[:1000],
            model,
        )

    # ---- what is computed ----

    async def store(self, repo: str, results: Mapping[str, Any], computed_at: datetime) -> None:
        """Replace the repository's series and scores with ``results`` (area → Meter, '' the repository)."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM slop_meter_weeks WHERE repo = $1", repo)
            await conn.execute("DELETE FROM slop_meter_scores WHERE repo = $1", repo)
            weeks = [
                (repo, area, w.week, w.cost, w.capacity, w.slop, w.lines, w.score)
                for area, meter in results.items()
                for w in meter.weeks
            ]
            if weeks:
                await conn.executemany(
                    "INSERT INTO slop_meter_weeks (repo, area, week, cost, capacity, slop, lines, score) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    weeks,
                )
            await conn.executemany(
                "INSERT INTO slop_meter_scores (repo, area, score, level, name, provisional, commits, computed_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                [
                    (repo, area, m.score, m.level, m.name, m.provisional, m.commits, computed_at)
                    for area, m in results.items()
                ],
            )

    async def scores(self, repo: str | None = None) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM slop_meter_scores WHERE ($1::text IS NULL OR repo = $1) ORDER BY repo, area", repo
        )
        return [dict(r) for r in rows]

    async def weeks(self, repo: str, area: str = "") -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT week, cost, capacity, slop, lines, score FROM slop_meter_weeks "
            "WHERE repo = $1 AND area = $2 ORDER BY week",
            repo,
            area,
        )
        return [dict(r) for r in rows]
