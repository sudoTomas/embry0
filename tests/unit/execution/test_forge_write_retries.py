"""The forge client sends a request again only when that cannot do anything twice (#45).

``ForgeClient.request`` retried every method on a 5xx and on any transport
error, a read timeout included: a write the forge accepted but answered 502,
or whose answer was lost, was sent again, and made two issues, two comments,
two notes. Now a failure that proves the forge did not act (a 429, a request
never sent) is retried for any method, and any other transient failure only
for a request that is safe to repeat: an idempotent method, or one whose
caller says so.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from embry0.execution.github_client import GitHubClient

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _client(first: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> tuple[GitHubClient, list[str]]:
    """A client whose first attempt meets ``first`` and every later one a 200; the methods it sent."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        return first(request) if len(sent) == 1 else httpx.Response(200, json={})

    async def _sleep(_seconds: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubClient(default_token="t", http_client=http, max_attempts=3, sleep=_sleep, **kwargs), sent


def _status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(code, json={})


def _raise(error: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    def first(request: httpx.Request) -> httpx.Response:
        raise error("the forge", request=request)

    return first


UNKNOWN = [
    _status(500),
    _status(502),
    _status(503),
    _status(504),
    _raise(httpx.ReadTimeout),
    _raise(httpx.RemoteProtocolError),
]
UNKNOWN_IDS = ["500", "502", "503", "504", "read-timeout", "dropped-answer"]
NOT_PROCESSED = [_status(429), _raise(httpx.ConnectError), _raise(httpx.ConnectTimeout), _raise(httpx.PoolTimeout)]
NOT_PROCESSED_IDS = ["429", "connect-error", "connect-timeout", "pool-timeout"]


async def _send(client: GitHubClient, method: str, **kwargs: Any) -> int | str:
    try:
        return (await client.request(method, "/repos/acme/app/issues", repo="acme/app", json={}, **kwargs)).status_code
    except httpx.TransportError as exc:
        return type(exc).__name__


@pytest.mark.parametrize("first", UNKNOWN, ids=UNKNOWN_IDS)
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_a_write_the_forge_may_have_taken_is_sent_once(method: str, first: Any) -> None:
    client, sent = _client(first)
    answer = await _send(client, method)
    assert sent == [method], f"{method} sent {len(sent)} times after an answer that leaves it unknown"
    assert answer != 200, "the failure is the caller's to see"


@pytest.mark.parametrize("first", NOT_PROCESSED, ids=NOT_PROCESSED_IDS)
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "GET"])
async def test_a_request_the_forge_did_not_act_on_is_sent_again(method: str, first: Any) -> None:
    client, sent = _client(first)
    assert await _send(client, method) == 200 and sent == [method, method]


@pytest.mark.parametrize("first", UNKNOWN, ids=UNKNOWN_IDS)
@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE"])
async def test_an_idempotent_request_is_sent_again_on_any_transient_failure(method: str, first: Any) -> None:
    client, sent = _client(first)
    assert await _send(client, method) == 200 and sent == [method, method]


@pytest.mark.parametrize("first", UNKNOWN, ids=UNKNOWN_IDS)
async def test_a_write_its_caller_says_is_safe_to_repeat_is_sent_again(first: Any) -> None:
    client, sent = _client(first)
    assert await _send(client, "POST", retry_transient=True) == 200 and sent == ["POST", "POST"]


async def test_retry_transient_false_keeps_only_the_retries_that_cannot_repeat_anything() -> None:
    client, sent = _client(_status(502))
    assert await _send(client, "GET", retry_transient=False) == 502 and sent == ["GET"]
    client, sent = _client(_status(429))
    assert await _send(client, "GET", retry_transient=False) == 200 and sent == ["GET", "GET"]


# ---- every write that opts in to repeats says why ------------------------------------------------------------

