"""CommentDispatchService (RAV-1092) — gating, resolution, dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.pull_requests import PullRequest
from embry0.services.comment_dispatch import CommentDispatchService


def _config(**overrides):
    base = {
        "comment_dispatch_enabled": True,
        "comment_dispatch_tag": "@embry0",
        "comment_dispatch_author_roles": "OWNER,MEMBER,COLLABORATOR",
        "github_token": "tok",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _payload(
    body="@embry0 please fix the logging",
    association="MEMBER",
    login="alex",
    pr=True,
    comment_id=101,
):
    return {
        "comment": {
            "id": comment_id,
            "body": body,
            "author_association": association,
            "user": {"login": login},
        },
        "issue": {"number": 418, **({"pull_request": {"url": "x"}} if pr else {})},
        "repository": {"full_name": "acme/backend"},
    }


class _FakeExecutor:
    def __init__(self):
        self.dispatched = []
        self._issues = MagicMock(find_by_id_prefix=AsyncMock(return_value=[]))

    def submit_job(self, job_id, *, repo, title, body="", github_number=None, additional_context="", kind=""):
        self.dispatched.append(job_id)


def _service(
    *,
    config=None,
    donor_session=True,
    pr_state="open",
    jobs_by_pr=None,
    from_fork=False,
    head_ref="embry0/iss-e9077b36-eng-1135-watcher-fix-non-i",
    default_branch="main",
):
    svc = CommentDispatchService(
        config=config or _config(),
        jobs_repo=MagicMock(
            create=AsyncMock(return_value="job-new1"),
            update=AsyncMock(),
            find_by_pr_url=AsyncMock(return_value=jobs_by_pr if jobs_by_pr is not None else [{"job_id": "job-donor"}]),
            list_all=AsyncMock(return_value=([], 0)),
        ),
        agent_sessions_repo=MagicMock(get=AsyncMock(return_value={"job_id": "job-donor"} if donor_session else None)),
        executor=_FakeExecutor(),
    )
    svc._fetch_pr = AsyncMock(
        return_value=PullRequest(
            number=418,
            state=pr_state,
            url="https://github.com/acme/backend/pull/418",
            head_ref=head_ref,
            from_fork=from_fork,
            default_branch=default_branch,
        )
    )
    return svc


# ---- gate ----


def test_gate_rejects_when_disabled():
    svc = _service(config=_config(comment_dispatch_enabled=False))
    assert svc.should_dispatch(_payload()) == (False, "disabled")


def test_gate_rejects_non_pr_comments():
    assert _service().should_dispatch(_payload(pr=False))[1] == "not_a_pr_comment"


def test_gate_rejects_untagged_and_accepts_case_insensitive_tag():
    svc = _service()
    assert svc.should_dispatch(_payload(body="just a normal comment"))[1] == "no_tag"
    assert svc.should_dispatch(_payload(body="Hey @EMBRY0 do the thing"))[0] is True


def test_gate_rejects_disallowed_association_and_bots():
    svc = _service()
    assert svc.should_dispatch(_payload(association="NONE"))[0] is False
    assert svc.should_dispatch(_payload(association="CONTRIBUTOR"))[0] is False
    assert svc.should_dispatch(_payload(login="renovate[bot]"))[1] == "bot_author"


# ---- dispatch ----


@pytest.mark.asyncio
async def test_dispatch_creates_followup_job_with_ref_and_adopted_session():
    svc = _service()
    out = await svc.handle_pr_comment(_payload())

    assert out["dispatched"] is True
    assert out["adopted_session_from"] == "job-donor"
    create_kwargs = svc._jobs.create.await_args.kwargs
    assert create_kwargs["pipeline_template"] == "pr-followup"
    # #281: the comment's author is who asked for the job.
    assert create_kwargs["dispatched_by"] == _payload()["comment"]["user"]["login"]
    ctx = create_kwargs["context"]
    assert ctx["type"] == "git"
    assert ctx["ref"] == "embry0/iss-e9077b36-eng-1135-watcher-fix-non-i"
    assert ctx["resume_from_job_id"] == "job-donor"
    assert "do NOT create a new PR" in create_kwargs["task"]
    # PR link recorded immediately on the new job.
    assert svc._jobs.update.await_args.kwargs["pr_url"].endswith("/pull/418")
    assert svc._executor.dispatched == ["job-new1"]


@pytest.mark.asyncio
async def test_dispatch_without_donor_session_still_dispatches_fresh():
    svc = _service(donor_session=False, jobs_by_pr=[])
    out = await svc.handle_pr_comment(_payload())

    assert out["dispatched"] is True
    assert out["adopted_session_from"] is None
    assert "resume_from_job_id" not in svc._jobs.create.await_args.kwargs["context"]


@pytest.mark.asyncio
async def test_dispatch_rejects_closed_pr():
    svc = _service(pr_state="closed")
    out = await svc.handle_pr_comment(_payload())
    assert out == {"dispatched": False, "reason": "pr_not_open"}
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_comment_delivery_dispatches_once():
    svc = _service()
    first = await svc.handle_pr_comment(_payload(comment_id=7))
    second = await svc.handle_pr_comment(_payload(comment_id=7))

    assert first["dispatched"] is True
    assert second == {"dispatched": False, "reason": "duplicate_delivery"}
    assert svc._jobs.create.await_count == 1


@pytest.mark.asyncio
async def test_pr_fetch_failure_fails_closed():
    svc = _service()
    svc._fetch_pr = AsyncMock(side_effect=RuntimeError("github down"))
    out = await svc.handle_pr_comment(_payload())
    assert out == {"dispatched": False, "reason": "pr_fetch_failed"}
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_donor_fallback_parses_full_issue_prefix_from_branch():
    """Branch embry0/iss-e9077b36-... must resolve prefix 'iss-e9077b36' —
    the id segment contains a dash, so a split('-', 1)[0] parse breaks."""
    svc = _service(jobs_by_pr=[])
    svc._executor._issues.find_by_id_prefix = AsyncMock(return_value=[{"id": "iss-e9077b36b88e"}])
    svc._jobs.list_all = AsyncMock(return_value=([{"job_id": "job-donor"}], 1))

    out = await svc.handle_pr_comment(_payload())

    svc._executor._issues.find_by_id_prefix.assert_awaited_once_with("iss-e9077b36")
    assert out["adopted_session_from"] == "job-donor"


@pytest.mark.asyncio
async def test_sessionless_pr_url_candidate_does_not_mask_branch_fallback():
    """A prior failed pr-followup job carries the pr_url but left no session
    — the branch fallback must still find the original PR-creating job."""
    svc = _service(jobs_by_pr=[{"job_id": "job-failed-followup"}])

    async def _get(job_id, agent_type):
        return {"job_id": job_id} if job_id == "job-original" else None

    svc._sessions.get = AsyncMock(side_effect=_get)
    svc._executor._issues.find_by_id_prefix = AsyncMock(return_value=[{"id": "iss-e9077b36b88e"}])
    svc._jobs.list_all = AsyncMock(return_value=([{"job_id": "job-original"}], 1))

    out = await svc.handle_pr_comment(_payload())

    assert out["adopted_session_from"] == "job-original"
    svc._executor._issues.find_by_id_prefix.assert_awaited_once_with("iss-e9077b36")


# ---- #344: the follow-up job is linked to the PR's healing case ----


@pytest.mark.asyncio
async def test_a_follow_up_is_reported_to_the_case_ledger():
    svc = _service()
    svc._case_recorder = MagicMock(pr_followup_dispatched=AsyncMock(return_value="hc-1"))
    result = await svc.handle_pr_comment(_payload())
    assert result["dispatched"] is True
    svc._case_recorder.pr_followup_dispatched.assert_awaited_once_with(
        pr_url="https://github.com/acme/backend/pull/418", job_id="job-new1"
    )


@pytest.mark.asyncio
async def test_a_ledger_failure_never_stops_the_follow_up():
    svc = _service()
    svc._case_recorder = MagicMock(pr_followup_dispatched=AsyncMock(side_effect=RuntimeError("ledger down")))
    result = await svc.handle_pr_comment(_payload())
    assert result["dispatched"] is True
    assert svc._executor.dispatched == ["job-new1"]


@pytest.mark.asyncio
async def test_a_fork_is_followed_up_neither_for_a_comment_nor_for_a_send_back():
    """Its branch is the fork's: a follow-up would work, and be granted a push, on this repository's branch of the
    same name. Refused before any job is made, for a comment and for a send-back alike."""
    svc = _service(from_fork=True)
    out = await svc.handle_pr_comment(_payload())
    assert out == {"dispatched": False, "reason": "pr_from_fork"}
    sent_back = await svc.start_followup(
        "acme/backend", 418, request="r", dispatched_by="alex", title="t", kind="send_back"
    )
    assert sent_back == {"dispatched": False, "reason": "pr_from_fork"}
    svc._jobs.create.assert_not_awaited()


# ---- the pull request's own forge (#181) ----


def _forge_service(handler):
    """A service that reads the forge over a mock transport, with the deployment's GitLab configured."""
    import httpx

    svc = _service()
    del svc._fetch_pr  # the real read, over the transport
    svc._config = _config(
        gitlab_token="key-000000000001", gitlab_url="https://gitlab.example.test", gitlab_ca_bundle=""
    )
    svc._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return svc


