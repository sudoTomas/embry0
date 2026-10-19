"""Migration 102 — credentials.provider takes the OpenAI-compatible LLM providers."""

import asyncpg
import pytest

from embry0.agents.providers import OPENAI_COMPATIBLE
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.credentials import PROVIDERS


def test_migration_102_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(102) == 1, "migration 102 (OpenAI-compatible credential providers) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_102_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_101.py)."""
    assert MIGRATIONS[-1][0] == 102, "migration 102 must widen credentials_provider_check"


def test_the_check_names_every_provider_the_repository_accepts() -> None:
    sql = next(sql for v, _, sql in MIGRATIONS if v == 102)
    missing = [p for p in PROVIDERS if f"'{p}'" not in sql]
    assert missing == [], f"the repository accepts providers the table would refuse: {missing}"
    family = [p.name for p in OPENAI_COMPATIBLE if p.name not in PROVIDERS]
    assert family == [], f"OpenAI-compatible providers the repository refuses: {family}"


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
async def test_an_unknown_provider_is_still_refused(db_with_migrations: DatabasePool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db_with_migrations, "mistral")
