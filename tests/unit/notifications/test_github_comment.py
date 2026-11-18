"""Test GitHub-comment outbound channel."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_github_channel_posts_one_comment_with_numbered_questions():
    from embry0.notifications.github_comment import ANSWER_COMMAND_HELP, GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 999, "html_url": "https://x"})
    )

    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://embry0.local")
    issue = {
        "id": "iss-1",
        "repo": "owner/repo",
        "tracker": "github",
        "tracker_key": "42",
        "title": "Test",
    }
    questions = [
        {"question": "[design] Which approach? (a / b)", "input_id": "inp-1", "asking_node": "developer"},
        {"question": "Should I support batch?", "input_id": "inp-2", "asking_node": "developer"},
    ]
    await channel.dispatch(issue, questions)

    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.call_args
    # URL should target the github-proxy
    assert "/repos/owner/repo/issues/42/comments" in args[0] or kwargs.get("url", "").endswith(
        "/repos/owner/repo/issues/42/comments"
    )
    body = kwargs["json"]["body"]
    assert "embry0 agent" in body
    # Questions are numbered 1..N — the numbers ANSWER_COMMAND_HELP refers to.
    assert "1. [design] Which approach? (a / b)" in body
    assert "2. Should I support batch?" in body
    # #253: each question prints the id the canonical form addresses.
    assert "\n   `inp-1`" in body
    assert "\n   `inp-2`" in body
    # Inbound `/answer` syntax documented, id form first, with an example of each.
    assert ANSWER_COMMAND_HELP in body
    assert "/answer <id>: <your answer>" in body
    assert "/answer inp-a1b2c3d4e5f6: go with option b" in body
    assert "/answer <n>: <your answer>" in body
    assert "/answer 1: go with option b" in body
    # `<n>` is scoped to this comment, not to the issue.
    assert "the numbering in this comment" in body
    # Who may answer at all (#253 authorship gate).
    assert "Only repository owners, members and collaborators can answer." in body
    # The stale "inbound not supported" claim is gone.
    assert "not yet supported" not in body
    # Dashboard answer link present as the alternative
    assert "https://embry0.local" in body


@pytest.mark.asyncio
async def test_rendered_comment_never_answers_its_own_question():
    """embry0's own comment comes back through the issue_comment webhook. If
    anything it renders parses as a directive — the help text's examples, or
    agent-authored question text that starts a line with `/answer` — embry0
    answers its own question and resumes the pipeline with garbage."""
    from embry0.notifications.github_comment import GitHubCommentChannel
    from embry0.notifications.inbound_parser import parse_answer_directives

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://embry0.local")
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "tracker": "github", "tracker_key": "42"},
        [
            {"question": "Pick one. For example:\n/answer 1: x\n/skip 2", "input_id": "inp-1"},
            {"question": "And this one?", "input_id": "inp-2"},
        ],
    )

    body = fake_client.post.call_args[1]["json"]["body"]
    assert parse_answer_directives(body) == []
    # Defused, not dropped — the human still reads the question as written.
    assert "/answer 1: x" in body


@pytest.mark.asyncio
async def test_github_channel_skipped_when_issue_not_github_synced():
    """If the row has no GitHub tracker ref, the channel is a no-op (no API call)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock()
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://x")
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "tracker": None, "tracker_key": None, "title": "T"},
        [{"question": "Q", "input_id": "inp-1"}],
    )
    fake_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_channel_does_not_add_per_call_auth_header():
    """Auth lives on the injected http_client's default headers (set at app
    lifespan time with the orchestrator's GITHUB_TOKEN). The channel itself
    must not add another Authorization header per-call — that would either
    duplicate or override the client default."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://x")
    issue = {"id": "iss-1", "repo": "o/r", "tracker": "github", "tracker_key": "1", "title": "T"}
    await channel.dispatch(issue, [{"question": "Q", "input_id": "inp-1"}])

    # The HTTP client passed in IS the github-proxy-aware client; its
    # base_url + auth headers are configured at construction time. We
    # assert the channel didn't try to bypass it (no separate Bearer
    # header added in the call).
    _, kwargs = fake_client.post.call_args
    # No explicit Authorization header — proxy handles it
    assert "Authorization" not in (kwargs.get("headers") or {})


@pytest.mark.asyncio
async def test_dispatch_uses_token_resolver_per_repo():
    """When a token_resolver is provided, dispatch adds a per-request
    Authorization header derived from it (per-repo owner tokens)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(
        http_client=fake_client,
        dashboard_base_url="http://dash",
        token_resolver=lambda repo: f"tok-for-{repo.split('/', 1)[0]}",
    )
    issue = {"id": "i1", "repo": "acme-corp/widgets", "tracker": "github", "tracker_key": "7"}
    await channel.dispatch(issue, [{"question": "Q?"}])

    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok-for-acme-corp"


