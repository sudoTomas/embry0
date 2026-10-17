"""organizations: the forge owners embry0 maintains (ADR 0010, migration 97)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.services.organizations import FORGES, STATUSES, Organization
from embry0.storage.database import DatabasePool

_COLUMNS = (
    "owner, forge, credential_id, display_name, settings, access, repo_count, "
    "discovered_at, status, created_by, created_at, updated_at"
)


def _org(row: Any) -> Organization:
    return Organization(
        owner=row["owner"],
        forge=row["forge"],
        credential_id=row["credential_id"],
        display_name=row["display_name"],
        settings=dict(row["settings"] or {}),
        access=dict(row["access"] or {}),
        repo_count=row["repo_count"],
        discovered_at=row["discovered_at"],
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class OrganizationsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM organizations WHERE ($1::boolean OR status <> 'removed') ORDER BY owner",
            include_removed,
        )
        return [_org(r) for r in rows]

    async def get(self, owner: str) -> Organization | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM organizations WHERE owner = $1", owner.strip().lower())
        return _org(row) if row else None

    async def register(
        self,
        owner: str,
        forge: str,
        *,
        credential_id: str | None = None,
        display_name: str | None = None,
        settings: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> Organization:
        """Create the organization, or update its forge, credential and name.

        Idempotent on ``owner``: a second register with the same owner is an
        update, never a duplicate, and a removed organization comes back as
        active. ``owner`` is stored lowercase, the way the forges compare it.
        """
        owner = owner.strip().lower()
        if not owner or "/" in owner:
            raise ValueError("an organization is a forge owner, with no slash")
        if forge not in FORGES:
            raise ValueError(f"unknown forge {forge!r}")
        row = await self._db.fetchrow(
            f"""
            INSERT INTO organizations (owner, forge, credential_id, display_name, settings, created_by)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT (owner) DO UPDATE SET
                forge = EXCLUDED.forge,
                credential_id = EXCLUDED.credential_id,
                display_name = COALESCE(EXCLUDED.display_name, organizations.display_name),
                settings = CASE WHEN EXCLUDED.settings = '{{}}'::jsonb THEN organizations.settings ELSE EXCLUDED.settings END,
                status = 'active',
                updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            owner,
            forge,
            credential_id,
            display_name,
            dict(settings or {}),
            created_by,
        )
        return _org(row)

    async def update_access(
        self, owner: str, access: dict[str, Any], *, repo_count: int | None = None
    ) -> Organization | None:
        row = await self._db.fetchrow(
            f"""
            UPDATE organizations
               SET access = $2::jsonb,
                   repo_count = COALESCE($3, repo_count),
                   updated_at = NOW()
             WHERE owner = $1
            RETURNING {_COLUMNS}
            """,
            owner.strip().lower(),
            dict(access),
            repo_count,
        )
        return _org(row) if row else None

    async def mark_discovered(
        self, owner: str, *, repo_count: int, when: datetime | None = None
    ) -> Organization | None:
        row = await self._db.fetchrow(
            f"""
            UPDATE organizations
               SET repo_count = $2, discovered_at = COALESCE($3, NOW()), updated_at = NOW()
             WHERE owner = $1
            RETURNING {_COLUMNS}
            """,
            owner.strip().lower(),
            repo_count,
            when,
        )
        return _org(row) if row else None

    async def set_status(self, owner: str, status: str) -> Organization | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        row = await self._db.fetchrow(
            f"UPDATE organizations SET status = $2, updated_at = NOW() WHERE owner = $1 RETURNING {_COLUMNS}",
            owner.strip().lower(),
            status,
        )
        return _org(row) if row else None
