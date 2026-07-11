"""Integration tests for RepoPreferencesRepository against real postgres."""

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def prefs_repo(db_with_migrations: DatabasePool) -> RepoPreferencesRepository:
    # Delete any existing rows to isolate this test run.
    await db_with_migrations.execute("DELETE FROM repo_preferences")
    return RepoPreferencesRepository(db_with_migrations)


@pytest.mark.asyncio
async def test_upsert_inserts_new_row(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(
        repo="acme/widgets",
        sandbox_profile="java-17",
        language_hint="java",
        notes="Legacy monolith",
    )
    assert row["repo"] == "acme/widgets"
    assert row["sandbox_profile"] == "java-17"
    assert row["language_hint"] == "java"
    assert row["notes"] == "Legacy monolith"
    assert row["updated_at"] is not None


@pytest.mark.asyncio
async def test_upsert_updates_existing_row(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="acme/app", sandbox_profile="python-3.12")
    updated = await prefs_repo.upsert(
        repo="acme/app", sandbox_profile="python-3.13", language_hint="python", notes="Upgraded"
    )
    assert updated["sandbox_profile"] == "python-3.13"
    assert updated["language_hint"] == "python"
    assert updated["notes"] == "Upgraded"

    # Only one row should exist (PK uniqueness).
    rows = await prefs_repo.list_all()
    assert sum(1 for r in rows if r["repo"] == "acme/app") == 1


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(prefs_repo: RepoPreferencesRepository):
    assert await prefs_repo.get("ghost/town") is None


@pytest.mark.asyncio
async def test_delete_removes_row(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="temp/gone", sandbox_profile="python-3.12")
    await prefs_repo.delete("temp/gone")
    assert await prefs_repo.get("temp/gone") is None


@pytest.mark.asyncio
async def test_list_all_sorted_by_repo(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="b/two", sandbox_profile="python-3.12")
    await prefs_repo.upsert(repo="a/one", sandbox_profile="java-17")
    rows = await prefs_repo.list_all()
    repos = [r["repo"] for r in rows]
    assert repos == ["a/one", "b/two"]


@pytest.mark.asyncio
async def test_upsert_with_nullable_fields(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(repo="bare/repo")
    assert row["sandbox_profile"] is None
    assert row["language_hint"] is None
    assert row["notes"] == ""
