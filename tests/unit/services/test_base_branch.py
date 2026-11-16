"""Base-branch precedence: override → preference → forge default → main."""

import httpx
import pytest
import structlog.testing

from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient
from embry0.services.base_branch import (
    DEFAULT_BASE_BRANCH,
    _forge_default,
    preferred_base_branch,
    resolve_base_branch,
)

REPO = "acme/group/backend"


class FakePrefs:
    def __init__(self, row: dict | None = None, *, fail: bool = False) -> None:
        self._row = row
        self._fail = fail

    async def get(self, repo: str) -> dict | None:
        if self._fail:
            raise RuntimeError("db down")
        return self._row


def _gitlab(default_branch: str | None) -> GitLabClient:
    def handler(request):
        if default_branch is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"id": 1, "default_branch": default_branch})

    return GitLabClient(
        default_token="glpat-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _github(default_branch: str | None) -> GitHubClient:
    def handler(request):
        if default_branch is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"default_branch": default_branch})

    return GitHubClient(
        default_token="ghp-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_override_wins_over_everything():
    base = await resolve_base_branch(
        repo=REPO,
        forge="gitlab",
        override="hotfix/urgent",
        prefs_repo=FakePrefs({"base_branch": "dev"}),
        gitlab_client=_gitlab("FBT_Source"),
    )
    assert base == "hotfix/urgent"


async def test_preference_wins_over_the_forge_default():
    base = await resolve_base_branch(
        repo=REPO,
        forge="gitlab",
        prefs_repo=FakePrefs({"base_branch": "dev"}),
        gitlab_client=_gitlab("main"),
    )
    assert base == "dev"


async def test_gitlab_default_branch_is_used_when_no_preference():
    base = await resolve_base_branch(
        repo=REPO,
        forge="gitlab",
        prefs_repo=FakePrefs({"base_branch": None}),
        gitlab_client=_gitlab("FBT_Source"),
    )
    assert base == "FBT_Source"


async def test_github_default_branch_is_used_when_no_preference():
    base = await resolve_base_branch(
        repo="o/r",
        forge="github",
        prefs_repo=None,
        github_client=_github("master"),
    )
    assert base == "master"


async def test_falls_back_to_main_with_nothing_wired():
    assert await resolve_base_branch(repo=REPO, forge="gitlab") == DEFAULT_BASE_BRANCH == "main"


async def test_forge_api_failure_falls_back_to_main_rather_than_raising():
    """Resolution must never be the reason a job dies — the clone will fail
    loudly later if the branch is genuinely wrong."""
    assert await resolve_base_branch(repo="o/r", forge="github", github_client=_github(None)) == "main"


async def test_gitlab_missing_project_falls_back_to_main():
    """A project GitLab will not show us (404) yields no default branch."""
    assert await resolve_base_branch(repo=REPO, forge="gitlab", gitlab_client=_gitlab(None)) == "main"


async def test_gitlab_lookup_error_falls_back_to_main_rather_than_raising():
    """The GitLab path (GitLabClient.get_project) has no internal
    try/except: a transport error propagates all the way out of the
    client, and _forge_default is the only thing standing between it and
    the caller."""

    def handler(request):
        raise httpx.ConnectError("gitlab unreachable")

    async def _no_sleep(seconds: float) -> None:
        return None

    client = GitLabClient(
        default_token="glpat-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_attempts=1,
        sleep=_no_sleep,
    )
    assert await resolve_base_branch(repo=REPO, forge="gitlab", gitlab_client=client) == "main"


async def _source_of(**kwargs) -> tuple[str, str]:
    """(branch, the ``source`` the resolver logged for it)."""
    with structlog.testing.capture_logs() as captured:
        branch = await resolve_base_branch(**kwargs)
    resolved = [e for e in captured if e.get("event") == "base_branch_resolved"]
    assert len(resolved) == 1, captured
    return branch, str(resolved[0]["source"])


async def test_github_404_is_reported_as_a_fallback_not_as_the_forges_answer():
    """Spec 1A §3.8. A wrapper that answered the literal "main" on a 404, a
    rate limit or a network blip made every failure look like a real answer
    — and that guess became the clone fetch ref, the review diff base and
    the MR target for the whole job."""
    branch, source = await _source_of(repo="o/r", forge="github", github_client=_github(None))
    assert (branch, source) == ("main", "fallback")


async def test_gitlab_404_is_reported_as_a_fallback_not_as_the_forges_answer():
    """Identical on GitLab: the resolver reads the project itself, and
    says when it fell back."""
    branch, source = await _source_of(repo=REPO, forge="gitlab", gitlab_client=_gitlab(None))
    assert (branch, source) == ("main", "fallback")


async def test_a_real_main_default_is_still_reported_as_the_forges_answer():
    """The other half of the distinction: when the project genuinely defaults
    to main, the resolver must still say so."""
    branch, source = await _source_of(repo="o/r", forge="github", github_client=_github("main"))
    assert (branch, source) == ("main", "forge")


async def test_forge_default_returns_none_when_the_forge_did_not_answer():
    assert await _forge_default(repo="o/r", forge="github", gitlab_client=None, github_client=_github(None)) is None
    assert await _forge_default(repo=REPO, forge="gitlab", gitlab_client=_gitlab(None), github_client=None) is None


async def test_preferences_lookup_failure_falls_through_to_the_forge():
    base = await resolve_base_branch(
        repo=REPO,
        forge="gitlab",
        prefs_repo=FakePrefs(fail=True),
        gitlab_client=_gitlab("FBT_Source"),
    )
    assert base == "FBT_Source"


@pytest.mark.parametrize("bad", ["feat branch", "a;rm -rf /", "", "  "])
async def test_unsafe_stored_values_are_ignored_not_interpolated(bad):
    """A row written before the API validator (or by hand) must not reach a
    shell line."""
    base = await resolve_base_branch(
        repo=REPO,
        forge="gitlab",
        prefs_repo=FakePrefs({"base_branch": bad}),
        gitlab_client=_gitlab("FBT_Source"),
    )
    assert base == "FBT_Source"


async def test_preferred_base_branch_returns_none_without_a_row():
    assert await preferred_base_branch(FakePrefs(None), REPO) is None
    assert await preferred_base_branch(None, REPO) is None
    assert await preferred_base_branch(FakePrefs({"base_branch": "dev"}), REPO) == "dev"
