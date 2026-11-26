"""Users repository — SSO identity mirror + session control (RAV-1056 Phase A).

Rows are JIT-populated at the SSO callback; roles/tier remain Auth0-owned
(`tier` here is a cache of the last login's fold, refreshed every login).
``disabled`` and ``session_epoch`` are the embry0-side controls: disable
denies access regardless of Auth0 roles, and bumping the epoch invalidates
every session cookie minted under the old one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from embry0.storage.database import DatabasePool

VALID_TIERS = frozenset({"none", "operator", "admin"})


@dataclass(frozen=True, slots=True)
class UserRow:
    id: str
    auth0_sub: str
    email: str
    name: str
    tier: str
    disabled: bool
    session_epoch: int
    login_count: int
    first_login_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime | None


def _row(r: Any) -> UserRow:
    return UserRow(
        id=r["id"],
        auth0_sub=r["auth0_sub"],
        email=r["email"],
        name=r["name"],
        tier=r["tier"],
        disabled=r["disabled"],
        session_epoch=r["session_epoch"],
        login_count=r["login_count"],
        first_login_at=r["first_login_at"],
        last_login_at=r["last_login_at"],
        created_at=r["created_at"],
    )


class UsersRepository:
    def __init__(self, db: DatabasePool, *, audit_log_path: str | Path | None = None):
        self._db = db
        self._audit_log_path = audit_log_path

    async def upsert_login(self, *, auth0_sub: str, email: str, name: str, tier: str) -> UserRow:
        """Insert-or-update at login time; returns the fresh row.

        Lookup by sub first; falls back to case-insensitive email so an
        identity migration in the tenant (same person, new sub) re-binds the
        existing row instead of duplicating it.
        """
        if tier not in VALID_TIERS:
            raise ValueError(f"invalid tier {tier!r}")
        existing = await self._db.fetchrow(
            "SELECT id FROM users WHERE auth0_sub = $1 OR LOWER(email) = LOWER($2) ORDER BY (auth0_sub = $1) DESC LIMIT 1",
            auth0_sub,
            email,
        )
        if existing:
            row = await self._db.fetchrow(
                """
                UPDATE users
                SET auth0_sub = $2, email = $3, name = $4, tier = $5,
                    login_count = login_count + 1, last_login_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                existing["id"],
                auth0_sub,
                email,
                name,
                tier,
            )
        else:
            row = await self._db.fetchrow(
                """
                INSERT INTO users (id, auth0_sub, email, name, tier, login_count)
                VALUES ($1, $2, $3, $4, $5, 1)
                RETURNING *
                """,
                f"usr-{uuid.uuid4().hex[:12]}",
                auth0_sub,
                email,
                name,
                tier,
            )
        assert row is not None
        return _row(row)

    async def list_users(self, limit: int = 100, offset: int = 0) -> list[UserRow]:
        rows = await self._db.fetch(
            "SELECT * FROM users ORDER BY last_login_at DESC, id DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        return [_row(r) for r in rows]

    async def count(self) -> int:
        row = await self._db.fetchrow("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    async def get(self, user_id: str) -> UserRow | None:
        row = await self._db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return _row(row) if row else None

    async def get_by_sub(self, auth0_sub: str) -> UserRow | None:
        row = await self._db.fetchrow("SELECT * FROM users WHERE auth0_sub = $1", auth0_sub)
        return _row(row) if row else None

    async def set_disabled(self, user_id: str, disabled: bool) -> UserRow | None:
        row = await self._db.fetchrow(
            "UPDATE users SET disabled = $2 WHERE id = $1 RETURNING *",
            user_id,
            disabled,
        )
        return _row(row) if row else None

    async def get_by_email(self, email: str) -> UserRow | None:
        row = await self._db.fetchrow("SELECT * FROM users WHERE LOWER(email) = LOWER($1)", email)
        return _row(row) if row else None

    async def people(self) -> list[UserRow]:
        """Everyone who can be sent a decision: the users not disabled, by name."""
        rows = await self._db.fetch(
            "SELECT * FROM users WHERE NOT disabled ORDER BY LOWER(COALESCE(NULLIF(name, ''), email)), id"
        )
        return [_row(r) for r in rows]

    async def disabled_ids(self) -> set[str]:
        """The users who may not sign in: no person they are linked to is asked, owns or decides (#179)."""
        return {str(r["id"]) for r in await self._db.fetch("SELECT id FROM users WHERE disabled")}

    async def bump_epoch(self, user_id: str) -> UserRow | None:
        """Invalidate every session cookie minted under the current epoch."""
        row = await self._db.fetchrow(
            "UPDATE users SET session_epoch = session_epoch + 1 WHERE id = $1 RETURNING *",
            user_id,
        )
        return _row(row) if row else None
