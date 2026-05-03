"""Tests for athanor/execution/proxy/server.py — the container entrypoint."""

import pytest
from aiohttp import web

from athanor.execution.proxy import server


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


def test_build_app_git_missing_token_raises(monkeypatch):
    monkeypatch.setenv("PROXY_TYPE", "git")
    monkeypatch.setenv("PROXY_ADMIN_TOKEN", "test-admin-tok")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GITHUB_TOKEN.*required"):
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
