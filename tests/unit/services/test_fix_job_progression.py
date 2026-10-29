"""#241: a context-audit finding's fix job is no audit, and its merge is credited once, as a merged proposal.

Accepting one finding of a context audit starts a fix job on the audit's
template, dispatched by the proposals actor (#240). It fixes one claim: it
earns no ``audited`` event, adds nothing to the context auditor's lore and
no ``audit_merged``. Merged, it earns what any accepted proposal's merged job
earns (``proposal_merged``). Each history also holds a real audit, which
earns its credit as before, so a check that counted nothing would fail.

Every test reads the history twice: once folding every raw event, and once
from the rollup, with every event in its folded days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.orchestration.dispatch_actors import CONTEXT_AUDIT, PROPOSALS
from embry0.services.progression import Progression, ProgressionService
from embry0.services.progression_pack import ShippedPack, default_rules
from embry0.storage.repositories.progression import ProgressionRepository
from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

RULES = default_rules()
AUDITED = RULES.outcomes["audited"]
T0 = datetime(2026, 3, 2, 9, tzinfo=UTC)
#: Late enough that every event below is in the rollup's folded days.
CLOCK = T0 + timedelta(days=10)
AUDITED_REPO = "acme/widgets"
FIXED_REPO = "acme/lib"


def hour(n: float) -> datetime:
    return T0 + timedelta(hours=n)


async def _job(
    db,
    job_id: str,
    *,
    repo: str,
    by: str,
    finished: datetime,
    report: str,
    pr: int,
    merged: bool = False,
) -> None:
    """A context-audit job created half an hour before it finished, with its pull request open or merged."""
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, pipeline_template, dispatched_by, result_summary, pr_url,
                          delivery_state, delivery_state_at, created_at, started_at, finished_at, updated_at,
                          budget_overrun_usd)
        VALUES ($1, $2, 't', $3, 'context-audit', $4, $5, $6, $7, $8, $9, $9, $10, $10, 0)
        """,
        job_id,
        repo,
        "pr_merged" if merged else "completed",
        by,
        report,
        f"https://forge/{repo}/pull/{pr}",
        "merged" if merged else "pr_open",
        finished + timedelta(minutes=5),
        finished - timedelta(minutes=30),
        finished,
    )


async def _accepted(db, job_id: str, at: datetime) -> None:
    """The owner's yes to the finding the fix job fixes."""
    await db.execute(
        "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, job_id, created_at) "
        "VALUES ($1, $2, 'finding', 'accept', 'octocat', $3, $4)",
        f"finding:{job_id}",
        FIXED_REPO,
        job_id,
        at,
    )


async def _read(db, through_rollup: bool) -> Progression:
    """The progression ``GET /progression`` answers: from every raw event, or from the rollup."""
    if not through_rollup:
        return await ProgressionService(ProgressionRepository(db), packs=ShippedPack(), rollups=None).read()
    service = ProgressionService(
        ProgressionRepository(db),
        packs=ShippedPack(),
        rollups=ProgressionRollupRepository(db),
        now=lambda: CLOCK,
    )
    await service.read()
    await service.settle()
    progression = await service.read()
    assert progression.rollup is not None, "the second read is the rollup's"
    boundary = datetime.fromisoformat(progression.rollup["boundary"])
    assert boundary > hour(24), f"not every event is folded: {progression.rollup}"
    return progression


def _documentation_xp(progression: Progression) -> int:
    auditor = progression.characters.get("context-auditor")
    return 0 if auditor is None else auditor.stat_xp.get(AUDITED.skill, 0)


def _value(progression: Progression, metric: str) -> int:
    return _counted(progression, metric)[0]


def _counted(progression: Progression, metric: str) -> tuple[int, str | None]:
    """How many times the achievement counting ``metric`` counted, and the pull request it counted last."""
    state = next(a for a in progression.achievements if a.achievement.entry.metric == metric)
    return int(state.value), None if state.evidence is None else state.evidence.url


