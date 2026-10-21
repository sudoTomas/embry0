"""DB-backed per-repo QA config — the qa.yaml v2 document in Postgres (RAV-1251).

Replaces the Phase-1 file store (``repo-configs/<owner>__<repo>/qa.yaml``) as
the source of truth: one row per repo holding the raw yaml text. Callers
validate the text with ``parse_qa_yaml_v2`` BEFORE upserting — this layer
stores what it is given. Upserts auto-register the repo in the ``repos``
registry so a configured repo always appears in listings. Every write that
changes the text appends a ``repo_config_revisions`` row in the same
transaction (ADR 0019), whichever path it came through.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import asyncpg
import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository, RevisionSource
from embry0.storage.repositories.repos import DEFAULT_FORGE

logger = structlog.get_logger(__name__)

_COLUMNS = "repo, yaml_text, updated_by, created_at, updated_at"

# The updated_by stamps the automatic writers use, and the path each one is.
# Anything else is a person, through the dashboard, the API or MCP.
_SOURCE_BY_WRITER: Final[dict[str, RevisionSource]] = {"bootstrap": "bootstrap", "onboard-pipeline": "onboard"}


class SetupChanged(Exception):
    """The stored test setup is not the one a compare-and-set expected."""


class RepoQAConfigsRepository:
    """CRUD for the ``repo_qa_configs`` table."""

    def __init__(self, db: DatabasePool, *, default_forge: str = DEFAULT_FORGE) -> None:
        self._db = db
        self._default_forge = default_forge
        self._revisions = RepoConfigRevisionsRepository(db)

    async def get(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repo_qa_configs WHERE repo = $1", repo)
        return dict(row) if row else None

    async def upsert(self, repo: str, yaml_text: str, updated_by: str = "") -> dict[str, Any]:
        """Store the config; ``upsert_recorded`` without the revision id."""
        return (await self.upsert_recorded(repo, yaml_text, updated_by))[0]

    async def upsert_recorded(
        self,
        repo: str,
        yaml_text: str,
        updated_by: str = "",
        *,
        source: RevisionSource | None = None,
        reason: str = "",
    ) -> tuple[dict[str, Any], int | None]:
        """Store the config, auto-registering the repo (the FK requires a row),
        and record the change. Returns the stored row and the new revision's
        id, or None when the text was unchanged and nothing was recorded.

        The forge is written EXPLICITLY, never left to the column's
        ``DEFAULT 'github'``: that default is a correct backfill for rows that
        predate #162, but as a live insert it is the same silent guess §3.8
        removed from ``repo_register`` — and a guessed row is worse than none,
        because ``forge_for`` then succeeds and ERR_REPO_UNREGISTERED never
        fires again for that repo. A deployment with no configured default
        refuses instead: register the repo with its forge first.

        ``source`` defaults to the path ``updated_by`` names: ``bootstrap``,
        ``onboard-pipeline`` (onboard), anything else a person.
        """
        async with self._db.transaction() as conn:
            return await self._upsert_on(conn, repo, yaml_text, updated_by, source=source, reason=reason)

    async def upsert_recorded_if_unchanged(
        self,
        conn: asyncpg.Connection,
        repo: str,
        yaml_text: str,
        *,
        base_updated_at: datetime | None,
        updated_by: str,
        source: RevisionSource,
        reason: str,
    ) -> tuple[dict[str, Any], int | None]:
        """``upsert_recorded`` as a compare-and-set, on the caller's connection
        inside its transaction. It writes only over the setup whose
        ``updated_at`` is ``base_updated_at`` (None: no setup is stored), and
        raises SetupChanged when another one is, so the caller's transaction
        rolls back with nothing written."""
        return await self._upsert_on(
            conn,
            repo,
            yaml_text,
            updated_by,
            source=source,
            reason=reason,
            check_base=True,
            base_updated_at=base_updated_at,
        )

    async def _upsert_on(
        self,
        conn: asyncpg.Connection,
        repo: str,
        yaml_text: str,
        updated_by: str,
        *,
        source: RevisionSource | None,
        reason: str,
        check_base: bool = False,
        base_updated_at: datetime | None = None,
    ) -> tuple[dict[str, Any], int | None]:
        if not self._default_forge:
            registered = await conn.fetchrow("SELECT 1 FROM repos WHERE repo = $1", repo)
            if not registered:
                raise ValueError(
                    f"repo {repo!r} is not registered and DEFAULT_FORGE is empty — "
                    "register it with its forge first (POST /repos with "
                    '{"repo": "<path>", "forge": "gitlab"} or the MCP repo_register tool), '
                    "then store its QA config."
                )
        else:
            await conn.execute(
                "INSERT INTO repos (repo, forge) VALUES ($1, $2) ON CONFLICT (repo) DO NOTHING",
                repo,
                self._default_forge,
            )
        stored = await conn.fetchrow(
            "SELECT yaml_text, updated_at FROM repo_qa_configs WHERE repo = $1 FOR UPDATE", repo
        )
        if check_base and (stored["updated_at"] if stored is not None else None) != base_updated_at:
            raise SetupChanged(f"the test setup of {repo} changed")
        # With no row there was nothing to lock: another writer's first save
        # can still be uncommitted. The insert then waits for it, and a
        # compare-and-set must not replace it ($4 false).
        row = await conn.fetchrow(
            f"""
            INSERT INTO repo_qa_configs (repo, yaml_text, updated_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (repo) DO UPDATE SET
                yaml_text = EXCLUDED.yaml_text,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            WHERE $4
            RETURNING {_COLUMNS}
            """,
            repo,
            yaml_text,
            updated_by,
            not (check_base and stored is None),
        )
        if row is None:
            raise SetupChanged(f"the test setup of {repo} was saved meanwhile")
        old = stored["yaml_text"] if stored is not None else None
        revision_id: int | None = None
        if old != yaml_text:
            revision_id = await self._revisions.append(
                conn,
                repo=repo,
                surface="qa_config",
                before=None if old is None else {"yaml_text": old},
                after={"yaml_text": yaml_text},
                actor=updated_by,
                source=source or _SOURCE_BY_WRITER.get(updated_by, "person"),
                reason=reason,
            )
        return dict(row), revision_id

    async def delete(self, repo: str) -> None:
        await self.delete_recorded(repo)

    async def delete_recorded(
        self, repo: str, *, actor: str = "", source: RevisionSource = "person", reason: str = ""
    ) -> int | None:
        """Remove the config and record it. Returns the revision id, or None
        when there was no config to remove."""
        async with self._db.transaction() as conn:
            old = await conn.fetchval("SELECT yaml_text FROM repo_qa_configs WHERE repo = $1 FOR UPDATE", repo)
            if old is None:
                return None
            await conn.execute("DELETE FROM repo_qa_configs WHERE repo = $1", repo)
            return await self._revisions.append(
                conn,
                repo=repo,
                surface="qa_config",
                before={"yaml_text": old},
                after=None,
                actor=actor,
                source=source,
                reason=reason,
            )

    async def list_repos(self) -> list[str]:
        rows = await self._db.fetch("SELECT repo FROM repo_qa_configs ORDER BY repo")
        return [r["repo"] for r in rows]
