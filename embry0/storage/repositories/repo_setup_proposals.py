"""Agent-proposed test setups a person applies (migration 104, ADR 0019).

The onboarding agent in propose mode drafts a test setup, validates it and
starts the app with it, then stores the draft here instead of writing
``repo_qa_configs``. A proposal is ``running`` while its job runs, then
``pending`` (a draft for a person to review) or ``failed``. A person moves a
pending one to ``applied`` or ``rejected``, and a pending one becomes
``stale`` when the test setup changed under it. ``base_updated_at`` is the
setup's ``updated_at`` when the run started (None: there was none), so an
apply can refuse a setup that moved on. The partial unique index
``uq_repo_setup_proposals_live`` allows one running or pending proposal per
repository: it is also the lock against two setup runs at once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

import asyncpg

from embry0.storage.database import DatabasePool

ProposalStatus = Literal["running", "pending", "applied", "rejected", "stale", "failed"]

_COLUMNS = (
    "id, repo, job_id, status, base_updated_at, proposed_yaml, notes_md, smoke, failure, "
    "created_by, decided_by, decided_at, created_at, updated_at"
)


class LiveProposalExists(Exception):
    """The repository already has a setup run going or a proposal waiting for a person."""


class RepoSetupProposalsRepository:
    """CRUD for the ``repo_setup_proposals`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(self, repo: str, *, base_updated_at: datetime | None, created_by: str) -> str:
        """Start a proposal as ``running`` and return its id."""
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        try:
            await self._db.execute(
                "INSERT INTO repo_setup_proposals (id, repo, status, base_updated_at, created_by) "
                "VALUES ($1, $2, 'running', $3, $4)",
                proposal_id,
                repo,
                base_updated_at,
                created_by,
            )
        except asyncpg.UniqueViolationError:
            raise LiveProposalExists(f"{repo} already has a setup run going or a proposal waiting") from None
        return proposal_id

    async def attach_job(self, proposal_id: str, job_id: str) -> None:
        await self._db.execute(
            "UPDATE repo_setup_proposals SET job_id = $2, updated_at = NOW() WHERE id = $1", proposal_id, job_id
        )

    async def finish(
        self,
        proposal_id: str,
        *,
        status: Literal["pending", "failed"],
        proposed_yaml: str | None = None,
        notes_md: str = "",
        smoke: dict[str, Any] | None = None,
        failure: str = "",
    ) -> bool:
        """Record how the run ended. Only a ``running`` proposal finishes, so
        it finishes once; False when it is gone or already finished."""
        finished = await self._db.fetchval(
            """
            UPDATE repo_setup_proposals
            SET status = $2, proposed_yaml = $3, notes_md = $4, smoke = $5::jsonb, failure = $6, updated_at = NOW()
            WHERE id = $1 AND status = 'running'
            RETURNING id
            """,
            proposal_id,
            status,
            proposed_yaml,
            notes_md,
            smoke,
            failure,
        )
        return finished is not None

    async def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repo_setup_proposals WHERE id = $1", proposal_id)
        return dict(row) if row else None

    async def latest(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_setup_proposals WHERE repo = $1 ORDER BY created_at DESC LIMIT 1", repo
        )
        return dict(row) if row else None

    async def list_pending(self) -> list[dict[str, Any]]:
        """Every proposal waiting for a person, newest first."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM repo_setup_proposals WHERE status = 'pending' ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]

    async def mark_stale_pending(self, repo: str) -> int:
        """Retire the repository's waiting proposal, so a new run can start. Returns how many moved."""
        result = await self._db.execute(
            "UPDATE repo_setup_proposals SET status = 'stale', updated_at = NOW() "
            "WHERE repo = $1 AND status = 'pending'",
            repo,
        )
        return int(str(result).split()[-1]) if result else 0

    async def decide(
        self, proposal_id: str, *, status: Literal["applied", "rejected", "stale"], decided_by: str
    ) -> bool:
        """A person's decision on a ``pending`` proposal; False when it is not pending."""
        decided = await self._db.fetchval(
            """
            UPDATE repo_setup_proposals
            SET status = $2, decided_by = $3, decided_at = NOW(), updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING id
            """,
            proposal_id,
            status,
            decided_by,
        )
        return decided is not None

    async def delete(self, proposal_id: str) -> None:
        """Remove a proposal whose job never started."""
        await self._db.execute("DELETE FROM repo_setup_proposals WHERE id = $1", proposal_id)
