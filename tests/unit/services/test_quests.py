"""Quests and repo diaries (spec 2026-10-24): each step and task done at the row that first met it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.quests import QUIET_DAYS, evaluate_diaries, evaluate_quests, quest_rewards
from embry0.storage.repositories.progression import JobRow, Journal, RunRow

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def job(
    hours: float,
    status: str = "completed",
    *,
    repo: str = "o/app",
    issue: str | None = None,
    template: str | None = None,
    report: str | None = None,
) -> JobRow:
    return JobRow(repo, issue, template, status, at(hours) - timedelta(minutes=30), at(hours), report)


def run(hours: float, agent: str, *, repo: str = "o/app", issue: str | None = None) -> RunRow:
    return RunRow(agent, repo, issue, at(hours))


def by_id(quests: list) -> dict:
    return {q.id: q for q in quests}


# ---- quests ----


def test_nothing_done_reads_as_four_quests_not_started() -> None:
    quests = evaluate_quests(Journal([], []), [], None)
    assert [q.id for q in quests] == ["repos_assistant", "restless_build", "gatekeeper", "divine_slayer"]
    assert {q.state for q in quests} == {"not_started"}
    assert quest_rewards(Journal([], [])) == []


def test_the_repos_assistant_follows_one_repo_from_onboarding_to_its_first_merge() -> None:
    journal = Journal(
        [
            job(1, template="onboard", repo="o/new"),
            job(0.5, "pr_merged", repo="o/new"),  # before onboarding: does not count
            job(2, repo="o/new"),
            job(3, "pr_merged", repo="o/new"),
        ],
        [],
    )
    quest = by_id(evaluate_quests(journal, [], None))["repos_assistant"]
    assert [s.done_at for s in quest.steps] == [at(1), at(2), at(3)]
    assert quest.state == "complete" and quest.completed_at == at(3) and quest.where == "o/new"
    assert [(t, r.agent_type, r.stat) for t, r in quest_rewards(journal)] == [
        (at(3), "onboarding", "operations"),
        (at(3), "developer", "coding"),
    ]


def test_the_restless_build_needs_a_merge_after_a_failure_on_the_same_issue() -> None:
    failed_only = Journal([job(1, "failed", issue="i-1"), job(2, "pr_merged", issue="i-2")], [])
    quest = by_id(evaluate_quests(failed_only, [], None))["restless_build"]
    assert quest.state == "in_progress" and quest.steps[0].done_at == at(1)
    comeback = Journal([job(1, "failed", issue="i-1"), job(2, "pr_merged", issue="i-1")], [])
    quest = by_id(evaluate_quests(comeback, [], None))["restless_build"]
    assert quest.completed_at == at(2) and quest.where == "i-1"


def test_gatekeeper_wants_review_qa_and_merge_on_one_issue() -> None:
    split = Journal([job(3, "pr_merged", issue="i-2")], [run(1, "review", issue="i-1"), run(2, "qa", issue="i-2")])
    quest = by_id(evaluate_quests(split, [], None))["gatekeeper"]
    assert quest.state == "in_progress" and quest.where == "i-2", "the issue furthest along"
    together = Journal([job(3, "pr_merged", issue="i-1")], [run(1, "review", issue="i-1"), run(2, "qa", issue="i-1")])
    quest = by_id(evaluate_quests(together, [], None))["gatekeeper"]
    assert quest.completed_at == at(3) and quest.points == 2


def test_divine_slayer_waits_for_its_requirement() -> None:
    merges = [job(h, "pr_merged") for h in range(100)]
    journal = Journal(merges, [])
    diaries = evaluate_diaries(journal)
    quest = by_id(evaluate_quests(journal, diaries, None))["divine_slayer"]
    assert quest.steps[0].done_at == at(99)
    assert quest.state == "in_progress" and quest.to_dict()["requirement_met"] is False
    quest = by_id(evaluate_quests(journal, diaries, at(200)))["divine_slayer"]
    assert quest.completed_at is None, "an Elite diary is still missing"


# ---- repo diaries ----


def test_a_diary_per_repo_with_four_tiers_of_tasks() -> None:
    journal = Journal(
        [
            job(1, repo="o/a", issue="i-1"),
            job(2, "pr_merged", repo="o/a", issue="i-1"),
            job(3, template="context-audit", repo="o/a", report="Stale: 0\nFiles: 4\n"),
            job(1, repo="o/b"),
        ],
        [run(0.5, "triage", repo="o/a", issue="i-1"), run(1.5, "qa", repo="o/a", issue="i-1")],
    )
    diaries = evaluate_diaries(journal)
    assert [d.repo for d in diaries] == ["o/a", "o/b"], "the one furthest along first"
    a = diaries[0]
    assert a.tier("easy").completed_at == at(1.5)
    assert [t.done_at for t in a.tier("medium").tasks] == [at(2), at(2), at(3)], "a merged issue with QA is a pass"
    assert a.tier("hard").tasks[1].done_at == at(3), "an audit with no outdated claim"
    assert a.tier("hard").completed_at is None
    assert a.to_dict()["tiers"][0] == {
        "tier": "easy",
        "tasks": [
            {"text": "Triage an issue", "done_at": at(0.5).isoformat()},
            {"text": "Finish a job", "done_at": at(1).isoformat()},
            {"text": "Run QA", "done_at": at(1.5).isoformat()},
        ],
        "completed_at": at(1.5).isoformat(),
    }


def test_an_audit_whose_report_cannot_be_read_is_not_a_clean_one() -> None:
    diary = evaluate_diaries(Journal([job(1, template="context-audit", report="cut short")], []))[0]
    assert diary.tier("medium").tasks[2].done_at == at(1)
    assert diary.tier("hard").tasks[1].done_at is None


def test_the_quiet_stretch_restarts_at_every_failure() -> None:
    day = 24
    jobs = [job(0), job(10 * day, "failed"), job((10 + QUIET_DAYS - 1) * day), job((10 + QUIET_DAYS + 1) * day)]
    diary = evaluate_diaries(Journal(jobs, []))[0]
    assert diary.tier("elite").tasks[1].done_at == at((10 + QUIET_DAYS + 1) * day)


def test_elite_needs_every_tier_below_it() -> None:
    merges = [job(h, "pr_merged") for h in range(50)]
    diary = evaluate_diaries(Journal(merges, []))[0]
    assert diary.tier("elite").tasks[0].done_at == at(49)
    assert diary.tier("elite").tasks[2].done_at is None and diary.tier("elite").completed_at is None
