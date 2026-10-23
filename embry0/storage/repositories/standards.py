"""standards and repo_standard_state: an owner's versioned standards and each repository's place under them (ADR 0014, migration 102)."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from embry0.services.standards import (
    MODES,
    RepoStandardState,
    Standard,
    check_kind,
    normalize_owner,
    owner_of,
)
from embry0.storage.database import DatabasePool

# "references" is a reserved word in Postgres, so it is quoted wherever it is named.
_STANDARD_COLUMNS = 'id, owner, kind, version, status, schema, values, prose, "references", created_by, created_at'
_STATE_COLUMNS = (
    "repo, owner, kind, mode, last_audit_at, last_audit_job_id, score, findings_open, exceptions, updated_at"
)


def new_standard_id() -> str:
    return "std-" + secrets.token_hex(6)


def _standard(row: Any) -> Standard:
    return Standard(
        id=row["id"],
        owner=row["owner"],
        kind=row["kind"],
        version=row["version"],
        status=row["status"],
        schema=dict(row["schema"] or {}),
        values=dict(row["values"] or {}),
        prose=row["prose"],
        references=list(row["references"] or []),
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _state(row: Any) -> RepoStandardState:
    return RepoStandardState(
        repo=row["repo"],
        owner=row["owner"],
        kind=row["kind"],
        mode=row["mode"],
        last_audit_at=row["last_audit_at"],
        last_audit_job_id=row["last_audit_job_id"],
        score=row["score"],
        findings_open=row["findings_open"],
        exceptions=[dict(e) for e in (row["exceptions"] or [])],
        updated_at=row["updated_at"],
    )


class StandardsRepository:
    """The ``standards`` table: one row per version, at most one active per owner and kind."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get_active(self, owner: str, kind: str) -> Standard | None:
        row = await self._db.fetchrow(
            f"SELECT {_STANDARD_COLUMNS} FROM standards WHERE owner = $1 AND kind = $2 AND status = 'active'",
            normalize_owner(owner),
            check_kind(kind),
        )
        return _standard(row) if row else None

    async def list_active(self, owner: str) -> list[Standard]:
        rows = await self._db.fetch(
            f"SELECT {_STANDARD_COLUMNS} FROM standards WHERE owner = $1 AND status = 'active' ORDER BY kind",
            normalize_owner(owner),
        )
        return [_standard(r) for r in rows]

    async def list_versions(self, owner: str, kind: str) -> list[Standard]:
        """Every version of the owner's standard of ``kind``, newest first."""
        rows = await self._db.fetch(
            f"SELECT {_STANDARD_COLUMNS} FROM standards WHERE owner = $1 AND kind = $2 ORDER BY version DESC",
            normalize_owner(owner),
            check_kind(kind),
        )
        return [_standard(r) for r in rows]

    async def put(
        self,
        owner: str,
        kind: str,
        *,
        schema: dict[str, Any],
        values: dict[str, Any],
        prose: str = "",
        references: list[str] | None = None,
        created_by: str | None = None,
    ) -> tuple[Standard, int | None]:
        """Store a new active version and supersede the active one, in one transaction.

        Returns the new version and the number of the version it superseded
        (None for the first). Writers of one owner and kind queue on an
        advisory lock, so two at once get consecutive versions instead of a
        unique-key failure.
        """
        owner = normalize_owner(owner)
        check_kind(kind)
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"standards:{owner}:{kind}")
            previous = await conn.fetchval(
                "SELECT version FROM standards WHERE owner = $1 AND kind = $2 AND status = 'active'", owner, kind
            )
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM standards WHERE owner = $1 AND kind = $2", owner, kind
            )
            await conn.execute(
                "UPDATE standards SET status = 'superseded' WHERE owner = $1 AND kind = $2 AND status = 'active'",
                owner,
                kind,
            )
            row = await conn.fetchrow(
                f"""
                INSERT INTO standards (id, owner, kind, version, status, schema, values, prose, "references", created_by)
                VALUES ($1, $2, $3, $4, 'active', $5::jsonb, $6::jsonb, $7, $8::jsonb, $9)
                RETURNING {_STANDARD_COLUMNS}
                """,
                new_standard_id(),
                owner,
                kind,
                version,
                dict(schema),
                dict(values),
                prose,
                list(references or []),
                created_by,
            )
        return _standard(row), previous


