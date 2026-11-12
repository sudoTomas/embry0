"""RAV-1079: open-PR listing + issue-ref extraction + duplicate matching.

The extraction is deliberately loose (tokens like "phase-2" match the
tracker-id pattern) because matches are only ever compared for equality
against the job's KNOWN ticket identifiers — precision lives in the
matcher, not the scanner.
"""

import httpx
import pytest

from embry0.services.github_prs import (
    OpenPRService,
    extract_pr_issue_refs,
    find_duplicate_pr,
    format_open_prs_block,
    parse_embry0_branch_issue_prefix,
)

# ---- branch-convention parser (shared with webhooks + comment_dispatch) ----


def test_parse_branch_prefix_iss_era():
    assert parse_embry0_branch_issue_prefix("embry0/iss-eafd1fec-eng-1066-watcher") == "iss-eafd1fec"


def test_parse_branch_prefix_legacy_hex_era():
    assert parse_embry0_branch_issue_prefix("embry0/abcd1234-some-slug") == "iss-abcd1234"


def test_parse_branch_prefix_foreign_branches():
    assert parse_embry0_branch_issue_prefix("someone/eng-1176-msgraph") is None
    assert parse_embry0_branch_issue_prefix("main") is None
    assert parse_embry0_branch_issue_prefix("") is None


def test_parse_branch_prefix_never_yields_iss_iss():
    # The historical bug: split("-", 1)[0] on "iss-eafd1fec-…" gave "iss",
    # producing the unmatchable prefix "iss-iss".
    assert parse_embry0_branch_issue_prefix("embry0/iss-eafd1fec-x") != "iss-iss"


# ---- ref extraction ----


def test_extract_refs_from_branch_title_and_body_markers():
    refs = extract_pr_issue_refs(
        "someone/eng-1176-msgraph-log-context",
        "fix(msgraph): add context (ENG-1176)",
        "Some text.\n\nFixes ENG-1176\nCloses #42",
    )
    assert refs["issue_refs"] == ["ENG-1176"]
    assert refs["closes_numbers"] == [42]
    assert refs["issue_prefix"] is None


def test_extract_refs_bare_body_mentions_do_not_count():
    refs = extract_pr_issue_refs("feature/x", "improve logging", "relates to ENG-999 but does not fix it")
    assert refs["issue_refs"] == []
    assert refs["closes_numbers"] == []


def test_extract_refs_embry0_branch_prefix():
    refs = extract_pr_issue_refs("embry0/iss-0011aabb-fix-thing", "fix thing", "")
    assert refs["issue_prefix"] == "iss-0011aabb"


# ---- duplicate matcher ----

_PRS = [
    {
        "number": 366,
        "title": "fix(msgraph): stop stub deals",
        "head_ref": "embry0/iss-eafd1fec-eng-1066-watcher-msgraphpr",
        "url": "https://github.com/o/r/pull/366",
        "issue_refs": ["ENG-1066"],
        "closes_numbers": [],
        "issue_prefix": "iss-eafd1fec",
    },
    {
        "number": 400,
        "title": "unrelated",
        "head_ref": "feature/other",
        "url": "https://github.com/o/r/pull/400",
        "issue_refs": [],
        "closes_numbers": [17],
        "issue_prefix": None,
    },
]


def test_find_duplicate_by_linear_identifier():
    dup = find_duplicate_pr(_PRS, linear_identifier="eng-1066")
    assert dup is not None and dup["number"] == 366
    assert "ENG-1066" in dup["matched_by"]


def test_find_duplicate_by_github_closes_number():
    dup = find_duplicate_pr(_PRS, github_number=17)
    assert dup is not None and dup["number"] == 400


def test_find_duplicate_by_issue_id_prefix():
    dup = find_duplicate_pr(_PRS, issue_id="iss-eafd1fec0123deadbeef")
    assert dup is not None and dup["number"] == 366
    assert "iss-eafd1fec" in dup["matched_by"]


def test_find_duplicate_no_match():
    assert find_duplicate_pr(_PRS, linear_identifier="ENG-9999", github_number=1, issue_id="iss-ffffffff") is None


def test_find_duplicate_no_keys_never_matches():
    assert find_duplicate_pr(_PRS) is None


# ---- prompt block ----


def test_format_block_empty():
    assert format_open_prs_block([]) == ""


def test_format_block_lists_and_caps():
    many = [{**_PRS[0], "number": n} for n in range(1, 25)]
    block = format_open_prs_block(many, max_entries=20)
    assert block.startswith("Existing OPEN pull requests")
    assert "#1 " in block and "#20 " in block
    assert "#21 " not in block
    assert "4 more open PRs" in block
    assert "refs ENG-1066" in block