#: The forge writes that pass ``retry_transient=True``: each is safe to send again. A create (an issue, a comment,
#: a note, a pull request) never belongs here.
SAFE_TO_REPEAT = {
    ("embry0/services/github_prs.py", "/labels"): "adding a label the pull request has adds nothing",
    ("embry0/services/github_prs.py", "/pulls"): (
        "GitHub refuses a second open request for the same head and base (422), and the opener finds the first"
    ),
    ("embry0/services/gitlab_mrs.py", "/merge_requests"): (
        "GitLab refuses a second open request for the same branches (409), and the opener finds the first"
    ),
    ("embry0/services/unfinished_note.py", "/labels"): "adding a label the pull request has adds nothing",
    ("embry0/services/verification_publish.py", "/labels"): "adding a label the pull request has adds nothing",
    ("embry0/services/leads/reviews.py", "/graphql"): "a GraphQL query, sent as a POST: a read",
    ("embry0/services/github_sync.py", "/issues/"): "a PATCH that sets the issue's fields",
    ("embry0/execution/pull_requests.py", "/pulls/"): "a PATCH that closes the pull request",
    (
        "embry0/execution/pull_requests.py",
        "self._path(repo, number)",
    ): "a PUT whose state_event closes the merge request",
    ("embry0/notifications/qa_comment.py", "/issues/comments/"): "a PATCH that sets the comment's body",
}


def _opted_in() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted((ROOT / "embry0").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            if not any(k.arg == "retry_transient" and ast.unparse(k.value) == "True" for k in node.keywords):
                continue
            target = ast.unparse(node.args[0]) if node.args else ""
            key = next((key for key in SAFE_TO_REPEAT if key[0] == rel and key[1] in target), (rel, target))
            found.add(key)
    return found


def test_every_write_that_opts_in_to_repeats_is_one_that_is_safe_to_repeat() -> None:
    opted = _opted_in()
    assert opted - SAFE_TO_REPEAT.keys() == set(), (
        "a forge write passes retry_transient=True without a reason in SAFE_TO_REPEAT; a create sent twice makes "
        f"two: {sorted(opted - SAFE_TO_REPEAT.keys())}"
    )
    assert SAFE_TO_REPEAT.keys() - opted == set(), (
        f"listed but no longer opting in: {sorted(SAFE_TO_REPEAT.keys() - opted)}"
    )


# ---- opening a pull request: a create the forge made but answered 5xx is found, not lost ---------------------


def _a_forge_that_makes_the_pull_then_fails(answer: str) -> tuple[Callable[[httpx.Request], httpx.Response], list]:
    """GitHub, for one branch: the first create makes the pull request and answers 502 or loses its answer; a
    second is refused as a duplicate, as GitHub does; the lookup lists what is open."""
    pulls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=list(pulls))
        if request.method == "POST" and request.url.path == "/repos/o/r/pulls":
            if pulls:
                return httpx.Response(
                    422,
                    json={
                        "message": "Validation Failed",
                        "errors": [{"message": "A pull request already exists for o:fix."}],
                    },
                )
            pulls.append({"html_url": "https://github.com/o/r/pull/9", "number": 9})
            if answer == "502":
                return httpx.Response(502)
            raise httpx.ReadTimeout("the answer was lost", request=request)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, pulls


@pytest.mark.parametrize("answer", ["502", "lost"])
async def test_a_pull_request_github_made_but_answered_badly_is_found(answer: str) -> None:
    from embry0.services.github_prs import ensure_pr_for_branch

    handler, pulls = _a_forge_that_makes_the_pull_then_fails(answer)
    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="fix",
        title="t",
        body="b",
        github_token="t",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert url == "https://github.com/o/r/pull/9", f"the pull request GitHub made is not found: {url}"
    assert len(pulls) == 1


@pytest.mark.parametrize("answer", ["502", "lost"])
async def test_a_merge_request_gitlab_made_but_answered_badly_is_found(answer: str) -> None:
    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.gitlab_mrs import ensure_mr_for_branch

    mrs: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            if mrs:
                return httpx.Response(409, json={"message": ["Another open merge request already exists"]})
            mrs.append({"web_url": "https://gitlab.com/o/r/-/merge_requests/9", "iid": 9})
            if answer == "502":
                return httpx.Response(502)
            raise httpx.ReadTimeout("the answer was lost", request=request)
        if "/merge_requests" in str(request.url):
            return httpx.Response(200, json=list(mrs))
        return httpx.Response(200, json={"default_branch": "main"})

    async def _sleep(_seconds: float) -> None:
        return None

    client = GitLabClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=_sleep
    )
    url = await ensure_mr_for_branch(repo="o/r", branch="fix", title="t", body="b", client=client)
    assert url == "https://gitlab.com/o/r/-/merge_requests/9", f"the merge request GitLab made is not found: {url}"
    assert len(mrs) == 1
