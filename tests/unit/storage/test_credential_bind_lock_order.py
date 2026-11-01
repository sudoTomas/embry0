"""A bind made for a key with an allowlist and one made without never wait on each other, on a real database.

A restricted bind takes the bound credentials ``FOR UPDATE`` in id order, then replaces the scope's bindings
(``CredentialsRepository.replace_bindings`` with a ``check``, and ``held``). An unrestricted bind takes them too, in
the same order, with a key share, before it deletes or inserts anything. The two replays below are the restricted
side run statement by statement on one connection against the repository's unrestricted bind on another: each must
finish with both committed, and without a deadlock retried.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from structlog.testing import capture_logs

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.credentials import CredentialsRepository

pytestmark = pytest.mark.requires_postgres

A, B = "cred-00000000000a", "cred-00000000000b"
_LOCK = "SELECT id FROM credentials WHERE id = ANY($1::text[]) ORDER BY id FOR UPDATE"
_INSERT = (
    "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by) "
    "VALUES ('repo', $1, $2, $3, $4, 'restricted')"
)


async def _credentials(db: DatabasePool) -> None:
    for cid, kind, provider in ((A, "forge_token", "github"), (B, "llm_api_key", "anthropic")):
        await db.execute(
            "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
            "VALUES ($1, $2, $3, 'h', $1, 'x', '****', 't')",
            cid,
            kind,
            provider,
        )
    await db.execute(_INSERT, "acme/web", "forge", "github", A)


async def _replay(
    db: DatabasePool, restricted: list[tuple[float, str, tuple[Any, ...]]], unrestricted: Any
) -> dict[str, str]:
    """Run ``restricted`` (delay, statement, arguments) in one transaction and ``unrestricted`` (a coroutine) at once."""
    outcome: dict[str, str] = {}
    conn = await asyncpg.connect(db._url)  # noqa: SLF001

    async def first() -> None:
        try:
            async with conn.transaction():
                for delay, sql, args in restricted:
                    await asyncio.sleep(delay)
                    await conn.execute(sql, *args)
            outcome["restricted"] = "committed"
        except asyncpg.DeadlockDetectedError:
            outcome["restricted"] = "deadlock"

    async def second() -> None:
        await asyncio.sleep(0.1)
        try:
            await unrestricted
            outcome["unrestricted"] = "committed"
        except asyncpg.DeadlockDetectedError:
            outcome["unrestricted"] = "deadlock"

    try:
        await asyncio.wait_for(asyncio.gather(first(), second()), timeout=20)
    finally:
        await conn.close()
    return outcome


async def test_a_restricted_and_an_unrestricted_bind_of_one_scope_and_credential_both_commit(
    db_with_migrations: DatabasePool,
) -> None:
    """The review's first replay: the restricted side holds the credential, then replaces the scope's bindings;
    the unrestricted one replaces the same scope with the same credential meanwhile."""
    await _credentials(db_with_migrations)
    repo = CredentialsRepository(db_with_migrations)
    restricted = [
        (0.0, _LOCK, ([A],)),
        (0.3, "DELETE FROM credential_bindings WHERE scope_kind = 'repo' AND scope_value = $1", ("acme/web",)),
        (0.0, _INSERT, ("acme/web", "forge", "github", A)),
    ]
    with capture_logs() as logs:
        outcome = await _replay(
            db_with_migrations,
            restricted,
            repo.replace_bindings(
                "repo", "acme/web", [{"credential_id": A, "role": "forge"}], created_by="unrestricted"
            ),
        )
    assert outcome == {"restricted": "committed", "unrestricted": "committed"}, outcome
    assert not [e for e in logs if e["event"] == "credential_bindings_deadlock_retried"], "no deadlock to retry"


async def test_a_restricted_and_an_unrestricted_bind_of_two_credentials_both_commit(
    db_with_migrations: DatabasePool,
) -> None:
    """The review's second replay: the restricted side takes A then B; the unrestricted request names B then A."""
    await _credentials(db_with_migrations)
    repo = CredentialsRepository(db_with_migrations)
    restricted = [(0.0, _LOCK, ([A],)), (0.3, _LOCK, ([B],))]
    with capture_logs() as logs:
        outcome = await _replay(
            db_with_migrations,
            restricted,
            repo.replace_bindings(
                "repo",
                "acme/api",
                [{"credential_id": B, "role": "llm"}, {"credential_id": A, "role": "forge"}],
                created_by="unrestricted",
            ),
        )
    assert outcome == {"restricted": "committed", "unrestricted": "committed"}, outcome
    assert not [e for e in logs if e["event"] == "credential_bindings_deadlock_retried"], "no deadlock to retry"


async def test_a_deadlock_from_another_path_is_retried_once(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _credentials(db_with_migrations)
    repo = CredentialsRepository(db_with_migrations)
    real = repo._replace  # noqa: SLF001
    calls: list[int] = []

    async def once_deadlocked(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise asyncpg.DeadlockDetectedError("deadlock detected")
        await real(*args, **kwargs)

    monkeypatch.setattr(repo, "_replace", once_deadlocked)
    rows = await repo.replace_bindings("repo", "acme/web", [{"credential_id": A, "role": "forge"}], created_by="t")
    assert len(calls) == 2 and [r["credential_id"] for r in rows] == [A]