# ---- service (fail-open fetch) ----


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_open_prs_extracts_refs():
    def handler(request):
        assert request.url.path == "/repos/o/r/pulls"
        assert request.url.params["state"] == "open"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 366,
                    "title": "fix(msgraph): stop stub deals (ENG-1066)",
                    "html_url": "https://github.com/o/r/pull/366",
                    "user": {"login": "someone"},
                    "head": {"ref": "embry0/iss-eafd1fec-eng-1066-watcher"},
                    "body": "Closes #12",
                }
            ],
        )

    prs = await OpenPRService(github_token="tok", http_client=_client(handler)).list_open_prs("o/r")
    assert len(prs) == 1
    assert prs[0]["number"] == 366
    assert prs[0]["issue_refs"] == ["ENG-1066"]
    assert prs[0]["closes_numbers"] == [12]
    assert prs[0]["issue_prefix"] == "iss-eafd1fec"
    assert prs[0]["author"] == "someone"


@pytest.mark.asyncio
async def test_list_open_prs_api_failure_raises_instead_of_reading_as_none_open():
    """#305: an empty list would say "no open PRs" and let a job open a
    duplicate. A failed listing raises; the caller fails closed."""
    from embry0.services.github_prs import OpenPRListError

    def handler(request):
        return httpx.Response(403, json={"message": "rate limited"})

    with pytest.raises(OpenPRListError, match=r"o/r \(HTTP 403\)") as caught:
        await OpenPRService(github_token="tok", http_client=_client(handler)).list_open_prs("o/r")
    assert "tok" not in str(caught.value)
    assert "api.github.com" not in str(caught.value), "the request URL must not reach the job row"


@pytest.mark.asyncio
async def test_list_open_prs_non_list_payload_raises():
    from embry0.services.github_prs import OpenPRListError

    def handler(request):
        return httpx.Response(200, json={"message": "not a list"})

    with pytest.raises(OpenPRListError, match="not a list"):
        await OpenPRService(github_token="tok", http_client=_client(handler)).list_open_prs("o/r")


@pytest.mark.asyncio
async def test_list_open_prs_empty_listing_is_a_plain_empty_list():
    def handler(request):
        return httpx.Response(200, json=[])

    assert await OpenPRService(github_token="tok", http_client=_client(handler)).list_open_prs("o/r") == []


@pytest.mark.asyncio
async def test_list_open_prs_rejects_bad_repo():
    assert await OpenPRService(github_token="tok").list_open_prs("not-a-repo") == []


# ---- RAV-1247: ensure_pr_for_branch (orchestrator-side PR fallback) ----

import json as _json  # noqa: E402

from embry0.services.github_prs import ensure_pr_for_branch  # noqa: E402


@pytest.mark.asyncio
async def test_ensure_pr_creates_when_none_exists():
    """No open PR for the head: look up the default branch and create."""
    posted = {}

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            assert request.url.params["head"] == "o:embry0/iss-abc-fix"
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "develop"})
        if request.method == "POST" and request.url.path == "/repos/o/r/pulls":
            posted.update(_json.loads(request.content))
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/9"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="fix the widget",
        body="Closes #12",
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/9"
    assert posted == {
        "title": "fix the widget",
        "head": "embry0/iss-abc-fix",
        "base": "develop",
        "body": "Closes #12",
    }


@pytest.mark.asyncio
async def test_ensure_pr_returns_existing_pr_without_creating():
    """Developer opened the PR but failed to report pr_url: recover the URL,
    never POST a duplicate."""

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7"}])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/7"


@pytest.mark.asyncio
async def test_ensure_pr_is_fail_open_on_api_error():
    def handler(request):
        return httpx.Response(403, json={"message": "rate limited"})

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        github_token="tok",
        http_client=_client(handler),
    )
    assert url is None


@pytest.mark.asyncio
async def test_ensure_pr_guards_bad_inputs():
    """No token, slashless repo, empty branch, or a default-branch head all
    skip without any HTTP traffic."""

    def handler(request):
        raise AssertionError("no HTTP call expected")

    for kwargs in (
        {"repo": "o/r", "branch": "embry0/x", "github_token": ""},
        {"repo": "not-a-repo", "branch": "embry0/x", "github_token": "tok"},
        {"repo": "o/r", "branch": "", "github_token": "tok"},
        {"repo": "o/r", "branch": "main", "github_token": "tok"},
        {"repo": "o/r", "branch": "master", "github_token": "tok"},
    ):
        assert (await ensure_pr_for_branch(title="t", body="b", http_client=_client(handler), **kwargs)) is None


