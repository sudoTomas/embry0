"""Smoke tests for /repos/{owner}/{repo}/preferences endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig


@pytest.fixture
def app():
    config = LegionConfig(_env_file=None, dev_mode=True)
    app = create_app(config)

    # Preferences repo — empty-by-default
    mock_prefs = MagicMock()
    mock_prefs.get = AsyncMock(return_value=None)

    async def _upsert(repo, sandbox_profile=None, language_hint=None, notes=""):
        return {
            "repo": repo,
            "sandbox_profile": sandbox_profile,
            "language_hint": language_hint,
            "notes": notes,
            "updated_at": datetime.now(UTC),
        }

    mock_prefs.upsert = AsyncMock(side_effect=_upsert)
    mock_prefs.delete = AsyncMock()
    app.state.repo_preferences_repo = mock_prefs

    # Sandbox profiles — only "python-3.12" exists.
    mock_profiles = MagicMock()

    async def _profile_get(name: str):
        if name == "python-3.12":
            return {"name": name, "base_image": "img"}
        return None

    mock_profiles.get = AsyncMock(side_effect=_profile_get)
    app.state.profiles_repo = mock_profiles

    return app


@pytest.mark.asyncio
async def test_get_preferences_returns_null_when_absent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/acme/widgets/preferences")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_put_preferences_accepts_known_sandbox_profile(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={
                "sandbox_profile": "python-3.12",
                "language_hint": "python",
                "notes": "Primary service",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "acme/widgets"
    assert body["sandbox_profile"] == "python-3.12"
    assert body["language_hint"] == "python"
    assert body["notes"] == "Primary service"


@pytest.mark.asyncio
async def test_put_preferences_rejects_unknown_sandbox_profile(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"sandbox_profile": "no-such-profile"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 400
    assert "no-such-profile" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_preferences_allows_null_sandbox_profile(app):
    """sandbox_profile is optional — omitting or setting None must not 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"language_hint": "rust"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sandbox_profile"] is None
    assert body["language_hint"] == "rust"


@pytest.mark.asyncio
async def test_delete_preferences_returns_204(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/repos/acme/widgets/preferences",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 204
