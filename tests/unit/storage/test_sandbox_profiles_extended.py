import pytest

from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_upsert_persists_new_qa_columns(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await repo.upsert(
        name="qa-jvm-test",
        base_image="athanor-sandbox-qa:latest",
        description="JVM + Node + DinD QA runtime",
        dind_enabled=True,
        idle_timeout_seconds=900,
        extra_networks=["backend"],
        env_defaults={"LANG": "C.UTF-8"},
        is_builtin=True,
        # Keep test idempotent: if a previous run left a builtin row behind,
        # the new repository guard would block re-upsert without this flag.
        _allow_builtin_overwrite=True,
    )
    row = await repo.get("qa-jvm-test")
    assert row is not None
    assert row["description"] == "JVM + Node + DinD QA runtime"
    assert row["dind_enabled"] is True
    assert row["idle_timeout_seconds"] == 900
    assert row["extra_networks"] == ["backend"]
    assert row["env_defaults"] == {"LANG": "C.UTF-8"}
    assert row["is_builtin"] is True


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_upsert_defaults_preserve_old_callers(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await repo.upsert(name="legacy-test")
    row = await repo.get("legacy-test")
    assert row["description"] == ""
    assert row["dind_enabled"] is False
    assert row["idle_timeout_seconds"] == 600
    assert row["extra_networks"] == []
    assert row["env_defaults"] == {}
    assert row["is_builtin"] is False


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_delete_rejects_builtin(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    # Idempotent across runs: a prior run may have left this row in place.
    await repo.upsert(name="builtin-test", is_builtin=True, _allow_builtin_overwrite=True)
    with pytest.raises(ValueError, match="builtin"):
        await repo.delete("builtin-test")
    # row still present
    assert await repo.get("builtin-test") is not None


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_delete_allows_user_profile(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await repo.upsert(name="user-test", is_builtin=False)
    await repo.delete("user-test")
    assert await repo.get("user-test") is None


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_upsert_rejects_overwriting_builtin(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await repo.upsert(name="seed-test", is_builtin=True, _allow_builtin_overwrite=True)
    # Re-upserting without the override flag must raise
    with pytest.raises(ValueError, match="builtin"):
        await repo.upsert(name="seed-test", base_image="evil:latest")
    # Original row unchanged
    row = await repo.get("seed-test")
    assert row["base_image"] == "athanor-sandbox:latest"  # default; not "evil:latest"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_upsert_allows_builtin_overwrite_with_flag(db_with_migrations):
    repo = SandboxProfilesRepository(db_with_migrations)
    await repo.upsert(name="seed-test-2", is_builtin=True, _allow_builtin_overwrite=True)
    # Re-upserting WITH the override flag must succeed
    await repo.upsert(
        name="seed-test-2",
        base_image="updated:latest",
        is_builtin=True,
        _allow_builtin_overwrite=True,
    )
    row = await repo.get("seed-test-2")
    assert row["base_image"] == "updated:latest"