class RepoStandardStateRepository:
    """The ``repo_standard_state`` table: one row per repository and kind, written once a person sets a mode.

    Repositories are stored lowercase, the form credential bindings and host
    links use. A repository with no row is in mode off.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, repo: str, kind: str) -> RepoStandardState | None:
        row = await self._db.fetchrow(
            f"SELECT {_STATE_COLUMNS} FROM repo_standard_state WHERE repo = lower($1) AND kind = $2",
            repo.strip(),
            check_kind(kind),
        )
        return _state(row) if row else None

    async def list_for_owner(self, owner: str, kind: str | None = None) -> list[RepoStandardState]:
        if kind is not None:
            check_kind(kind)
        rows = await self._db.fetch(
            f"""
            SELECT {_STATE_COLUMNS} FROM repo_standard_state
             WHERE owner = $1 AND ($2::text IS NULL OR kind = $2)
             ORDER BY repo, kind
            """,
            normalize_owner(owner),
            kind,
        )
        return [_state(r) for r in rows]

    async def list_for_repo(self, repo: str) -> list[RepoStandardState]:
        rows = await self._db.fetch(
            f"SELECT {_STATE_COLUMNS} FROM repo_standard_state WHERE repo = lower($1) ORDER BY kind", repo.strip()
        )
        return [_state(r) for r in rows]

    async def set_mode(
        self, repo: str, kind: str, mode: str, *, exceptions: list[dict[str, Any]] | None = None
    ) -> RepoStandardState:
        """Set the repository's mode, and its exceptions when given (None keeps them)."""
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        repo = repo.strip().lower()
        row = await self._db.fetchrow(
            f"""
            INSERT INTO repo_standard_state (repo, owner, kind, mode, exceptions)
            VALUES ($1, $2, $3, $4, COALESCE($5::jsonb, '[]'::jsonb))
            ON CONFLICT (repo, kind) DO UPDATE SET
                mode = EXCLUDED.mode,
                exceptions = COALESCE($5::jsonb, repo_standard_state.exceptions),
                updated_at = NOW()
            RETURNING {_STATE_COLUMNS}
            """,
            repo,
            owner_of(repo),
            check_kind(kind),
            mode,
            [dict(e) for e in exceptions] if exceptions is not None else None,
        )
        return _state(row)

    async def start_audit(
        self, repo: str, kind: str, job_id: str, *, expected_job_id: str | None
    ) -> RepoStandardState | None:
        """Name ``job_id`` the repository's newest audit, if the newest is still ``expected_job_id``.

        A compare-and-set: the caller passes the ``last_audit_job_id`` it read
        and checked. Under READ COMMITTED, Postgres re-checks the WHERE after
        it takes the row lock, so of callers that read the same newest audit
        exactly one wins. None when another audit started since the read, the
        repository is in mode off, or it has no row.
        """
        row = await self._db.fetchrow(
            f"""
            UPDATE repo_standard_state SET last_audit_job_id = $3, updated_at = NOW()
             WHERE repo = lower($1) AND kind = $2 AND mode <> 'off'
               AND last_audit_job_id IS NOT DISTINCT FROM $4::text
            RETURNING {_STATE_COLUMNS}
            """,
            repo.strip(),
            check_kind(kind),
            job_id,
            expected_job_id,
        )
        return _state(row) if row else None

    async def record_audit(
        self, job_id: str, *, score: int | None, findings_open: int, finished_at: datetime
    ) -> RepoStandardState | None:
        """Write a completed audit's result onto the repository whose newest audit it is.

        None for any other job, and for a repository an admin switched to mode
        off while the audit ran: a repository in mode off holds no new result.
        """
        row = await self._db.fetchrow(
            f"""
            UPDATE repo_standard_state
               SET score = $2, findings_open = $3, last_audit_at = $4, updated_at = NOW()
             WHERE last_audit_job_id = $1 AND mode <> 'off'
            RETURNING {_STATE_COLUMNS}
            """,
            job_id,
            score,
            max(0, findings_open),
            finished_at,
        )
        return _state(row) if row else None
