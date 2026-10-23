"""What boot does with each job a previous orchestrator left in flight (#307)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from embry0.orchestration.restart import plan_recovery

STAMP = datetime(2026, 10, 10, tzinfo=UTC)
LAUNCH = {"runner": "workflow", "issue_id": "iss-1"}


def _row(job_id: str, status: str = "running", **extra: Any) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "pipeline_template": None,
        "launch": None,
        "interrupted_at": None,
        "resume_attempts": 0,
        **extra,
    }


def _actions(rows: list[dict[str, Any]], checkpointed: set[str], max_attempts: int = 3) -> dict[str, str]:
    plan = plan_recovery(rows, checkpointed=checkpointed, max_attempts=max_attempts)
    return {d.job_id: d.action for d in plan.decisions}


def test_a_checkpointed_running_job_resumes() -> None:
    assert _actions([_row("j")], {"j"}) == {"j": "resume"}


def test_qa_and_onboard_jobs_that_began_their_graph_are_not_resumed() -> None:
    rows = [_row("qa", pipeline_template="qa", launch=LAUNCH), _row("ob", pipeline_template="onboard")]
    assert _actions(rows, {"qa", "ob"}) == {"qa": "fail", "ob": "fail"}, (
        "QA and onboard fan out over sandboxes a resume would not re-enroll; they must fail, not resume"
    )


def test_a_job_that_never_started_is_launched_again_from_its_launch_record() -> None:
    rows = [_row("pending", "pending", launch=LAUNCH), _row("pre-graph", launch=LAUNCH)]
    assert _actions(rows, set()) == {"pending": "relaunch", "pre-graph": "relaunch"}


def test_a_job_without_resumable_state_fails() -> None:
    rows = [_row("running"), _row("pending", "pending")]
    assert _actions(rows, set()) == {"running": "fail", "pending": "fail"}


def test_attempts_bound_only_recoveries_after_an_unclean_stop() -> None:
    rows = [
        _row("crashed", resume_attempts=3),
        _row("drained", resume_attempts=3, interrupted_at=STAMP),
        _row("fresh", resume_attempts=2),
    ]
    plan = plan_recovery(rows, checkpointed={"crashed", "drained", "fresh"}, max_attempts=3)

    assert {d.job_id: d.action for d in plan.decisions} == {
        "crashed": "exhausted",
        "drained": "resume",
        "fresh": "resume",
    }
    assert plan.counted() == ["fresh"], "only the recovered job whose last stop was unclean spends an attempt"


def test_zero_attempts_turns_recovery_off() -> None:
    rows = [_row("j"), _row("p", "pending", launch=LAUNCH, interrupted_at=STAMP)]
    assert _actions(rows, {"j"}, max_attempts=0) == {"j": "fail", "p": "fail"}


def test_a_pr_review_job_that_began_its_graph_is_not_resumed() -> None:
    rows = [_row("rv", pipeline_template="pr-review", launch={"runner": "pr-review"})]
    assert _actions(rows, {"rv"}) == {"rv": "fail"}, (
        "a review that started must fail and be asked for again, not resume"
    )


def test_a_pr_review_job_that_never_started_is_launched_again() -> None:
    rows = [_row("rv", "pending", pipeline_template="pr-review", launch={"runner": "pr-review"})]
    assert _actions(rows, set()) == {"rv": "relaunch"}
