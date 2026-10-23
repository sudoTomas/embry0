"""Merge approvals (#319 phase 2): the ask, the decision, and every refusal.

The pure parts (denylist, CI folding) need nothing; the decision tests run
against the real repository so the state machine under test is the SQL one,
with GitHub and the webhook notifier faked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.execution.github_client import MergeResult
from embry0.services.merge_approvals import (
    DASHBOARD_CHANNEL,
    DecisionRefused,
    MergeApprovalService,
    classify_checks,
    denylist_hits,
    parse_pr_url,
)
from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository

REPO = "acme/backend"
PR_URL = f"https://github.com/{REPO}/pull/42"
CHAT = "19:approval-chat@thread.v2"
HEAD = "a" * 40
NEW_HEAD = "b" * 40

GREEN_RUNS = {"total_count": 1, "check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}
NO_STATUSES = {"state": "pending", "total_count": 0, "statuses": []}


# -- denylist ----------------------------------------------------------------


def test_denylist_flags_sensitive_paths() -> None:
    hits = denylist_hits(
        [
            "platform/common/src/main/resources/db/migration/V130__add_lanes.sql",
            "platform/api/src/main/java/com/acme/backend/security/authorization/policies/QuotePolicy.java",
            "platform/api/src/main/java/com/acme/backend/integrations/auth0/Auth0Client.java",
            ".github/workflows/ci.yml",
            "infra/docker-compose.prod.yml",
            "platform/docker/Dockerfile",
            "frontend/package.json",
            "platform/pom.xml",
            "ml/.env.example",
            "platform/api/src/main/java/com/acme/backend/services/QuoteService.java",
            "docs/runbook.md",
        ]
    )
    assert set(hits) == {"migrations", "auth", "ci", "infra", "dependencies", "secrets"}
    assert hits["ci"] == [".github/workflows/ci.yml"], "the leading dot of .github must survive normalising"
    assert "platform/api/src/main/java/com/acme/backend/integrations/auth0/Auth0Client.java" in hits["auth"]
    flat = {p for paths in hits.values() for p in paths}
    assert "platform/api/src/main/java/com/acme/backend/services/QuoteService.java" not in flat
    assert "docs/runbook.md" not in flat


def test_denylist_matches_root_level_files() -> None:
    hits = denylist_hits(["package.json", "Dockerfile", ".env"])
    assert set(hits) == {"dependencies", "infra", "secrets"}


def test_an_ordinary_change_hits_nothing() -> None:
    assert denylist_hits(["platform/api/src/main/java/com/acme/backend/quotes/QuoteMapper.java"]) == {}


# -- CI folding ----------------------------------------------------------------


def test_all_completed_and_passing_is_green() -> None:
    runs = {
        "total_count": 2,
        "check_runs": [
            {"name": "build", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "skipped"},
        ],
    }
    assert classify_checks(runs, NO_STATUSES).state == "green"


def test_zero_statuses_reporting_pending_does_not_block_green_runs() -> None:
    """GitHub's combined status says "pending" when there are no statuses at all."""
    assert classify_checks(GREEN_RUNS, NO_STATUSES).state == "green"


def test_a_running_check_is_pending() -> None:
    runs = {"total_count": 1, "check_runs": [{"name": "build", "status": "in_progress", "conclusion": None}]}
    verdict = classify_checks(runs, NO_STATUSES)
    assert verdict.state == "pending"
    assert "build" in verdict.describe()


def test_a_failed_check_or_status_is_red() -> None:
    runs = {"total_count": 1, "check_runs": [{"name": "build", "status": "completed", "conclusion": "failure"}]}
    assert classify_checks(runs, NO_STATUSES).state == "red"
    statuses = {"state": "failure", "total_count": 1, "statuses": [{"context": "ci/legacy", "state": "error"}]}
    assert classify_checks(GREEN_RUNS, statuses).state == "red"


def test_no_checks_at_all_is_not_green() -> None:
    """Nothing having run must never read as everything having passed."""
    assert classify_checks({"total_count": 0, "check_runs": []}, NO_STATUSES).state == "pending"


def test_an_unreadable_or_truncated_ci_state_is_unknown() -> None:
    assert classify_checks(None, NO_STATUSES).state == "unknown"
    assert classify_checks({"total_count": 101, "check_runs": GREEN_RUNS["check_runs"]}, NO_STATUSES).state == "unknown"