def _audit_tasks(progression: Progression, repo: str) -> dict[str, datetime | None]:
    """When the repo's diary tasks that read its audits were done."""
    diary = next(d for d in progression.diaries if d.repo == repo)
    done: dict[str, datetime | None] = {}
    for entry in RULES.pack.diaries.tiers:
        for task_entry, task in zip(entry.tasks, diary.tier(entry.id).tasks, strict=True):
            if task_entry.kind in ("audit", "clean_audit"):
                done[task_entry.kind] = task.done_at
    return done


@pytest.mark.parametrize("through_rollup", [False, True], ids=["raw", "rollup"])
async def test_a_completed_fix_job_earns_no_audit_credit(db_with_migrations, through_rollup: bool) -> None:
    db = db_with_migrations
    await _job(
        db, "job-audit", repo=AUDITED_REPO, by=CONTEXT_AUDIT, finished=hour(1), report="Stale: 3\nFiles: 2\n", pr=1
    )
    await _accepted(db, "job-fix-stale", hour(2))
    await _job(
        db, "job-fix-stale", repo=FIXED_REPO, by=PROPOSALS, finished=hour(3), report="Stale: 2\nFiles: 1\n", pr=2
    )
    await _accepted(db, "job-fix-clean", hour(4))
    await _job(
        db, "job-fix-clean", repo=FIXED_REPO, by=PROPOSALS, finished=hour(5), report="Stale: 0\nFiles: 1\n", pr=3
    )

    progression = await _read(db, through_rollup)
    got = {
        "the context auditor's documentation XP": _documentation_xp(progression),
        "outdated claims (stale_claims)": _value(progression, "stale_claims"),
        "clean audits (clean_audit)": _value(progression, "clean_audit"),
        f"{AUDITED_REPO}'s diary": _audit_tasks(progression, AUDITED_REPO),
        f"{FIXED_REPO}'s diary": _audit_tasks(progression, FIXED_REPO),
    }
    want = {
        "the context auditor's documentation XP": AUDITED.xp + 3 * AUDITED.per_stale_claim,
        "outdated claims (stale_claims)": 3,
        "clean audits (clean_audit)": 0,
        f"{AUDITED_REPO}'s diary": {"audit": hour(1), "clean_audit": None},
        f"{FIXED_REPO}'s diary": {"audit": None, "clean_audit": None},
    }
    assert got == want, (
        f"one audit with 3 outdated claims, and two fix jobs that completed with reports of their own: only the "
        f"audit earns audit credit. Read {'from the rollup' if through_rollup else 'from every raw event'}: {got}"
    )


@pytest.mark.parametrize("through_rollup", [False, True], ids=["raw", "rollup"])
async def test_a_merged_fix_job_is_credited_once_as_a_merged_proposal(db_with_migrations, through_rollup: bool) -> None:
    db = db_with_migrations
    await _job(
        db,
        "job-audit",
        repo=AUDITED_REPO,
        by=CONTEXT_AUDIT,
        finished=hour(1),
        report="Stale: 4\nFiles: 1\n",
        pr=1,
        merged=True,
    )
    await _accepted(db, "job-fix", hour(2))
    await _job(
        db, "job-fix", repo=FIXED_REPO, by=PROPOSALS, finished=hour(3), report="Stale: 1\nFiles: 1\n", pr=2, merged=True
    )

    progression = await _read(db, through_rollup)
    got = {
        "audit_merged": _counted(progression, "audit_merged"),
        "proposal_merged": _counted(progression, "proposal_merged"),
        "every merge (merged)": _value(progression, "merged"),
        "the context auditor's documentation XP": _documentation_xp(progression),
        "outdated claims (stale_claims)": _value(progression, "stale_claims"),
    }
    want = {
        "audit_merged": (1, f"https://forge/{AUDITED_REPO}/pull/1"),
        "proposal_merged": (1, f"https://forge/{FIXED_REPO}/pull/2"),
        "every merge (merged)": 2,
        "the context auditor's documentation XP": AUDITED.xp + 4 * AUDITED.per_stale_claim,
        "outdated claims (stale_claims)": 4,
    }
    assert got == want, (
        f"a merged audit and a merged fix job an owner accepted: the fix job's merge counts once, as a merged "
        f"proposal, and earns no audit credit. Read {'from the rollup' if through_rollup else 'from every raw event'}: "
        f"{got}"
    )
