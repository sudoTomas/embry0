"""What an orchestrator restart does to the jobs in flight (#307).

Sandboxes live on the agent runtime (DinD or the rootless daemon), not in the
orchestrator container, so they outlive an orchestrator restart. The graph's
state outlives it too: LangGraph checkpoints every node into Postgres. Two
halves use that:

- **Drain** (shutdown). ``IssueExecutor.drain`` stops starting jobs, lets the
  running ones go on until their next node boundary or a deadline, and stamps
  ``jobs.interrupted_at`` on every job it stopped. A job stops at a node
  boundary by raising :class:`ShutdownInterrupt` out of its stream.
- **Recovery** (boot). :func:`plan_recovery` decides, per job the previous
  process left ``running`` or ``pending``, whether to resume it from its
  checkpoint, launch it again from its recorded launch spec, or fail it.

A job that keeps taking the orchestrator down with it must not come back
forever: every recovery after an *unclean* stop (no drain stamp, so a crash or
a kill) counts against ``MAX_RESUME_ATTEMPTS``. A clean drain does not count,
so a long job may cross any number of deploys.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

# Pipelines whose graph can be resumed from a checkpoint after a restart. The
# QA, onboard and pr-review graphs fan out over sandboxes of their own that a
# resume would have to re-enroll one by one; such a job that had started is
# failed instead, and is dispatched again (mirrors
# IssueExecutor._select_workflow).
NOT_RESUMABLE_PIPELINES = frozenset({"qa", "onboard", "pr-review"})


class ShutdownInterrupt(BaseException):
    """A job stopped at a node boundary because the orchestrator is draining.

    A BaseException, like ``asyncio.CancelledError``: the job is not failing,
    so none of the runners' ``except Exception`` failure paths may see it.
    Their ``finally`` blocks still run.
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} stopped for an orchestrator shutdown")
        self.job_id = job_id


class ExecutorDraining(RuntimeError):
    """A resume was asked for while the orchestrator is shutting down."""


Action = Literal["resume", "relaunch", "exhausted", "fail"]


@dataclass(frozen=True)
class Decision:
    job_id: str
    action: Action
    # Whether this recovery counts against MAX_RESUME_ATTEMPTS: the job's last
    # stop was unclean (no drain stamp).
    counts: bool = False


@dataclass
class RecoveryPlan:
    decisions: list[Decision] = field(default_factory=list)

    def ids(self, action: Action) -> list[str]:
        return [d.job_id for d in self.decisions if d.action == action]

    def recovered(self) -> list[str]:
        """Jobs boot takes back: resumed or launched again."""
        return [d.job_id for d in self.decisions if d.action in ("resume", "relaunch")]

    def counted(self) -> list[str]:
        """The recovered jobs whose recovery uses up one attempt."""
        return [d.job_id for d in self.decisions if d.action in ("resume", "relaunch") and d.counts]


def plan_recovery(
    rows: Iterable[Mapping[str, Any]],
    *,
    checkpointed: set[str],
    max_attempts: int,
) -> RecoveryPlan:
    """Decide what boot does with each job the previous process left in flight.

    ``rows`` are ``running`` / ``pending`` jobs with ``job_id``, ``status``,
    ``pipeline_template``, ``launch``, ``interrupted_at`` and
    ``resume_attempts``. ``checkpointed`` holds the job ids with a LangGraph
    checkpoint. ``max_attempts`` 0 turns recovery off: every job fails as it
    did before recovery existed.

    - ``resume``: ``running`` with a checkpoint, on a resumable pipeline.
    - ``relaunch``: ``running`` with no checkpoint (it stopped before its
      graph began) or ``pending`` (it never started), with a launch record.
    - ``exhausted``: either of those after an unclean stop, with every
      attempt used up.
    - ``fail``: anything else. ``JobsRepository.fail_orphaned`` words it.
    """
    plan = RecoveryPlan()
    for row in rows:
        job_id = str(row["job_id"])
        status = row.get("status")
        action: Action = "fail"
        if max_attempts > 0:
            if status == "running" and job_id in checkpointed:
                if row.get("pipeline_template") not in NOT_RESUMABLE_PIPELINES:
                    action = "resume"
            elif row.get("launch"):
                action = "relaunch"
        unclean = row.get("interrupted_at") is None
        if action != "fail" and unclean and int(row.get("resume_attempts") or 0) >= max_attempts:
            action = "exhausted"
        plan.decisions.append(Decision(job_id, action, counts=unclean))
    return plan


RESUME_LIMIT_MESSAGE = (
    "Orchestrator restarted during this job {attempts} times without a clean shutdown; "
    "it may be what stops the orchestrator, so it was not resumed again. Dispatch it again."
)


async def take_back_jobs(jobs_repo: Any, db: Any, *, max_attempts: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Boot's first half of recovery: decide, and write the decision (#307).

    Fails the jobs whose attempts are used up (``ERR_RESUME_LIMIT``) and
    takes back the ones it will resume or launch again (``begin_recovery``).
    Returns ``(recovered rows, each with an "action" key; exhausted ids)``.
    Everything else in flight is left for ``fail_orphaned``, which must be
    given the recovered ids as ``keep``. Starting the recovered jobs waits for
    the executor (``IssueExecutor.resume_after_restart`` / ``relaunch``).
    """
    from embry0.orchestration.checkpoint import checkpointed_threads
    from embry0.safety.error_codes import ErrorCode

    rows = await jobs_repo.inflight_for_recovery()
    checkpointed = await checkpointed_threads(db, [row["job_id"] for row in rows])
    plan = plan_recovery(rows, checkpointed=checkpointed, max_attempts=max_attempts)

    exhausted = await jobs_repo.fail_jobs(
        plan.ids("exhausted"),
        ErrorCode.RESUME_LIMIT.value,
        RESUME_LIMIT_MESSAGE.format(attempts=max_attempts),
    )
    taken = set(await jobs_repo.begin_recovery(plan.recovered(), counted=plan.counted()))
    actions = {d.job_id: d.action for d in plan.decisions}
    recovered = [{**row, "action": actions[row["job_id"]]} for row in rows if row["job_id"] in taken]
    return recovered, exhausted
