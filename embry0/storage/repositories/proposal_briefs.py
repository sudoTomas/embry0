"""Proposal briefs (migration 117, spec 2026-10-25): one plain-words brief per proposal still asked.

Each records the input hash it was written from, which readers match, and a hash of the system prompt it
was written with (migration 138, #166), which only the brief writer reads.
"""

from __future__ import annotations

from typing import Any

from embry0.services.proposal_briefs import Brief
from embry0.storage.database import DatabasePool


class ProposalBriefsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def all(self) -> dict[str, dict[str, Any]]:
        """Every stored brief, by proposal id."""
        rows = await self._db.fetch(
            "SELECT proposal_id, input_hash, prompt_hash, title, problem, fix, size, model, written_at "
            "FROM proposal_briefs"
        )
        return {str(r["proposal_id"]): dict(r) for r in rows}

    async def save(self, proposal_id: str, input_hash: str, brief: Brief, *, model: str, prompt_hash: str) -> None:
        """Store the brief written from ``input_hash`` with the prompt ``prompt_hash``, in place of any earlier one."""
        await self._db.execute(
            """
            INSERT INTO proposal_briefs (proposal_id, input_hash, title, problem, fix, size, model, prompt_hash)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (proposal_id) DO UPDATE SET
                input_hash = EXCLUDED.input_hash, title = EXCLUDED.title, problem = EXCLUDED.problem,
                fix = EXCLUDED.fix, size = EXCLUDED.size, model = EXCLUDED.model,
                prompt_hash = EXCLUDED.prompt_hash, written_at = NOW()
            """,
            proposal_id,
            input_hash,
            brief.title,
            brief.problem,
            brief.fix,
            brief.size,
            model,
            prompt_hash,
        )

    async def prune(self, keep: set[str]) -> int:
        """Delete the briefs of proposals no longer asked; how many went."""
        result = await self._db.execute(
            "DELETE FROM proposal_briefs WHERE NOT (proposal_id = ANY($1::text[]))", sorted(keep)
        )
        try:
            return int(str(result).rsplit(" ", 1)[-1])
        except ValueError:
            return 0
