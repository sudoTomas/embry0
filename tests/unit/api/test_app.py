import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.postgres import postgres_unavailable

try:
    from embry0.api.app import _check_postgres_password, create_app, lifespan
    from embry0.storage.database import DatabasePool
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


def _refusal_message(database_url: str) -> str:
    from embry0.config import Embry0Config

    config = Embry0Config(
        _env_file=None,
        database_url=database_url,
        auth_dev_mode=False,
        webhook_dev_mode=False,
        api_key="test-key",
        github_webhook_secret="test-secret",
        proxy_admin_token="test-proxy-token",
    )
    with pytest.raises(RuntimeError) as exc:
        _check_postgres_password(config)
    return str(exc.value)


def test_password_refusal_names_no_role_or_database():
    """#324: role and db are deployment-specific (POSTGRES_USER / POSTGRES_DB).
    The old text hardcoded `-U embry0` / `ALTER USER embry0`, which fails on a
    deployment whose role is anything else — and must not echo this one's."""
    message = _refusal_message("postgresql://athanor:embry0@postgres:5432/athanor")
    assert "athanor" not in message
    assert "-U embry0" not in message
    assert "ALTER USER" not in message
    assert "psql" not in message  # one procedure, kept in docs/ops/backup-restore.md — not a second copy here


def test_password_refusal_points_at_the_rotation_procedure_and_the_right_key():
    message = _refusal_message("postgresql://embry0:embry0@postgres:5432/embry0")
    assert "docs/ops/backup-restore.md" in message
    assert "Upgrading POSTGRES_PASSWORD on a live deploy" in message
    assert "POSTGRES_PASSWORD in infra/.env" in message
    # DATABASE_URL is not what the compose stack reads; naming it as the thing
    # to edit is what sent operators to the wrong file.
    assert "update DATABASE_URL" not in message
    assert "POSTGRES_USER" in message and "POSTGRES_DB" in message


def test_password_refusal_states_the_url_safe_constraint():
    message = _refusal_message("postgresql://embry0:embry0@postgres:5432/embry0")
    assert "URL-safe" in message
    assert "@ / : # % ?" in message and "$" in message
    assert "A-Z a-z 0-9 _ -" in message
    assert "secrets.token_urlsafe(32)" in message


def test_rotation_heading_the_message_cites_exists_in_the_runbook():
    """#529 moved the procedure out of CLAUDE.md; the message must name the file that holds it."""
    from pathlib import Path

    runbook = Path(__file__).resolve().parents[3] / "docs" / "ops" / "backup-restore.md"
    assert runbook.is_file(), f"the refusal cites {runbook}, which does not exist"
    assert "**Upgrading POSTGRES_PASSWORD on a live deploy.**" in runbook.read_text(encoding="utf-8")


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


# ---------------------------------------------------------------------------
# EMB-46: initial xai-proxy token push retries the listener-startup race.
# ---------------------------------------------------------------------------


async def test_push_initial_xai_token_retries_then_succeeds():
    from embry0.api.app import _push_initial_xai_token
    from embry0.execution.xai_credential import FreshToken

    class _Registry:
        def __init__(self):
            self.calls = 0

        async def refresh_all(self):
            self.calls += 1
            return {"cred-a": FreshToken("access-token-cred-a", "2030-01-01T00:00:00+00:00", 1)}

    class _ProxyMgr:
        def __init__(self):
            self.calls = []

        async def push_xai_token(self, credential_id, access_token=None, expires_at=None, *, seq=None):
            self.calls.append((credential_id, access_token, expires_at))
            if len(self.calls) < 3:
                raise RuntimeError("Connection refused")

    registry = _Registry()
    mgr = _ProxyMgr()
    await _push_initial_xai_token(registry, mgr, attempts=5, delay_seconds=0)
    assert len(mgr.calls) == 3
    assert mgr.calls[-1] == ("cred-a", "access-token-cred-a", "2030-01-01T00:00:00+00:00")
    assert registry.calls == 3


async def test_push_initial_xai_token_gives_up_after_attempts():
    from embry0.api.app import _push_initial_xai_token
    from embry0.execution.xai_credential import FreshToken

    class _Registry:
        def __init__(self):
            self.calls = 0

        async def refresh_all(self):
            self.calls += 1
            return {"cred-a": FreshToken("access-token-cred-a", "2030-01-01T00:00:00+00:00", 1)}

    class _ProxyMgr:
        def __init__(self):
            self.calls = 0

        async def push_xai_token(self, credential_id, access_token=None, expires_at=None, *, seq=None):
            self.calls += 1
            raise RuntimeError("still down")

    registry = _Registry()
    mgr = _ProxyMgr()
    await _push_initial_xai_token(registry, mgr, attempts=3, delay_seconds=0)
    assert mgr.calls == 3  # bounded — never raises
    assert registry.calls == 3


CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDFTCCAf2gAwIBAgIUKGxb659gemN1Fu46hjP/cM50qQcwDQYJKoZIhvcNAQEL
BQAwGTEXMBUGA1UEAwwOZW1icnkwLXRlc3QtY2EwIBcNMjYwOTE4MTM1NTMxWhgP
MjEyNjA4MjUxMzU1MzFaMBkxFzAVBgNVBAMMDmVtYnJ5MC10ZXN0LWNhMIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAps3bhaL+Zory+o1N1k2As7R5O2Ts
RAiM8Sy1gRgBWtZBoDSxsEiwj9y/mF3ktm1lIBCfC4DJC1s3l8y6Z2M6w8wU1fHJ
7FJcSyC8Xb5pW+DY16EmXs1N0Z/Sk8gaXKDxC/Eb2aWmbH82NO6lLkiXdlRLFM72
WJORS8RzLZWBSGJxegh+VzmisIFPe5pRyYw5aQ+79f4guvNhseKoN2/0vdsBEK1a
X/6zygGrh0jpeDaFmDj3mQI/N+ttg81rL5PRfwE+LCdSxgzVbmDSkyeslNDXJOOl
H6JbfgLbBb3eNOrCERngxcN0eO7GhHi5aSBASAHFs/EDnvX8cXnm3zvo/wIDAQAB
o1MwUTAdBgNVHQ4EFgQUK214K4DPd64gDs8w4/oQNCSX4V4wHwYDVR0jBBgwFoAU
K214K4DPd64gDs8w4/oQNCSX4V4wDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0B
AQsFAAOCAQEAQ65bqCRpk1rsCUb55zrmLXKJwvAhfAoUvla6DDuyTX1IYLsFpSwE
sxBPs8f8vXhg703ospwDAaRGPljF0yEuBdh0XMK2cMFDgu2aY9f3/edr5MFaj8h6
R2LrznGBf7knKJr4MPKVhlUvNXPI0g90j2ofSxQ56GHC5ozk+/6JUF3bsOgqLrle
qoRdavJt88e/4KcKCra5RIwipuNVmlCeTYxVwCCSXlccESJ39bjHD2vDDqv2ro/E
c4e0+qw4L6q4kPNiZiNmumxWekny53FXSVrhhHJS4MwcuLyfvwBD4LsvuNhKYpgm
mJ6PoFKMXR9xjyWUTmkVT6oYfPdl0vzcQw==
-----END CERTIFICATE-----
"""


def test_no_ca_bundle_is_the_normal_case():
    from embry0.api.app import _load_gitlab_ca_pem
    from embry0.config import Embry0Config

    assert _load_gitlab_ca_pem(Embry0Config(_env_file=None)) == ""


def test_ca_bundle_is_read_at_startup(tmp_path):
    from embry0.api.app import _load_gitlab_ca_pem
    from embry0.config import Embry0Config

    bundle = tmp_path / "ca.pem"
    bundle.write_text(CA_PEM)
    config = Embry0Config(_env_file=None, gitlab_ca_bundle=str(bundle))
    assert _load_gitlab_ca_pem(config) == CA_PEM


def test_unreadable_ca_bundle_refuses_the_boot(tmp_path):
    """Naming the path matters: the operator's next move is to fix the mount."""
    from embry0.api.app import _load_gitlab_ca_pem
    from embry0.config import Embry0Config
    from embry0.execution.tls import CABundleError

    missing = str(tmp_path / "absent.pem")
    config = Embry0Config(_env_file=None, gitlab_ca_bundle=missing)
    with pytest.raises(CABundleError, match="absent.pem"):
        _load_gitlab_ca_pem(config)


