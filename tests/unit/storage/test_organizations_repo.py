import os

import pytest

from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def orgs_repo() -> OrganizationsRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await db.execute("DELETE FROM org_repo_facts")
    await db.execute("DELETE FROM org_discovery_runs")
    await db.execute("DELETE FROM organizations")
    yield OrganizationsRepository(db)
    await db.close()


@pytest.fixture
async def facts_repo() -> OrgReposRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await db.execute("DELETE FROM org_repo_facts")
    await db.execute("DELETE FROM org_discovery_runs")
    yield OrgReposRepository(db)
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


@pytest.mark.asyncio
async def test_facts_replace_per_repo_and_runs_keep_history(facts_repo: OrgReposRepository) -> None:
    run1 = await facts_repo.start_run("Acme")
    assert run1["status"] == "running" and run1["owner"] == "acme"
    assert (await facts_repo.running_run("acme")) is not None
    await facts_repo.progress(run1["id"], seen=1, total=2)
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/web", facts={"frontend": True}), run_id=run1["id"])
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/old", archived=True), run_id=run1["id"])
    done = await facts_repo.finish_run(run1["id"], summary={"repos": 2})
    assert done is not None and done["status"] == "done" and done["summary"] == {"repos": 2} and done["repos_seen"] == 1
    assert (await facts_repo.running_run("acme")) is None
    run2 = await facts_repo.start_run("acme")
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/web", facts={"frontend": False}), run_id=run2["id"])
    failed = await facts_repo.finish_run(run2["id"], error="RuntimeError: boom")
    assert failed is not None and failed["status"] == "error"
    rows = await facts_repo.list_for_owner("acme")
    assert {r["repo"]: r["facts"] for r in rows} == {"acme/web": {"frontend": False}, "acme/old": {}}
    assert [r["repo"] for r in await facts_repo.list_for_owner("acme", include_archived=False)] == ["acme/web"]
    runs = await facts_repo.list_runs("acme")
    assert [r["status"] for r in runs] == ["error", "done"]
