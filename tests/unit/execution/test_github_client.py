"""GitHubClient — headers, retry/backoff, pagination, lifecycle (#158)."""

import httpx
import pytest

from embry0.execution.github_client import API_VERSION, GITHUB_API, GitHubClient


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def _client(handler, **kwargs) -> GitHubClient:
    """Client wired to a mock transport, with sleep stubbed out."""
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GitHubClient(default_token="tok", http_client=http, sleep=_sleep, **kwargs)
    client.slept = slept  # type: ignore[attr-defined]
    return client


# --- headers ---------------------------------------------------------------


def test_headers_always_carry_the_api_version():
    """Two of the nine hand-rolled header blocks used to omit this."""
    headers = GitHubClient(default_token="tok").headers()
    assert headers["X-GitHub-Api-Version"] == API_VERSION
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["Authorization"] == "Bearer tok"


def test_headers_resolve_the_per_owner_token(monkeypatch):
    """The bug this refactor fixes: qa_check/qa_comment never did this."""
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-acme")
    client = GitHubClient(default_token="tok-default")
    assert client.headers("acme-corp/widgets")["Authorization"] == "Bearer tok-acme"
    assert client.headers("other-org/thing")["Authorization"] == "Bearer tok-default"


def test_headers_omit_authorization_without_a_token():
    assert "Authorization" not in GitHubClient(default_token="").headers()


def test_accept_override_is_honoured():
    headers = GitHubClient(default_token="t").headers(accept="application/vnd.github.raw+json")
    assert headers["Accept"] == "application/vnd.github.raw+json"


# --- url -------------------------------------------------------------------


def test_relative_paths_resolve_against_the_api_base():
    assert GitHubClient().url("/repos/o/r") == f"{GITHUB_API}/repos/o/r"
    assert GitHubClient().url("repos/o/r") == f"{GITHUB_API}/repos/o/r"


def test_absolute_urls_pass_through():
    """Link-header URLs are absolute and must not be re-prefixed."""
    absolute = f"{GITHUB_API}/repositories/1/issues?page=2"
    assert GitHubClient().url(absolute) == absolute


# --- retry -----------------------------------------------------------------


async def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    resp = await client.get("/x")
    assert resp.status_code == 200
    assert calls["n"] == 2
    assert client.slept == [7.0]  # honoured the advertised delay


async def test_retries_on_5xx_with_exponential_backoff():
    def handler(request):
        return httpx.Response(503, json={})

    client = _client(handler, max_attempts=3)
    resp = await client.get("/x")
    assert resp.status_code == 503  # returned after exhausting attempts
    assert client.slept == [1.0, 2.0]


