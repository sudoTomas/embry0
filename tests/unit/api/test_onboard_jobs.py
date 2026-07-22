"""POST /api/v1/jobs pipeline='onboard' + /repos/{o}/{r}/qa-config (EMB-50)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_post_jobs_onboard_accepts_payload(api_client) -> None:
    """Schema accepts the onboard payload shape (no task, no branch needed)."""
    r = await api_client.post("/api/v1/jobs", json={"repo": "x/y", "pipeline": "onboard"})
    # 201/500/503 depending on executor wiring — the point is NOT 422.
    assert r.status_code != 422, f"Schema rejected the onboard payload: {r.text}"


@pytest.mark.asyncio
async def test_post_jobs_onboard_requires_repo(api_client) -> None:
    r = await api_client.post("/api/v1/jobs", json={"pipeline": "onboard"})
    assert r.status_code == 422
    assert "repo" in r.text.lower()


@pytest.mark.asyncio
async def test_post_jobs_onboard_accepts_skip_smoke(api_client) -> None:
    r = await api_client.post(
        "/api/v1/jobs",
        json={"repo": "x/y", "pipeline": "onboard", "skip_smoke": True, "branch": "dev"},
    )
    assert r.status_code != 422, r.text


@pytest.mark.asyncio
async def test_post_jobs_unknown_pipeline_still_rejected(api_client) -> None:
    r = await api_client.post("/api/v1/jobs", json={"repo": "x/y", "pipeline": "not-a-pipeline", "task": "t"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_qa_config_get_404_when_absent(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    r = await api_client.get("/api/v1/repos/acme/widgets/qa-config")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_qa_config_put_validates_and_round_trips(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    yaml_text = (
        "version: 2\n"
        "workspace_provider:\n"
        "  type: static-apps\n"
        "apps:\n"
        "  web:\n"
        '    boot_command: "npm run dev"\n'
        '    frontend_url: "http://localhost:5173"\n'
        "    ready_checks:\n"
        '      - http: "http://localhost:5173"\n'
    )
    r = await api_client.put(
        "/api/v1/repos/acme/widgets/qa-config",
        content=yaml_text,
        headers={"Content-Type": "application/yaml"},
    )
    assert r.status_code == 200, r.text
    r2 = await api_client.get("/api/v1/repos/acme/widgets/qa-config")
    assert r2.status_code == 200
    assert r2.text == yaml_text


@pytest.mark.asyncio
async def test_qa_config_put_rejects_invalid_schema(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    r = await api_client.put(
        "/api/v1/repos/acme/widgets/qa-config",
        content="version: 3\n",
        headers={"Content-Type": "application/yaml"},
    )
    assert r.status_code == 422
    assert not (tmp_path / "acme__widgets").exists()


@pytest.mark.asyncio
async def test_qa_config_delete_removes(api_client, tmp_path, monkeypatch) -> None:
    from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV

    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    d = tmp_path / "acme__widgets"
    d.mkdir()
    (d / "qa.yaml").write_text("version: 2\n")
    r = await api_client.delete("/api/v1/repos/acme/widgets/qa-config")
    assert r.status_code == 204
    assert not (d / "qa.yaml").exists()
