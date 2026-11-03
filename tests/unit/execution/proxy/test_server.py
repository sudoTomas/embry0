"""Tests for embry0/execution/proxy/server.py — the container entrypoint."""

import base64

import pytest
from aiohttp import web

from embry0.execution.proxy import app_keys, server
from embry0.execution.proxy.context_proxy import CONTAINER_ALLOWLIST, QUERY_QUOTA
from embry0.execution.proxy.gitlab_proxy import GITLAB_TOKEN

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


def test_build_app_git_returns_git_app(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "git")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)
    routes = [str(r.resource) for r in app.router.routes()]
    assert any("/git-credentials" in r for r in routes)


def test_build_app_github_returns_github_app(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "github")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)


def test_build_app_auth_returns_auth_app(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "auth")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)


def test_build_app_minio_returns_minio_app(monkeypatch):
    # minio is a network-plumbing proxy and does NOT require PROXY_ADMIN_TOKEN.
    monkeypatch.setenv("PROXY_TYPE", "minio")
    monkeypatch.setenv("UPSTREAM_URL", "http://minio:9000")
    monkeypatch.delenv("PROXY_ADMIN_TOKEN", raising=False)
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)
    routes = [str(r.resource) for r in app.router.routes()]
    assert any("/health" in r for r in routes)


def test_build_app_presign_returns_presign_app(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "presign")
    monkeypatch.setenv("UPSTREAM_URL", "http://orchestrator:8000")
    monkeypatch.delenv("PROXY_ADMIN_TOKEN", raising=False)
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)
    routes = [str(r.resource) for r in app.router.routes()]
    assert any("/api/v1/internal/qa/presign" in r for r in routes)


def test_build_app_context_returns_context_app(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.setenv("UPSTREAM_URL", "http://10.0.0.1:8000")
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "api-key")
    monkeypatch.setenv("CONTAINER_ALLOWLIST", "acme-backend, acme-frontend")
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)
    routes = [str(r.resource) for r in app.router.routes()]
    assert any("/api/v1/logs/query" in r for r in routes)
    assert app[CONTAINER_ALLOWLIST] == ["acme-backend", "acme-frontend"]
    assert app[QUERY_QUOTA] == 30


def test_build_app_context_missing_allowlist_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.setenv("UPSTREAM_URL", "http://10.0.0.1:8000")
    monkeypatch.delenv("CONTAINER_ALLOWLIST", raising=False)
    with pytest.raises(ValueError, match="CONTAINER_ALLOWLIST.*required"):
        server.build_app_from_env()


def test_build_app_context_missing_admin_token_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.delenv("PROXY_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("UPSTREAM_URL", "http://10.0.0.1:8000")
    monkeypatch.setenv("CONTAINER_ALLOWLIST", "acme-backend")
    with pytest.raises(ValueError, match="PROXY_ADMIN_TOKEN.*required"):
        server.build_app_from_env()


def test_build_app_context_empty_api_key_is_tolerated(monkeypatch):
    # Dev-mode: upstream auth is skipped when API_KEY is unset, so the proxy
    # builds (with a warning) rather than refusing to start.
    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.setenv("UPSTREAM_URL", "http://10.0.0.1:8000")
    monkeypatch.setenv("CONTAINER_ALLOWLIST", "acme-backend")
    monkeypatch.delenv("ORCHESTRATOR_API_KEY", raising=False)
    app = server.build_app_from_env()
    assert isinstance(app, web.Application)


def test_listen_port_context_default(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.delenv("LISTEN_PORT", raising=False)
    assert server.listen_port_from_env() == 9107


def test_build_app_minio_missing_upstream_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "minio")
    monkeypatch.delenv("UPSTREAM_URL", raising=False)
    with pytest.raises(ValueError, match="UPSTREAM_URL.*required"):
        server.build_app_from_env()


def test_build_app_presign_missing_upstream_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "presign")
    monkeypatch.delenv("UPSTREAM_URL", raising=False)
    with pytest.raises(ValueError, match="UPSTREAM_URL.*required"):
        server.build_app_from_env()


def test_listen_port_minio_default(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "minio")
    monkeypatch.delenv("LISTEN_PORT", raising=False)
    assert server.listen_port_from_env() == 9100


def test_listen_port_presign_default(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "presign")
    monkeypatch.delenv("LISTEN_PORT", raising=False)
    assert server.listen_port_from_env() == 9104


def test_build_app_unknown_type_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "bogus")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    with pytest.raises(ValueError, match="Unknown PROXY_TYPE"):
        server.build_app_from_env()


def test_build_app_missing_type_raises(monkeypatch):
    monkeypatch.delenv("PROXY_TYPE", raising=False)
    with pytest.raises(ValueError, match="PROXY_TYPE.*required"):
        server.build_app_from_env()


def test_build_app_auth_missing_key_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "auth")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY.*required"):
        server.build_app_from_env()


def test_build_app_missing_admin_token_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "git")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.delenv("PROXY_ADMIN_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PROXY_ADMIN_TOKEN.*required"):
        server.build_app_from_env()


