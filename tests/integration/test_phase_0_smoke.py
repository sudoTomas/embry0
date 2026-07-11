"""Phase 0 end-to-end smoke test — exercises sandbox profiles, env scope,
reserved keys, builtin profile reset, and the /sandboxes/active endpoint.

This test runs against the integration test DB (provisioned by the integration
conftest), not the production DB. Requires Postgres reachable at TEST_DATABASE_URL.
"""

from __future__ import annotations

import pytest


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_phase_0_smoke_round_trip(app, builtin_profile_seeded, database_url):
    """End-to-end smoke test: builtin profiles seeded, env scope round-trips,
    builtin profiles can be reset, qa-jvm has dind_enabled."""

    # 1. Builtins are present in the API response
    r = await app.get("/api/v1/sandbox-profiles")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert "slim" in names, f"Expected 'slim' in {names}"
    assert "qa-jvm" in names, f"Expected 'qa-jvm' in {names}"

    # 2. qa-jvm has the expected QA characteristics
    r = await app.get("/api/v1/sandbox-profiles/qa-jvm")
    assert r.status_code == 200
    qa = r.json()
    assert qa["dind_enabled"] is True
    assert qa["is_builtin"] is True
    # Phase 1.5: qa-jvm reaches dind via sandbox-restricted gateway (NAT-routed),
    # not by attaching extra docker networks. SandboxManager injects --add-host
    # for minio-proxy / presign-proxy at create time. See seeds/sandbox_profiles_builtin.py.
    assert qa["extra_networks"] == []
    assert qa["base_image"] == "embry0-sandbox-qa:latest"

    # 3. Editing builtin via PUT is rejected with 403
    r = await app.put(
        "/api/v1/sandbox-profiles/qa-jvm",
        json={
            "name": "qa-jvm",
            "base_image": "evil:latest",
            "description": "",
            "dind_enabled": False,
            "idle_timeout_seconds": 1,
            "extra_networks": [],
            "env_defaults": {},
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

    # 4. env vars: scope round-trip via repo PUT
    repo_payload = {
        "variables": [
            {"key": "DB_URL", "value": "x", "var_type": "secret", "scope": "app"},
            {"key": "QA_TEST_USER", "value": "qa@x", "var_type": "config", "scope": "qa"},
        ]
    }
    r = await app.put(
        "/api/v1/repos/owner/proj/environment",
        json=repo_payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200, f"PUT failed: {r.status_code} {r.text}"

    r = await app.get("/api/v1/repos/owner/proj/environment")
    assert r.status_code == 200
    by_key = {v["key"]: v for v in r.json()["variables"]}
    assert by_key["DB_URL"]["scope"] == "app"
    assert by_key["QA_TEST_USER"]["scope"] == "qa"

    # 5. Reserved infrastructure keys are rejected at the API
    r = await app.put(
        "/api/v1/repos/owner/proj/environment",
        json={"variables": [{"key": "QA_JOB_ID", "value": "x", "scope": "qa"}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 422, f"Expected 422 for QA_JOB_ID, got {r.status_code}"

    r = await app.put(
        "/api/v1/repos/owner/proj/environment",
        json={"variables": [{"key": "DOCKER_HOST", "value": "tcp://evil:2376", "scope": "app"}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 422, f"Expected 422 for DOCKER_HOST, got {r.status_code}"

    # Reserved prefix
    r = await app.put(
        "/api/v1/repos/owner/proj/environment",
        json={"variables": [{"key": "DOCKER_BUILDKIT", "value": "1", "scope": "app"}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 422, f"Expected 422 for DOCKER_BUILDKIT prefix, got {r.status_code}"

    # 6. QA scope key without QA_ prefix is rejected
    r = await app.put(
        "/api/v1/repos/owner/proj/environment",
        json={"variables": [{"key": "DB_URL", "value": "x", "scope": "qa"}]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 422, f"Expected 422 for QA scope w/o QA_ prefix, got {r.status_code}"

    # 7. Active sandboxes endpoint responds (containers list may be empty in test env)
    r = await app.get("/api/v1/sandboxes/active")
    assert r.status_code == 200
    body = r.json()
    assert "containers" in body
    assert "count" in body
    assert isinstance(body["containers"], list)

    # 8. Reset endpoint restores builtin defaults.
    # First, simulate drift via a direct repo write (bypassing the API guards).
    from embry0.storage.database import DatabasePool
    from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository

    pool = DatabasePool(database_url)
    await pool.connect()
    try:
        repo = SandboxProfilesRepository(pool)
        await repo.upsert(
            name="qa-jvm",
            dind_enabled=False,
            is_builtin=True,
            _allow_builtin_overwrite=True,
        )
        # Confirm drift took effect via the API
        r2 = await app.get("/api/v1/sandbox-profiles/qa-jvm")
        assert r2.json()["dind_enabled"] is False
    finally:
        await pool.close()

    # Reset via the API
    r = await app.post(
        "/api/v1/sandbox-profiles/qa-jvm/reset",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dind_enabled"] is True
    assert body["base_image"] == "embry0-sandbox-qa:latest"