@pytest.mark.asyncio
async def test_dispatch_without_resolver_sends_no_per_request_auth():
    """Without a token_resolver, dispatch falls back to today's behavior:
    no per-request Authorization header (client-level default header wins)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="http://dash")
    issue = {"id": "i1", "repo": "acme-corp/widgets", "tracker": "github", "tracker_key": "7"}
    await channel.dispatch(issue, [{"question": "Q?"}])

    _, kwargs = fake_client.post.call_args
    assert "headers" not in kwargs or kwargs.get("headers") is None


@pytest.mark.asyncio
async def test_github_channel_pins_each_question_to_its_position_in_the_comment():
    """#253: the render-time ordering is what inbound /answer N: resolves
    against, so it has to be persisted when the comment is posted."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 12345, "html_url": "https://x"})
    )
    inputs_repo = MagicMock()
    inputs_repo.set_github_comment_ref = AsyncMock()

    channel = GitHubCommentChannel(
        http_client=fake_client,
        dashboard_base_url="https://embry0.local",
        inputs_repo=inputs_repo,
    )
    issue = {"id": "iss-1", "repo": "owner/repo", "tracker": "github", "tracker_key": "42"}
    await channel.dispatch(
        issue,
        [
            {"question": "Q1?", "input_id": "inp-1"},
            {"question": "Q2?", "input_id": "inp-2"},
        ],
    )

    assert inputs_repo.set_github_comment_ref.await_count == 2
    inputs_repo.set_github_comment_ref.assert_any_await("inp-1", 12345, 1)
    inputs_repo.set_github_comment_ref.assert_any_await("inp-2", 12345, 2)