@pytest.mark.asyncio
async def test_a_merge_request_is_followed_up_on_its_source_branch():
    import httpx

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if not request.url.path.endswith("/merge_requests/7"):
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(
            200,
            json={
                "state": "opened",
                "source_branch": "embry0/fix-it",
                "web_url": "https://gitlab.example.test/acme/sub/web/-/merge_requests/7",
                "source_project_id": 12,
                "target_project_id": 12,
            },
        )

    svc = _forge_service(handler)
    out = await svc.start_followup(
        "acme/sub/web", 7, request="sent back", dispatched_by="alex", title="t", kind="send_back", forge="gitlab"
    )
    assert out["dispatched"] is True, out
    assert asked == [
        "https://gitlab.example.test/api/v4/projects/acme%2Fsub%2Fweb/merge_requests/7",
        "https://gitlab.example.test/api/v4/projects/acme%2Fsub%2Fweb",
    ]
    assert svc._jobs.create.await_args.kwargs["context"]["ref"] == "embry0/fix-it"
    assert svc._jobs.update.await_args.kwargs["pr_url"] == "https://gitlab.example.test/acme/sub/web/-/merge_requests/7"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "reason"), [(404, {}, "pr_fetch_failed"), (200, {"state": "merged"}, "pr_not_open")]
)
async def test_a_merge_request_that_cannot_be_followed_up_says_why(status, body, reason):
    import httpx

    svc = _forge_service(lambda _request: httpx.Response(status, json=body))
    out = await svc.start_followup(
        "acme/sub/web", 7, request="sent back", dispatched_by="alex", title="t", kind="send_back", forge="gitlab"
    )
    assert out == {"dispatched": False, "reason": reason}
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_github_pull_request_is_still_read_on_github():
    import httpx

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "state": "open",
                "head": {"ref": "embry0/fix", "repo": {"full_name": "acme/web"}},
                "base": {"ref": "main", "repo": {"full_name": "acme/web", "default_branch": "main"}},
                "html_url": "https://github.com/acme/web/pull/7",
            },
        )

    svc = _forge_service(handler)
    out = await svc.start_followup(
        "acme/web", 7, request="sent back", dispatched_by="alex", title="t", kind="send_back"
    )
    assert out["dispatched"] is True, out
    assert asked == ["https://api.github.com/repos/acme/web/pulls/7"], "GitHub's read gives the default branch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forge", "body"),
    [
        (
            "gitlab",
            {"state": "opened", "source_branch": "main", "source_project_id": 99, "target_project_id": 12},
        ),
        (
            "github",
            {
                "state": "open",
                "head": {"ref": "main", "repo": {"full_name": "someone/web"}},
                "base": {"ref": "main", "repo": {"full_name": "acme/web"}},
            },
        ),
    ],
)
async def test_a_fork_s_pull_request_is_not_followed_up(forge, body):
    """Its branch is the fork's: a follow-up would work, and be granted a push, on the job repository's branch of the
    same name. Refused before any job is made."""
    import httpx

    svc = _forge_service(lambda _request: httpx.Response(200, json=body))
    kw = {"forge": forge} if forge == "gitlab" else {}
    out = await svc.start_followup("acme/web", 7, request="r", dispatched_by="alex", title="t", kind="send_back", **kw)
    assert out == {"dispatched": False, "reason": "pr_from_fork"}
    svc._jobs.create.assert_not_awaited()


