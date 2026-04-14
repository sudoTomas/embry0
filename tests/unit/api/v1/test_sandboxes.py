"""Tests for GET /api/v1/sandboxes — docker ps wrapper for ops visibility."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig


@pytest.fixture
def app():
    config = LegionConfig(_env_file=None, dev_mode=True)
    return create_app(config)


@pytest.mark.asyncio
async def test_list_sandboxes_empty_when_docker_absent(app):
    """When no docker client is on app.state, return an empty list (no 500)."""
    # Ensure no docker attribute on app.state
    if hasattr(app.state, "docker"):
        delattr(app.state, "docker")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/sandboxes")
    assert resp.status_code == 200
    assert resp.json() == {"containers": [], "count": 0}


@pytest.mark.asyncio
async def test_list_sandboxes_parses_ps_output(app):
    """docker ps output is split on `|` into name/status/running_for fields."""
    mock_docker = MagicMock()
    mock_docker._build_base_cmd = MagicMock(return_value=["docker"])
    mock_docker.run_cmd = AsyncMock(
        return_value=(
            "sandbox-abc|Up 2 hours|2 hours ago\n"
            "sandbox-def|Exited (0) 5 minutes ago|5 minutes ago\n"
        )
    )
    app.state.docker = mock_docker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/sandboxes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["containers"] == [
        {"name": "sandbox-abc", "status": "Up 2 hours", "running_for": "2 hours ago"},
        {
            "name": "sandbox-def",
            "status": "Exited (0) 5 minutes ago",
            "running_for": "5 minutes ago",
        },
    ]
    # Verify the ps command was built with the sandbox- filter and expected format.
    call_args = mock_docker.run_cmd.call_args
    cmd = call_args.args[0]
    assert "ps" in cmd
    assert "name=sandbox-" in cmd
    assert "{{.Names}}|{{.Status}}|{{.RunningFor}}" in cmd
    # include_stopped defaults to False → `-a` should NOT be in the command.
    assert "-a" not in cmd
