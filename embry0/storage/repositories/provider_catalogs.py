"""A provider's live model list, as last read (migration 114).

One row per provider whose models are read live; only ``openrouter`` today.
``models`` is the last good list. A failed refresh writes only the error and
its time, so the list in force survives a provider outage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


class ProviderCatalogsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, provider: str) -> dict[str, Any] | None:
        """The stored list and its state: ``models``, ``skipped``, ``fetched_at``, ``last_error``, ``last_error_at``."""
        row = await self._db.fetchrow(
            "SELECT models, skipped, fetched_at, last_error, last_error_at FROM provider_catalogs WHERE provider = $1",
            provider,
        )
        if row is None:
            return None
        return {
            "models": list(row["models"] or []),
            "skipped": dict(row["skipped"] or {}),
            "fetched_at": row["fetched_at"],
            "last_error": row["last_error"],
            "last_error_at": row["last_error_at"],
        }

    async def save(
        self, provider: str, models: list[dict[str, Any]], skipped: dict[str, int], fetched_at: datetime
    ) -> None:
        """Store a fresh list; clears the last error."""
        await self._db.execute(
            """
            INSERT INTO provider_catalogs (provider, models, skipped, fetched_at, last_error, last_error_at)
            VALUES ($1, $2, $3, $4, NULL, NULL)
            ON CONFLICT (provider) DO UPDATE SET
                models = EXCLUDED.models,
                skipped = EXCLUDED.skipped,
                fetched_at = EXCLUDED.fetched_at,
                last_error = NULL,
                last_error_at = NULL
            """,
            provider,
            models,
            skipped,
            fetched_at,
        )

    async def save_error(self, provider: str, error: str, at: datetime) -> None:
        """Record a failed refresh; the stored list stays as it was."""
        await self._db.execute(
            """
            INSERT INTO provider_catalogs (provider, last_error, last_error_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (provider) DO UPDATE SET last_error = EXCLUDED.last_error, last_error_at = EXCLUDED.last_error_at
            """,
            provider,
            error[:500],
            at,
        )
