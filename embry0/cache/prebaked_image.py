"""Lookup wrapper for the active pre-baked image tag per repo.

Sits between `acquire_sandbox_node` and the qa_image_tags repository so the
node body can pass `image_repo=None` in test contexts and gracefully degrade
to base-profile behavior. Production wires the real repository through.

With a ``digest_resolver`` the lookup also refuses an image whose base sandbox
image has been rebuilt since it was committed (#334) — see
``embry0/cache/prebaked_staleness.py``. Without one it behaves as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from embry0.cache.prebaked_staleness import BaseImageDigestResolver, DigestResolver, usable_image_tag
from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository

logger = structlog.get_logger(__name__)


class PrebakedImageLookup:
    def __init__(
        self,
        image_repo: QAImageTagsRepository | None,
        digest_resolver: DigestResolver | None = None,
    ) -> None:
        self._repo = image_repo
        self._resolver = digest_resolver

    async def get_tag_for_repo(self, repo: str, *, default_branch: str | None) -> str | None:
        """The active image's tag when it was baked from ``default_branch``, else None.

        ``default_branch`` is the repository's default branch as the forge
        reported it (``embry0.cache.trusted_run``). A row records the branch
        it was baked from in ``metadata['branch']``; only a row naming exactly
        that branch is selected. With no default branch known, or a row that
        records no branch, nothing is selected and the run uses the profile's
        own image.
        """
        if self._repo is None or not default_branch:
            return None
        row = await self._repo.get_active(repo)
        if row is None:
            return None
        if row.metadata.get("branch") != default_branch:
            logger.info(
                "qa_prebaked_image_not_default_branch",
                repo=repo,
                baked_branch=row.metadata.get("branch"),
                default_branch=default_branch,
            )
            return None
        return await usable_image_tag(row, self._resolver)


async def lookup_prebaked_tag(configurable: Mapping[str, Any], repo: str, *, default_branch: str | None) -> str | None:
    """The one selection path for QA graph nodes.

    Reads ``qa_image_tags_repo`` and ``qa_base_digest_resolver`` (falling back
    to a resolver over ``runtime``) from the graph's ``configurable``. With
    neither there is nothing to compare against, so the lookup degrades to the
    unchecked behavior.

    ``default_branch`` is the forge's default branch: only an image baked
    from it is selected, and None selects none.
    """
    image_repo = configurable.get("qa_image_tags_repo")
    if image_repo is None:
        return None
    # The orchestrator threads one long-lived resolver through (app.state), so
    # both selection sites in a job share its cache and its single-flight
    # refresh. Building one here per call would re-pull on every lookup.
    resolver = configurable.get("qa_base_digest_resolver")
    if resolver is None:
        runtime = configurable.get("runtime")
        resolver = BaseImageDigestResolver(runtime) if runtime is not None else None
    return await PrebakedImageLookup(image_repo=image_repo, digest_resolver=resolver).get_tag_for_repo(
        repo, default_branch=default_branch
    )
