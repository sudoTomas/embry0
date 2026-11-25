"""A job's pull request on its own forge (#181): GitHub's pull requests and GitLab's merge requests, read, commented
on and closed through one shape, over each forge's own client and paths."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient
from embry0.execution.pull_requests import (
    GitHubPullRequests,
    GitLabPullRequests,
    PullRequest,
    pull_requests_for,
)

GITLAB_URL = "https://gitlab.example.test"


class Forge:
    """A forge over a mock transport: answers each request with ``answer`` and keeps (method, url, body)."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body
        self.requests: list[tuple[str, str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content) if request.content else None
        self.requests.append((request.method, str(request.url), sent))
        return httpx.Response(self.status, json=self.body if self.body is not None else {})

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _github(forge: Forge) -> GitHubPullRequests:
    return GitHubPullRequests(
        GitHubClient(
            default_token="key-000000000001",
            http_client=forge.http(),
            owner_env_tokens=False,
            token_owner="acme",
            max_attempts=1,
        )
    )


def _gitlab(forge: Forge) -> GitLabPullRequests:
    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url=GITLAB_URL,
        http_client=forge.http(),
        owner_env_tokens=False,
        token_owner="acme",
        max_attempts=1,
    )
    return GitLabPullRequests(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"state": "open", "merged": False}, "open"),
        ({"state": "closed", "merged": False}, "closed"),
        ({"state": "closed", "merged": True}, "merged"),
    ],
)
async def test_a_github_pull_request_reads_into_one_shape(body: dict[str, Any], state: str) -> None:
    forge = Forge(
        body={
            **body,
            "head": {"ref": "embry0/fix", "repo": {"full_name": "acme/web"}},
            "base": {"ref": "main", "repo": {"full_name": "acme/web"}},
            "html_url": "https://github.com/acme/web/pull/7",
            "title": "Fix it",
            "changed_files": 3,
            "additions": 40,
            "deletions": 2,
            "commits": 1,
        }
    )
    pr = await _github(forge).read("acme/web", 7)
    assert pr == PullRequest(7, state, "embry0/fix", "https://github.com/acme/web/pull/7", False, "Fix it", 3, 40, 2, 1)  # type: ignore[arg-type]
    assert forge.requests == [("GET", "https://api.github.com/repos/acme/web/pulls/7", None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "state"), [("opened", "open"), ("closed", "closed"), ("merged", "merged"), ("locked", "closed")]
)
async def test_a_gitlab_merge_request_reads_into_one_shape(raw: str, state: str) -> None:
    forge = Forge(
        body={
            "state": raw,
            "source_branch": "embry0/fix",
            "web_url": f"{GITLAB_URL}/acme/sub/web/-/merge_requests/7",
            "title": "Fix it",
            "changes_count": "1000+",
            "source_project_id": 12,
            "target_project_id": 12,
        }
    )
    pr = await _gitlab(forge).read("acme/sub/web", 7)
    assert pr == PullRequest(
        7,
        state,
        "embry0/fix",
        f"{GITLAB_URL}/acme/sub/web/-/merge_requests/7",
        False,
        "Fix it",
        files=1000,  # type: ignore[arg-type]
    ), "GitLab's read gives no line or commit counts: those are None"
    assert forge.requests == [("GET", f"{GITLAB_URL}/api/v4/projects/acme%2Fsub%2Fweb/merge_requests/7", None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make", "body"),
    [
        (
            _github,
            {
                "head": {"ref": "main", "repo": {"full_name": "someone/web"}},
                "base": {"repo": {"full_name": "acme/web"}},
            },
        ),
        (_github, {"head": {"ref": "main", "repo": None}, "base": {"repo": {"full_name": "acme/web"}}}),
        (_gitlab, {"source_branch": "main", "source_project_id": 99, "target_project_id": 12}),
        (_gitlab, {"source_branch": "main", "target_project_id": 12}),
    ],
    ids=["github-another-repository", "github-deleted-head", "gitlab-another-project", "gitlab-no-source-project"],
)
async def test_a_pull_request_from_another_repository_is_a_fork(make: Any, body: dict[str, Any]) -> None:
    """Its head branch is another repository's: read as a fork, as the reviewer reads one, an unknown source
    included."""
    pr = await make(Forge(body={"state": "open", **body})).read("acme/web", 7)
    assert pr is not None and pr.from_fork and pr.head_ref == "main"


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_github, _gitlab], ids=["github", "gitlab"])
@pytest.mark.parametrize("status", [404, 403, 500])
async def test_a_pull_request_the_forge_does_not_give_reads_as_none(make: Any, status: int) -> None:
    assert await make(Forge(status=status, body={"message": "no"})).read("acme/web", 7) is None


