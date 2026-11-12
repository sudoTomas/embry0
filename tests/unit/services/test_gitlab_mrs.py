"""GitLab merge-request fallback (#167)."""

import json as json_mod

import httpx
import pytest

from embry0.execution.gitlab_client import GitLabClient
from embry0.services.gitlab_mrs import (
    default_branch_for,
    ensure_mr_for_branch,
    find_open_mr_for_branch,
)

REPO = "acme/products/fbt"
BRANCH = "embry0/iss-abcd1234-fix"


def _client(handler) -> GitLabClient:
    return GitLabClient(
        default_token="glpat-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_default_branch_is_read_not_assumed():
    """fbt's default is FBT_Source — targeting `main` would fail the create."""

    def handler(request):
        return httpx.Response(200, json={"id": 1, "default_branch": "FBT_Source"})

    assert await default_branch_for(_client(handler), REPO) == "FBT_Source"


async def test_default_branch_falls_back_to_main():
    def handler(request):
        return httpx.Response(404, json={})

    assert await default_branch_for(_client(handler), REPO) == "main"


async def test_existing_open_mr_is_reused_not_duplicated():
    posted = {"n": 0}

    def handler(request):
        if request.method == "POST":
            posted["n"] += 1
            return httpx.Response(201, json={"web_url": "https://gitlab.com/new"})
        return httpx.Response(200, json=[{"web_url": "https://gitlab.com/existing/-/merge_requests/3"}])

    url = await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=_client(handler))
    assert url == "https://gitlab.com/existing/-/merge_requests/3"
    assert posted["n"] == 0


async def test_creates_against_the_projects_default_branch():
    seen: dict = {}

    def handler(request):
        if request.method == "POST":
            seen["body"] = json_mod.loads(request.content)
            seen["url"] = str(request.url)
            return httpx.Response(201, json={"web_url": "https://gitlab.com/mr/1"})
        if "/merge_requests" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"default_branch": "FBT_Source"})

    url = await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="Fix it", body="why", client=_client(handler))
    assert url == "https://gitlab.com/mr/1"
    assert seen["body"]["source_branch"] == BRANCH
    assert seen["body"]["target_branch"] == "FBT_Source"
    assert seen["body"]["title"] == "Fix it"
    # Project path must be %2F-encoded in the URL.
    assert "acme%2Fproducts%2Ffbt" in seen["url"]


async def test_409_conflict_recovers_the_existing_mr():
    """A race between our lookup and our create must not lose the URL."""
    calls = {"list": 0}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(409, json={"message": "already exists"})
        if "/merge_requests" in str(request.url):
            calls["list"] += 1
            if calls["list"] == 1:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"web_url": "https://gitlab.com/raced"}])
        return httpx.Response(200, json={"default_branch": "main"})

    url = await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=_client(handler))
    assert url == "https://gitlab.com/raced"


async def test_never_raises_on_api_failure():
    def handler(request):
        raise httpx.ConnectError("down")

    assert await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=_client(handler)) is None


async def test_refuses_to_target_the_default_branch_itself():
    def handler(request):
        if "/merge_requests" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"default_branch": "FBT_Source"})

    assert (
        await ensure_mr_for_branch(repo=REPO, branch="FBT_Source", title="t", body="b", client=_client(handler)) is None
    )


async def test_protected_branch_names_are_skipped():
    def handler(request):
        raise AssertionError("must not call the API for main/master")

    for branch in ("main", "master", ""):
        assert (
            await ensure_mr_for_branch(repo=REPO, branch=branch, title="t", body="b", client=_client(handler)) is None
        )


async def test_find_open_mr_returns_none_when_absent():
    def handler(request):
        return httpx.Response(200, json=[])

    assert await find_open_mr_for_branch(_client(handler), REPO, BRANCH) is None


# --- Spec 1A §3.7: an explicit target branch --------------------------------


async def test_explicit_target_branch_is_used_without_reading_the_project():
    """The resolver already answered; a second round trip would only risk
    disagreeing with the branch the clone actually fetched."""
    seen: dict = {}

    def handler(request):
        if request.method == "POST":
            seen["body"] = json_mod.loads(request.content)
            return httpx.Response(201, json={"web_url": "https://gitlab.com/mr/7"})
        if "/merge_requests" in str(request.url):
            return httpx.Response(200, json=[])
        raise AssertionError("must not fetch the project record when a target is given")

    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=_client(handler), target_branch="dev"
    )
    assert url == "https://gitlab.com/mr/7"
    assert seen["body"]["target_branch"] == "dev"


async def test_same_branch_guard_compares_against_the_explicit_target():
    # The POST is counted rather than merely asserted against: ensure_mr_for_branch
    # is fail-open, so an AssertionError raised inside the handler would be
    # swallowed and the test would pass whether or not the guard fired.
    posted = {"n": 0}

    def handler(request):
        if request.method == "POST":
            posted["n"] += 1
            raise AssertionError("must not create an MR from dev into dev")
        if "/merge_requests" in str(request.url):
            return httpx.Response(200, json=[])
        raise AssertionError("must not fetch the project record when a target is given")

    assert (
        await ensure_mr_for_branch(
            repo=REPO, branch="dev", title="t", body="b", client=_client(handler), target_branch="dev"
        )
        is None
    )
    assert posted["n"] == 0


async def test_main_is_mergeable_when_the_target_is_a_different_branch():
    """The old literal main/master skip meant a main-based fix could never
    open an MR into dev."""
    seen: dict = {}

    def handler(request):
        if request.method == "POST":
            seen["body"] = json_mod.loads(request.content)
            return httpx.Response(201, json={"web_url": "https://gitlab.com/mr/8"})
        return httpx.Response(200, json=[])

    url = await ensure_mr_for_branch(
        repo=REPO, branch="main", title="t", body="b", client=_client(handler), target_branch="dev"
    )
    assert url == "https://gitlab.com/mr/8"
    assert seen["body"]["target_branch"] == "dev"


async def test_says_what_gitlab_answered_when_it_opens_none():
    """#459: the MR stays None, and the caller that asks gets GitLab's answer."""

    def handler(request):
        if request.method == "POST":
            return httpx.Response(422, json={"message": ["Source branch does not exist"]})
        return httpx.Response(200, json=[])

    diagnostics: dict = {}
    try:
        url = await ensure_mr_for_branch(
            repo=REPO,
            branch=BRANCH,
            title="t",
            body="b",
            client=_client(handler),
            target_branch="main",
            diagnostics=diagnostics,
        )
    except TypeError as exc:
        pytest.fail(f"ensure_mr_for_branch cannot say why it opened no merge request: {exc}")
    assert url is None
    why = diagnostics.get("change_request_error", "")
    assert "422" in why and "Source branch does not exist" in why, (
        f"the reason does not say what GitLab answered: {why!r}"
    )
