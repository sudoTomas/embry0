"""Tests for QAWorkspaceProviderOverridesRepository — Phase 5G admin overrides."""

from __future__ import annotations

import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.qa_workspace_provider_overrides import (
    QAWorkspaceProviderOverridesRepository,
    WorkspaceProviderOverride,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repo(
    db_with_migrations: DatabasePool,
) -> QAWorkspaceProviderOverridesRepository:
    return QAWorkspaceProviderOverridesRepository(db=db_with_migrations)


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(repo):
    assert await repo.get("org/missing") is None


@pytest.mark.asyncio
async def test_upsert_then_get_round_trip(repo):
    await repo.upsert(
        repo="org/r1",
        provider_type="npm-workspaces-turbo",
        config={
            "affected_filter": "[HEAD^1]",
            "apps_glob": "apps/*",
            "ignore_globs": ["**/*.md"],
        },
    )
    out = await repo.get("org/r1")
    assert isinstance(out, WorkspaceProviderOverride)
    assert out.repo == "org/r1"
    assert out.provider_type == "npm-workspaces-turbo"
    assert out.config == {
        "affected_filter": "[HEAD^1]",
        "apps_glob": "apps/*",
        "ignore_globs": ["**/*.md"],
    }
    assert out.updated_at is not None


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_row(repo):
    await repo.upsert(
        repo="org/r1",
        provider_type="npm-workspaces-turbo",
        config={"affected_filter": "[HEAD^1]"},
    )
    await repo.upsert(
        repo="org/r1",
        provider_type="pnpm-workspaces",
        config={"apps_glob": "packages/apps/*"},
    )
    out = await repo.get("org/r1")
    assert out is not None
    assert out.provider_type == "pnpm-workspaces"
    assert out.config == {"apps_glob": "packages/apps/*"}


@pytest.mark.asyncio
async def test_list_all_returns_sorted_by_repo(repo):
    await repo.upsert(repo="org/c", provider_type="t", config={})
    await repo.upsert(repo="org/a", provider_type="t", config={})
    await repo.upsert(repo="org/b", provider_type="t", config={})
    rows = await repo.list_all()
    assert [r.repo for r in rows] == ["org/a", "org/b", "org/c"]


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_existed(repo):
    await repo.upsert(repo="org/r1", provider_type="t", config={})
    assert await repo.delete("org/r1") is True
    assert await repo.get("org/r1") is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_row_missing(repo):
    assert await repo.delete("org/never-existed") is False
