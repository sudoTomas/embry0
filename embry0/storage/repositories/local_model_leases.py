"""Leases on the local model servers' slots (migration 115; each on its server since #174; docs/providers.md).

A lease is one agent run, or one background call, on one of the operator's
own model servers (``server``, :mod:`embry0.services.local_slots`). Taking one
is a single transaction under that server's advisory lock: expired leases are
dropped, the server's live ones counted, and a new one written only below the
cap, so two servers never wait on each other. Its expiry is the run's own time
limit plus a margin, so a lease whose process died with it frees itself; the
run's normal end deletes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.storage.database import DatabasePool

_LOCK_KEY = "local_model_slots"
_MARGIN = timedelta(minutes=5)


class LocalModelLeasesRepository:
    """Take, release and count the local model servers' leases."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def try_take(
        self,
        *,
        cap: int,
        server: str,
        job_id: str,
        agent_type: str,
        model: str,
        run_seconds: int,
        now: datetime | None = None,
    ) -> str | None:
        """A new lease on ``server``, or None when ``cap`` live leases on it are held already."""
        at = now or datetime.now(UTC)
        expires = at + timedelta(seconds=max(run_seconds, 0)) + _MARGIN
        lease_id = f"lease-{uuid.uuid4().hex[:16]}"
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{_LOCK_KEY}:{server}")
            await conn.execute("DELETE FROM local_model_leases WHERE server = $1 AND expires_at <= $2", server, at)
            held = await conn.fetchval("SELECT count(*) FROM local_model_leases WHERE server = $1", server)
            if int(held or 0) >= max(cap, 1):
                return None
            await conn.execute(
                "INSERT INTO local_model_leases (lease_id, server, job_id, agent_type, model, acquired_at, expires_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7)",
                lease_id,
                server,
                job_id,
                agent_type,
                model,
                at,
                expires,
            )
        return lease_id

    async def release(self, lease_id: str) -> None:
        await self._db.execute("DELETE FROM local_model_leases WHERE lease_id = $1", lease_id)

    async def live(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """The leases held now, on every server, oldest first."""
        rows = await self._db.fetch(
            "SELECT lease_id, server, job_id, agent_type, model, acquired_at, expires_at FROM local_model_leases"
            " WHERE expires_at > $1 ORDER BY acquired_at, lease_id",
            now or datetime.now(UTC),
        )
        return [dict(r) for r in rows]
