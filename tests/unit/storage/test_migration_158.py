"""Migration 158 — a credential's LLM provider is the provider catalogue's, not a list in the table (#173).

The table kept every LLM provider's name in ``credentials_provider_check``, so a provider a release or a deployment
added to the catalogue took a migration before its key could be stored. The CHECK now holds what the catalogue does
not: the other kinds' four providers, and an LLM provider's name by the catalogue's rule. The API checks a new
credential's provider against the catalogue in force. The number is ``VERSION`` alone.
"""

from __future__ import annotations

import importlib

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 158

#: Every (kind, provider) the old CHECK allowed and the API wrote.
BEFORE = [
    ("forge_token", "github"),
    ("forge_token", "gitlab"),
    ("ssh_key", "ssh"),
    ("tailnet_api_key", "tailscale"),
    ("llm_oauth", "anthropic"),
    ("llm_oauth", "xai"),
    *[("llm_api_key", p) for p in ("anthropic", "xai", "openai", "openrouter", "google", "groq", "deepseek", "custom")],
]


def _known_provider(kind: str, provider: str) -> bool:
    known = getattr(importlib.import_module("embry0.storage.repositories.credentials"), "known_provider", None)
    if known is None:
        pytest.fail("the repository must say which providers a credential may be for, as the table does")
    return bool(known(kind, provider))


def test_the_provider_check_migration_is_listed_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_159.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _insert(db: DatabasePool, kind: str, provider: str) -> None:
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
        "VALUES ($1, $2, $3, '', $1, 'x', '****', 'test')",
        f"cred_{kind}_{provider}"[:60],
        kind,
        provider,
    )


async def _stored(db: DatabasePool, kind: str, provider: str) -> bool:
    try:
        await _insert(db, kind, provider)
    except asyncpg.CheckViolationError:
        return False
    return True


@pytest.mark.requires_postgres
@pytest.mark.changes_schema
async def test_every_row_the_old_check_allowed_keeps(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await db.execute("ALTER TABLE credentials DROP CONSTRAINT IF EXISTS credentials_provider_check")
    await db.execute(
        "ALTER TABLE credentials ADD CONSTRAINT credentials_provider_check CHECK (provider IN ('github', 'gitlab', "
        "'anthropic', 'xai', 'ssh', 'tailscale', 'openai', 'openrouter', 'google', 'groq', 'deepseek', 'custom'))"
    )
    for kind, provider in BEFORE:
        await _insert(db, kind, provider)
    await run_migrations(db)
    kept = sorted(
        (str(r["kind"]), str(r["provider"])) for r in await db.fetch("SELECT kind, provider FROM credentials")
    )
    assert kept == sorted(BEFORE), kept
    assert await _stored(db, "llm_api_key", "mistral"), "a provider the catalogue adds needs no migration"


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("kind", "provider", "stored"),
    [
        ("llm_api_key", "mistral", True),  # a provider the catalogue adds
        ("llm_api_key", "local_box-2", True),  # by the catalogue's rule for a name
        ("llm_oauth", "anthropic", True),
        ("llm_api_key", "github", False),  # another kind's provider is not an LLM provider
        ("llm_api_key", "tailscale", False),
        ("llm_api_key", "Mistral", False),  # not the catalogue's rule
        ("llm_api_key", "a" * 41, False),
        ("llm_api_key", "", False),
        ("forge_token", "mistral", False),  # the other kinds keep their four
        ("forge_token", "anthropic", False),
        ("ssh_key", "github", True),
        ("tailnet_api_key", "tailscale", True),
    ],
)
async def test_the_table_and_the_repository_take_the_same_providers(
    db_with_migrations: DatabasePool, kind: str, provider: str, stored: bool
) -> None:
    assert await _stored(db_with_migrations, kind, provider) is stored, (kind, provider)
    assert _known_provider(kind, provider) is stored, f"the repository disagrees with the table on {(kind, provider)}"
