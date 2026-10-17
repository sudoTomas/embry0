import os

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.organizations import OrganizationsRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def orgs_repo() -> OrganizationsRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await db.execute("DELETE FROM organizations")
    yield OrganizationsRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_register_is_idempotent_on_owner_and_lowercases(orgs_repo: OrganizationsRepository) -> None:
    first = await orgs_repo.register("Acme", "github", display_name="Acme Inc", created_by="tests")
    second = await orgs_repo.register("acme", "github", settings={"tracker": "github"})
    assert first.owner == "acme"
    assert second.display_name == "Acme Inc"
    assert second.settings == {"tracker": "github"}
    assert len(await orgs_repo.list_all()) == 1


@pytest.mark.asyncio
async def test_access_repo_count_status_and_removed_filter(orgs_repo: OrganizationsRepository) -> None:
    await orgs_repo.register("acme", "github")
    updated = await orgs_repo.update_access("acme", {"read": True, "hooks": False}, repo_count=12)
    assert updated is not None and updated.access == {"read": True, "hooks": False} and updated.repo_count == 12
    marked = await orgs_repo.mark_discovered("acme", repo_count=13)
    assert marked is not None and marked.repo_count == 13 and marked.discovered_at is not None
    removed = await orgs_repo.set_status("acme", "removed")
    assert removed is not None and removed.status == "removed"
    assert await orgs_repo.list_all() == []
    assert len(await orgs_repo.list_all(include_removed=True)) == 1
    revived = await orgs_repo.register("acme", "github")
    assert revived.status == "active"


@pytest.mark.asyncio
async def test_register_refuses_a_repository_path_and_an_unknown_forge(orgs_repo: OrganizationsRepository) -> None:
    with pytest.raises(ValueError):
        await orgs_repo.register("acme/backend", "github")
    with pytest.raises(ValueError):
        await orgs_repo.register("acme", "bitbucket")
    with pytest.raises(ValueError):
        await orgs_repo.set_status("acme", "sleeping")
