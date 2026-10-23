"""Propose mode's start branch (#29): a repository with no base branch setting
and no discovery fact leaves the branch empty, so the executor asks the forge
instead of this helper guessing "main"."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from embry0.services.repo_setup import start_branch


def _state(prefs: dict | None, fact: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        repo_preferences_repo=SimpleNamespace(get=AsyncMock(return_value=prefs)),
        org_repos_repo=SimpleNamespace(get=AsyncMock(return_value=fact)),
    )


async def test_the_setting_wins() -> None:
    assert await start_branch(_state({"base_branch": "develop"}, {"default_branch": "trunk"}), "acme/web") == "develop"


async def test_then_the_discovery_fact() -> None:
    assert await start_branch(_state(None, {"default_branch": "trunk"}), "acme/web") == "trunk"


async def test_otherwise_the_executor_asks_the_forge() -> None:
    got = await start_branch(_state(None, None), "acme/web")
    assert got is None, f"start_branch guessed {got!r} instead of leaving the branch to the executor's resolver"
