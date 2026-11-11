import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    return create_app(config)


@pytest.mark.asyncio
async def test_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_ready(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


# ---- watcher check (#270) ---------------------------------------------------


STACK = "acme/backend"


async def _ready(app, *, watcher_health: str, watcher_enabled: bool = True) -> dict:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    app.state.db = MagicMock(pool=object(), fetchval=AsyncMock(return_value=1))
    # The fixture's AUTH_DEV_MODE, kept: every caller then reads each stack's state.
    app.state.config = SimpleNamespace(watcher_enabled=watcher_enabled, auth_dev_mode=True)
    app.state.watcher_service = SimpleNamespace(health=watcher_health, _repo=STACK)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_ready_is_degraded_when_watcher_logql_is_invalid(app):
    data = await _ready(app, watcher_health="invalid_logql")
    assert data == {
        "status": "degraded",
        "checks": {"database": "ok", "watcher": "invalid_logql", "watcher_stacks": {STACK: "invalid_logql"}},
    }


@pytest.mark.asyncio
async def test_ready_reports_but_tolerates_an_unverified_watcher(app):
    """Loki being down is not an embry0 fault."""
    data = await _ready(app, watcher_health="unverified")
    assert data == {
        "status": "ok",
        "checks": {"database": "ok", "watcher": "unverified", "watcher_stacks": {STACK: "unverified"}},
    }


@pytest.mark.asyncio
async def test_ready_omits_the_watcher_when_the_loop_is_disabled(app):
    data = await _ready(app, watcher_health="invalid_logql", watcher_enabled=False)
    assert data == {"status": "ok", "checks": {"database": "ok"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("health", ["missing_logql", "loki_misconfigured", "tick_unrecordable"])
async def test_ready_is_degraded_for_every_state_that_stops_the_watcher(app, health: str):
    data = await _ready(app, watcher_health=health)
    assert data == {
        "status": "degraded",
        "checks": {"database": "ok", "watcher": health, "watcher_stacks": {STACK: health}},
    }
