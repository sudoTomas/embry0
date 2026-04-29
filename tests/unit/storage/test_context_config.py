import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.context_config import ContextConfigRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def ctx_repo(db_with_migrations: DatabasePool) -> ContextConfigRepository:
    return ContextConfigRepository(db_with_migrations)


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
