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
    # Phase 1.5: extra_networks is intentionally empty. The sandbox no longer
    # attaches to `backend` (which doesn't exist inside DinD anyway). Instead
    # SandboxManager injects --add-host=dind:<ip> for the docker daemon, and
    # the Phase 1.5 minio-proxy / presign-proxy are reached by Docker DNS on
    # sandbox-restricted.
    assert qa["extra_networks"] == []


def test_builtin_seeds_define_dev_python():
    assert "dev-python" in BUILTIN_SANDBOX_PROFILES
    dev = BUILTIN_SANDBOX_PROFILES["dev-python"]
    assert dev["base_image"] == "athanor-sandbox-dev-python:latest"
    assert dev["dind_enabled"] is False
    # Poetry/pip need PyPI egress; the Claude CLI needs api.anthropic.com —
    # same rationale as the slim profile's sandbox-internet attachment.
    assert dev["extra_networks"] == ["sandbox-internet"]


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
