from unittest.mock import AsyncMock

import pytest

from athanor.cache.prebaked_image import PrebakedImageLookup
from athanor.storage.repositories.qa_image_tags import QAImageTagRow


def _row(image_tag: str, lockfile_sha: str = "deadbeef") -> QAImageTagRow:
    from datetime import datetime, timezone
    return QAImageTagRow(
        repo="org/r1",
        image_tag=image_tag,
        lockfile_sha=lockfile_sha,
        built_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        built_by="cli",
        is_active=True,
        metadata={},
    )


@pytest.mark.asyncio
async def test_lookup_returns_tag_when_active_exists():
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=_row("repo:t1"))
    lookup = PrebakedImageLookup(image_repo=image_repo)
    tag = await lookup.get_tag_for_repo("org/r1")
    assert tag == "repo:t1"


@pytest.mark.asyncio
async def test_lookup_returns_none_when_no_active_tag():
    image_repo = AsyncMock()
    image_repo.get_active = AsyncMock(return_value=None)
    lookup = PrebakedImageLookup(image_repo=image_repo)
    tag = await lookup.get_tag_for_repo("org/r1")
    assert tag is None


@pytest.mark.asyncio
async def test_lookup_skips_when_image_repo_is_none():
    """When the cache state DB isn't configured (e.g. unit-test environment),
    PrebakedImageLookup gracefully reports None — sub-task falls back to base."""
    lookup = PrebakedImageLookup(image_repo=None)
    tag = await lookup.get_tag_for_repo("org/r1")
    assert tag is None