async def test_ca_pem_reaches_the_sandbox_and_proxy_managers(tmp_path, monkeypatch):
    """The PEM read at startup must arrive at BOTH fan-out call sites.

    `gitlab_ca_pem` is one `str` kwarg among several on each of these calls, so
    mypy cannot tell it apart from `gitlab_token` or `gitlab_url` — swapping or
    dropping it would type-check cleanly. This pins the value that actually
    arrives, not that a call happened.

    The lifespan is run for real from the CA load through `proxy_mgr.start`;
    only the Docker-side collaborators in between are stubbed, since they need
    a live DinD. The spy on `start` raises to stop the lifespan there, before
    the schedulers and background tasks that follow it.
    """
    import asyncpg

    from embry0.config import Embry0Config

    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://embry0:embry0@localhost:5432/embry0_unit_api_test",
    )
    try:
        conn = await asyncpg.connect(database_url)
        await conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        postgres_unavailable(exc)

    bundle = tmp_path / "ca.pem"
    bundle.write_text(CA_PEM)

    captured: dict[str, str] = {}

    class _StopLifespan(Exception):
        """Sentinel: both call sites are behind us, stop before the schedulers."""

    def _spy_sandbox_manager(*args, **kwargs):
        captured["sandbox"] = kwargs["gitlab_ca_pem"]
        return MagicMock()

    async def _spy_proxy_start(self, **kwargs):
        captured["proxy"] = kwargs["gitlab_ca_pem"]
        raise _StopLifespan

    monkeypatch.setattr("embry0.execution.sandbox_manager.SandboxManager", _spy_sandbox_manager)
    monkeypatch.setattr("embry0.execution.proxy.manager.ProxyManager.start", _spy_proxy_start)
    # Everything below needs a live DinD; none of it is under test here.
    # spec= because DockerClient has an `assert_`-prefixed method, which a bare
    # Mock refuses to hand out as an attribute.
    from embry0.execution.docker_client import DockerClient

    monkeypatch.setattr("embry0.execution.docker_client.DockerClient", lambda **kw: AsyncMock(spec=DockerClient))
    monkeypatch.setattr("embry0.execution.image_manager.SandboxImageManager", lambda *a, **kw: AsyncMock())
    monkeypatch.setattr("embry0.execution.image_manager.ContainerReaper", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("embry0.execution.runtime.egress.EgressEnforcer", lambda *a, **kw: AsyncMock())

    pools: list[DatabasePool] = []
    real_pool = DatabasePool

    def _recording_pool(url):
        pool = real_pool(url)
        pools.append(pool)
        return pool

    monkeypatch.setattr("embry0.api.app.DatabasePool", _recording_pool)

    config = Embry0Config(
        _env_file=None,
        database_url=database_url,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        github_webhook_secret="test-secret",
        gitlab_ca_bundle=str(bundle),
    )
    app = create_app(config)
    try:
        with pytest.raises(_StopLifespan):
            async with lifespan(app):
                pass
    finally:
        for pool in pools:
            await pool.close()

    assert captured["sandbox"] == CA_PEM
    assert captured["proxy"] == CA_PEM


async def test_the_initial_push_carries_the_number_each_token_was_fetched_with():
    from embry0.api.app import _push_initial_xai_token
    from embry0.execution.xai_credential import FreshToken

    class _Registry:
        async def refresh_all(self):
            return {"cred-a": FreshToken("access-token-cred-a", "2030-01-01T00:00:00+00:00", 41)}

    class _ProxyMgr:
        def __init__(self):
            self.seqs = []

        async def push_xai_token(self, credential_id, access_token=None, expires_at=None, *, seq=None):
            self.seqs.append(seq)

    mgr = _ProxyMgr()
    await _push_initial_xai_token(_Registry(), mgr, attempts=1, delay_seconds=0)
    assert mgr.seqs == [41], f"the push was not numbered when its token was fetched: {mgr.seqs}"


async def test_the_refresh_loop_pushes_each_token_with_the_number_it_was_fetched_with(monkeypatch):
    import asyncio

    import embry0.api.app as app_module
    from embry0.execution.xai_credential import FreshToken

    monkeypatch.setattr(app_module, "_XAI_HEALTH_POLL_SECONDS", 0.001)
    monkeypatch.setattr(app_module, "_XAI_TOKEN_PUSH_INTERVAL_SECONDS", 0.001)  # every tick refreshes

    class _Registry:
        async def refresh_all(self):
            return {"cred-a": FreshToken("access-token-cred-a", "2030-01-01T00:00:00+00:00", 43)}

    class _ProxyMgr:
        def __init__(self):
            self.seqs = []

        async def push_xai_token(self, credential_id, access_token=None, expires_at=None, *, seq=None):
            self.seqs.append(seq)
            raise asyncio.CancelledError  # one tick is enough

    mgr = _ProxyMgr()
    with pytest.raises(asyncio.CancelledError):
        await app_module._xai_token_refresh_loop(_Registry(), mgr, None)
    assert mgr.seqs == [43], f"the loop's push was not numbered when its token was fetched: {mgr.seqs}"
