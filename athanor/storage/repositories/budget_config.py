"""Budget config repository — API-managed budget controls.

Defaults are derived from environment variables on every read, so changes
to ``.env`` flow through to the Settings UI on a fresh DB. Once the user
saves changes via the UI a row exists in ``budget_config`` and that row
takes precedence forever.
"""

import os
from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_HARDCODED_FALLBACKS = {
    "max_budget_per_job_usd": 10.0,
    "daily_cap_usd": 100.0,
    "monthly_cap_usd": 500.0,
    "rate_limit_per_author_per_hour": 5,
    "overrun_mode": "soft",
}

_UPDATABLE_FIELDS = frozenset(_HARDCODED_FALLBACKS.keys())


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return float(raw)
    except ValueError:
        logger.warning("env_var_parse_failed", var=name, value=raw, fallback=fallback)
        return fallback


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return int(raw)
    except ValueError:
        logger.warning("env_var_parse_failed", var=name, value=raw, fallback=fallback)
        return fallback


def _env_overrun_mode(fallback: str) -> str:
    raw = os.environ.get("BUDGET_OVERRUN_MODE", fallback)
    if raw not in ("soft", "hard"):
        logger.warning("env_var_invalid_overrun_mode", value=raw, fallback=fallback)
        return fallback
    return raw


def _env_derived_defaults() -> dict[str, Any]:
    """Read defaults from env at call time, falling back to hardcoded values."""
    return {
        "max_budget_per_job_usd": _env_float("MAX_BUDGET_USD", _HARDCODED_FALLBACKS["max_budget_per_job_usd"]),
        "daily_cap_usd": _env_float("DAILY_BUDGET_CAP_USD", _HARDCODED_FALLBACKS["daily_cap_usd"]),
        "monthly_cap_usd": _env_float("MONTHLY_BUDGET_CAP_USD", _HARDCODED_FALLBACKS["monthly_cap_usd"]),
        "rate_limit_per_author_per_hour": _env_int(
            "RATE_LIMIT_PER_AUTHOR_PER_HOUR", _HARDCODED_FALLBACKS["rate_limit_per_author_per_hour"]
        ),
        "overrun_mode": _env_overrun_mode(_HARDCODED_FALLBACKS["overrun_mode"]),
    }


class BudgetConfigRepository:
    """CRUD for budget configuration."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self) -> dict[str, Any]:
        """Get current budget config. Returns env-derived defaults if no row exists."""
        row = await self._db.fetchrow("SELECT * FROM budget_config WHERE id = 'default'")
        if row:
            return dict(row)
        return {**_env_derived_defaults(), "id": "default"}

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
