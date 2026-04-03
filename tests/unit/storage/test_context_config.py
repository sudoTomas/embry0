import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.context_config import ContextConfigRepository


@pytest.fixture
async def ctx_repo(pg_pool: asyncpg.Pool) -> ContextConfigRepository:
    import os
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield ContextConfigRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_set_and_get_global_context(ctx_repo: ContextConfigRepository):
    await ctx_repo.set_global(system_context="Use TypeScript strict mode.", assistant_context="Focus on tests.")
    ctx = await ctx_repo.get_global()
    assert ctx is not None
    assert ctx["system_context"] == "Use TypeScript strict mode."
    assert ctx["assistant_context"] == "Focus on tests."


@pytest.mark.asyncio
async def test_set_and_get_repo_context(ctx_repo: ContextConfigRepository):
    await ctx_repo.set_repo(repo="owner/repo", system_context="Follow hexagonal architecture.")
    ctx = await ctx_repo.get_repo("owner/repo")
    assert ctx is not None
    assert ctx["system_context"] == "Follow hexagonal architecture."


@pytest.mark.asyncio
async def test_get_nonexistent_repo_context(ctx_repo: ContextConfigRepository):
    ctx = await ctx_repo.get_repo("no/such-repo")
    assert ctx is None


@pytest.mark.asyncio
async def test_merge_context(ctx_repo: ContextConfigRepository):
    await ctx_repo.set_global(system_context="Global rules.")
    await ctx_repo.set_repo(repo="a/b", system_context="Repo rules.")
    merged = await ctx_repo.get_merged("a/b")
    assert merged["system_context"] == "Repo rules."


@pytest.mark.asyncio
async def test_merge_context_fallback_to_global(ctx_repo: ContextConfigRepository):
    await ctx_repo.set_global(system_context="Global rules.", assistant_context="Global assistant.")
    merged = await ctx_repo.get_merged("no/repo")
    assert merged["system_context"] == "Global rules."
    assert merged["assistant_context"] == "Global assistant."
