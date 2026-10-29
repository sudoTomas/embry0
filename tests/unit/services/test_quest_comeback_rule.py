"""#234: Try, Try Again's merge step counts the merges the comeback counts, and no others.

A merge on an issue is a comeback when a job on that issue created before
the merged one failed. The comeback XP event and the achievement that counts
comebacks read that rule in SQL; the quest step "Merge a later job on that
issue" reads it from the journal. Each history below is read the way
``GET /progression`` reads it, and the three must agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.progression import ProgressionService
from embry0.services.progression_pack import ShippedPack
from embry0.storage.repositories.progression import ProgressionRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

REPO = "acme/widgets"
ISSUE = "iss-1"
T0 = datetime(2026, 3, 2, 9, tzinfo=UTC)


def hour(n: float) -> datetime:
    return T0 + timedelta(hours=n)


async def _job(db, job_id: str, status: str, *, created: datetime, finished: datetime) -> None:
    """A job on the issue; a merged one merged five minutes after it finished."""
    merged = status == "pr_merged"
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, issue_id, pr_url, delivery_state, delivery_state_at,
                          created_at, started_at, finished_at, updated_at, budget_overrun_usd)
        VALUES ($1, $2, 't', $3, $4, $5, $6, $7, $8, $8, $9, $9, 0)
        """,
        job_id,
        REPO,
        status,
        ISSUE,
        f"https://forge/{REPO}/pull/1" if merged else None,
        "merged" if merged else "none",
        finished + timedelta(minutes=5) if merged else None,
        created,
        finished,
    )


# The failed job's (created, finished), the merged job's (created, finished), whether it is a comeback, and
# when the quest's merge step is done.
HISTORIES = [
    pytest.param(
        (hour(0), hour(1)), (hour(2), hour(3)), True, hour(3) + timedelta(minutes=5), id="retry-after-failure"
    ),
    # The merged job was created first: it is not a later job, though the failure landed before it merged.
    pytest.param((hour(1), hour(2)), (hour(0), hour(3)), False, None, id="earlier-job-merges-after-failure"),
    # A parallel retry, created after the failed job, merges before the failure lands: done when it lands.
    pytest.param((hour(0), hour(4)), (hour(1), hour(2)), True, hour(4), id="parallel-retry-merges-first"),
]


@pytest.mark.parametrize(("failed", "merged", "comeback", "step_done_at"), HISTORIES)
async def test_the_try_again_step_the_comeback_xp_and_the_achievement_agree(
    db_with_migrations,
    failed: tuple[datetime, datetime],
    merged: tuple[datetime, datetime],
    comeback: bool,
    step_done_at: datetime | None,
) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, 'Fix the thing', $2)", ISSUE, REPO)
    await _job(db, "job-failed", "failed", created=failed[0], finished=failed[1])
    await _job(db, "job-merged", "pr_merged", created=merged[0], finished=merged[1])

    repository = ProgressionRepository(db)
    progression = await ProgressionService(repository, packs=ShippedPack(), rollups=None).read()
    quest = next(q for q in progression.quests if q.id == "try_again")
    achievement = next(a for a in progression.achievements if a.achievement.entry.metric == "comeback")
    events = [e for e in await repository.events() if e.source == "comeback"]

    counted = {
        "the quest's merge step": quest.steps[1].done_at is not None,
        "the comeback XP event": bool(events),
        "the comeback achievement": achievement.value > 0,
    }
    assert counted == dict.fromkeys(counted, comeback), (
        f"the failed job was created at {failed[0]:%H:%M} and failed at {failed[1]:%H:%M}, the merged job was "
        f"created at {merged[0]:%H:%M} and finished at {merged[1]:%H:%M}: a comeback is {comeback}, "
        f"but the three read {counted}"
    )
    assert quest.steps[1].done_at == step_done_at, (
        f"the merge step should be done at {step_done_at}, when the merge and the failure had both happened; "
        f"it reads {quest.steps[1].done_at}"
    )
    assert quest.steps[0].done_at == failed[1], f"the failure step is the failure: {quest.steps[0].done_at}"
    assert quest.where == (ISSUE if comeback else None), f"the quest happened on {quest.where}"