class _Resolver:
    """The credential registry: a secret per forge for any repository; keeps what it was asked."""

    def __init__(self):
        self.asked = []

    async def resolve_forge(self, repo, forge):
        self.asked.append((repo, forge))
        return SimpleNamespace(secret=f"key-registry-{forge}")


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", [True, False], ids=["registry", "environment"])
async def test_a_merge_request_is_read_with_the_repository_s_gitlab_credential(resolver):
    """The registry's GitLab credential for the repository, else the deployment's GitLab token: never the GitHub
    token."""
    import httpx

    tokens: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append((request.headers.get("private-token"), request.headers.get("authorization")))
        if not request.url.path.endswith("/merge_requests/7"):
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(
            200, json={"state": "opened", "source_branch": "b", "source_project_id": 1, "target_project_id": 1}
        )

    svc = _forge_service(handler)
    svc._config.github_token = "key-github-000001"
    svc._config.gitlab_token = "key-gitlab-000001"
    registry = _Resolver() if resolver else None
    svc._credential_resolver = registry
    out = await svc.start_followup(
        "acme/web", 7, request="r", dispatched_by="alex", title="t", kind="send_back", forge="gitlab"
    )
    assert out["dispatched"] is True, out
    expected = "key-registry-gitlab" if resolver else "key-gitlab-000001"
    assert tokens == [(expected, None), (expected, None)], "the merge request and its project's default branch"
    if registry is not None:
        assert registry.asked == [("acme/web", "gitlab")] * 2


