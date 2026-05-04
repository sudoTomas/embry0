"""API-level tests for /sandbox-profiles QA-foundation field round-tripping.

These tests need real DB round-trip behavior (the round-trip assertions verify
that new fields written via POST are returned via GET, and that the repository's
ValueError on builtin deletes maps to HTTP 403). They use the shared
`api_client` fixture from tests/unit/api/conftest.py.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_with_qa_fields_round_trips(api_client: AsyncClient):
    payload = {
        "name": "qa-test-create",
        "base_image": "athanor-sandbox-qa:latest",
        "description": "QA test profile",
        "dind_enabled": True,
        "idle_timeout_seconds": 900,
        "extra_networks": ["backend"],
        "env_defaults": {"LANG": "C.UTF-8"},
    }
    r = await api_client.post("/api/v1/sandbox-profiles", json=payload)
    assert r.status_code == 201

    r = await api_client.get("/api/v1/sandbox-profiles/qa-test-create")
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "QA test profile"
    assert body["dind_enabled"] is True
    assert body["idle_timeout_seconds"] == 900
    assert body["extra_networks"] == ["backend"]
    assert body["env_defaults"] == {"LANG": "C.UTF-8"}
    assert body["is_builtin"] is False  # server-controlled, defaults false


@pytest.mark.asyncio
async def test_request_rejects_is_builtin_input(api_client: AsyncClient):
    r = await api_client.post(
        "/api/v1/sandbox-profiles",
        json={
            "name": "hax",
            "is_builtin": True,
        },
    )
    assert r.status_code == 422  # Pydantic extra=forbid


@pytest.mark.asyncio
async def test_delete_user_profile_returns_200(api_client: AsyncClient):
    """Deleting a non-builtin profile succeeds."""
    payload = {"name": "delete-me-test", "base_image": "athanor-sandbox:latest"}
    r = await api_client.post("/api/v1/sandbox-profiles", json=payload)
    assert r.status_code == 201
    r = await api_client.delete("/api/v1/sandbox-profiles/delete-me-test")
    assert r.status_code == 200
    r = await api_client.get("/api/v1/sandbox-profiles/delete-me-test")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_rejects_builtin_via_api(api_client: AsyncClient, builtin_profile_seeded):
    """Trying to delete a builtin profile must return 403."""
    r = await api_client.delete("/api/v1/sandbox-profiles/slim")
    assert r.status_code == 403
    assert "builtin" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reset_unknown_profile_returns_404(api_client: AsyncClient):
    r = await api_client.post("/api/v1/sandbox-profiles/does-not-exist/reset")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reset_user_profile_returns_404(api_client: AsyncClient):
    """Reset is for builtin profiles only — user-created profiles cannot be 'reset'."""
    payload = {"name": "user-profile-x", "base_image": "athanor-sandbox:latest"}
    create_r = await api_client.post("/api/v1/sandbox-profiles", json=payload)
    assert create_r.status_code == 201
    r = await api_client.post("/api/v1/sandbox-profiles/user-profile-x/reset")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reset_builtin_restores_seed(api_client: AsyncClient, builtin_profile_seeded):
    """Mutate the builtin row through legitimate channels (the repo's seed-style upsert)
    then call /reset and verify the canonical config is restored."""
    # Reach the repo: import inside the test so we use the same DB.
    import os

    from athanor.storage.database import DatabasePool
    from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository

    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_unit_api_test",
    )

    # Mutate qa-jvm to flip dind_enabled OFF (simulating bit-rot or a manual edit).
    pool = DatabasePool(database_url)
    await pool.connect()
    try:
        repo = SandboxProfilesRepository(pool)
        await repo.upsert(name="qa-jvm", dind_enabled=False, is_builtin=True, _allow_builtin_overwrite=True)
        mutated = await repo.get("qa-jvm")
        assert mutated["dind_enabled"] is False
    finally:
        await pool.close()

    # Reset
    r = await api_client.post("/api/v1/sandbox-profiles/qa-jvm/reset")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "qa-jvm"
    assert body["dind_enabled"] is True
    assert body["base_image"] == "athanor-sandbox-qa:latest"
