"""Budget config repository — API-managed budget controls."""

from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_DEFAULTS = {
    "max_budget_per_job_usd": 10.0,
    "daily_cap_usd": 100.0,
    "monthly_cap_usd": 500.0,
    "rate_limit_per_author_per_hour": 5,
    "overrun_mode": "soft",
}

_UPDATABLE_FIELDS = frozenset(_DEFAULTS.keys())


class BudgetConfigRepository:
    """CRUD for budget configuration."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self) -> dict[str, Any]:
        """Get current budget config. Returns defaults if no row exists."""
        row = await self._db.fetchrow("SELECT * FROM budget_config WHERE id = 'default'")
        if row:
            return dict(row)
        return {**_DEFAULTS, "id": "default"}

    async def update(self, **fields: Any) -> None:
        """Update budget config fields. Creates default row if needed."""
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not valid:
            return
        await self._db.execute(
            "INSERT INTO budget_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING",
        )
        sets: list[str] = []
        args: list[Any] = []
        idx = 1
        for key, value in valid.items():
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1
        sets.append("updated_at = NOW()")
        await self._db.execute(
            f"UPDATE budget_config SET {', '.join(sets)} WHERE id = 'default'",
            *args,
        )
        logger.info("budget_config_updated", fields=list(valid.keys()))
