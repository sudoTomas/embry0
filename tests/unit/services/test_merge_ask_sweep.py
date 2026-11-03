"""The ask sweep: post once CI settles, and retire asks nobody answered.

Real repository (the SQL is the state machine), GitHub and the notifier faked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.merge_approvals import MergeApprovalService
from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository
from tests.unit.services.test_merge_approvals import (
    GREEN_RUNS,
    HEAD,
    NEW_HEAD,
    PR_URL,
    FakeCases,
    FakeGitHub,
    FakeNotifier,
    FakeVerifications,
    _config,
)
from tests.unit.services.test_merge_recommendation import REVIEW_JSON

pytestmark = pytest.mark.requires_postgres

RUNNING = {"total_count": 1, "check_runs": [{"name": "build", "status": "in_progress", "conclusion": None}]}
RED = {"total_count": 1, "check_runs": [{"name": "build", "status": "completed", "conclusion": "failure"}]}


class FakeJobs:
    def __init__(self, result_summary: str | None = REVIEW_JSON) -> None:
        self.result_summary = result_summary

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return {"job_id": job_id, "result_summary": self.result_summary}


def _svc(db, github, notifier, *, jobs=None, **config) -> MergeApprovalService:
    return MergeApprovalService(
        repo=MergeApprovalsRepository(db),
        github=github,
        notifier=notifier,
        config=_config(**config),
        verifications=FakeVerifications(None),
        cases=FakeCases(),
        jobs=jobs if jobs is not None else FakeJobs(),
    )


@pytest.fixture
def github() -> FakeGitHub:
    gh = FakeGitHub()
    gh.runs = RUNNING
    return gh


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


async def _age(db, approval_id: str, **cols: timedelta) -> None:
    """Move a row's timestamps into the past."""
    for col, delta in cols.items():
        await db.execute(f"UPDATE merge_approvals SET {col} = {col} - $2::interval WHERE id = $1", approval_id, delta)


async def _get(db, approval_id: str) -> dict[str, Any]:
    row = await MergeApprovalsRepository(db).get(approval_id)
    assert row is not None
    return row


# -- posting ---------------------------------------------------------------------


