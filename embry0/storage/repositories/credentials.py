"""Credential registry — accounts embry0 acts as, and which repo uses which.

Decision: docs/adr/0006-credential-registry.md.
Storage-only: ciphertext in, ciphertext out. Encryption is the caller's job
(services.credentials.resolver / api.v1.credentials), exactly like
repositories.environment. Validation of the binding rules lives HERE so the
API is not the only guard.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

KINDS = ("forge_token", "llm_oauth", "llm_api_key", "ssh_key", "tailnet_api_key")
PROVIDERS = (
    "github",
    "gitlab",
    "anthropic",
    "xai",
    "ssh",
    "tailscale",
    # The OpenAI-compatible LLM providers (embry0/agents/providers.py OPENAI_COMPATIBLE).
    "openai",
    "openrouter",
    "google",
    "groq",
    "deepseek",
    "custom",
)
FORGE_PROVIDERS = ("github", "gitlab")
ROLES = ("forge", "llm", "deploy", "tailnet")
SCOPE_KINDS = ("repo", "owner", "host", "global")
KIND_FOR_ROLE: dict[str, set[str]] = {
    "forge": {"forge_token"},
    "llm": {"llm_oauth", "llm_api_key"},
    "deploy": {"ssh_key"},
    # ADR 0013: the key embry0 reads a tailnet's devices with, to find and probe hosts.
    "tailnet": {"tailnet_api_key"},
}
SCOPES_FOR_ROLE: dict[str, set[str]] = {
    "forge": {"repo", "owner", "host", "global"},
    "llm": {"repo", "owner", "global"},
    "deploy": {"host"},
    "tailnet": {"global"},
}


class _Keep:
    """Type of ``KEEP``: an update argument that leaves the stored value alone."""

    def __repr__(self) -> str:
        return "KEEP"


KEEP = _Keep()

_PUBLIC_COLUMNS = (
    "id, kind, provider, host, label, git_author_name, git_author_email, public_material, "
    "masked_preview, verified_login, status, status_reason, quarantined_until, created_by, "
    "created_at, updated_at, rotated_at, last_used_at, metadata"
)
_ALL_COLUMNS = _PUBLIC_COLUMNS + ", secret_ciphertext"

# A job's own id ($1) or a sandbox id derived from it ($2 = LIKE pattern for ``<job_id>__%``).
_JOB_OR_DERIVED = "(job_id = $1 OR job_id LIKE $2 ESCAPE '\\')"


def _derived_job_pattern(job_id: str) -> str:
    """LIKE pattern for ``<job_id>__<suffix>``, with the job id's own wildcards taken literally."""
    escaped = job_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "\\_\\_%"


class CredentialBoundError(ValueError):
    """Delete refused: bindings still reference the credential."""


class BindingValidationError(ValueError):
    """A binding's role, scope, kind, provider or pinned flag is inconsistent."""


def new_credential_id() -> str:
    return "cred-" + secrets.token_hex(6)


def normalize_scope_value(scope_kind: str, scope_value: str) -> str:
    """The form ``CredentialResolver.resolve_forge`` looks each scope up in.

    repo: the lowercase path. owner: ``<provider>:`` kept, the owner lowercased with
    '-' and '.' as '_' (``CredentialResolver.owner_of``). host: lowercase. global: as given.
    """
    if scope_kind in ("repo", "host"):
        return scope_value.lower()
    if scope_kind == "owner":
        prefix, sep, owner = scope_value.rpartition(":")
        return prefix + sep + owner.lower().replace("-", "_").replace(".", "_")
    return scope_value


@dataclass(frozen=True)
class Held:
    """A credential as a bind or a change takes it: who created it, and what uses it."""

    created_by: str
    holders: list[tuple[str, str]]


class CredentialsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # -- credentials ---------------------------------------------------------

    async def create(
        self,
        *,
        kind: str,
        provider: str,
        host: str,
        label: str,
        secret_ciphertext: str,
        masked_preview: str,
        created_by: str,
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        public_material: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``metadata`` holds non-secret settings that go with the secret (a tailnet key's tailnet)."""
        if kind not in KINDS:
            raise ValueError(f"unknown credential kind {kind!r}")
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        cid = new_credential_id()
        row = await self._db.fetchrow(
            f"""
            INSERT INTO credentials (id, kind, provider, host, label, git_author_name, git_author_email,
                                     secret_ciphertext, public_material, masked_preview, created_by, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
            RETURNING {_PUBLIC_COLUMNS}
            """,
            cid,
            kind,
            provider,
            host,
            label,
            git_author_name,
            git_author_email,
            secret_ciphertext,
            public_material,
            masked_preview,
            created_by,
            dict(metadata or {}),
        )
        assert row is not None, "INSERT ... RETURNING must return a row"
        logger.info("credential_created", credential_id=cid, provider=provider, label=label, kind=kind)
        return dict(row)

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_ALL_COLUMNS} FROM credentials WHERE id = $1", credential_id)
        return dict(row) if row else None

    async def find_by_label(self, provider: str, label: str) -> dict[str, Any] | None:
        """Includes ``secret_ciphertext``: the importer compares secrets to detect a rotation."""
        row = await self._db.fetchrow(
            f"SELECT {_ALL_COLUMNS} FROM credentials WHERE provider = $1 AND label = $2", provider, label
        )
        return dict(row) if row else None

    async def all_ciphertexts(self) -> list[dict[str, Any]]:
        """Every secret in one read. ``known_secret_values`` must not ``get()`` per row (#439)."""
        rows = await self._db.fetch("SELECT id, secret_ciphertext FROM credentials")
        return [dict(r) for r in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"""
            SELECT {", ".join("c." + c.strip() for c in _PUBLIC_COLUMNS.split(","))},
                   (SELECT COUNT(*) FROM credential_bindings b WHERE b.credential_id = c.id) AS binding_count
              FROM credentials c
             ORDER BY c.provider, c.label
            """
        )
        return [dict(r) for r in rows]

    async def update_meta(
        self,
        credential_id: str,
        *,
        label: str | None = None,
        git_author_name: str | None | _Keep = KEEP,
        git_author_email: str | None | _Keep = KEEP,
        conn: Any = None,
    ) -> dict[str, Any] | None:
        """Change a credential's label and git author identity.

        ``label=None`` keeps the label. An author field left at ``KEEP``
        keeps its value, and ``None`` clears it back to "use branding" (#440).
        """
        set_name = not isinstance(git_author_name, _Keep)
        set_email = not isinstance(git_author_email, _Keep)
        row = await (conn or self._db).fetchrow(
            f"""
            UPDATE credentials
               SET label = COALESCE($2, label),
                   git_author_name = CASE WHEN $5 THEN $3 ELSE git_author_name END,
                   git_author_email = CASE WHEN $6 THEN $4 ELSE git_author_email END,
                   updated_at = NOW()
             WHERE id = $1
            RETURNING {_PUBLIC_COLUMNS}
            """,
            credential_id,
            label,
            git_author_name if set_name else None,
            git_author_email if set_email else None,
            set_name,
            set_email,
        )
        return dict(row) if row else None

    async def rotate(
        self, credential_id: str, *, secret_ciphertext: str, masked_preview: str, conn: Any = None
    ) -> dict[str, Any] | None:
        row = await (conn or self._db).fetchrow(
            f"""
            UPDATE credentials
               SET secret_ciphertext = $2, masked_preview = $3, rotated_at = NOW(), updated_at = NOW(),
                   status = CASE WHEN status_reason = 'decryption_failed' THEN 'ok' ELSE status END,
                   status_reason = CASE WHEN status_reason = 'decryption_failed' THEN NULL ELSE status_reason END
             WHERE id = $1
            RETURNING {_PUBLIC_COLUMNS}
            """,
            credential_id,
            secret_ciphertext,
            masked_preview,
        )
        if row:
            logger.info("credential_rotated", credential_id=credential_id)
        return dict(row) if row else None

    async def set_status(
        self,
        credential_id: str,
        status: str,
        *,
        reason: str | None = None,
        quarantined_until: datetime | None = None,
        conn: Any = None,
    ) -> dict[str, Any] | None:
        if status not in ("ok", "disabled", "quarantined"):
            raise ValueError(f"unknown status {status!r}")
        row = await (conn or self._db).fetchrow(
            f"""
            UPDATE credentials
               SET status = $2, status_reason = $3, quarantined_until = $4, updated_at = NOW()
             WHERE id = $1
            RETURNING {_PUBLIC_COLUMNS}
            """,
            credential_id,
            status,
            reason if status != "ok" else None,
            quarantined_until if status == "quarantined" else None,
        )
        if row:
            logger.info("credential_status_changed", credential_id=credential_id, status=status, reason=reason)
        return dict(row) if row else None

    async def set_verified_login(self, credential_id: str, login: str, *, conn: Any = None) -> None:
        await (conn or self._db).execute(
            "UPDATE credentials SET verified_login = $2, updated_at = NOW() WHERE id = $1", credential_id, login
        )

    async def delete(self, credential_id: str, *, conn: Any = None) -> bool:
        db = conn or self._db
        bound = await db.fetchrow(
            "SELECT COUNT(*) AS n FROM credential_bindings WHERE credential_id = $1", credential_id
        )
        if bound and bound["n"]:
            raise CredentialBoundError(f"credential {credential_id} has {bound['n']} binding(s); unbind first")
        try:
            result = await db.execute("DELETE FROM credentials WHERE id = $1", credential_id)
        except asyncpg.ForeignKeyViolationError as exc:
            # A binding was created between the count and the delete.
            raise CredentialBoundError(f"credential {credential_id} is bound; unbind first") from exc
        deleted = result.endswith(" 1")
        if deleted:
            logger.info("credential_deleted", credential_id=credential_id)
        return deleted

    async def any_for_provider(self, provider: str) -> bool:
        row = await self._db.fetchrow(
            "SELECT 1 AS one FROM credentials WHERE provider = $1 AND status <> 'disabled' LIMIT 1", provider
        )
        return row is not None

    # -- bindings ------------------------------------------------------------

    async def bindings_for_scope(self, scope_kind: str, scope_value: str) -> list[dict[str, Any]]:
        scope_value = normalize_scope_value(scope_kind, scope_value)
        rows = await self._db.fetch(
            """
            SELECT b.id, b.scope_kind, b.scope_value, b.role, b.provider, b.credential_id, b.pinned, b.priority,
                   b.created_by, b.created_at, c.label, c.status
              FROM credential_bindings b JOIN credentials c ON c.id = b.credential_id
             WHERE b.scope_kind = $1 AND b.scope_value = $2
             ORDER BY b.role, b.provider, b.priority, b.id
            """,
            scope_kind,
            scope_value,
        )
        return [dict(r) for r in rows]

    async def holders(self, credential_id: str, *, conn: Any = None) -> list[tuple[str, str]]:
        """What uses the credential: each binding as ``(scope_kind, scope_value)``, each organization that names it
        as ``("owner", "<forge>:<owner>")`` and each skill source that names it as ``("skill_source", id)``."""
        return (await self.all_holders([credential_id], conn=conn)).get(credential_id, [])

    async def all_holders(
        self, credential_ids: Iterable[str] | None = None, *, conn: Any = None
    ) -> dict[str, list[tuple[str, str]]]:
        """:meth:`holders` of each of ``credential_ids``, or of every credential, by id; a credential nothing uses is
        left out."""
        ids = None if credential_ids is None else sorted(set(credential_ids))
        rows = await (conn or self._db).fetch(
            """
            SELECT credential_id AS id, scope_kind AS kind, scope_value AS value FROM credential_bindings
             WHERE $1::text[] IS NULL OR credential_id = ANY($1::text[])
            UNION ALL SELECT credential_id, 'owner', forge || ':' || owner FROM organizations
             WHERE credential_id IS NOT NULL AND ($1::text[] IS NULL OR credential_id = ANY($1::text[]))
            UNION ALL SELECT credential_id, 'skill_source', id FROM skill_sources
             WHERE credential_id IS NOT NULL AND ($1::text[] IS NULL OR credential_id = ANY($1::text[]))
            """,
            ids,
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            out.setdefault(str(r["id"]), []).append((str(r["kind"]), str(r["value"])))
        return out

    @asynccontextmanager
    async def held(self, credential_ids: Iterable[str]) -> AsyncIterator[tuple[Any, dict[str, Held]]]:
        """A transaction holding the credentials' rows, each with who created it and what used it when taken.

        ``FOR UPDATE`` conflicts with the key share a new binding or organization takes on the credential it names,
        so nothing comes to use it until the transaction ends: a check of what uses a credential and the write it
        allows, both on the connection yielded, cannot be split by a binding made between them. The rows are taken
        in id order, as every bind takes them (:meth:`replace_bindings`), so two never wait on each other.
        """
        ids = sorted(set(credential_ids))
        async with self._db.transaction() as conn:
            yield conn, await self._take(conn, ids, "FOR UPDATE")

    async def _take(self, conn: Any, ids: list[str], lock: str) -> dict[str, Held]:
        """Lock the credentials ``ids`` in id order (``lock``), and say who created each and what uses it."""
        rows = await conn.fetch(
            f"SELECT id, created_by FROM credentials WHERE id = ANY($1::text[]) ORDER BY id {lock}", ids
        )
        holders = await self.all_holders(ids, conn=conn)
        return {str(r["id"]): Held(str(r["created_by"] or ""), holders.get(str(r["id"]), [])) for r in rows}

    async def bindings_for_credential(self, credential_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT id, scope_kind, scope_value, role, provider, credential_id, pinned, priority, created_by, created_at"
            " FROM credential_bindings WHERE credential_id = $1 ORDER BY id",
            credential_id,
        )
        return [dict(r) for r in rows]

    async def replace_bindings(
        self,
        scope_kind: str,
        scope_value: str,
        bindings: list[dict[str, Any]],
        *,
        created_by: str,
        repo_forge: str | None = None,
        check: Callable[[dict[str, Held]], Awaitable[None]] | None = None,
    ) -> list[dict[str, Any]]:
        """Replace every binding of one scope. Validates everything first, writes nothing on error.

        The credentials bound are locked first, in id order, as :meth:`held` locks them: with a key share, which a
        new binding takes anyway, or with ``check`` given ``FOR UPDATE``, and ``check`` is awaited with who created
        each and what uses it before anything is written; it refuses by raising, and nothing is written. Every bind
        taking them in one order, a restricted and an unrestricted one never wait on each other; a deadlock all the
        same, from a path that does not, is retried once.
        """
        if scope_kind not in SCOPE_KINDS:
            raise BindingValidationError(f"unknown scope kind {scope_kind!r}")
        scope_value = normalize_scope_value(scope_kind, scope_value)
        if scope_kind == "global" and scope_value != "":
            raise BindingValidationError("global scope takes an empty scope_value")
        if scope_kind != "global" and not scope_value:
            raise BindingValidationError(f"scope '{scope_kind}' needs a scope_value")
        owner_forge = ""
        if scope_kind == "owner":
            # The resolver only ever looks up '<forge>:<owner>' (CredentialResolver.resolve_forge).
            owner_forge, sep, owner = scope_value.partition(":")
            # An owner never holds ':', so 'github:a:b' could never be looked up.
            if not sep or owner_forge not in FORGE_PROVIDERS or not owner or ":" in owner:
                raise BindingValidationError(
                    f"owner scope_value must be 'github:<owner>' or 'gitlab:<owner>', got {scope_value!r}"
                )
        prepared: list[tuple[str, str, bool, int, str]] = []
        seen: set[tuple[str, str]] = set()
        for b in bindings:
            role = b.get("role")
            cid = b.get("credential_id")
            pinned = b.get("pinned", False)
            if not isinstance(pinned, bool):
                raise BindingValidationError(f"pinned must be a boolean, got {pinned!r}")
            priority = int(b.get("priority") or 0)
            if role not in ROLES:
                raise BindingValidationError(f"unknown role {role!r}")
            if scope_kind not in SCOPES_FOR_ROLE[role]:
                raise BindingValidationError(f"scope '{scope_kind}' is not valid for role '{role}'")
            cred = await self.get(str(cid))
            if cred is None:
                raise BindingValidationError(f"credential {cid!r} does not exist")
            if cred["kind"] not in KIND_FOR_ROLE[role]:
                raise BindingValidationError(
                    f"role '{role}' needs a credential of kind {sorted(KIND_FOR_ROLE[role])}, got '{cred['kind']}'"
                )
            if pinned and not (role == "llm" and scope_kind in ("repo", "owner")):
                raise BindingValidationError("pinned is only valid on llm bindings at repo or owner scope")
            if role == "forge" and owner_forge and cred["provider"] != owner_forge:
                raise BindingValidationError(
                    f"owner scope is on forge '{owner_forge}' but credential '{cred['label']}' is for '{cred['provider']}'"
                )
            if role == "forge" and repo_forge and cred["provider"] != repo_forge:
                raise BindingValidationError(
                    f"repo is on forge '{repo_forge}' but credential '{cred['label']}' is for '{cred['provider']}'"
                )
            # Mirrors the credential_bindings_single partial index: one credential per
            # scope, role and provider, except the global llm pool.
            if not (scope_kind == "global" and role == "llm"):
                key = (role, str(cred["provider"]))
                if key in seen:
                    raise BindingValidationError(
                        f"only one '{role}' credential per provider '{key[1]}' at scope '{scope_kind}'"
                    )
                seen.add(key)
            prepared.append((str(cid), role, pinned, priority, str(cred["provider"])))
        ids = sorted({cid for cid, *_ in prepared})
        for attempt in (1, 2):
            try:
                await self._replace(scope_kind, scope_value, prepared, ids, created_by=created_by, check=check)
                break
            except asyncpg.DeadlockDetectedError:
                if attempt == 2:
                    raise
                logger.warning("credential_bindings_deadlock_retried", scope_kind=scope_kind, scope_value=scope_value)
        logger.info("credential_bindings_replaced", scope_kind=scope_kind, scope_value=scope_value, count=len(prepared))
        return await self.bindings_for_scope(scope_kind, scope_value)

    async def _replace(
        self,
        scope_kind: str,
        scope_value: str,
        prepared: list[tuple[str, str, bool, int, str]],
        ids: list[str],
        *,
        created_by: str,
        check: Callable[[dict[str, Held]], Awaitable[None]] | None,
    ) -> None:
        """One transaction of :meth:`replace_bindings`: the credentials taken in id order, checked, then the scope's
        bindings replaced."""
        async with self._db.transaction() as conn:
            taken = await self._take(conn, ids, "FOR UPDATE" if check is not None else "FOR KEY SHARE")
            if check is not None:
                await check(taken)
            await conn.execute(
                "DELETE FROM credential_bindings WHERE scope_kind = $1 AND scope_value = $2", scope_kind, scope_value
            )
            for cid, role, pinned, priority, provider in prepared:
                await conn.execute(
                    """
                    INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, pinned, priority, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    scope_kind,
                    scope_value,
                    role,
                    provider,
                    cid,
                    pinned,
                    priority,
                    created_by,
                )

    async def candidates(self, *, scope_kind: str, scope_value: str, role: str, provider: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"""
            SELECT b.pinned, b.priority, {", ".join("c." + c.strip() for c in _ALL_COLUMNS.split(","))}
              FROM credential_bindings b JOIN credentials c ON c.id = b.credential_id
             WHERE b.scope_kind = $1 AND b.scope_value = $2 AND b.role = $3 AND b.provider = $4
             ORDER BY b.priority ASC, c.last_used_at ASC NULLS FIRST, b.id ASC
            """,
            scope_kind,
            scope_value,
            role,
            provider,
        )
        return [dict(r) for r in rows]

    # -- uses ----------------------------------------------------------------

    async def record_use(
        self, *, credential_id: str, credential_label: str, job_id: str, role: str, provider: str
    ) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO credential_uses (credential_id, credential_label, job_id, role, provider)"
                " VALUES ($1, $2, $3, $4, $5)",
                credential_id,
                credential_label,
                job_id,
                role,
                provider,
            )
            await conn.execute("UPDATE credentials SET last_used_at = NOW() WHERE id = $1", credential_id)

    async def finish_uses(self, job_id: str, outcome: str) -> int:
        """Stamp the job's open uses, including its derived sandbox ids (``<job_id>__<suffix>``, QA/onboarding)."""
        result = await self._db.execute(
            f"UPDATE credential_uses SET outcome = $3 WHERE {_JOB_OR_DERIVED} AND outcome IS NULL",
            job_id,
            _derived_job_pattern(job_id),
            outcome,
        )
        return int(result.rsplit(" ", 1)[-1] or 0)

    async def uses_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """The job's uses, including its derived sandbox ids (``<job_id>__<suffix>``, QA/onboarding)."""
        rows = await self._db.fetch(
            "SELECT id, credential_id, credential_label, job_id, role, provider, resolved_at, outcome"
            f" FROM credential_uses WHERE {_JOB_OR_DERIVED} ORDER BY id",
            job_id,
            _derived_job_pattern(job_id),
        )
        return [dict(r) for r in rows]
