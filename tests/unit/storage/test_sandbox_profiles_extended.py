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
