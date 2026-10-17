"""org_repo_facts and org_discovery_runs: what discovery found under an owner (ADR 0010, migration 98)."""

from __future__ import annotations

import secrets
from typing import Any

from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool

_FACT_COLUMNS = (
    "owner, repo, run_id, archived, private, fork, default_branch, language, pushed_at, open_issues, facts, seen_at"
)
_RUN_COLUMNS = "id, owner, status, repos_total, repos_seen, summary, error, started_at, finished_at"


def _fact(row: Any) -> dict[str, Any]:
    return {
        "owner": row["owner"],
        "repo": row["repo"],
        "run_id": row["run_id"],
        "archived": bool(row["archived"]),
        "private": bool(row["private"]),
        "fork": bool(row["fork"]),
        "default_branch": row["default_branch"],
        "language": row["language"],
        "pushed_at": row["pushed_at"].isoformat() if row["pushed_at"] else None,
        "open_issues": row["open_issues"],
        "facts": dict(row["facts"] or {}),
        "seen_at": row["seen_at"].isoformat() if row["seen_at"] else None,
    }


def _run(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "status": row["status"],
        "repos_total": row["repos_total"],
        "repos_seen": row["repos_seen"],
        "summary": dict(row["summary"] or {}),
        "error": row["error"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


class OrgReposRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert_fact(self, facts: RepoFacts, *, run_id: str) -> None:
        await self._db.execute(
            """
            INSERT INTO org_repo_facts (owner, repo, run_id, archived, private, fork, default_branch, language, pushed_at, open_issues, facts, seen_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, NOW())
            ON CONFLICT (repo) DO UPDATE SET
                owner = EXCLUDED.owner, run_id = EXCLUDED.run_id, archived = EXCLUDED.archived,
                private = EXCLUDED.private, fork = EXCLUDED.fork, default_branch = EXCLUDED.default_branch,
                language = EXCLUDED.language, pushed_at = EXCLUDED.pushed_at, open_issues = EXCLUDED.open_issues,
                facts = EXCLUDED.facts, seen_at = NOW()
            """,
            facts.owner,
            facts.repo,
            run_id,
            facts.archived,
            facts.private,
            facts.fork,
            facts.default_branch,
            facts.language,
            facts.pushed_at,
            facts.open_issues,
            dict(facts.facts),
        )

    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"SELECT {_FACT_COLUMNS} FROM org_repo_facts WHERE owner = $1 AND ($2::boolean OR NOT archived) ORDER BY pushed_at DESC NULLS LAST, repo",
            owner.lower(),
            include_archived,
        )
        return [_fact(r) for r in rows]

    async def start_run(self, owner: str) -> dict[str, Any]:
        run_id = f"odr-{secrets.token_hex(6)}"
        row = await self._db.fetchrow(
            f"INSERT INTO org_discovery_runs (id, owner, status) VALUES ($1, $2, 'running') RETURNING {_RUN_COLUMNS}",
            run_id,
            owner.lower(),
        )
        return _run(row)

    async def progress(self, run_id: str, *, seen: int, total: int) -> None:
        await self._db.execute(
            "UPDATE org_discovery_runs SET repos_seen = $2, repos_total = $3 WHERE id = $1", run_id, seen, total
        )

    async def finish_run(
        self, run_id: str, *, summary: dict[str, Any] | None = None, error: str | None = None
    ) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"""
            UPDATE org_discovery_runs
               SET status = $2, summary = $3::jsonb, error = $4, finished_at = NOW()
             WHERE id = $1
            RETURNING {_RUN_COLUMNS}
            """,
            run_id,
            "error" if error else "done",
            dict(summary or {}),
            error,
        )
        return _run(row) if row else None

    async def running_run(self, owner: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_RUN_COLUMNS} FROM org_discovery_runs WHERE owner = $1 AND status = 'running' ORDER BY started_at DESC LIMIT 1",
            owner.lower(),
        )
        return _run(row) if row else None

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"SELECT {_RUN_COLUMNS} FROM org_discovery_runs WHERE owner = $1 ORDER BY started_at DESC LIMIT $2",
            owner.lower(),
            limit,
        )
        return [_run(r) for r in rows]
