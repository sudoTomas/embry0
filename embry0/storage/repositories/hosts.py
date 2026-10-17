"""hosts and repo_hosts: the machines that run the code, and which repositories each serves (ADR 0013, migration 100)."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

import asyncpg

from embry0.services.hosts import SOURCES, STATUSES, TRANSPORTS, Host
from embry0.storage.database import DatabasePool

_COLUMNS = "id, name, address, transport, roles, log_source, tags, source, last_seen_at, status, created_at, updated_at"
_JSONB = ("roles", "log_source", "tags")
#: The columns ``update`` may set. Anything else is a programming error.
_UPDATABLE = ("name", "address", "transport", "roles", "log_source", "tags", "last_seen_at", "status")


class HostNameTakenError(ValueError):
    """Another host already has this name (names are unique, ignoring case)."""


def new_host_id() -> str:
    return "host-" + secrets.token_hex(6)


def _host(row: Any, repos: list[dict[str, str]] | None = None) -> Host:
    return Host(
        id=row["id"],
        name=row["name"],
        address=row["address"],
        transport=row["transport"],
        roles=list(row["roles"] or []),
        log_source=dict(row["log_source"]) if row["log_source"] else None,
        tags=list(row["tags"] or []),
        source=row["source"],
        last_seen_at=row["last_seen_at"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        repos=list(repos or []),
    )


def _check(transport: str | None = None, source: str | None = None, status: str | None = None) -> None:
    if transport is not None and transport not in TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}")
    if source is not None and source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")


class HostsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def _links(self, host_ids: list[str]) -> dict[str, list[dict[str, str]]]:
        if not host_ids:
            return {}
        rows = await self._db.fetch(
            "SELECT host_id, repo, role FROM repo_hosts WHERE host_id = ANY($1::text[]) ORDER BY repo, role",
            host_ids,
        )
        links: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            links.setdefault(r["host_id"], []).append({"repo": r["repo"], "role": r["role"]})
        return links

    async def list_all(self, *, source: str | None = None, include_removed: bool = False) -> list[Host]:
        _check(source=source)
        rows = await self._db.fetch(
            f"""
            SELECT {_COLUMNS} FROM hosts
             WHERE ($1::text IS NULL OR source = $1)
               AND ($2::boolean OR status <> 'removed')
             ORDER BY lower(name)
            """,
            source,
            include_removed,
        )
        links = await self._links([r["id"] for r in rows])
        return [_host(r, links.get(r["id"])) for r in rows]

    async def get(self, host_id: str) -> Host | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM hosts WHERE id = $1", host_id)
        if row is None:
            return None
        return _host(row, (await self._links([row["id"]])).get(row["id"]))

    async def get_by_name(self, name: str) -> Host | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM hosts WHERE lower(name) = lower($1)", name.strip())
        if row is None:
            return None
        return _host(row, (await self._links([row["id"]])).get(row["id"]))

    async def create(
        self,
        *,
        name: str,
        address: str,
        transport: str,
        roles: list[str] | None = None,
        log_source: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        source: str = "manual",
        last_seen_at: datetime | None = None,
        status: str = "active",
    ) -> Host:
        """Insert a host. A removed host with the same name is brought back with these values, keeping its id.

        Raises ``HostNameTakenError`` when a host that is not removed has the name.
        """
        _check(transport=transport, source=source, status=status)
        name = name.strip()
        if not name:
            raise ValueError("a host needs a name")
        row = await self._db.fetchrow(
            f"""
            INSERT INTO hosts (id, name, address, transport, roles, log_source, tags, source, last_seen_at, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9, $10)
            ON CONFLICT ((lower(name))) DO UPDATE SET
                name = EXCLUDED.name,
                address = EXCLUDED.address,
                transport = EXCLUDED.transport,
                roles = EXCLUDED.roles,
                log_source = EXCLUDED.log_source,
                tags = EXCLUDED.tags,
                source = EXCLUDED.source,
                last_seen_at = EXCLUDED.last_seen_at,
                status = EXCLUDED.status,
                updated_at = NOW()
              WHERE hosts.status = 'removed'
            RETURNING {_COLUMNS}
            """,
            new_host_id(),
            name,
            address.strip(),
            transport,
            list(roles or []),
            dict(log_source) if log_source else None,
            list(tags or []),
            source,
            last_seen_at,
            status,
        )
        if row is None:
            raise HostNameTakenError(f"a host named {name!r} already exists")
        return _host(row, (await self._links([row["id"]])).get(row["id"]))

    async def update(self, host_id: str, fields: dict[str, Any]) -> Host | None:
        """Set the given columns. ``log_source=None`` clears it; a removed host's repository links are dropped."""
        unknown = sorted(set(fields) - set(_UPDATABLE))
        if unknown:
            raise ValueError(f"cannot update {unknown}")
        _check(transport=fields.get("transport"), status=fields.get("status"))
        if not fields:
            return await self.get(host_id)
        assignments: list[str] = []
        values: list[Any] = [host_id]
        for column in _UPDATABLE:
            if column not in fields:
                continue
            value = fields[column]
            if column == "name":
                value = str(value).strip()
                if not value:
                    raise ValueError("a host needs a name")
            elif column == "address":
                value = str(value).strip()
            elif column in ("roles", "tags"):
                value = list(value or [])
            elif column == "log_source":
                value = dict(value) if value else None
            values.append(value)
            cast = "::jsonb" if column in _JSONB else ""
            assignments.append(f"{column} = ${len(values)}{cast}")
        try:
            async with self._db.transaction() as conn:
                row = await conn.fetchrow(
                    f"UPDATE hosts SET {', '.join(assignments)}, updated_at = NOW() WHERE id = $1 RETURNING {_COLUMNS}",
                    *values,
                )
                if row is not None and row["status"] == "removed":
                    await conn.execute("DELETE FROM repo_hosts WHERE host_id = $1", host_id)
        except asyncpg.UniqueViolationError:
            raise HostNameTakenError(f"a host named {fields.get('name')!r} already exists") from None
        if row is None:
            return None
        return _host(row, (await self._links([row["id"]])).get(row["id"]))

    async def remove(self, host_id: str) -> Host | None:
        """Mark the host removed and drop its repository links. The row stays, so an import does not bring it back."""
        return await self.update(host_id, {"status": "removed"})

    async def record_probe(self, host_id: str, *, reachable: bool, last_seen_at: datetime | None) -> Host | None:
        """Write what a probe observed. Never touches a removed host, nor anything a person configured."""
        row = await self._db.fetchrow(
            f"""
            UPDATE hosts
               SET status = CASE WHEN $2 THEN 'active' ELSE 'unreachable' END,
                   last_seen_at = COALESCE($3, last_seen_at),
                   updated_at = NOW()
             WHERE id = $1 AND status <> 'removed'
            RETURNING {_COLUMNS}
            """,
            host_id,
            reachable,
            last_seen_at,
        )
        if row is None:
            return None
        return _host(row, (await self._links([row["id"]])).get(row["id"]))

    async def replace_repos(self, host_id: str, links: list[tuple[str, str]]) -> list[dict[str, str]]:
        """Replace the repositories this host serves. ``links`` is ``(repo, role)``; repos are stored lowercase."""
        seen: dict[str, str] = {}
        for repo, role in links:
            seen[repo.strip().lower()] = role.strip()
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM repo_hosts WHERE host_id = $1", host_id)
            for repo, role in sorted(seen.items()):
                await conn.execute(
                    "INSERT INTO repo_hosts (repo, host_id, role) VALUES ($1, $2, $3)", repo, host_id, role
                )
        return (await self._links([host_id])).get(host_id, [])

    async def hosts_for_repo(self, repo: str) -> list[dict[str, Any]]:
        """The hosts serving ``repo`` (any case), each with the role it plays for it. Removed hosts are left out."""
        rows = await self._db.fetch(
            f"""
            SELECT {", ".join("h." + c.strip() for c in _COLUMNS.split(","))}, rh.role AS link_role
              FROM repo_hosts rh JOIN hosts h ON h.id = rh.host_id
             WHERE rh.repo = lower($1) AND h.status <> 'removed'
             ORDER BY lower(h.name)
            """,
            repo.strip(),
        )
        return [{**_host(r).to_dict(with_repos=False), "role": r["link_role"]} for r in rows]