async def test_the_ask_waits_for_ci_then_posts_with_its_recommendation(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None

    done = await svc.sweep_once()
    assert done["waiting"] == 1
    assert notifier.events == [], "posted while CI was still running"

    github.runs = GREEN_RUNS
    done = await svc.sweep_once()
    assert done["posted"] == 1
    assert notifier.names() == ["merge.approval_requested"]
    data = notifier.events[0][1]
    assert data["summary"].startswith("Focused, well-tested fix")
    assert data["summary_source"] == "review"
    assert data["recommendation"]["verdict"] == "review_first", "the reviewer left one important comment"
    assert "1 important review comment(s)" in data["recommendation"]["reasons"]
    assert "CI green (1 checks)" in data["recommendation"]["positives"]
    assert data["checks"] == "all 1 checks passed"

    stored = await _get(db_with_migrations, row["id"])
    assert stored["posted_at"] is not None
    assert stored["ask"]["recommendation"] == data["recommendation"], "the row keeps what the chat was told"

    await svc.sweep_once()
    assert notifier.names() == ["merge.approval_requested"], "posted twice"


async def test_red_ci_posts_at_once_as_dont_merge(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    await svc.ask(job_id="job-1", pr_url=PR_URL)
    github.runs = RED
    await svc.sweep_once()
    assert notifier.events[0][1]["recommendation"]["verdict"] == "dont_merge"


async def test_ci_that_never_settles_is_posted_after_the_cap(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier, merge_approval_ci_wait_minutes=45)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None
    await _age(db_with_migrations, row["id"], created_at=timedelta(minutes=44))
    await svc.sweep_once()
    assert notifier.events == [], "posted before the cap"

    await _age(db_with_migrations, row["id"], created_at=timedelta(minutes=2))
    await svc.sweep_once()
    data = notifier.events[0][1]
    assert data["recommendation"]["verdict"] == "review_first"
    assert "still not finished after 45 min" in data["checks"]


async def test_a_pr_without_an_embry0_review_falls_back_to_its_body(db_with_migrations, github, notifier) -> None:
    github.pr["body"] = "Rounds tax rates half-even so invoices stop drifting by a cent."
    github.runs = GREEN_RUNS
    svc = _svc(db_with_migrations, github, notifier, jobs=FakeJobs(None))
    await svc.ask(job_id=None, pr_url=PR_URL)
    await svc.sweep_once()
    data = notifier.events[0][1]
    assert data["summary"] == "Rounds tax rates half-even so invoices stop drifting by a cent."
    assert data["summary_source"] == "pr_body"
    assert any("no embry0 review" in r for r in data["recommendation"]["reasons"])


async def test_a_push_before_posting_re_pins_the_ask_silently(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    old = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert old is not None
    github.pr["head"] = {"sha": NEW_HEAD, "ref": "x"}
    done = await svc.sweep_once()
    assert done["restaled"] == 1
    assert (await _get(db_with_migrations, old["id"]))["status"] == "stale"
    assert notifier.events == [], "a re-pin before anyone saw the ask should not reach the chat"

    github.runs = GREEN_RUNS
    await svc.sweep_once()
    assert notifier.events[0][1]["head_sha"] == NEW_HEAD


# -- retiring --------------------------------------------------------------------


async def test_an_unposted_ask_whose_pr_merged_elsewhere_is_closed_unposted(
    db_with_migrations, github, notifier
) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None
    github.pr.update(merged=True, state="closed", merge_commit_sha="e" * 40)
    done = await svc.sweep_once()
    assert done["closed"] == 1
    stored = await _get(db_with_migrations, row["id"])
    assert stored["status"] == "closed"
    assert "merged outside embry0" in stored["detail"]
    assert notifier.events == []


async def _posted(db, github, notifier) -> tuple[MergeApprovalService, dict[str, Any]]:
    svc = _svc(db, github, notifier)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None
    github.runs = GREEN_RUNS
    await svc.sweep_once()
    assert notifier.names() == ["merge.approval_requested"]
    return svc, row


async def test_a_posted_ask_whose_pr_was_closed_retires_without_a_reply(db_with_migrations, github, notifier) -> None:
    svc, row = await _posted(db_with_migrations, github, notifier)
    github.pr.update(state="closed")
    await svc.sweep_once()
    assert (await _get(db_with_migrations, row["id"]))["status"] == "pending", "rechecked before 10 minutes"

    await _age(db_with_migrations, row["id"], updated_at=timedelta(minutes=11))
    await svc.sweep_once()
    stored = await _get(db_with_migrations, row["id"])
    assert stored["status"] == "closed"
    assert stored["detail"] == "the PR was closed without merging"


async def test_a_posted_ask_nobody_answers_expires(db_with_migrations, github, notifier) -> None:
    svc, row = await _posted(db_with_migrations, github, notifier)
    await _age(db_with_migrations, row["id"], posted_at=timedelta(days=8), updated_at=timedelta(days=8))
    done = await svc.sweep_once()
    assert done["expired"] == 1
    stored = await _get(db_with_migrations, row["id"])
    assert stored["status"] == "expired"
    assert stored["detail"] == "no answer within 7 days"


async def test_an_expired_ask_cannot_be_approved(db_with_migrations, github, notifier) -> None:
    svc, row = await _posted(db_with_migrations, github, notifier)
    await _age(db_with_migrations, row["id"], posted_at=timedelta(days=8), updated_at=timedelta(days=8))
    await svc.sweep_once()
    result = await svc.decide(row["id"], decision="approve", decided_by="T", chat_id=_config().merge_approval_chat_id)
    assert result["outcome"] == "already_decided"
    assert github.merge_calls == []


async def test_a_posted_ask_whose_head_moved_is_re_asked_after_ci(db_with_migrations, github, notifier) -> None:
    svc, row = await _posted(db_with_migrations, github, notifier)
    github.pr["head"] = {"sha": NEW_HEAD, "ref": "x"}
    github.runs = RUNNING
    await _age(db_with_migrations, row["id"], updated_at=timedelta(minutes=11))
    await svc.sweep_once()
    assert (await _get(db_with_migrations, row["id"]))["status"] == "stale"
    assert notifier.names() == ["merge.approval_requested"], "re-asked before CI ran on the new commit"

    github.runs = GREEN_RUNS
    await svc.sweep_once()
    assert notifier.names() == ["merge.approval_requested", "merge.approval_requested"]
    assert notifier.events[-1][1]["head_sha"] == NEW_HEAD


async def test_an_open_untouched_ask_is_left_alone(db_with_migrations, github, notifier) -> None:
    svc, row = await _posted(db_with_migrations, github, notifier)
    await _age(db_with_migrations, row["id"], updated_at=timedelta(minutes=11))
    await svc.sweep_once()
    stored = await _get(db_with_migrations, row["id"])
    assert stored["status"] == "pending"
    assert stored["head_sha"] == HEAD


async def test_github_unreadable_changes_nothing(db_with_migrations, github, notifier) -> None:
    svc = _svc(db_with_migrations, github, notifier)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None

    async def unreadable(repo: str, number: int) -> None:
        return None

    github.get_pull = unreadable  # type: ignore[method-assign]
    await svc.sweep_once()
    stored = await _get(db_with_migrations, row["id"])
    assert (stored["status"], stored["posted_at"]) == ("pending", None)


def test_the_new_settings_have_their_defaults() -> None:
    from embry0.config import Embry0Config

    c = Embry0Config(_env_file=None)
    assert (c.merge_approval_ci_wait_minutes, c.merge_approval_expiry_days, c.merge_approval_sweep_seconds) == (
        45,
        7,
        60,
    )


async def test_two_sweeps_holding_the_same_snapshot_post_it_once(db_with_migrations, github, notifier) -> None:
    """Two overlapping passes (a slow one and the next) can both read the row
    as unposted; only the one whose mark_posted matched may post. The fake
    GitHub never yields, so a real gather would not interleave — hand both
    passes the same snapshot instead, which is exactly the race."""
    github.runs = GREEN_RUNS
    svc = _svc(db_with_migrations, github, notifier)
    await svc.ask(job_id="job-1", pr_url=PR_URL)
    [snapshot] = await MergeApprovalsRepository(db_with_migrations).unposted_pending()
    now = datetime.now(UTC)
    for _ in range(2):
        await svc._advance_unposted(dict(snapshot), now, {"posted": 0, "waiting": 0, "closed": 0, "restaled": 0})
    assert notifier.names() == ["merge.approval_requested"], "one ask reached the chat twice"


async def test_the_ask_is_stored_as_a_json_object(db_with_migrations, github, notifier) -> None:
    """Pre-encoding with json.dumps stored every ask as a JSON *string*: the app
    read it back fine, and every `ask->>` query saw NULL (storage/database.py)."""
    github.runs = GREEN_RUNS
    svc = _svc(db_with_migrations, github, notifier)
    row = await svc.ask(job_id="job-1", pr_url=PR_URL)
    assert row is not None
    kind = "SELECT jsonb_typeof(ask) FROM merge_approvals WHERE id = $1"
    assert await db_with_migrations.fetchval(kind, row["id"]) == "object", "recorded ask is double-encoded"
    await svc.sweep_once()
    assert await db_with_migrations.fetchval(kind, row["id"]) == "object", "posted ask is double-encoded"
    verdict = await db_with_migrations.fetchval(
        "SELECT ask->'recommendation'->>'verdict' FROM merge_approvals WHERE id = $1", row["id"]
    )
    assert verdict == "review_first"
