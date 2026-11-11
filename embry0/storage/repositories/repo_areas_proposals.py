"""The area mapper's areas, waiting for a person to apply them (migration 145, ADR 0019, spec 2026-11-10).

A map of a repository's areas is ``running`` while its job runs, then
``pending`` (a document for a person to review), ``unchanged`` (the
document it found is the stored one) or ``failed``. A person moves a
pending one to ``applied`` or ``rejected``; a pending one is retired as
``stale`` when the next map starts or an apply finds the stored document
moved. ``base_generation`` is the stored document's generation when the map
started (0: none), so an apply is a compare-and-set. The partial unique
index ``uq_repo_areas_proposals_live`` allows one running or pending
proposal per repository: it is also the lock against two maps at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal

import asyncpg

from embry0.storage.database import DatabasePool

AreasProposalStatus = Literal["running", "pending", "applied", "rejected", "stale", "failed", "unchanged"]

_COLUMNS = (
    "id, repo, job_id, status, base_generation, proposed_text, notes_md, failure, "
    "created_by, decided_by, decided_at, created_at, updated_at"
)


class LiveAreasProposalExists(Exception):
    """The repository already has a map running or one waiting for a person."""


class RepoAreasProposalsRepository:
    """CRUD for the ``repo_areas_proposals`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(self, repo: str, *, base_generation: int, created_by: str) -> str:
        """Start a proposal as ``running`` and return its id."""
        proposal_id = f"areas-{uuid.uuid4().hex[:12]}"
        try:
            await self._db.execute(
                "INSERT INTO repo_areas_proposals (id, repo, status, base_generation, created_by) "
                "VALUES ($1, $2, 'running', $3, $4)",
                proposal_id,
                repo,
                base_generation,
                created_by,
            )
        except asyncpg.UniqueViolationError:
            raise LiveAreasProposalExists(f"{repo} already has a map running or waiting") from None
        return proposal_id

    async def attach_job(self, proposal_id: str, job_id: str) -> None:
        await self._db.execute(
            "UPDATE repo_areas_proposals SET job_id = $2, updated_at = NOW() WHERE id = $1", proposal_id, job_id
        )

    async def finish(
        self,
        proposal_id: str,
        *,
        status: Literal["pending", "failed", "unchanged"],
        proposed_text: str | None = None,
        notes_md: str = "",
        failure: str = "",
    ) -> bool:
        """Record how the map ended. Only a ``running`` proposal finishes, so it finishes once; False when it is
        gone or already finished."""
        finished = await self._db.fetchval(
            """
            UPDATE repo_areas_proposals
            SET status = $2, proposed_text = $3, notes_md = $4, failure = $5, updated_at = NOW()
            WHERE id = $1 AND status = 'running'
            RETURNING id
            """,
            proposal_id,
            status,
            proposed_text,
            notes_md,
            failure,
        )
        return finished is not None

    async def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repo_areas_proposals WHERE id = $1", proposal_id)
        return dict(row) if row else None

    async def latest(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_areas_proposals WHERE repo = $1 ORDER BY created_at DESC LIMIT 1", repo
        )
        return dict(row) if row else None

    async def list_pending(self, *, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Every proposal waiting for a person, newest first. ``repos`` (lower-cased slugs) keeps only those
        repositories'; None keeps every one."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM repo_areas_proposals WHERE status = 'pending' "
            "AND ($1::text[] IS NULL OR lower(repo) = ANY($1::text[])) ORDER BY created_at DESC",
            None if repos is None else list(repos),
        )
        return [dict(r) for r in rows]

    async def start(self, repo: str, *, base_generation: int, created_by: str) -> tuple[str, list[str]]:
        """Retire the repository's waiting proposal (``stale``) and start a ``running`` one, in one transaction:
        the new id and the ids retired, which :meth:`withdraw` puts back if the map's job does not start."""
        proposal_id = f"areas-{uuid.uuid4().hex[:12]}"
        try:
            async with self._db.transaction() as conn:
                retired = await conn.fetch(
                    "UPDATE repo_areas_proposals SET status = 'stale', updated_at = NOW() "
                    "WHERE repo = $1 AND status = 'pending' RETURNING id",
                    repo,
                )
                await conn.execute(
                    "INSERT INTO repo_areas_proposals (id, repo, status, base_generation, created_by) "
                    "VALUES ($1, $2, 'running', $3, $4)",
                    proposal_id,
                    repo,
                    base_generation,
                    created_by,
                )
        except asyncpg.UniqueViolationError:
            raise LiveAreasProposalExists(f"{repo} already has a map running or waiting") from None
        return proposal_id, [str(r["id"]) for r in retired]

    async def withdraw(self, proposal_id: str, *, revive: Sequence[str] = ()) -> None:
        """Remove a proposal whose job never started and put back, as waiting, the ones its start retired, in one
        transaction: a map that could not start changes nothing."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM repo_areas_proposals WHERE id = $1 AND status = 'running'", proposal_id)
            if revive:
                await conn.execute(
                    "UPDATE repo_areas_proposals SET status = 'pending', updated_at = NOW() "
                    "WHERE id = ANY($1::text[]) AND status = 'stale' AND decided_at IS NULL",
                    list(revive),
                )

    async def decide(
        self,
        proposal_id: str,
        *,
        status: Literal["applied", "rejected", "stale"],
        decided_by: str,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        """A person's decision on a ``pending`` proposal; False when it is not pending. On ``conn``, inside the
        caller's transaction, when given."""
        decided = await (conn or self._db).fetchval(
            """
            UPDATE repo_areas_proposals
            SET status = $2, decided_by = $3, decided_at = NOW(), updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING id
            """,
            proposal_id,
            status,
            decided_by,
        )
        return decided is not None