async def test_does_not_retry_403():
    """403 is also 'your token cannot do that' — retrying just burns quota."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={})

    client = _client(handler)
    assert (await client.get("/x")).status_code == 403
    assert calls["n"] == 1


async def test_does_not_retry_404():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={})

    client = _client(handler)
    assert (await client.get("/x")).status_code == 404
    assert calls["n"] == 1


async def test_backoff_is_capped():
    """A reset an hour out must not block a webhook handler for an hour."""

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "9000"}, json={})

    client = _client(handler, max_attempts=2)
    await client.get("/x")
    assert client.slept == [30.0]


async def test_rate_limit_reset_used_only_when_remaining_is_zero():
    import time

    def handler(request):
        return httpx.Response(
            429,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 5)},
            json={},
        )

    client = _client(handler, max_attempts=2)
    await client.get("/x")
    assert 0 < client.slept[0] <= 6


async def test_transport_errors_retry_then_raise():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    client = _client(handler, max_attempts=3)
    with pytest.raises(httpx.ConnectError):
        await client.get("/x")
    assert calls["n"] == 3


# --- lifecycle -------------------------------------------------------------


async def test_injected_client_is_not_closed():
    """The long-lived app.state client is owned by the caller."""

    def handler(request):
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await GitHubClient(http_client=http).get("/x")
    assert not http.is_closed
    await http.aclose()


# --- pagination ------------------------------------------------------------


async def test_paginate_follows_link_next():
    """No caller followed Link before; only one hand-rolled page counter existed."""

    def handler(request):
        page = request.url.params.get("page")
        if page is None:
            return httpx.Response(
                200,
                json=[1, 2],
                headers={"Link": f'<{GITHUB_API}/x?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[3])

    assert await _client(handler).paginate("/x") == [1, 2, 3]


async def test_paginate_stops_at_max_pages():
    def handler(request):
        return httpx.Response(200, json=[1], headers={"Link": f'<{GITHUB_API}/x?page=9>; rel="next"'})

    items = await _client(handler).paginate("/x", max_pages=3)
    assert items == [1, 1, 1]


async def test_paginate_listing_says_whether_it_read_everything():
    """Complete when the last page has no next link; not when max_pages
    stopped it with one, nor when a non-200 ended it early."""

    def last_page(request):
        return httpx.Response(200, json=[1])

    def endless(request):
        return httpx.Response(200, json=[1], headers={"Link": f'<{GITHUB_API}/x?page=9>; rel="next"'})

    def refused_second(request):
        if request.url.params.get("page") is None:
            return httpx.Response(200, json=[1], headers={"Link": f'<{GITHUB_API}/x?page=2>; rel="next"'})
        return httpx.Response(403, json={})

    whole = await _client(last_page).paginate_listing("/x")
    capped = await _client(endless).paginate_listing("/x", max_pages=3)
    cut = await _client(refused_second, max_attempts=1).paginate_listing("/x")
    assert (whole.items, whole.complete) == ([1], True)
    assert (capped.items, capped.complete) == ([1, 1, 1], False)
    assert (cut.items, cut.complete) == ([1], False)


async def test_paginate_strict_raises_on_non_200():
    """Discovery records a failed credential only when the listing raises."""

    def handler(request):
        return httpx.Response(401, json={"message": "no"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler, max_attempts=1).paginate("/x", strict=True)


async def test_paginate_returns_what_it_has_on_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=[1], headers={"Link": f'<{GITHUB_API}/x?page=2>; rel="next"'})
        return httpx.Response(500, json={})

    # max_attempts=1 so the 500 is not retried into success
    assert await _client(handler, max_attempts=1).paginate("/x") == [1]


async def test_paginate_sets_per_page():
    seen = {}

    def handler(request):
        seen["per_page"] = request.url.params.get("per_page")
        return httpx.Response(200, json=[])

    await _client(handler).paginate("/x", per_page=50)
    assert seen["per_page"] == "50"


async def test_get_repo_default_branch_reads_the_repo_record():
    def handler(request):
        assert request.url.path == "/repos/o/r"
        return httpx.Response(200, json={"default_branch": "develop"})

    client = GitHubClient(
        default_token="ghp-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.get_repo_default_branch("o/r") == "develop"


async def test_get_repo_default_branch_falls_back_to_main_on_error():
    def handler(request):
        return httpx.Response(404, json={})

    client = GitHubClient(
        default_token="ghp-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.get_repo_default_branch("o/r") == "main"


async def test_get_repo_returns_none_instead_of_guessing_main():
    """The raw read the base-branch resolver needs: it must be able to tell
    "the repo says main" from "the call failed" (Spec 1A §3.8)."""

    def handler(request):
        assert request.url.path == "/repos/o/r"
        return httpx.Response(404, json={})

    client = GitHubClient(
        default_token="ghp-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.get_repo("o/r") is None


async def test_get_repo_returns_the_record():
    def handler(request):
        return httpx.Response(200, json={"default_branch": "develop", "id": 7})

    client = GitHubClient(
        default_token="ghp-x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert (await client.get_repo("o/r")) == {"default_branch": "develop", "id": 7}


# --- pull requests (merge approvals, #319 phase 2) ---------------------------


async def test_merge_pull_pins_the_head_sha_and_squashes():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"merged": True, "sha": "m3rg3", "message": "Pull Request successfully merged"})

    result = await _client(handler).merge_pull("o/r", 7, sha="abc123", commit_title="Fix it (#7)")
    assert seen["method"] == "PUT"
    assert seen["path"] == "/repos/o/r/pulls/7/merge"
    assert seen["body"] == {"sha": "abc123", "merge_method": "squash", "commit_title": "Fix it (#7)"}
    assert result.merged is True
    assert result.sha == "m3rg3"


async def test_merge_pull_reports_a_moved_head_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Head branch was modified. Review and try the merge again."})

    result = await _client(handler).merge_pull("o/r", 7, sha="abc123")
    assert result.status_code == 409
    assert result.merged is False
    assert "modified" in result.message


async def test_merge_pull_refuses_without_a_sha():
    """An unpinned merge would merge whatever the branch holds at that moment."""
    with pytest.raises(ValueError):
        await _client(lambda r: httpx.Response(200, json={})).merge_pull("o/r", 7, sha="")


async def test_get_pull_is_none_on_404():
    assert await _client(lambda r: httpx.Response(404, json={})).get_pull("o/r", 7) is None


async def test_list_pull_files_returns_the_paths():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"filename": "a.py"}, {"filename": "db/migrations/V9__x.sql"}])

    assert await _client(handler).list_pull_files("o/r", 7) == ["a.py", "db/migrations/V9__x.sql"]


async def test_check_runs_and_status_are_fetched_for_the_sha():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": 0, "check_runs": []})
        return httpx.Response(200, json={"state": "pending", "total_count": 0, "statuses": []})

    client = _client(handler)
    assert (await client.get_check_runs("o/r", "abc"))["total_count"] == 0
    assert (await client.get_combined_status("o/r", "abc"))["total_count"] == 0
    assert paths == ["/repos/o/r/commits/abc/check-runs", "/repos/o/r/commits/abc/status"]


# --- issues (a stack whose tracker is GitHub Issues, #378) -------------------


async def test_create_issue_sends_no_labels():
    """A labelled issue would dispatch the pipeline the moment it was filed —
    the trigger label is the human's acceptance step."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = _json.loads(request.content)
        return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/o/r/issues/7"})

    issue = await _client(handler).create_issue("o/r", title="[watcher] boom", body="why")
    assert (seen["method"], seen["path"]) == ("POST", "/repos/o/r/issues")
    assert seen["body"] == {"title": "[watcher] boom", "body": "why"}
    assert "labels" not in seen["body"]
    assert issue["number"] == 7


async def test_create_issue_returns_none_when_github_refuses():
    assert (
        await _client(lambda r: httpx.Response(410, json={"message": "Issues are disabled"})).create_issue(
            "o/r", title="t", body="b"
        )
        is None
    )


async def test_list_issues_asks_for_the_most_recently_updated():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[{"number": 1}, {"number": 2, "pull_request": {}}])

    issues = await _client(handler).list_issues("o/r", limit=5)
    assert seen == {"state": "open", "per_page": "5", "sort": "updated", "direction": "desc"}
    assert [i["number"] for i in issues] == [1, 2], "the client returns what GitHub sent; filers drop PRs"


async def test_get_issue_is_none_when_it_is_gone():
    assert await _client(lambda r: httpx.Response(404, json={})).get_issue("o/r", 9) is None
    got = await _client(lambda r: httpx.Response(200, json={"number": 9, "state": "closed"})).get_issue("o/r", 9)
    assert got["state"] == "closed"
