"""POST /api/v1/repos/bootstrap endpoint tests (EMB-49)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_repo_shape(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "not-a-repo"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_app_slug(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "a/b", "app": "Bad App"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_rejects_unknown_field(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "a/b", "nope": 1})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_happy_path_writes_store(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))

    async def _fake_verify(repo, token):
        return "main", True

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake_verify)
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qa_required"] == "never"
    assert (tmp_path / "acme__fresh" / "qa.yaml").is_file()
    # And the qa-config GET now serves it.
    r2 = await api_client.get("/api/v1/repos/acme/fresh/qa-config")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_conflict_maps_to_400(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))

    async def _fake_verify(repo, token):
        return "main", True

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake_verify)
    assert (await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})).status_code == 200
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]
