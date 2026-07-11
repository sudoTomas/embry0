import pytest
from httpx import ASGITransport, AsyncClient

try:
    from embry0.api.app import _check_postgres_password, create_app
except ImportError:
    pytest.skip("psycopg not available", allow_module_level=True)


@pytest.mark.asyncio
async def test_default_postgres_password_rejected_in_production():
    """Orchestrator must refuse to start with the well-known default password."""
    from embry0.config import Embry0Config

    config = Embry0Config(
        _env_file=None,
        database_url="postgresql://embry0:embry0@postgres:5432/embry0",
        auth_dev_mode=False,
        webhook_dev_mode=False,
        api_key="test-key",
        github_webhook_secret="test-secret",
        proxy_admin_token="test-proxy-token",
    )
    with pytest.raises(RuntimeError, match="insecure default"):
        _check_postgres_password(config)


@pytest.mark.asyncio
async def test_default_postgres_password_allowed_in_dev_mode():
    """In auth_dev_mode the default password is accepted (local dev convenience)."""
    from embry0.config import Embry0Config

    config = Embry0Config(
        _env_file=None,
        database_url="postgresql://embry0:embry0@postgres:5432/embry0",
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        github_webhook_secret="",
        proxy_admin_token="test-proxy-token",
    )
    # Should not raise
    _check_postgres_password(config)


@pytest.mark.asyncio
async def test_app_creates_successfully():
    app = create_app()
    assert app is not None
    assert app.title == "embry0 API"


@pytest.mark.asyncio
async def test_health_endpoint():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