@pytest.mark.asyncio
async def test_github_comments_on_the_conversation_and_closes_by_state() -> None:
    forge = Forge(status=201)
    pulls = _github(forge)
    assert (await pulls.comment("acme/web", 7, "not wanted")).ok
    forge.status = 200
    assert (await pulls.close("acme/web", 7)).ok
    assert forge.requests == [
        ("POST", "https://api.github.com/repos/acme/web/issues/7/comments", {"body": "not wanted"}),
        ("PATCH", "https://api.github.com/repos/acme/web/pulls/7", {"state": "closed"}),
    ]


@pytest.mark.asyncio
async def test_gitlab_comments_with_a_note_and_closes_by_state_event() -> None:
    forge = Forge(status=201)
    merges = _gitlab(forge)
    assert (await merges.comment("acme/sub/web", 7, "not wanted\n/merge")).ok
    forge.status = 200
    assert (await merges.close("acme/sub/web", 7)).ok
    base = f"{GITLAB_URL}/api/v4/projects/acme%2Fsub%2Fweb/merge_requests/7"
    assert forge.requests == [
        ("POST", f"{base}/notes", {"body": "not wanted\n\u200b/merge"}),
        ("PUT", base, {"state_event": "close"}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_github, _gitlab], ids=["github", "gitlab"])
async def test_a_refused_write_says_so(make: Any) -> None:
    answer = await make(Forge(status=403, body={"message": "forbidden"})).close("acme/web", 7)
    assert (answer.ok, answer.status_code, answer.detail) == (False, 403, "forbidden")


def test_each_forge_has_its_pull_requests() -> None:
    assert isinstance(pull_requests_for("github", object()), GitHubPullRequests)
    assert isinstance(pull_requests_for("gitlab", object()), GitLabPullRequests)
    with pytest.raises(ValueError, match="bitbucket"):
        pull_requests_for("bitbucket", object())


@pytest.mark.asyncio
async def test_the_pull_request_s_own_repository_in_another_case_is_not_a_fork() -> None:
    body = {
        "state": "open",
        "head": {"ref": "embry0/fix", "repo": {"full_name": "ACME/Web"}},
        "base": {"ref": "main", "repo": {"full_name": "acme/web"}},
    }
    pr = await _github(Forge(body=body)).read("acme/web", 7)
    assert pr is not None and not pr.from_fork


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make", "url"),
    [(_github, "https://api.github.com/repos/acme/web"), (_gitlab, f"{GITLAB_URL}/api/v4/projects/acme%2Fweb")],
    ids=["github", "gitlab"],
)
async def test_the_default_branch_is_the_repository_s_own_read(make: Any, url: str) -> None:
    forge = Forge(body={"default_branch": "trunk"})
    assert await make(forge).default_branch("acme/web") == "trunk"
    assert forge.requests == [("GET", url, None)]
    assert await make(Forge(status=404, body={"message": "no"})).default_branch("acme/web") is None
    assert await make(Forge(body={"default_branch": ""})).default_branch("acme/web") is None


@pytest.mark.asyncio
async def test_github_s_pull_request_read_names_the_default_branch() -> None:
    body = {
        "state": "open",
        "head": {"ref": "fix", "repo": {"full_name": "acme/web"}},
        "base": {"ref": "trunk", "repo": {"full_name": "acme/web", "default_branch": "trunk"}},
    }
    pr = await _github(Forge(body=body)).read("acme/web", 7)
    assert pr is not None and pr.default_branch == "trunk"
