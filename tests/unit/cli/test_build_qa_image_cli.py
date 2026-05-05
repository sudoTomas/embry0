"""Unit tests for athanor/cache/image_builder_cli.py — Task B4."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from athanor.cache.image_builder import ImageBuildResult
from athanor.cache.image_builder_cli import run_build_qa_image


@pytest.mark.asyncio
async def test_run_skips_when_existing_active_tag_matches_lockfile(monkeypatch):
    """If qa_image_tags already has an active row for (repo, current lockfile_sha),
    no new build kicks off (idempotency)."""
    from athanor.storage.repositories.qa_image_tags import QAImageTagRow

    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(
        return_value=QAImageTagRow(
            repo="org/r1",
            image_tag="org_r1:existing",
            lockfile_sha="abcdef" + "0" * 58,
            built_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
            built_by="cli",
            is_active=True,
            metadata={},
        )
    )

    # Stub: the current lockfile sha "as-cloned" matches the existing active tag.
    async def fake_compute_remote_lockfile_sha(*_a, **_kw):
        return "abcdef" + "0" * 58

    monkeypatch.setattr(
        "athanor.cache.image_builder_cli._compute_remote_lockfile_sha",
        fake_compute_remote_lockfile_sha,
    )

    # If the build path were called, this AsyncMock would record it.
    build_mock = AsyncMock()
    monkeypatch.setattr("athanor.cache.image_builder_cli.build_qa_image", build_mock)

    result = await run_build_qa_image(
        repo="org/r1",
        branch="main",
        force=False,
        deps={
            "image_repo": image_repo,
            "sandbox_mgr": AsyncMock(),
            "docker": AsyncMock(),
            "profiles_repo": AsyncMock(),
            "proxy_mgr": AsyncMock(),
        },
    )
    assert result == "skipped"
    build_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_force_rebuilds_even_with_matching_sha(monkeypatch):
    """--force flag triggers a fresh build regardless of existing active tag."""
    from athanor.storage.repositories.qa_image_tags import QAImageTagRow

    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(
        return_value=QAImageTagRow(
            repo="org/r1",
            image_tag="org_r1:existing",
            lockfile_sha="aaa",
            built_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
            built_by="cli",
            is_active=True,
            metadata={},
        )
    )

    async def fake_compute_remote_lockfile_sha(*_a, **_kw):
        return "aaa"

    monkeypatch.setattr(
        "athanor.cache.image_builder_cli._compute_remote_lockfile_sha",
        fake_compute_remote_lockfile_sha,
    )

    build_mock = AsyncMock(
        return_value=ImageBuildResult(image_tag="t2", lockfile_sha="aaa", head_sha="x")
    )
    monkeypatch.setattr("athanor.cache.image_builder_cli.build_qa_image", build_mock)

    result = await run_build_qa_image(
        repo="org/r1",
        branch="main",
        force=True,
        deps={
            "image_repo": image_repo,
            "sandbox_mgr": AsyncMock(),
            "docker": AsyncMock(),
            "profiles_repo": AsyncMock(),
            "proxy_mgr": AsyncMock(),
        },
    )
    assert result == "built"
    build_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_builds_when_no_active_tag_exists(monkeypatch):
    """When no active image tag exists for the repo, a new build is triggered."""
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=None)

    async def fake_compute_remote_lockfile_sha(*_a, **_kw):
        return "abc"

    monkeypatch.setattr(
        "athanor.cache.image_builder_cli._compute_remote_lockfile_sha",
        fake_compute_remote_lockfile_sha,
    )

    build_mock = AsyncMock(
        return_value=ImageBuildResult(image_tag="t1", lockfile_sha="abc", head_sha="x")
    )
    monkeypatch.setattr("athanor.cache.image_builder_cli.build_qa_image", build_mock)

    result = await run_build_qa_image(
        repo="org/r1",
        branch="main",
        force=False,
        deps={
            "image_repo": image_repo,
            "sandbox_mgr": AsyncMock(),
            "docker": AsyncMock(),
            "profiles_repo": AsyncMock(),
            "proxy_mgr": AsyncMock(),
        },
    )
    assert result == "built"
