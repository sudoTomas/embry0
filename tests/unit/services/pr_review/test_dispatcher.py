"""PrReviewDispatcher — the five gates, dedupe and job creation (#30)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.orchestration import dispatch_actors
from embry0.orchestration.budget_gate import DispatchDecision
from embry0.services.pr_review.dispatcher import PrReviewDispatcher
from tests.fixtures.webhooks import (
    github_issue_comment,
    github_review_pull_request,
    gitlab_merge_request,
    gitlab_mr_note,
)

SHA = "a" * 40
ENABLED = {"pr_review": {"enabled": True, "depth": "full", "model": None}}


class _Repos:
    def __init__(self, registered: set[str]) -> None:
        self.registered = registered

    async def registered_as(self, repo: str) -> str | None:
        return repo if repo in self.registered else None

    async def forge_for(self, repo: str) -> str:
        return "gitlab" if repo.startswith("acme/products/") else "github"


class _Prefs:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def get(self, repo: str) -> dict[str, Any] | None:
        return self.rows.get(repo)


class _Jobs:
    def __init__(self, by_url: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.by_url = by_url or {}

    async def find_by_pr_url(self, pr_url: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.by_url.get(pr_url, [])


class _Reviews:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def exists(self, trigger_key: str) -> bool:
        return any(r["trigger_key"] == trigger_key for r in self.rows.values())

    async def claim(self, *, repo: str, number: int, head_sha: str, trigger: str, trigger_key: str) -> str | None:
        if await self.exists(trigger_key):
            return None
        review_id = f"prr-{len(self.rows) + 1}"
        self.rows[review_id] = {
            "repo": repo,
            "number": number,
            "head_sha": head_sha,
            "trigger": trigger,
            "trigger_key": trigger_key,
            "job_id": None,
        }
        return review_id

    async def attach_job(self, review_id: str, job_id: str) -> None:
        self.rows[review_id]["job_id"] = job_id

    async def release(self, review_id: str) -> None:
        self.rows.pop(review_id, None)


class _Executor:
    def __init__(self, *, refusal: DispatchDecision | None = None, fail: bool = False) -> None:
        self.refusal = refusal
        self.fail = fail
        self.started: list[dict[str, Any]] = []
        self.asked_actor: list[str | None] = []

    async def dispatch_refusal(self, *, actor: str | None = None) -> DispatchDecision | None:
        self.asked_actor.append(actor)
        return self.refusal

    async def start_pr_review_job(self, *, repo: str, review: dict[str, Any], dispatched_by: str) -> str:
        if self.fail:
            raise RuntimeError("database is down")
        self.started.append({"repo": repo, "review": review, "dispatched_by": dispatched_by})
        return f"job-{len(self.started)}"


class _DbExecutor(_Executor):
    """Creates real job rows, for the test that runs against Postgres (pr_reviews.job_id is a foreign key)."""

    def __init__(self, db: Any) -> None:
        super().__init__()
        self.db = db

    async def start_pr_review_job(self, *, repo: str, review: dict[str, Any], dispatched_by: str) -> str:
        from embry0.storage.repositories.jobs import JobsRepository

        self.started.append({"repo": repo, "review": review, "dispatched_by": dispatched_by})
        return await JobsRepository(self.db).create(
            repo=repo, task="t", pipeline_template="pr-review", pipeline_config=review, dispatched_by=dispatched_by
        )


class _GitHub:
    def __init__(self, pull: dict[str, Any] | None) -> None:
        self.pull = pull

    async def get_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        return self.pull


class _GitLab:
    def __init__(self, level: int | None) -> None:
        self.level = level
        self.asked: list[tuple[str, int]] = []

    async def get_member_access_level(self, repo: str, user_id: int) -> int | None:
        self.asked.append((repo, user_id))
        return self.level

    async def get_merge_request(self, repo: str, iid: int) -> dict[str, Any] | None:
        return gitlab_merge_request(iid=iid, project_path=repo)["object_attributes"]


def _dispatcher(
    *,
    prefs: dict[str, dict[str, Any]] | None = None,
    registered: set[str] | None = None,
    jobs: _Jobs | None = None,
    executor: _Executor | None = None,
    github: _GitHub | None = None,
    gitlab: _GitLab | None = None,
    reviews: Any = None,
) -> tuple[PrReviewDispatcher, _Executor, Any]:
    executor = executor or _Executor()
    reviews = reviews if reviews is not None else _Reviews()
    dispatcher = PrReviewDispatcher(
        config=SimpleNamespace(comment_dispatch_author_roles="OWNER,MEMBER,COLLABORATOR"),
        repos_repo=_Repos(registered if registered is not None else {"acme/widgets", "acme/products/web"}),
        prefs_repo=_Prefs(prefs if prefs is not None else {"acme/widgets": ENABLED, "acme/products/web": ENABLED}),
        jobs_repo=jobs or _Jobs(),
        reviews_repo=reviews,
        executor=executor,
        github=github,
        gitlab=gitlab,
    )
    return dispatcher, executor, reviews


# ---- not a trigger ---------------------------------------------------------


async def test_a_push_is_not_a_review_trigger() -> None:
    dispatcher, executor, _ = _dispatcher()
    assert (
        await dispatcher.handle_github_pull_request(github_review_pull_request(action="synchronize"), "synchronize")
        is None
    )
    assert executor.started == []


async def test_a_tag_only_comment_is_not_a_review_trigger() -> None:
    dispatcher, _, _ = _dispatcher()
    payload = github_issue_comment(body="@embry0 fix it", repo="acme/widgets", number=7, on_pull_request=True)
    assert await dispatcher.handle_github_comment(payload) is None


# ---- gate 1 ----------------------------------------------------------------


async def test_a_disabled_repo_is_refused() -> None:
    dispatcher, executor, _ = _dispatcher(prefs={})
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and (result.dispatched, result.reason) == (False, "review_disabled")
    assert executor.started == [] and executor.asked_actor == [], "a refused trigger must not reach the budget gate"


async def test_an_unregistered_repo_is_refused() -> None:
    dispatcher, executor, _ = _dispatcher(registered=set())
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and result.reason == "repo_not_registered"
    assert executor.started == []


# ---- gate 2 ----------------------------------------------------------------


async def test_embry0s_own_branch_is_refused() -> None:
    dispatcher, executor, _ = _dispatcher()
    payload = github_review_pull_request(head_ref="embry0/iss-abcd1234-fix-login")
    result = await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    assert result is not None and result.reason == "own_pull_request"
    assert executor.started == [] and executor.asked_actor == [], "gate 2 runs before the budget gate"


async def test_a_pull_request_a_job_opened_is_refused() -> None:
    url = "https://github.com/acme/widgets/pull/7"
    jobs = _Jobs({url: [{"job_id": "job-old", "pipeline_template": None}]})
    dispatcher, executor, _ = _dispatcher(jobs=jobs)
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and result.reason == "own_pull_request"


async def test_a_follow_up_or_an_earlier_review_does_not_make_it_ours() -> None:
    url = "https://github.com/acme/widgets/pull/7"
    jobs = _Jobs(
        {
            url: [
                {"job_id": "job-f", "pipeline_template": "pr-followup"},
                {"job_id": "job-r", "pipeline_template": "pr-review"},
            ]
        }
    )
    dispatcher, executor, _ = _dispatcher(jobs=jobs)
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and result.dispatched, f"refused as {result and result.reason}"


# ---- gate 3 ----------------------------------------------------------------


def _pull(**overrides: Any) -> dict[str, Any]:
    return {**github_review_pull_request()["pull_request"], **overrides}


async def test_a_trusted_github_comment_starts_a_command_review() -> None:
    dispatcher, executor, reviews = _dispatcher(github=_GitHub(_pull()))
    payload = github_issue_comment(body="/embry0 review", repo="acme/widgets", number=7, on_pull_request=True)
    result = await dispatcher.handle_github_comment(payload)
    assert result is not None and result.dispatched and result.job_id == "job-1"
    review = executor.started[0]["review"]
    assert (review["trigger"], review["trigger_key"], review["head_sha"]) == ("command", "command:github:1001", SHA)
    assert executor.started[0]["dispatched_by"] == "octocat" and executor.asked_actor == ["octocat"]


@pytest.mark.parametrize(
    ("association", "login", "user_type", "reason"),
    [
        ("CONTRIBUTOR", "octocat", "User", "author_association_CONTRIBUTOR_not_allowed"),
        ("NONE", "octocat", "User", "author_association_NONE_not_allowed"),
        ("OWNER", "ci[bot]", "User", "bot_author"),
        ("OWNER", "app", "Bot", "bot_author"),
    ],
)
async def test_an_untrusted_github_comment_is_refused(
    association: str, login: str, user_type: str, reason: str
) -> None:
    dispatcher, executor, _ = _dispatcher(github=_GitHub(_pull()))
    payload = github_issue_comment(
        body="/embry0 review", repo="acme/widgets", number=7, login=login, association=association, on_pull_request=True
    )
    payload["comment"]["user"]["type"] = user_type
    result = await dispatcher.handle_github_comment(payload)
    assert result is not None and (result.dispatched, result.reason) == (False, reason)
    assert executor.started == [] and executor.asked_actor == [], "gate 3 runs before the budget gate"


@pytest.mark.parametrize(("level", "dispatched"), [(20, False), (None, False), (30, True), (40, True)])
async def test_gitlab_commenters_need_developer_access(level: int | None, dispatched: bool) -> None:
    gitlab = _GitLab(level)
    dispatcher, executor, _ = _dispatcher(gitlab=gitlab)
    result = await dispatcher.handle_gitlab_note(gitlab_mr_note())
    assert result is not None and result.dispatched is dispatched, f"access level {level}: {result and result.reason}"
    assert gitlab.asked == [("acme/products/web", 42)]
    if not dispatched:
        assert result.reason.startswith("access_level_")


async def test_a_command_on_a_closed_pull_request_is_refused() -> None:
    dispatcher, executor, _ = _dispatcher(github=_GitHub(_pull(state="closed")))
    payload = github_issue_comment(body="/embry0 review", repo="acme/widgets", number=7, on_pull_request=True)
    result = await dispatcher.handle_github_comment(payload)
    assert result is not None and result.reason == "pr_not_open"


async def test_a_command_whose_pull_request_cannot_be_read_is_refused() -> None:
    dispatcher, _, _ = _dispatcher(github=_GitHub(None))
    payload = github_issue_comment(body="/embry0 review", repo="acme/widgets", number=7, on_pull_request=True)
    result = await dispatcher.handle_github_comment(payload)
    assert result is not None and result.reason == "pr_unreadable"


# ---- gate 4 ----------------------------------------------------------------


async def test_a_second_delivery_of_the_same_ready_event_is_refused() -> None:
    dispatcher, executor, _ = _dispatcher()
    payload = github_review_pull_request()
    first = await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    second = await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    assert first is not None and first.dispatched
    assert second is not None and (second.dispatched, second.reason) == (False, "duplicate")
    assert len(executor.started) == 1
    assert executor.asked_actor == ["octocat"], "a duplicate must be refused before the budget gate"


class _RacingReviews(_Reviews):
    """A second delivery that passed exists() before the first one claimed the key."""

    async def exists(self, trigger_key: str) -> bool:
        return False

    async def claim(self, *, repo: str, number: int, head_sha: str, trigger: str, trigger_key: str) -> str | None:
        if any(r["trigger_key"] == trigger_key for r in self.rows.values()):
            return None
        return await super().claim(
            repo=repo, number=number, head_sha=head_sha, trigger=trigger, trigger_key=trigger_key
        )


async def test_a_delivery_racing_past_exists_is_refused_by_the_claim() -> None:
    dispatcher, executor, _ = _dispatcher(reviews=_RacingReviews())
    payload = github_review_pull_request()
    await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    second = await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    assert second is not None and (second.dispatched, second.reason) == (False, "duplicate")
    assert len(executor.started) == 1, f"{len(executor.started)} jobs for one key: the claim must be the authority"


async def test_a_new_head_is_a_new_ready_review() -> None:
    dispatcher, executor, _ = _dispatcher()
    await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    again = await dispatcher.handle_github_pull_request(
        github_review_pull_request(head_sha="b" * 40), "ready_for_review"
    )
    assert again is not None and again.dispatched


# ---- gate 5 ----------------------------------------------------------------


async def test_over_budget_is_refused_before_anything_is_claimed() -> None:
    refusal = DispatchDecision(allowed=False, limit="daily", message="capped")
    dispatcher, executor, reviews = _dispatcher(executor=_Executor(refusal=refusal))
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and (result.dispatched, result.reason) == (False, "budget_daily")
    assert reviews.rows == {}, "a refused review must not hold its key, or a later redelivery could never run it"
    assert executor.started == []


async def test_a_ready_event_charges_the_pull_requests_author() -> None:
    dispatcher, executor, _ = _dispatcher()
    await dispatcher.handle_github_pull_request(github_review_pull_request(author="mona"), "ready_for_review")
    assert executor.asked_actor == ["mona"] and executor.started[0]["dispatched_by"] == "mona"


async def test_a_nameless_trigger_is_charged_to_the_service_actor() -> None:
    dispatcher, executor, _ = _dispatcher()
    payload = github_review_pull_request(author="")
    payload["sender"] = {}
    await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    assert executor.started[0]["dispatched_by"] == dispatch_actors.PR_REVIEW


# ---- the job ---------------------------------------------------------------


async def test_the_job_carries_the_whole_review_config() -> None:
    prefs = {"acme/widgets": {"pr_review": {"enabled": True, "depth": "targeted", "model": "claude-sonnet-5"}}}
    dispatcher, executor, reviews = _dispatcher(prefs=prefs)
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and result.dispatched
    review = executor.started[0]["review"]
    assert review == {
        "pipeline": "pr-review",
        "review_id": "prr-1",
        "trigger_key": f"ready:acme/widgets#7@{SHA}",
        "repo": "acme/widgets",
        "forge": "github",
        "number": 7,
        "url": "https://github.com/acme/widgets/pull/7",
        "title": "Add the login form",
        "body": "Adds the login form and its validation.",
        "author": "octocat",
        "head_ref": "feature/login",
        "head_sha": SHA,
        "base_ref": "main",
        "from_fork": False,
        "trigger": "ready",
        "depth": "targeted",
        "model": "claude-sonnet-5",
    }
    assert reviews.rows["prr-1"]["job_id"] == "job-1"


async def test_a_fork_is_reviewed_read_only_whatever_the_depth() -> None:
    dispatcher, executor, _ = _dispatcher()
    await dispatcher.handle_github_pull_request(
        github_review_pull_request(head_repo="someone/widgets"), "ready_for_review"
    )
    review = executor.started[0]["review"]
    assert (review["from_fork"], review["depth"]) == (True, "read_only")


async def test_a_gitlab_fork_is_reviewed_read_only() -> None:
    dispatcher, executor, _ = _dispatcher()
    await dispatcher.handle_gitlab_merge_request(gitlab_merge_request(source_project_id=9))
    review = executor.started[0]["review"]
    assert (review["forge"], review["from_fork"], review["depth"]) == ("gitlab", True, "read_only")


async def test_a_job_that_cannot_be_created_gives_its_claim_back() -> None:
    dispatcher, executor, reviews = _dispatcher(executor=_Executor(fail=True))
    result = await dispatcher.handle_github_pull_request(github_review_pull_request(), "ready_for_review")
    assert result is not None and (result.dispatched, result.reason) == (False, "dispatch_failed")
    assert reviews.rows == {}, "the claim must be released so a redelivery can try again"


# ---- manual ----------------------------------------------------------------


async def test_a_manual_review_skips_the_enabled_setting() -> None:
    dispatcher, executor, _ = _dispatcher(prefs={}, github=_GitHub(_pull()))
    result = await dispatcher.dispatch_manual(repo="acme/widgets", number=7, actor="ops@example.com")
    assert result.dispatched, result.reason
    review = executor.started[0]["review"]
    assert review["trigger"] == "command" and review["trigger_key"].startswith("command:github:manual-")
    assert executor.started[0]["dispatched_by"] == "ops@example.com"


async def test_a_manual_review_still_refuses_an_unregistered_repo() -> None:
    dispatcher, executor, _ = _dispatcher(registered=set(), github=_GitHub(_pull()))
    result = await dispatcher.dispatch_manual(repo="acme/widgets", number=7, actor="ops@example.com")
    assert (result.dispatched, result.reason) == (False, "repo_not_registered")


async def test_a_manual_gitlab_review_reads_the_merge_request() -> None:
    dispatcher, executor, _ = _dispatcher(gitlab=_GitLab(40))
    result = await dispatcher.dispatch_manual(repo="acme/products/web", number=5, actor="ops@example.com")
    assert result.dispatched, result.reason
    assert executor.started[0]["review"]["forge"] == "gitlab"


# ---- dedupe against Postgres -----------------------------------------------


@pytest.mark.requires_postgres
async def test_a_redelivery_is_refused_by_the_unique_key(db_with_migrations) -> None:
    from embry0.storage.repositories.pr_reviews import PrReviewsRepository

    reviews = PrReviewsRepository(db_with_migrations)
    dispatcher, executor, _ = _dispatcher(reviews=reviews, executor=_DbExecutor(db_with_migrations))
    payload = github_review_pull_request()

    first = await dispatcher.handle_github_pull_request(payload, "ready_for_review")
    second = await dispatcher.handle_github_pull_request(payload, "ready_for_review")

    assert first is not None and first.dispatched
    assert second is not None and second.reason == "duplicate"
    count = await db_with_migrations.fetchval("SELECT count(*) FROM pr_reviews")
    assert count == 1, f"{count} pr_reviews rows for one ready event"