@pytest.mark.asyncio
async def test_github_channel_still_delivers_when_the_mapping_write_fails():
    """The questions reached the human either way; only positional addressing
    degrades, and the input ids still resolve."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 7, "html_url": "https://x"})
    )
    inputs_repo = MagicMock()
    inputs_repo.set_github_comment_ref = AsyncMock(side_effect=RuntimeError("db down"))

    channel = GitHubCommentChannel(
        http_client=fake_client,
        dashboard_base_url="https://embry0.local",
        inputs_repo=inputs_repo,
    )
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "tracker": "github", "tracker_key": "42"},
        [{"question": "Q1?", "input_id": "inp-1"}],
    )

    fake_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_github_channel_records_nothing_when_github_rejects_the_comment():
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=403, headers={}, json=lambda: {}))
    inputs_repo = MagicMock()
    inputs_repo.set_github_comment_ref = AsyncMock()

    channel = GitHubCommentChannel(
        http_client=fake_client,
        dashboard_base_url="https://embry0.local",
        inputs_repo=inputs_repo,
    )
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "tracker": "github", "tracker_key": "42"},
        [{"question": "Q1?", "input_id": "inp-1"}],
    )

    inputs_repo.set_github_comment_ref.assert_not_awaited()


# -- retry of transient failures (#252) -----------------------------------


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _retry_channel(responses, sleeps):
    """Channel over a fake transport serving `responses` in order (last repeats)."""
    import httpx

    from embry0.notifications.github_comment import GitHubCommentChannel

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.test")
    channel = GitHubCommentChannel(
        http_client=client,
        dashboard_base_url="https://embry0.local",
        sleep=sleeps,
    )
    return channel, seen


_RETRY_ISSUE = {"id": "iss-1", "repo": "owner/repo", "tracker": "github", "tracker_key": "42", "title": "T"}
_RETRY_QUESTIONS = [{"question": "Q?", "input_id": "inp-1"}]


@pytest.mark.asyncio
async def test_github_channel_posts_once_after_two_rate_limit_refusals():
    import httpx

    sleeps = _Sleeps()
    channel, seen = _retry_channel(
        [httpx.Response(429), httpx.Response(429), httpx.Response(201, json={"html_url": "https://x"})],
        sleeps,
    )
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)

    assert len(seen) == 3
    assert len(sleeps.delays) == 2


_BOT = "embry0-bot"  # the login the channel's token posts as


def _comment_forge(
    first: str,
    *,
    unreadable: str = "",
    earlier: tuple[str, ...] = ("bot-two-days-ago", "bot-two-days-ago-edited-now", "person-now"),
):
    """GitHub for one issue. The first comment POST meets ``first``: "made-502" or "made-503" (GitHub made the
    comment and answered 5xx), "made-lost" (made, its answer lost), or "not-made-502" (answered 502 before it was
    made). Later POSTs make the comment. ``earlier``: identical comments already on the issue when the first POST
    comes: "bot-two-days-ago" (the token's own, before the window), "bot-two-days-ago-edited-now" (the same, but
    edited now, so GitHub's ``since``, which reads the update time, lists it) and "person-now" (a person's, inside
    the window).
    ``unreadable``: "comments" or "user" cannot be read. The list honours ``since`` as GitHub does (updated at)."""
    import json
    from datetime import UTC, datetime, timedelta

    import httpx

    from embry0.notifications.github_comment import GitHubCommentChannel

    def stamp(when: datetime) -> str:
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")

    comments: list[dict] = []
    seen: list[str] = []
    sleeps = _Sleeps()

    def comment(body: str, login: str, when: datetime, edited: datetime | None = None) -> dict:
        return {
            "id": len(comments) + 1,
            "body": body,
            "user": {"login": login},
            "html_url": "https://x",
            "created_at": stamp(when),
            "updated_at": stamp(edited or when),
        }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path.rsplit('/', 1)[-1]}")
        if request.method == "GET" and request.url.path == "/user":
            return httpx.Response(500) if unreadable == "user" else httpx.Response(200, json={"login": _BOT})
        if request.method == "GET":
            if unreadable == "comments":
                return httpx.Response(500)
            since = request.url.params.get("since") or ""
            return httpx.Response(200, json=[c for c in comments if c["updated_at"] >= since])
        body = json.loads(request.content)["body"]
        posts = sum(1 for s in seen if s.startswith("POST"))
        if posts == 1:
            now = datetime.now(UTC)
            if "bot-two-days-ago" in earlier:
                comments.append(comment(body, _BOT, now - timedelta(days=2)))
            if "bot-two-days-ago-edited-now" in earlier:
                comments.append(comment(body, _BOT, now - timedelta(days=2), edited=now))
            if "person-now" in earlier:
                comments.append(comment(body, "a-person", now))
        if posts == 1 and first == "not-made-502":
            return httpx.Response(502)
        made = comment(body, _BOT, datetime.now(UTC))
        comments.append(made)
        if posts == 1 and first in ("made-502", "made-503"):
            return httpx.Response(int(first[-3:]))
        if posts == 1 and first == "made-lost":
            raise httpx.ReadTimeout("the answer was lost", request=request)
        return httpx.Response(201, json=made)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.test")
    channel = GitHubCommentChannel(http_client=client, dashboard_base_url="https://x", sleep=sleeps)
    return channel, comments, seen, sleeps


def _ours_in_the_window(comments: list[dict]) -> int:
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return sum(1 for c in comments if c["user"]["login"] == _BOT and c["created_at"] >= recent)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["made-502", "made-503", "made-lost"])
async def test_a_comment_github_made_behind_a_failed_answer_is_found_not_posted_twice(first: str):
    """#45, #613: GitHub may have made the comment it answered 5xx, or whose answer was lost. The token's user and
    the issue's comments are read once: GitHub made it, so it is taken as posted, and not posted again. Neither
    the token's own identical comment from two days ago nor a person's identical one is taken for it."""
    channel, comments, seen, sleeps = _comment_forge(first)
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)
    assert seen == ["POST comments", "GET user", "GET comments"], f"{first}: {seen}"
    assert _ours_in_the_window(comments) == 1
    assert 1.5 in sleeps.delays, "the lookup does not wait for the comment to show"


@pytest.mark.asyncio
async def test_a_comment_github_did_not_make_is_posted_once_more():
    """#613: a 502 for a comment GitHub did not make no longer drops it: no comment of the token's user with its
    body was made in the window (the token's older one and a person's do not count), so it is sent once more."""
    channel, comments, seen, _ = _comment_forge("not-made-502")
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)
    assert seen == ["POST comments", "GET user", "GET comments", "POST comments"], seen
    assert _ours_in_the_window(comments) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", ["comments", "user"])
async def test_an_outcome_the_comments_cannot_tell_is_not_posted_again(unreadable: str):
    """The issue's comments, or the token's user, cannot be read: whether GitHub made the comment stays unknown,
    and a second post could make a second one. The failure is raised, and stays visible."""
    import httpx

    from embry0.notifications.retry import TransientSendError

    channel, comments, seen, _ = _comment_forge("made-502", unreadable=unreadable)
    with pytest.raises((TransientSendError, httpx.HTTPError)):
        await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)
    assert sum(1 for s in seen if s.startswith("POST")) == 1 and _ours_in_the_window(comments) == 1, seen


@pytest.mark.asyncio
async def test_github_channel_does_not_retry_a_permanent_rejection():
    """A 422 or a rejected token repeats identically — one attempt, no raise."""
    import httpx

    sleeps = _Sleeps()
    channel, seen = _retry_channel([httpx.Response(422, json={"message": "Validation Failed"})], sleeps)
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)

    assert len(seen) == 1
    assert sleeps.delays == []


@pytest.mark.asyncio
async def test_github_channel_honours_retry_after_on_429():
    import httpx

    sleeps = _Sleeps()
    channel, _ = _retry_channel(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(201, json={"html_url": "https://x"}),
        ],
        sleeps,
    )
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)

    assert sleeps.delays == [2.0]


@pytest.mark.asyncio
async def test_github_channel_raises_once_the_transient_budget_is_spent():
    """Exhausted transients stay visible: channels.dispatch_to_channels
    isolates the raise per channel and logs it."""
    import httpx

    from embry0.notifications.retry import TransientSendError

    sleeps = _Sleeps()
    channel, seen = _retry_channel([httpx.Response(429)], sleeps)
    with pytest.raises(TransientSendError):
        await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)

    assert len(seen) == 3


@pytest.mark.asyncio
async def test_github_channel_retries_a_dropped_connection():
    import httpx

    from embry0.notifications.github_comment import GitHubCommentChannel

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("github unreachable")
        return httpx.Response(201, json={"html_url": "https://x"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.test")
    channel = GitHubCommentChannel(http_client=client, dashboard_base_url="https://x", sleep=_Sleeps())
    await channel.dispatch(_RETRY_ISSUE, _RETRY_QUESTIONS)

    assert len(calls) == 2
