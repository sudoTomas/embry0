"""No job starts on a removed organization's repository (#721): refused where every job is created."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository, OrganizationRemoved
from embry0.storage.repositories.organizations import OrganizationsRepository

pytestmark = pytest.mark.requires_postgres

REMOVED = "the organization acme was removed: no job starts on its repositories until it is made active again"


async def _organization(db: DatabasePool, owner: str, status: str) -> None:
    organizations = OrganizationsRepository(db)
    await organizations.register(owner, "gitlab" if owner == "lab" else "github", created_by="tests")
    await organizations.set_status(owner, status)


@pytest.mark.parametrize(
    "asked",
    [
        {"repo": "acme/widgets"},
        {"repo": "Acme/Widgets"},
        {"context": {"type": "git", "repo": "acme/widgets", "branch": "main"}},
    ],
    ids=["repo", "any-case", "context-only"],
)
async def test_a_removed_organization_s_repository_starts_no_job(
    db_with_migrations: DatabasePool, asked: dict[str, object]
) -> None:
    await _organization(db_with_migrations, "acme", "removed")
    jobs = JobsRepository(db_with_migrations)
    with pytest.raises(OrganizationRemoved) as refused:
        await jobs.create(task="fix it", **asked)  # type: ignore[arg-type]
    assert str(refused.value) == REMOVED and refused.value.owner == "acme"
    assert await db_with_migrations.fetchval("SELECT count(*) FROM jobs") == 0, "nothing was written"


async def test_a_paused_or_active_organization_and_a_subgroup_path_start_jobs(db_with_migrations: DatabasePool) -> None:
    """Paused still works; a GitLab path's organization is its first segment; a job with no repository starts."""
    await _organization(db_with_migrations, "acme", "paused")
    await _organization(db_with_migrations, "lab", "removed")
    jobs = JobsRepository(db_with_migrations)
    assert await jobs.create(repo="acme/widgets", task="fix it")
    assert await jobs.create(repo="globex/gizmos", task="fix it"), "an owner nobody registered is no removed one"
    assert await jobs.create(task="no repository")
    with pytest.raises(OrganizationRemoved):
        await jobs.create(repo="lab/sub/web", task="fix it")


async def test_making_the_organization_active_again_lets_its_repositories_start_jobs(
    db_with_migrations: DatabasePool,
) -> None:
    await _organization(db_with_migrations, "acme", "removed")
    jobs = JobsRepository(db_with_migrations)
    with pytest.raises(OrganizationRemoved):
        await jobs.create(repo="acme/widgets", task="fix it")
    await OrganizationsRepository(db_with_migrations).set_status("acme", "active")
    assert await jobs.create(repo="acme/widgets", task="fix it")


async def test_asked_first_the_store_answers_as_its_insert_would(db_with_migrations: DatabasePool) -> None:
    """``organization_removed`` is the insert's own rule, for a caller that stops before its work (the watcher's
    tick): the owner of a removed organization's repository, in any case and by a subgroup path; None for a paused
    one, an owner nobody registered or no repository; None again once the organization is active."""
    await _organization(db_with_migrations, "acme", "removed")
    await _organization(db_with_migrations, "lab", "removed")
    await _organization(db_with_migrations, "initech", "paused")
    jobs = JobsRepository(db_with_migrations)
    assert await jobs.organization_removed("acme/widgets") == "acme"
    assert await jobs.organization_removed("Acme/Widgets") == "acme"
    assert await jobs.organization_removed("lab/sub/web") == "lab"
    for repo in ("initech/app", "globex/gizmos", None, ""):
        assert await jobs.organization_removed(repo) is None, repo
    await OrganizationsRepository(db_with_migrations).set_status("acme", "active")
    assert await jobs.organization_removed("acme/widgets") is None
