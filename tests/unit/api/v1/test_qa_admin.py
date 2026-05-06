"""Route tests for /api/v1/qa/admin/providers — Phase 5G admin surface."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_workspace_provider_overrides import (
    WorkspaceProviderOverride,
)

_KEY = "test-api-key-32-characters-minimum-x"
_AUTH = {"Authorization": f"Bearer {_KEY}"}
# Mutating methods (POST/DELETE) hit CSRFMiddleware; X-Requested-With
# satisfies it. GETs are exempt — but it's harmless to send always, so
# we include it everywhere for symmetry.
_AUTH_MUT = {**_AUTH, "X-Requested-With": "fetch"}


def _make_app(overrides_repo=None):
    from athanor.api.app import create_app

    app = create_app()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    if overrides_repo is not None:
        app.state.qa_workspace_provider_overrides_repo = overrides_repo
    return app, TestClient(app)


def _row(
    repo: str = "org/r1",
    provider_type: str = "npm-workspaces-turbo",
    config: dict | None = None,
) -> WorkspaceProviderOverride:
    return WorkspaceProviderOverride(
        repo=repo,
        provider_type=provider_type,
        config=config if config is not None else {"affected_filter": "[HEAD^1]"},
        updated_at=datetime.now(UTC),
    )


# ─── GET /qa/admin/providers ────────────────────────────────────────────────


def test_list_returns_empty_when_none():
    repo = AsyncMock()
    repo.list_all = AsyncMock(return_value=[])
    _, client = _make_app(repo)

    resp = client.get("/api/v1/qa/admin/providers", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_all_overrides():
    repo = AsyncMock()
    repo.list_all = AsyncMock(
        return_value=[
            _row("org/a", "npm-workspaces-turbo", {"affected_filter": "[HEAD^1]"}),
            _row("org/b", "pnpm-workspaces", {"apps_glob": "apps/*"}),
        ]
    )
    _, client = _make_app(repo)

    resp = client.get("/api/v1/qa/admin/providers", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["repo"] == "org/a"
    assert body[0]["provider_type"] == "npm-workspaces-turbo"
    assert body[0]["config"] == {"affected_filter": "[HEAD^1]"}
    assert body[1]["repo"] == "org/b"


# ─── GET /qa/admin/providers/{repo} ─────────────────────────────────────────


def test_get_returns_404_when_missing():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    _, client = _make_app(repo)

    resp = client.get("/api/v1/qa/admin/providers/org/missing", headers=_AUTH)
    assert resp.status_code == 404


def test_get_returns_override():
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row("org/r1", "npm-workspaces-turbo", {"apps_glob": "apps/*"})
    )
    _, client = _make_app(repo)

    resp = client.get("/api/v1/qa/admin/providers/org/r1", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "org/r1"
    assert body["provider_type"] == "npm-workspaces-turbo"
    assert body["config"] == {"apps_glob": "apps/*"}


# ─── POST /qa/admin/providers/{repo} ────────────────────────────────────────


def test_upsert_creates_new():
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row("org/new", "npm-workspaces-turbo", {"affected_filter": "[HEAD^1]"})
    )
    _, client = _make_app(repo)

    resp = client.post(
        "/api/v1/qa/admin/providers/org/new",
        headers=_AUTH_MUT,
        json={
            "provider_type": "npm-workspaces-turbo",
            "config": {"affected_filter": "[HEAD^1]"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "org/new"
    repo.upsert.assert_awaited_once_with(
        repo="org/new",
        provider_type="npm-workspaces-turbo",
        config={"affected_filter": "[HEAD^1]"},
    )


def test_upsert_updates_existing():
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row("org/r1", "pnpm-workspaces", {"apps_glob": "packages/apps/*"})
    )
    _, client = _make_app(repo)

    resp = client.post(
        "/api/v1/qa/admin/providers/org/r1",
        headers=_AUTH_MUT,
        json={
            "provider_type": "pnpm-workspaces",
            "config": {"apps_glob": "packages/apps/*"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_type"] == "pnpm-workspaces"
    assert body["config"] == {"apps_glob": "packages/apps/*"}


def test_upsert_validates_provider_type_required():
    repo = AsyncMock()
    _, client = _make_app(repo)

    resp = client.post(
        "/api/v1/qa/admin/providers/org/r1",
        headers=_AUTH_MUT,
        json={"provider_type": "", "config": {}},
    )
    # min_length=1 on provider_type triggers 422 from FastAPI/pydantic.
    assert resp.status_code == 422


# ─── DELETE /qa/admin/providers/{repo} ──────────────────────────────────────


def test_delete_returns_204_when_existed():
    repo = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    _, client = _make_app(repo)

    resp = client.delete("/api/v1/qa/admin/providers/org/r1", headers=_AUTH_MUT)
    assert resp.status_code == 204
    assert resp.text == ""
    repo.delete.assert_awaited_once_with("org/r1")


def test_delete_returns_404_when_missing():
    repo = AsyncMock()
    repo.delete = AsyncMock(return_value=False)
    _, client = _make_app(repo)

    resp = client.delete("/api/v1/qa/admin/providers/org/never", headers=_AUTH_MUT)
    assert resp.status_code == 404


# ─── Auth ───────────────────────────────────────────────────────────────────


def test_routes_require_auth():
    repo = AsyncMock()
    repo.list_all = AsyncMock(return_value=[])
    repo.get = AsyncMock(return_value=None)
    repo.delete = AsyncMock(return_value=False)
    _, client = _make_app(repo)

    # Every method on every path must 401 without the Bearer header. The
    # CSRF middleware runs after auth here; we still send X-Requested-With
    # so the response we observe is the auth failure, not 403-CSRF.
    csrf = {"X-Requested-With": "fetch"}
    assert client.get("/api/v1/qa/admin/providers").status_code == 401
    assert client.get("/api/v1/qa/admin/providers/org/r1").status_code == 401
    assert client.post(
        "/api/v1/qa/admin/providers/org/r1",
        headers=csrf,
        json={"provider_type": "x", "config": {}},
    ).status_code == 401
    assert (
        client.delete("/api/v1/qa/admin/providers/org/r1", headers=csrf).status_code
        == 401
    )
