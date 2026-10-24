"""API keys repository (RAV-1056 Phase B).

Keys are owned by a user (self-service, scope capped at the owner's tier)
or by the system (``user_id IS NULL`` — service keys such as the
context-proxy's boot-rotated credential and a chat relay's key). Only the
sha256 of the token is stored; the plaintext exists exactly once, in the
mint response.

``allowed_repos`` narrows a key to the repositories it may start work on;
``None`` is unrestricted. The check itself lives in
``embry0.api.repo_allowlist``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

# `merge` is the chat relay's narrow scope (#319 phase 2); see api/deps.py.
VALID_SCOPES = frozenset({"read", "operate", "admin", "merge", "propose"})
TOKEN_PREFIX = "emb_"
# last_used_at is a coarse activity signal, not per-request telemetry —
# writes are throttled to once a minute per key.
LAST_USED_THROTTLE_SECONDS = 60


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ApiKeyRow:
    id: str
    user_id: str | None
    name: str
    prefix: str
    scope: str
    created_by: str
    created_at: datetime | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    allowed_repos: tuple[str, ...] | None = None


def _row(r: Any) -> ApiKeyRow:
    return ApiKeyRow(
        id=r["id"],
        user_id=r["user_id"],
        name=r["name"],
        prefix=r["prefix"],
        scope=r["scope"],
        created_by=r["created_by"],
        created_at=r["created_at"],
        expires_at=r["expires_at"],
        last_used_at=r["last_used_at"],
        revoked_at=r["revoked_at"],
        allowed_repos=_allowed(r.get("allowed_repos")),
    )


def _allowed(value: Any) -> tuple[str, ...] | None:
    # The column refuses an empty array, so only NULL means unrestricted.
    return tuple(value) if value else None


class ApiKeysRepository:
    def __init__(self, db: DatabasePool):
        self._db = db

    async def create(
        self,
        *,
        name: str,
        scope: str,
        user_id: str | None,
        created_by: str,
        expires_at: datetime | None = None,
        allowed_repos: list[str] | None = None,
    ) -> tuple[ApiKeyRow, str]:
        """Mint a key; returns (row, plaintext). The plaintext is never
        retrievable again. ``allowed_repos`` empty or None: unrestricted."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r}")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = await self._db.fetchrow(
            """
            INSERT INTO api_keys (id, user_id, name, key_hash, prefix, scope, created_by, expires_at, allowed_repos)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            f"key-{uuid.uuid4().hex[:12]}",
            user_id,
            name,
            hash_token(token),
            token[:12],
            scope,
            created_by,
            expires_at,
            allowed_repos or None,
        )
        assert row is not None
        return _row(row), token

    async def get_by_hash(self, key_hash: str) -> ApiKeyRow | None:
        row = await self._db.fetchrow("SELECT * FROM api_keys WHERE key_hash = $1", key_hash)
        return _row(row) if row else None

    async def get(self, key_id: str) -> ApiKeyRow | None:
        row = await self._db.fetchrow("SELECT * FROM api_keys WHERE id = $1", key_id)
        return _row(row) if row else None

    async def list_all(self) -> list[ApiKeyRow]:
        rows = await self._db.fetch("SELECT * FROM api_keys ORDER BY created_at DESC")
        return [_row(r) for r in rows]

    async def list_for_user(self, user_id: str) -> list[ApiKeyRow]:
        rows = await self._db.fetch(
            "SELECT * FROM api_keys WHERE user_id = $1 ORDER BY created_at DESC",
            user_id,
        )
        return [_row(r) for r in rows]

    async def set_allowed_repos(self, key_id: str, allowed_repos: list[str] | None) -> ApiKeyRow | None:
        """Replace the key's repository allowlist; empty or None lifts it."""
        row = await self._db.fetchrow(
            "UPDATE api_keys SET allowed_repos = $2 WHERE id = $1 RETURNING *",
            key_id,
            allowed_repos or None,
        )
        return _row(row) if row else None

    async def revoke(self, key_id: str) -> ApiKeyRow | None:
        row = await self._db.fetchrow(
            "UPDATE api_keys SET revoked_at = NOW() WHERE id = $1 AND revoked_at IS NULL RETURNING *",
            key_id,
        )
        return _row(row) if row else None

    async def touch_last_used(self, key_id: str) -> None:
        await self._db.execute(
            """
            UPDATE api_keys SET last_used_at = NOW()
            WHERE id = $1
              AND (last_used_at IS NULL OR last_used_at < NOW() - make_interval(secs => $2))
            """,
            key_id,
            LAST_USED_THROTTLE_SECONDS,
        )

    async def rotate_service_key(self, name: str, scope: str) -> str:
        """Boot-time service credential: find the unowned key by name and
        replace its hash with a fresh token (or insert). Returns the new
        plaintext — the previous token stops working immediately, which is
        correct for consumers recreated alongside the orchestrator."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r}")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        updated = await self._db.fetchrow(
            """
            UPDATE api_keys
            SET key_hash = $2, prefix = $3, scope = $4, revoked_at = NULL, created_at = NOW()
            WHERE name = $1 AND user_id IS NULL
            RETURNING id
            """,
            name,
            hash_token(token),
            token[:12],
            scope,
        )
        if not updated:
            await self._db.fetchrow(
                """
                INSERT INTO api_keys (id, user_id, name, key_hash, prefix, scope, created_by)
                VALUES ($1, NULL, $2, $3, $4, $5, 'system')
                RETURNING id
                """,
                f"key-{uuid.uuid4().hex[:12]}",
                name,
                hash_token(token),
                token[:12],
                scope,
            )
        return token
