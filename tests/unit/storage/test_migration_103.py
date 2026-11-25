"""Migration 103 — credentials.provider takes the OpenAI-compatible LLM providers.

Since migration 158 the table names no LLM provider (#173): what an LLM credential's provider may be is the
provider catalogue's, and tests/unit/storage/test_migration_158.py holds the table to it.
"""

import asyncpg
import pytest

from embry0.agents.providers import OPENAI_COMPATIBLE
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_103_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(103) == 1, "migration 103 (OpenAI-compatible credential providers) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))


def test_the_check_named_every_shipped_openai_compatible_provider() -> None:
    sql = next(sql for v, _, sql in MIGRATIONS if v == 103)
    missing = [p.name for p in OPENAI_COMPATIBLE if f"'{p.name}'" not in sql]
    assert missing == [], f"migration 103 left out OpenAI-compatible providers: {missing}"


async def _insert(db: DatabasePool, provider: str) -> None:
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by)"
        " VALUES ($1, 'llm_api_key', $2, 'example.test', 'k', 'enc', '****', 'test')",
        f"cred-{provider}",
        provider,
    )


@pytest.mark.requires_postgres
@pytest.mark.parametrize("provider", [p.name for p in OPENAI_COMPATIBLE])
async def test_an_openai_compatible_key_is_stored(db_with_migrations: DatabasePool, provider: str) -> None:
    await _insert(db_with_migrations, provider)
    row = await db_with_migrations.fetchrow("SELECT provider FROM credentials WHERE id = $1", f"cred-{provider}")
    assert row is not None and row["provider"] == provider


@pytest.mark.requires_postgres
async def test_an_llm_key_under_a_forge_s_name_is_still_refused(db_with_migrations: DatabasePool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db_with_migrations, "github")
