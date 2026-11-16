"""The merge request embry0 takes as a job's is one into the job's target branch (#524).

``ensure_mr_for_branch`` looked for the open merge request of the job's
branch by its source branch only, and took the first GitLab listed. A branch
with an open merge request into another branch (``release``, say) had that one
taken as the job's, before the create and again when the create answered 409,
and the job's labels went on it. The lookup now asks for the target too, and
passes over a listed merge request that names another.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from embry0.execution.gitlab_client import GitLabClient
from embry0.services.gitlab_mrs import ensure_mr_for_branch, find_open_mr_for_branch

REPO = "acme/widgets"
BRANCH = "embry0/iss-1-fix"
INTO_RELEASE = {
    "iid": 9,
    "web_url": "https://gitlab.example/acme/widgets/-/merge_requests/9",
    "target_branch": "release",
}
INTO_MAIN = {"iid": 4, "web_url": "https://gitlab.example/acme/widgets/-/merge_requests/4", "target_branch": "main"}
CREATED = "https://gitlab.example/acme/widgets/-/merge_requests/12"


class Forge:
    """A project's open merge requests from one branch; ``filters`` says whether the listing honours ``target_branch``.

    ``conflict`` answers the create with 409, as GitLab does when the branch
    already has an open merge request into that target.
    """

    def __init__(
        self, open_mrs: list[dict[str, Any]], *, filters: bool = True, conflict: bool = False, default: str = "main"
    ):
        self.open_mrs, self.filters, self.conflict, self.default = open_mrs, filters, conflict, default
        self.listed: list[dict[str, str]] = []
        self.created: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/merge_requests") and request.method == "GET":
            params = dict(request.url.params)
            self.listed.append(params)
            mrs = [mr for mr in self.open_mrs if mr.get("source_branch", BRANCH) == params.get("source_branch")]
            if self.filters and "target_branch" in params:
                mrs = [mr for mr in mrs if mr.get("target_branch") == params["target_branch"]]
            return httpx.Response(200, json=mrs)
        if path.endswith("/merge_requests") and request.method == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            if self.conflict:
                return httpx.Response(409, json={"message": ["Another open merge request already exists"]})
            return httpx.Response(201, json={"iid": 12, "web_url": CREATED, "target_branch": body["target_branch"]})
        if request.method == "GET" and path.endswith(("/projects/acme%2Fwidgets", "/projects/acme/widgets")):
            return httpx.Response(200, json={"id": 1, "default_branch": self.default})
        return httpx.Response(404, json={})

    def client(self) -> GitLabClient:
        return GitLabClient(
            default_token="glpat-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )


async def _ensure(forge: Forge, **kwargs: Any) -> tuple[str | None, dict[str, Any]]:
    diagnostics: dict[str, Any] = {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), diagnostics=diagnostics, **kwargs
    )
    return url, diagnostics


async def test_the_lookup_asks_for_the_jobs_target() -> None:
    forge = Forge([])
    url, _ = await _ensure(forge, target_branch="main")
    assert url == CREATED
    assert forge.listed and all(p.get("target_branch") == "main" for p in forge.listed), (
        f"the open merge request is looked up without the job's target: {forge.listed}"
    )


async def test_a_merge_request_into_another_branch_is_not_taken_as_the_jobs() -> None:
    """The branch's only open merge request is into ``release``: the job, into ``main``, opens its own."""
    for filters in (True, False):
        forge = Forge([INTO_RELEASE], filters=filters)
        url, _ = await _ensure(forge, target_branch="main")
        assert url == CREATED, f"the job took a merge request into release as its own (filters={filters}): {url}"
        assert [c["target_branch"] for c in forge.created] == ["main"], forge.created


async def test_of_two_open_merge_requests_the_one_into_the_target_is_the_jobs() -> None:
    """Newest first into ``release``, then the job's into ``main``: the one into ``main`` is found, none opened."""
    for filters in (True, False):
        forge = Forge([INTO_RELEASE, INTO_MAIN], filters=filters)
        url, _ = await _ensure(forge, target_branch="main")
        assert url == INTO_MAIN["web_url"], f"the lookup took the first listed (filters={filters}): {url}"
        assert forge.created == [], "no duplicate"


async def test_recovery_after_a_conflict_takes_only_a_merge_request_into_the_target() -> None:
    """The issue's case: the create answers 409, and the branch's only open merge request is into ``release``."""
    for filters in (True, False):
        forge = Forge([INTO_RELEASE], filters=filters, conflict=True)
        url, diagnostics = await _ensure(forge, target_branch="main")
        assert url is None, f"recovered after 409: {url} | list params: {forge.listed[-1]}"
        assert "409" in diagnostics.get("change_request_error", ""), diagnostics

    raced = Forge([INTO_RELEASE], conflict=True)
    # Someone opens the job's merge request between the lookup and the create.
    original = raced.handler

    def racing(request: httpx.Request) -> httpx.Response:
        answer = original(request)
        if request.method == "POST":
            raced.open_mrs.append(INTO_MAIN)
        return answer

    client = GitLabClient(default_token="glpat-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(racing)))
    url = await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=client, target_branch="main")
    assert url == INTO_MAIN["web_url"], f"the raced merge request into main is not recovered: {url}"


async def test_without_a_target_the_projects_default_is_asked_for() -> None:
    """No explicit target: the default branch is read first, and only a merge request into it is the job's."""
    forge = Forge([INTO_RELEASE, INTO_MAIN], filters=False, default="main")
    url, _ = await _ensure(forge)
    assert url == INTO_MAIN["web_url"], f"without a target, the lookup took a merge request off the default: {url}"
    assert forge.listed[0].get("target_branch") == "main", forge.listed

    forge = Forge([INTO_RELEASE], default="develop")
    url, _ = await _ensure(forge)
    assert url == CREATED and [c["target_branch"] for c in forge.created] == ["develop"], forge.created


async def test_find_open_mr_for_branch_takes_a_target() -> None:
    forge = Forge([INTO_RELEASE, INTO_MAIN], filters=False)
    assert await find_open_mr_for_branch(forge.client(), REPO, BRANCH, "main") == INTO_MAIN["web_url"]
    assert await find_open_mr_for_branch(forge.client(), REPO, BRANCH, "next") is None
    assert await find_open_mr_for_branch(forge.client(), REPO, BRANCH) == INTO_RELEASE["web_url"], (
        "without a target, any open merge request from the branch is found, as before"
    )
