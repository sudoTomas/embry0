"""Lookup wrapper for the most-recent active pre-baked image tag per repo.

Sits between `acquire_sandbox_node` and the qa_image_tags repository so the
node body can pass `image_repo=None` in test contexts and gracefully degrade
to base-profile behavior. Production wires the real repository through.
"""

from __future__ import annotations

from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository


class PrebakedImageLookup:
    def __init__(self, image_repo: QAImageTagsRepository | None) -> None:
        self._repo = image_repo

    async def get_tag_for_repo(self, repo: str) -> str | None:
        if self._repo is None:
            return None
        row = await self._repo.get_active(repo)
        return row.image_tag if row else None