def test_parse_pr_url() -> None:
    assert parse_pr_url(PR_URL) == (REPO, 42)
    assert parse_pr_url("https://gitlab.com/o/r/-/merge_requests/3") is None


# -- fakes -----------------------------------------------------------------------


class FakeGitHub:
    def __init__(self) -> None:
        self.pr: dict[str, Any] = {
            "state": "open",
            "merged": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "title": "Fix the tax rate rounding",
            "head": {"sha": HEAD, "ref": "embry0/fix-rounding"},
            "base": {"ref": "main"},
            "additions": 12,
            "deletions": 3,
            "changed_files": 2,
        }
        self.files = ["platform/api/src/main/java/com/acme/backend/quotes/QuoteMapper.java", "docs/x.md"]
        self.runs: dict[str, Any] | None = GREEN_RUNS
        self.status: dict[str, Any] | None = NO_STATUSES
        self.merge_result = MergeResult(status_code=200, merged=True, sha="m" * 40)
        self.merge_calls: list[dict[str, Any]] = []
        self.get_pull_calls = 0

    async def get_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        self.get_pull_calls += 1
        return dict(self.pr)

    async def list_pull_files(self, repo: str, number: int) -> list[str]:
        return list(self.files)

    async def get_check_runs(self, repo: str, sha: str) -> dict[str, Any] | None:
        return self.runs

    async def get_combined_status(self, repo: str, sha: str) -> dict[str, Any] | None:
        return self.status

    async def merge_pull(self, repo: str, number: int, **kwargs: Any) -> MergeResult:
        self.merge_calls.append({"repo": repo, "number": number, **kwargs})
        await asyncio.sleep(0)
        return self.merge_result


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit_soon(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


class FakeVerifications:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.row


class FakeCases:
    async def find_by_job(self, job_id: str) -> dict[str, Any] | None:
        return {"case_id": "case-7"} if job_id == "job-1" else None


def _config(**overrides: Any) -> SimpleNamespace:
    base = {"merge_approval_mode": "dry_run", "merge_approval_repos": REPO, "merge_approval_chat_id": CHAT}
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


def _svc(db, github, notifier, *, verification=None, sleep=None, **config) -> MergeApprovalService:
    return MergeApprovalService(
        repo=MergeApprovalsRepository(db),
        github=github,
        notifier=notifier,
        config=_config(**config),
        verifications=FakeVerifications(verification),
        cases=FakeCases(),
        # #400: never sleep for real in a test; the recorder says how long production would have.
        sleep=sleep or _no_sleep,
    )


async def _no_sleep(seconds: float) -> None:
    await asyncio.sleep(0)


async def _ask(svc: MergeApprovalService) -> dict[str, Any]:
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None, "the ask was not recorded"
    return row


async def _approve(svc: MergeApprovalService, approval_id: str, **kwargs: Any) -> dict[str, Any]:
    return await svc.decide(
        approval_id,
        decision=kwargs.pop("decision", "approve"),
        decided_by=kwargs.pop("decided_by", "Alex"),
        chat_id=kwargs.pop("chat_id", CHAT),
    )


pg = pytest.mark.requires_postgres


# -- the ask -----------------------------------------------------------------------


@pg
async def test_a_pr_is_asked_about_once_pinned_to_its_head(db_with_migrations, github, notifier) -> None:
    github.files = ["platform/common/src/main/resources/db/migration/V1__x.sql", "src/A.java"]
    svc = _svc(db_with_migrations, github, notifier)
    row = await _ask(svc)
    assert row["status"] == "pending"
    assert row["head_sha"] == HEAD
    assert row["case_id"] == "case-7"
    assert row["posted_at"] is None
    assert notifier.events == [], "the ask is recorded, not posted, until CI settles"

    await svc.sweep_once()
    assert notifier.names() == ["merge.approval_requested"]
    data = notifier.events[0][1]
    assert data["approval_id"] == row["id"]
    assert data["mode"] == "dry_run"
    assert data["denylisted"]["migrations"]["count"] == 1
    assert data["verification"] == {"present": False}
    assert "chat_id" not in data, "the ask must not hand the relay the chat id it is checked against"

    again = await _ask(svc)
    assert again["id"] == row["id"], "a second ask for the same commit duplicated the row"
    await svc.sweep_once()
    assert notifier.names() == ["merge.approval_requested"], "a second ask for the same commit re-posted it"


@pg
async def test_a_repo_outside_the_allowlist_is_never_asked_about(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    assert await svc.ask(job_id="job-1", pr_url="https://github.com/example-org/embry0/pull/9") is None
    assert github.get_pull_calls == 0
    assert notifier.events == []


@pg
async def test_an_ask_for_a_moved_head_supersedes_the_old_one(db_with_migrations, github, notifier) -> None:
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier)
    old = await _ask(svc)
    github.pr["head"] = {"sha": NEW_HEAD, "ref": "x"}
    new = await _ask(svc)
    assert new["id"] != old["id"]
    assert new["head_sha"] == NEW_HEAD
    assert (await repo.get(old["id"]))["status"] == "stale"


# -- who may decide -------------------------------------------------------------------


@pg
async def test_a_decision_from_another_chat_is_refused_and_changes_nothing(
    db_with_migrations, github, notifier
) -> None:
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier)
    row = await _ask(svc)
    with pytest.raises(DecisionRefused) as refused:
        await _approve(svc, row["id"], chat_id="19:some-other-chat@thread.v2")
    assert refused.value.status_code == 403
    assert (await repo.get(row["id"]))["status"] == "pending"
    assert github.merge_calls == []


