import pytest

from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from athanor.storage.seeds.sandbox_profiles_builtin import (
    BUILTIN_SANDBOX_PROFILES,
    seed_builtin_sandbox_profiles,
)


def test_builtin_seeds_define_slim_and_qa_jvm():
    assert "slim" in BUILTIN_SANDBOX_PROFILES
    assert "qa-jvm" in BUILTIN_SANDBOX_PROFILES
    qa = BUILTIN_SANDBOX_PROFILES["qa-jvm"]
    assert qa["base_image"] == "athanor-sandbox-qa:latest"
    assert qa["dind_enabled"] is True
    assert "backend" in qa["extra_networks"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_idempotent(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await seed_builtin_sandbox_profiles(repo)
    n_first = len(await repo.list())
    # Run again — must not duplicate or error
    await seed_builtin_sandbox_profiles(repo)
    n_second = len(await repo.list())
    assert n_second == n_first


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_marks_is_builtin(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await seed_builtin_sandbox_profiles(repo)
    slim = await repo.get("slim")
    assert slim is not None
    assert slim["is_builtin"] is True


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_overwrites_user_modifications(db_with_migrations):
    """Builtin seeds win on every startup. User cannot accidentally rebrand
    'qa-jvm' as 'no-dind' and have Athanor honor it for QA jobs."""
    repo = SandboxProfilesRepository(db_with_migrations)
    await seed_builtin_sandbox_profiles(repo)
    # Simulate someone bypassing the API and editing the row.
    # Note: the repo's _allow_builtin_overwrite guard (added in Task 6 followups)
    # blocks raw upserts on builtin rows — pass _allow_builtin_overwrite=True to
    # simulate the legitimate seed/admin path.
    await repo.upsert(
        name="qa-jvm",
        dind_enabled=False,
        is_builtin=True,
        _allow_builtin_overwrite=True,
    )
    qa = await repo.get("qa-jvm")
    assert qa["dind_enabled"] is False
    # Re-seed
    await seed_builtin_sandbox_profiles(repo)
    qa = await repo.get("qa-jvm")
    assert qa["dind_enabled"] is True  # restored
