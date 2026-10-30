"""skill_sources and inline_skills: where agent skills come from (spec 3.2).

A row here is a promise the sync service keeps: `status` says whether the
store holds the tree, `skills` is the catalog read from it at sync time, and
`plugin_name` is the prefix every skill of the source carries.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

import structlog

from embry0.git_refs import REF_RULE, valid_ref
from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
INLINE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
INLINE_BODY_MAX = 64 * 1024
SOURCE_KINDS = ("git", "path", "inline")
_UPDATE_FIELDS = {"ref", "enabled", "status", "error", "resolved_sha", "plugin_name", "skills", "synced_at"}
_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}


class SkillSourceRefused(ValueError):
    """The table must not store this source or skill; the API answers 422 with the message."""


def validate_source_url(kind: str, url: str) -> str:
    if kind == "inline":
        if url:
            raise SkillSourceRefused("an inline source has no url")
        return ""
    if kind == "path":
        if not url.startswith("/") or "/../" in f"{url}/":
            raise SkillSourceRefused("a path source names an absolute directory inside the orchestrator container")
        return url.rstrip("/") or "/"
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SkillSourceRefused("a git source is cloned anonymously over https; only https:// urls are accepted")
    if parts.username or parts.password:
        raise SkillSourceRefused("a git source url must not carry credentials")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host in _LOCAL_HOSTS or host.endswith((".local", ".localhost", ".internal")) or "." not in host:
        raise SkillSourceRefused("a git source url must name a public host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SkillSourceRefused("a git source url must name a host, not an IP address")
    return url


def _check_id(source_id: str) -> str:
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise SkillSourceRefused("id must match ^[a-z][a-z0-9-]{0,39}$")
    return source_id


def _check_ref(kind: str, ref: str) -> str:
    if kind != "git":
        if ref:
            raise SkillSourceRefused(f"a {kind} source has no ref")
        return ""
    # The ref is passed to git on a command line: embry0.git_refs is the rule the clone applies too.
    if not valid_ref(ref):
        raise SkillSourceRefused(f"ref must be a tag, branch or sha: {REF_RULE}")
    return ref


class SkillSourcesRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM skill_sources ORDER BY is_builtin DESC, id")
        return [dict(r) for r in rows]

    async def get(self, source_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM skill_sources WHERE id = $1", source_id)
        return None if row is None else dict(row)

    async def create(
        self, *, source_id: str, kind: str, url: str = "", ref: str = "", enabled: bool = True, is_builtin: bool = False
    ) -> dict[str, Any]:
        if kind not in SOURCE_KINDS:
            raise SkillSourceRefused(f"kind must be one of {', '.join(SOURCE_KINDS)}")
        _check_id(source_id)
        url = validate_source_url(kind, url)
        ref = _check_ref(kind, ref)
        row = await self._db.fetchrow(
            """
            INSERT INTO skill_sources (id, kind, url, ref, enabled, is_builtin)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            source_id,
            kind,
            url,
            ref,
            enabled,
            is_builtin,
        )
        assert row is not None
        logger.info("skill_source_created", source_id=source_id, kind=kind)
        return dict(row)

    async def update(self, source_id: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - _UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown fields for update: {sorted(unknown)}")
        if "ref" in fields:
            fields["ref"] = _check_ref("git", str(fields["ref"]))
        if not fields:
            current = await self.get(source_id)
            if current is None:
                raise ValueError(f"skill source {source_id!r} not found")
            return current
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=1))
        params: list[Any] = [*fields.values(), source_id]
        row = await self._db.fetchrow(
            f"UPDATE skill_sources SET {sets}, updated_at = NOW() WHERE id = ${len(params)} RETURNING *", *params
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def mark_synced(
        self, source_id: str, *, resolved_sha: str, plugin_name: str, skills: list[dict[str, str]]
    ) -> dict[str, Any]:
        row = await self._db.fetchrow(
            """
            UPDATE skill_sources
            SET status = 'ready', error = '', resolved_sha = $1, plugin_name = $2, skills = $3,
                synced_at = NOW(), updated_at = NOW()
            WHERE id = $4 RETURNING *
            """,
            resolved_sha,
            plugin_name,
            skills,
            source_id,
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def mark_error(self, source_id: str, error: str) -> dict[str, Any]:
        row = await self._db.fetchrow(
            "UPDATE skill_sources SET status = 'error', error = $1, updated_at = NOW() WHERE id = $2 RETURNING *",
            error[:2000],
            source_id,
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def delete(self, source_id: str) -> None:
        row = await self.get(source_id)
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        if row["is_builtin"]:
            raise ValueError(f"cannot delete built-in skill source {source_id!r}")
        await self._db.execute("DELETE FROM skill_sources WHERE id = $1", source_id)
        logger.info("skill_source_deleted", source_id=source_id)

    async def catalog(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for src in await self.list_all():
            prefix = src["plugin_name"] or src["id"]
            available = src["status"] == "ready" and bool(src["enabled"])
            for skill in src["skills"] or []:
                out.append(
                    {
                        "name": f"{prefix}:{skill['name']}",
                        "source_id": src["id"],
                        "description": skill.get("description", ""),
                        "available": available,
                        "status": src["status"] if src["enabled"] else "disabled",
                    }
                )
        return sorted(out, key=lambda c: c["name"])


class InlineSkillsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM inline_skills ORDER BY name")
        return [dict(r) for r in rows]

    async def get(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM inline_skills WHERE name = $1", name)
        return None if row is None else dict(row)

    async def upsert(self, *, name: str, description: str, body: str) -> dict[str, Any]:
        if not INLINE_NAME_RE.fullmatch(name):
            raise SkillSourceRefused("name must match ^[a-z][a-z0-9-]{0,63}$")
        if not description.strip():
            raise SkillSourceRefused("description is required")
        if len(body.encode("utf-8")) > INLINE_BODY_MAX:
            raise SkillSourceRefused("body must be at most 64 KiB")
        row = await self._db.fetchrow(
            """
            INSERT INTO inline_skills (name, description, body) VALUES ($1, $2, $3)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, body = EXCLUDED.body, updated_at = NOW()
            RETURNING *
            """,
            name,
            description,
            body,
        )
        assert row is not None
        return dict(row)

    async def delete(self, name: str) -> None:
        status = await self._db.execute("DELETE FROM inline_skills WHERE name = $1", name)
        if status.endswith(" 0"):
            raise ValueError(f"inline skill {name!r} not found")
