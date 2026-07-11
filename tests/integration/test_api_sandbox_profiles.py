"""Integration tests for Sandbox Profiles API — real PostgreSQL."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get_profile(app: AsyncClient):
    resp = await app.post(
        "/api/v1/sandbox-profiles",
        json={"name": "python-3.12", "base_image": "embry0-sandbox-python:3.12", "memory": "8g", "cpus": "4"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201

    resp = await app.get("/api/v1/sandbox-profiles/python-3.12")
    assert resp.status_code == 200
    assert resp.json()["base_image"] == "embry0-sandbox-python:3.12"


@pytest.mark.asyncio
async def test_list_profiles(app: AsyncClient):
    await app.post(
        "/api/v1/sandbox-profiles",
        json={"name": "java-17", "base_image": "embry0-sandbox-java:17"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    resp = await app.get("/api/v1/sandbox-profiles")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "java-17" in names


@pytest.mark.asyncio
async def test_delete_profile(app: AsyncClient):
    await app.post(
        "/api/v1/sandbox-profiles",
        json={"name": "temp-profile", "base_image": "img"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    resp = await app.delete("/api/v1/sandbox-profiles/temp-profile", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200

    resp = await app.get("/api/v1/sandbox-profiles/temp-profile")
    assert resp.status_code == 404