# ---- the repository's default branch ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head_ref", "default_branch", "reason"),
    [("main", "main", "pr_head_is_default_branch"), ("embry0/fix", None, "default_branch_unread")],
    ids=["head-is-default", "default-unread"],
)
async def test_a_pull_request_from_the_default_branch_is_not_followed_up(head_ref, default_branch, reason):
    """A follow-up is granted a push to its pull request's branch: never the repository's default branch, and not
    when which branch that is could not be read. Refused before any job is made, for a comment and a send-back."""
    svc = _service(head_ref=head_ref, default_branch=default_branch)
    assert await svc.handle_pr_comment(_payload()) == {"dispatched": False, "reason": reason}
    sent_back = await svc.start_followup(
        "acme/backend", 418, request="r", dispatched_by="alex", title="t", kind="send_back"
    )
    assert sent_back == {"dispatched": False, "reason": reason}
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forge", "answers", "reason"),
    [
        (
            "github",
            {
                "pulls/7": {
                    "state": "open",
                    "head": {"ref": "trunk", "repo": {"full_name": "acme/web"}},
                    "base": {"ref": "trunk", "repo": {"full_name": "acme/web", "default_branch": "trunk"}},
                }
            },
            "pr_head_is_default_branch",
        ),
        (
            "gitlab",
            {
                "merge_requests/7": {
                    "state": "opened",
                    "source_branch": "trunk",
                    "source_project_id": 1,
                    "target_project_id": 1,
                },
                "projects/acme%2Fweb": {"default_branch": "trunk"},
            },
            "pr_head_is_default_branch",
        ),
        (
            "gitlab",
            {
                "merge_requests/7": {
                    "state": "opened",
                    "source_branch": "fix",
                    "source_project_id": 1,
                    "target_project_id": 1,
                }
            },
            "default_branch_unread",
        ),
    ],
    ids=["github-head-is-default", "gitlab-head-is-default", "gitlab-project-unread"],
)
async def test_the_default_branch_is_read_on_the_pull_request_s_forge(forge, answers, reason):
    """GitHub's pull request read names the repository's default branch; GitLab's is the project's own read."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        for suffix, body in answers.items():
            if path.endswith(suffix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": "404 Not Found"})

    svc = _forge_service(handler)
    kw = {"forge": forge} if forge == "gitlab" else {}
    out = await svc.start_followup("acme/web", 7, request="r", dispatched_by="alex", title="t", kind="send_back", **kw)
    assert out == {"dispatched": False, "reason": reason}
    svc._jobs.create.assert_not_awaited()
