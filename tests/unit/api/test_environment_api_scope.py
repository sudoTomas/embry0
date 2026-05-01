"""End-to-end HTTP tests covering the env-scope round-trip.

Tasks 10 + 11 wired the storage and schema layers; this test confirms the
scope field flows correctly through both the global and per-repo PUT
endpoints, including the QA_-prefix validator surfacing as HTTP 422.
"""


async def test_set_global_with_scope(api_client):
    payload = {
        "variables": [
            {"key": "PUBLIC_API_URL", "value": "https://x", "var_type": "config", "scope": "app"},
            {"key": "QA_TEST_USER", "value": "qa@example.com", "var_type": "config", "scope": "qa"},
        ]
    }
    r = await api_client.put("/api/v1/environment/global", json=payload)
    assert r.status_code == 200
    body = r.json()
    by_key = {v["key"]: v for v in body["variables"]}
    assert by_key["PUBLIC_API_URL"]["scope"] == "app"
    assert by_key["QA_TEST_USER"]["scope"] == "qa"


async def test_repo_qa_scope_requires_prefix(api_client):
    payload = {
        "variables": [
            {"key": "DB_URL", "value": "x", "var_type": "secret", "scope": "qa"},
        ]
    }
    r = await api_client.put("/api/v1/repos/owner/proj/environment", json=payload)
    assert r.status_code == 422
    assert "QA_" in r.text