@pg
async def test_no_configured_chat_refuses_every_decision(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_chat_id="")
    row = await _ask(svc)
    with pytest.raises(DecisionRefused) as refused:
        await _approve(svc, row["id"], chat_id="")
    assert refused.value.status_code == 403


@pg
async def test_a_dashboard_decision_needs_no_approval_chat(db_with_migrations, github, notifier) -> None:
    """Spec 3.8: the dashboard is a second channel. With no chat configured at all,
    an admin's decision from the dashboard is taken, recorded under the literal
    channel name, and attributed to the actor the route named."""
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier, merge_approval_chat_id="")
    row = await _ask(svc)
    result = await svc.decide(row["id"], decision="approve", decided_by="ops@example.test", chat_id="", via="dashboard")
    assert result["outcome"] == "would_merge", result
    stored = await repo.get(row["id"])
    assert stored is not None
    assert (stored["decided_by"], stored["chat_id"]) == ("ops@example.test", DASHBOARD_CHANNEL), (
        "a dashboard decision names its actor and the dashboard channel, whatever chat_id was passed"
    )


@pg
async def test_a_dashboard_reject_is_recorded_from_the_dashboard(db_with_migrations, github, notifier) -> None:
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier, merge_approval_chat_id="")
    row = await _ask(svc)
    result = await svc.decide(row["id"], decision="reject", decided_by="ops@example.test", chat_id="", via="dashboard")
    assert result["outcome"] == "rejected", result
    stored = await repo.get(row["id"])
    assert stored is not None and stored["chat_id"] == DASHBOARD_CHANNEL
    assert github.merge_calls == []