# ---- #163: best-effort PR labels (orchestrator-side, replaces the prompt ask) --


@pytest.mark.asyncio
async def test_ensure_pr_applies_labels_on_create():
    labeled = {}

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "POST" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/9", "number": 9})
        if request.method == "POST" and request.url.path == "/repos/o/r/issues/9/labels":
            labeled.update(_json.loads(request.content))
            return httpx.Response(200, json=[{"name": "embry0"}])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        labels=["embry0"],
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/9"
    assert labeled == {"labels": ["embry0"]}


@pytest.mark.asyncio
async def test_ensure_pr_applies_labels_on_found_existing():
    """A developer-opened PR that only lost its pr_url report still gets the
    label — adding labels is idempotent, so found-existing PRs are labeled too."""
    labeled = {}

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7", "number": 7}])
        if request.method == "POST" and request.url.path == "/repos/o/r/issues/7/labels":
            labeled.update(_json.loads(request.content))
            return httpx.Response(200, json=[{"name": "embry0"}])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        labels=["embry0"],
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/7"
    assert labeled == {"labels": ["embry0"]}


@pytest.mark.asyncio
async def test_ensure_pr_label_failure_does_not_fail_the_pr():
    """Labels are best-effort: a labels-API failure must never cost the caller
    the PR URL."""

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7", "number": 7}])
        if request.method == "POST" and request.url.path == "/repos/o/r/issues/7/labels":
            return httpx.Response(403, json={"message": "forbidden"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        labels=["embry0"],
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/7"


@pytest.mark.asyncio
async def test_ensure_pr_without_labels_skips_the_labels_api():
    """No labels param → no label POST, even when the PR number is known."""

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7", "number": 7}])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        github_token="tok",
        http_client=_client(handler),
    )
    assert url == "https://github.com/o/r/pull/7"


@pytest.mark.asyncio
async def test_ensure_pr_uses_an_explicit_target_branch():
    seen = {}

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.path == "/repos/o/r":
            raise AssertionError("must not read the repo record when a target is given")
        if request.method == "POST" and request.url.path == "/repos/o/r/pulls":
            seen.update(_json.loads(request.content))
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/11", "number": 11})
        if request.method == "POST" and request.url.path == "/repos/o/r/issues/11/labels":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    url = await ensure_pr_for_branch(
        repo="o/r",
        branch="embry0/iss-abc-fix",
        title="t",
        body="b",
        github_token="tok",
        http_client=_client(handler),
        target_branch="dev",
    )
    assert url.endswith("/pull/11")
    assert seen["base"] == "dev"


@pytest.mark.asyncio
async def test_ensure_pr_skips_when_the_head_equals_the_resolved_base():
    # Counted, not merely asserted: ensure_pr_for_branch is fail-open, so an
    # AssertionError from the handler would be swallowed and the test would
    # pass whether or not the guard fired.
    posted = {"n": 0}

    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/o/r/pulls":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "develop"})
        posted["n"] += 1
        raise AssertionError("must not create a PR from develop into develop")

    assert (
        await ensure_pr_for_branch(
            repo="o/r", branch="develop", title="t", body="b", github_token="tok", http_client=_client(handler)
        )
        is None
    )
    assert posted["n"] == 0


@pytest.mark.asyncio
async def test_ensure_pr_says_what_github_answered_when_it_opens_none():
    """#459: the PR stays None, and the caller that asks gets GitHub's own answer, not a silent skip."""

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [
                        {"resource": "PullRequest", "code": "custom", "message": "No commits between main and x"}
                    ],
                },
            )
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"default_branch": "main"})

    diagnostics: dict = {}
    try:
        url = await ensure_pr_for_branch(
            repo="o/r",
            branch="embry0/iss-abc-fix",
            title="t",
            body="b",
            github_token="tok",
            http_client=_client(handler),
            target_branch="main",
            diagnostics=diagnostics,
        )
    except TypeError as exc:
        pytest.fail(f"ensure_pr_for_branch cannot say why it opened no PR: {exc}")
    assert url is None
    why = diagnostics.get("change_request_error", "")
    assert "422" in why and "Validation Failed" in why and "No commits between main and x" in why, (
        f"the reason does not say what GitHub answered: {why!r}"
    )
    for kwargs, said in (
        ({"repo": "o/r", "branch": "embry0/x", "github_token": ""}, "no GitHub token"),
        ({"repo": "o/r", "branch": "main", "github_token": "tok"}, "is the base branch"),
    ):
        skipped: dict = {}
        assert await ensure_pr_for_branch(title="t", body="b", diagnostics=skipped, **kwargs) is None
        assert said in skipped.get("change_request_error", ""), (kwargs, skipped)
