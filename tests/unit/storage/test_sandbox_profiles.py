import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.sandbox_profiles import SandboxProfilesRepository


@pytest.fixture
async def profiles_repo(pg_pool: asyncpg.Pool) -> SandboxProfilesRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield SandboxProfilesRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_create_and_get_profile(profiles_repo: SandboxProfilesRepository):
    await profiles_repo.upsert(
        name="python-3.12",
        base_image="legion-sandbox-python:3.12",
        memory="8g",
        cpus="4",
    )
    profile = await profiles_repo.get("python-3.12")
    assert profile is not None
    assert profile["base_image"] == "legion-sandbox-python:3.12"
    assert profile["memory"] == "8g"


@pytest.mark.asyncio
async def test_list_profiles(profiles_repo: SandboxProfilesRepository):
    await profiles_repo.upsert(name="python-3.12", base_image="img1")
    await profiles_repo.upsert(name="java-17", base_image="img2")

    profiles = await profiles_repo.list()
    assert len(profiles) >= 2
    names = {p["name"] for p in profiles}
    assert "python-3.12" in names
    assert "java-17" in names


@pytest.mark.asyncio
async def test_delete_profile(profiles_repo: SandboxProfilesRepository):
    await profiles_repo.upsert(name="temp", base_image="img")
    await profiles_repo.delete("temp")
    assert await profiles_repo.get("temp") is None


@pytest.mark.asyncio
async def test_upsert_updates_existing(profiles_repo: SandboxProfilesRepository):
    await profiles_repo.upsert(name="test", base_image="old-image")
    await profiles_repo.upsert(name="test", base_image="new-image")
    profile = await profiles_repo.get("test")
    assert profile is not None
    assert profile["base_image"] == "new-image"


@pytest.mark.asyncio
async def test_get_nonexistent(profiles_repo: SandboxProfilesRepository):
    assert await profiles_repo.get("no-such-profile") is None