def test_listen_port_default(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "git")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.delenv("LISTEN_PORT", raising=False)
    assert server.listen_port_from_env() == 9101


def test_listen_port_explicit(monkeypatch):
    monkeypatch.setenv("LISTEN_PORT", "12345")
    assert server.listen_port_from_env() == 12345


def test_build_app_git_tolerates_missing_github_token(monkeypatch):
    """GitLab-only deployments run the git proxy with NO GitHub token — the
    served credential comes from each sandbox's enrollment payload, and the
    env token is only the payload-less-enrollment fallback. Regression: a
    hard _require_env here crash-looped the container and killed every
    GitLab clone at sandbox enrollment."""
    monkeypatch.setenv("PROXY_TYPE", "git")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "adm")
    app = server.build_app_from_env()
    assert app[app_keys.GITHUB_TOKEN] == ""


@pytest.mark.parametrize(
    ("proxy_type", "env_key", "app_key"),
    [("github", "GITHUB_TOKEN", app_keys.GITHUB_TOKEN), ("gitlab", "GITLAB_TOKEN", GITLAB_TOKEN)],
)
def test_build_app_forge_api_proxy_tolerates_empty_token(monkeypatch, proxy_type, env_key, app_key):
    """#424: a registry-only deployment launches github-/gitlab-proxy with an
    empty baked token; the real token arrives in each sandbox's enrollment
    payload. A hard _require_env here would crash the container at startup."""
    monkeypatch.setenv("PROXY_TYPE", proxy_type)
    monkeypatch.setenv(env_key, "")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "adm")
    app = server.build_app_from_env()
    assert app[app_key] == ""


def test_gitlab_ca_pem_passes_a_raw_pem_through(monkeypatch):
    monkeypatch.setenv("GITLAB_CA_PEM", CA_PEM)
    assert server._gitlab_ca_pem() == CA_PEM


def test_gitlab_ca_pem_decodes_base64(monkeypatch):
    """ProxyManager must base64 it: docker --env-file truncates at a newline."""
    monkeypatch.setenv("GITLAB_CA_PEM", base64.b64encode(CA_PEM.encode()).decode())
    assert server._gitlab_ca_pem() == CA_PEM


def test_gitlab_ca_pem_is_empty_when_unset(monkeypatch):
    monkeypatch.delenv("GITLAB_CA_PEM", raising=False)
    assert server._gitlab_ca_pem() == ""


def test_gitlab_ca_pem_ignores_garbage(monkeypatch):
    """A mangled value must not take the proxy down; it degrades to system trust."""
    monkeypatch.setenv("GITLAB_CA_PEM", "%%% not base64 and not a pem %%%")
    assert server._gitlab_ca_pem() == ""


def test_build_app_gitlab_wires_the_ca(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "gitlab")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-x")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.setenv("GITLAB_CA_PEM", base64.b64encode(CA_PEM.encode()).decode())
    app = server.build_app_from_env()
    assert app[app_keys.CA_PEM] == CA_PEM


def test_gitlab_ca_pem_rejects_a_truncated_pem(monkeypatch):
    """docker --env-file cuts a raw multi-line PEM at its first line.

    That first line contains "BEGIN CERTIFICATE", so a marker check alone lets
    it through and the SSLError surfaces later, killing proxy startup.
    """
    monkeypatch.setenv("GITLAB_CA_PEM", "-----BEGIN CERTIFICATE-----")
    assert server._gitlab_ca_pem() == ""


def test_gitlab_ca_pem_accepts_line_wrapped_base64(monkeypatch):
    """base64(1) and openssl base64 wrap at 76 columns by default."""
    packed = base64.b64encode(CA_PEM.encode()).decode()
    wrapped = "\n".join(packed[i : i + 76] for i in range(0, len(packed), 76))
    assert "\n" in wrapped
    monkeypatch.setenv("GITLAB_CA_PEM", wrapped)
    assert server._gitlab_ca_pem() == CA_PEM


def test_gitlab_ca_pem_rejects_base64_of_non_pem(monkeypatch):
    """Valid base64 is not enough — it must decode to a real certificate."""
    monkeypatch.setenv("GITLAB_CA_PEM", base64.b64encode(b"hello").decode())
    assert server._gitlab_ca_pem() == ""


def test_build_app_context_registers_the_redaction_fields(monkeypatch):
    """#294: LOG_REDACTION_FIELDS is what the context proxy redacts besides secrets."""
    from embry0.safety import redaction

    monkeypatch.setenv("PROXY_TYPE", "context")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.setenv("UPSTREAM_URL", "http://10.0.0.1:8000")
    monkeypatch.setenv("CONTAINER_ALLOWLIST", "acme-backend")
    monkeypatch.setenv("LOG_REDACTION_FIELDS", "phone")
    try:
        server.build_app_from_env()
        out = redaction.redact_log_text("phone=+15551234", [])
    finally:
        redaction.register_redacted_fields([])
    assert out == f"phone={redaction.REDACTED}", out
