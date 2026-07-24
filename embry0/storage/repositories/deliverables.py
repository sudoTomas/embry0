"""Deliverables repository — typed job outputs beyond the PR (RAV-603).

A deliverable is what a completed job actually produced: the PR for code
kinds, a markdown report for research/analysis/ops, a stored file
(artifact), or a short message. Rows are written once at job completion
(``IssueExecutor._handle_workflow_result``) and are immutable afterwards —
there is no update path by design.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

DELIVERABLE_TYPES = frozenset({"pr", "report", "artifact", "message"})


class DeliverablesRepository:
    """Insert/read operations for the deliverables table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        job_id: str,
        type: str,
        title: str = "",
        content: str | None = None,
        url: str | None = None,
        storage_bucket: str | None = None,
        storage_key: str | None = None,
        media_type: str | None = None,
        size_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a deliverable row and return its id."""
        if type not in DELIVERABLE_TYPES:
            raise ValueError(f"unknown deliverable type {type!r} (expected one of {sorted(DELIVERABLE_TYPES)})")
        deliverable_id = f"del-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO deliverables (
                id, job_id, type, title, content, url,
                storage_bucket, storage_key, media_type, size_bytes, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            deliverable_id,
            job_id,
            type,
            title,
            content,
            url,
            storage_bucket,
            storage_key,
            media_type,
            size_bytes,
            metadata or {},
        )
        logger.info("deliverable_created", deliverable_id=deliverable_id, job_id=job_id, type=type)
        return deliverable_id

    async def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """All deliverables for a job, in creation order."""
        rows = await self._db.fetch(
            "SELECT * FROM deliverables WHERE job_id = $1 ORDER BY created_at, id",
            job_id,
        )
        return [dict(r) for r in rows]

    async def get(self, deliverable_id: str) -> dict[str, Any] | None:
        """Fetch a single deliverable by id."""
        row = await self._db.fetchrow("SELECT * FROM deliverables WHERE id = $1", deliverable_id)
        return dict(row) if row is not None else None