@pg
async def test_an_unknown_decision_word_is_refused(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await _ask(svc)
    with pytest.raises(DecisionRefused) as refused:
        await _approve(svc, row["id"], decision="maybe")
    assert refused.value.status_code == 422


# -- outcomes ---------------------------------------------------------------------------


@pg
async def test_reject_is_recorded_once(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await _ask(svc)
    result = await _approve(svc, row["id"], decision="reject")
    assert result["outcome"] == "rejected"
    assert "merge.rejected" in notifier.names()
    again = await _approve(svc, row["id"])
    assert again["outcome"] == "already_decided"
    assert github.merge_calls == []


@pg
async def test_dry_run_checks_everything_and_does_not_merge(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await _ask(svc)
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "would_merge"
    assert result["status"] == "approved"
    assert result["dry_run"] is True
    assert github.merge_calls == [], "dry run wrote to GitHub"
    event, data = notifier.events[-1]
    assert event == "merge.completed"
    assert data["dry_run"] is True


@pg
async def test_on_mode_squash_merges_pinned_to_the_asked_head(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "merged"
    assert result["merge_sha"] == "m" * 40
    assert github.merge_calls == [
        {
            "repo": REPO,
            "number": 42,
            "sha": HEAD,
            "method": "squash",
            "commit_title": "Fix the tax rate rounding (#42)",
        }
    ]
    assert notifier.names()[-1] == "merge.completed"


@pg
async def test_denylisted_paths_warn_but_do_not_block_a_human_merge(db_with_migrations, github, notifier) -> None:
    github.files = ["platform/common/src/main/resources/db/migration/V1__x.sql"]
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    assert (await _approve(svc, row["id"]))["outcome"] == "merged"


@pg
async def test_a_merged_ask_is_never_merged_twice(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    await _approve(svc, row["id"])
    again = await _approve(svc, row["id"])
    assert again["outcome"] == "already_decided"
    assert len(github.merge_calls) == 1


@pg
async def test_two_racing_approvals_merge_once(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    results = await asyncio.gather(_approve(svc, row["id"]), _approve(svc, row["id"], decided_by="approver-2"))
    assert sorted(r["outcome"] for r in results) == ["already_decided", "merged"]
    assert len(github.merge_calls) == 1


@pg
async def test_a_moved_head_turns_the_ask_stale_and_asks_again(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.pr["head"] = {"sha": NEW_HEAD, "ref": "x"}
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "stale"
    assert github.merge_calls == [], "merged a commit nobody was asked about"
    assert result["new_approval_id"] and result["new_approval_id"] != row["id"]
    fresh = await MergeApprovalsRepository(db_with_migrations).get(result["new_approval_id"])
    assert fresh["head_sha"] == NEW_HEAD
    assert fresh["status"] == "pending"
    assert fresh["posted_at"] is None, "the fresh ask waits for CI on the new commit"
    assert notifier.names()[-1] == "merge.stale"


@pg
async def test_running_ci_refuses_and_the_same_ask_can_be_answered_again(db_with_migrations, github, notifier) -> None:
    repo = MergeApprovalsRepository(db_with_migrations)
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.runs = {"total_count": 1, "check_runs": [{"name": "build", "status": "in_progress", "conclusion": None}]}
    refused = await _approve(svc, row["id"])
    assert refused["outcome"] == "refused"
    stored = await repo.get(row["id"])
    assert stored["status"] == "pending", "a CI refusal must leave the ask answerable"
    assert "build" in stored["detail"]
    assert github.merge_calls == []

    github.runs = GREEN_RUNS
    assert (await _approve(svc, row["id"]))["outcome"] == "merged"


@pg
@pytest.mark.parametrize(
    ("runs", "status", "needle"),
    [
        (
            {"total_count": 1, "check_runs": [{"name": "build", "status": "completed", "conclusion": "failure"}]},
            NO_STATUSES,
            "failed",
        ),
        ({"total_count": 0, "check_runs": []}, NO_STATUSES, "no CI checks"),
        (None, NO_STATUSES, "could not read"),
    ],
)
async def test_ci_that_is_not_green_refuses(db_with_migrations, github, notifier, runs, status, needle) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.runs, github.status = runs, status
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "refused"
    assert needle in result["detail"]
    assert github.merge_calls == []


@pg
async def test_a_blocking_verification_refuses(db_with_migrations, github, notifier) -> None:
    verification = {"blocking": True, "head_sha": HEAD, "mode": "on", "gate_verdict": "new_failures", "report": {}}
    svc = _svc(db_with_migrations, github, notifier, verification=verification, merge_approval_mode="on")
    row = await _ask(svc)
    assert row["ask"]["verification"]["blocking"] is True
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "refused"
    assert "verification" in result["detail"]
    assert github.merge_calls == []


@pg
@pytest.mark.parametrize(("mergeable", "needle"), [(None, "still working out"), (False, "cannot be merged")])
async def test_a_pr_github_will_not_merge_refuses(db_with_migrations, github, notifier, mergeable, needle) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.pr["mergeable"] = mergeable
    github.pr["mergeable_state"] = "dirty"
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "refused"
    assert needle in result["detail"]
    assert github.merge_calls == []


# -- #400: a null mergeable is "not yet", not a verdict ------------------------------


@pg
async def test_a_null_mergeable_is_polled_and_the_pr_merges_once_github_answers(
    db_with_migrations, github, notifier
) -> None:
    from embry0.services.merge_approvals import MERGEABLE_POLL_SECONDS

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on", sleep=sleep)
    row = await _ask(svc)
    reads_before = github.get_pull_calls
    github.pr["mergeable"] = None
    github.pr["mergeable_state"] = "unknown"

    async def computes_on_the_second_read(repo: str, number: int) -> dict[str, Any]:
        github.get_pull_calls += 1
        if github.get_pull_calls - reads_before >= 2:
            github.pr["mergeable"], github.pr["mergeable_state"] = True, "clean"
        return dict(github.pr)

    github.get_pull = computes_on_the_second_read  # type: ignore[method-assign]
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "merged", result
    assert slept == [MERGEABLE_POLL_SECONDS], "one poll, spaced by the real interval"
    assert len(github.merge_calls) == 1


@pg
async def test_a_mergeable_that_stays_null_is_refused_after_the_polls(db_with_migrations, github, notifier) -> None:
    from embry0.services.merge_approvals import MERGEABLE_POLL_ATTEMPTS, MERGEABLE_POLL_SECONDS

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on", sleep=sleep)
    row = await _ask(svc)
    reads_before = github.get_pull_calls
    github.pr["mergeable"] = None
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "refused"
    assert "still working out" in result["detail"]
    assert f"waited {int(MERGEABLE_POLL_ATTEMPTS * MERGEABLE_POLL_SECONDS)}s" in result["detail"]
    assert slept == [MERGEABLE_POLL_SECONDS] * MERGEABLE_POLL_ATTEMPTS
    assert github.get_pull_calls - reads_before == 1 + MERGEABLE_POLL_ATTEMPTS
    assert github.merge_calls == []
    assert (await svc.get(row["id"]))["status"] == "pending", "still answerable"


@pg
async def test_a_head_that_moves_during_the_poll_is_stale_not_merged(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    reads_before = github.get_pull_calls
    github.pr["mergeable"] = None

    async def moves_on_the_second_read(repo: str, number: int) -> dict[str, Any]:
        github.get_pull_calls += 1
        pr = dict(github.pr)
        if github.get_pull_calls - reads_before >= 2:
            pr["head"] = {**pr["head"], "sha": "f" * 40}
            pr["mergeable"] = True
        return pr

    github.get_pull = moves_on_the_second_read  # type: ignore[method-assign]
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "stale", result
    assert github.merge_calls == [], "a moved head must never be merged under the old approval"


@pg
async def test_a_409_from_the_merge_call_is_a_moved_head(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.merge_result = MergeResult(status_code=409, merged=False, message="Head branch was modified.")

    async def moved_after_merge_call(repo: str, number: int) -> dict[str, Any]:
        pr = dict(github.pr)
        if github.merge_calls:
            pr["head"] = {"sha": NEW_HEAD, "ref": "x"}
        return pr

    github.get_pull = moved_after_merge_call  # type: ignore[method-assign]
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "stale"
    assert result["new_approval_id"]


@pg
async def test_a_refused_merge_call_fails_and_can_be_approved_again(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.merge_result = MergeResult(status_code=405, merged=False, message="Required status check is expected.")
    failed = await _approve(svc, row["id"])
    assert failed["outcome"] == "failed"
    assert "405" in failed["detail"]
    assert notifier.names()[-1] == "merge.failed"

    github.merge_result = MergeResult(status_code=200, merged=True, sha="m" * 40)
    assert (await _approve(svc, row["id"]))["outcome"] == "merged"


@pg
async def test_a_dry_run_approval_can_be_merged_for_real_once_on(db_with_migrations, github, notifier) -> None:
    dry = _svc(db_with_migrations, github, notifier)
    row = await _ask(dry)
    assert (await _approve(dry, row["id"]))["outcome"] == "would_merge"
    live = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    assert (await _approve(live, row["id"]))["outcome"] == "merged"
    assert len(github.merge_calls) == 1


@pg
async def test_a_pr_merged_outside_embry0_is_recorded_not_merged_again(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_mode="on")
    row = await _ask(svc)
    github.pr.update(merged=True, state="closed", merge_commit_sha="e" * 40)
    result = await _approve(svc, row["id"])
    assert result["outcome"] == "merged"
    assert result["merge_sha"] == "e" * 40
    assert github.merge_calls == []


def test_an_unknown_mode_is_a_dry_run() -> None:
    svc = MergeApprovalService(repo=None, github=None, notifier=None, config=_config(merge_approval_mode="yes"))
    assert svc.mode == "dry_run"


def test_the_defaults_are_dry_run_and_no_chat() -> None:
    from embry0.config import Embry0Config

    c = Embry0Config(_env_file=None)
    assert c.merge_approval_mode == "dry_run"
    assert c.merge_approval_chat_id == ""
    # Which repos get asked about is an operator's configuration, not a
    # product default: the first deployment's repo must not ship as the
    # value every install starts with (CONTRIBUTING, vendor-neutral naming).
    assert c.merge_approval_repos == ""
