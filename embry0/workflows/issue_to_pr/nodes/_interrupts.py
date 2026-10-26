"""retry / max_retries / ask_user interrupt nodes (RAV-1235 split of nodes.py)."""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed) are resolved through the package namespace at CALL
# time so test patches on `embry0.workflows.issue_to_pr.nodes.<seam>` reach
# this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.
import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.orchestration.budget import allowance_to_continue, resolve_job_cap
from embry0.orchestration.nodes.agent import (
    USER_RETRY_ROUNDS_CAP,
    load_budget_policy,
)
from embry0.orchestration.routing.conditions import check_budget

logger = structlog.get_logger(__name__)


async def retry_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Inject review feedback into state and increment retry count."""
    writer = _nodes.get_stream_writer()
    retry_count = state.get("retry_count", 0) + 1
    writer(events.progress(message=f"Retry {retry_count}: addressing review feedback", node="retry"))
    logger.info("retry_developer", retry_count=retry_count)

    # Extract feedback from the latest review output
    feedback = ""
    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if review_outputs:
        feedback = review_outputs[-1].get("output", "")

    return {
        "retry_count": retry_count,
        "feedback_context": feedback,
        "current_stage": "developer_retry",
    }


def _over_budget(state: dict[str, Any]) -> bool:
    from embry0.orchestration.routing.conditions import check_budget

    return check_budget(state) == "over_budget"


async def max_retries_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Max retries reached — pause pipeline and ask user what to do.

    Special case: if the job arrives here because the agent_question_rounds
    cap was exhausted (ERR_MAX_AGENT_QUESTIONS), do NOT offer 'continue_retrying'
    — the cap is already tripped, so continuing would loop. Offer only
    'merge_as_is' (if a PR was produced) or 'abandon'.
    """
    writer = _nodes.get_stream_writer()
    retry_count = state.get("retry_count", 0)
    pr_url = state.get("pr_url", "")
    exhausted = bool(state.get("agent_questions_exhausted"))

    if exhausted:
        from embry0.workflows.issue_to_pr.nodes._salvage import pause_note

        message = "Agent exceeded the ask_user round cap — cannot continue." + pause_note(state.get("unfinished_work"))
        reason = "agent_questions_exhausted"
        options = ["merge_as_is", "abandon"] if pr_url else ["abandon"]
    else:
        # RAV-1152: say what actually happened — this node is reached from
        # several paths and the old blanket "Review failed after N attempts"
        # was wrong for most of them (e.g. dev_branch_missing pauses).
        arrival_stage = state.get("current_stage", "")
        if arrival_stage == "dev_branch_missing":
            # The branch really is absent from origin, but saying only that
            # blames the agent for forgetting when the usual cause is that it
            # never got to the push: a turn-cap death, or a run spent retrying
            # a tool the safety policy will never allow. Surfacing the
            # developer's own error turns a misleading summary into an
            # actionable one — same treatment review_errored got in RAV-1260.
            _dev_err = ""
            _denials: dict[str, int] = {}
            for o in reversed(state.get("agent_outputs", []) or []):
                if o.get("agent_type") == "developer":
                    _dev_err = (o.get("error_message") or "")[:300]
                    _denials = o.get("policy_denials") or {}
                    break
            message = "Developer finished without pushing its branch to origin"
            if _dev_err:
                message += f" ({_dev_err})"
            if _denials:
                # A turn-cap death plus blocked tools is not a coincidence: the
                # agent had no way to learn the tool would never be allowed, so
                # it retried until the budget ran out. Name the tool it fought.
                _total = sum(_denials.values())
                _worst = max(_denials.items(), key=lambda kv: kv[1])[0]
                message += f" — {_total} tool call(s) blocked by safety policy, mostly: {_worst[:120]}"
        elif arrival_stage == "dev_run_unfinished":
            # #358: the opposite of dev_branch_missing — the work is NOT lost.
            # The developer ran out of turns before committing and embry0
            # committed and pushed it, so the person has something to look at
            # and something to continue from. Say which branch, and say plainly
            # that it is unfinished so nobody reads it as a delivery.
            # developer_node writes branch_name into the same updates that carry
            # this stage, so it is present; derive it the shared way if a
            # resumed checkpoint predates that key rather than guessing a name.
            _branch = str(state.get("branch_name") or "")
            if not _branch:
                from embry0.orchestration.branch_names import job_branch_name

                _branch = job_branch_name(
                    issue_id=state.get("issue_id", ""),
                    task=state.get("task", ""),
                    job_id=state.get("job_id"),
                    context=state.get("context"),
                )
            _kept = state.get("unfinished_work") or {}
            _who = (
                f"{_kept.get('agent') or 'developer'} stopped ({_kept['reason']})"
                if _kept.get("reason")
                else ("Developer hit its turn cap")
            )
            message = (
                f"{_who[0].upper()}{_who[1:]} before committing — embry0 preserved its work"
                + (f" on `{_branch}`" if _branch else "")
                + ". The change is UNFINISHED and was never self-reviewed; continue from that "
                "branch rather than starting over."
            )
        elif arrival_stage == "review_errored":
            # RAV-1260: the review agent ERRORED (turn cap, crash) — it never
            # emitted a verdict. Saying "requested changes" here was the
            # RAV-1152 dishonest-message family's last survivor.
            _review_err = ""
            for o in reversed(state.get("agent_outputs", []) or []):
                if o.get("agent_type") == "review":
                    _review_err = (o.get("error_message") or "")[:300]
                    break
            message = f"Review errored before emitting a verdict ({_review_err or 'unknown error'})"
        elif arrival_stage == "review_unparseable":
            # #356: the reviewer ran and did not error, but emitted no verdict
            # this pipeline acts on. Before that fix this path silently read as
            # "approved" — and looked *cleaner* than a real review, because an
            # unreadable verdict also carries no findings, no validation block
            # and no duplicate-PR flag. Name it, and show the output so the
            # person can see whether the work itself is fine.
            message = (
                "Review emitted no readable verdict (no `decision` of `approved` "
                "or `changes_requested`) — paused instead of assuming approval"
            )
        elif arrival_stage == "review_toolchain_missing":
            # The reviewer approved nothing: the compiler or test runner is
            # absent. continue_retrying re-enters the same sandbox, which
            # still does not have it, so that option is not offered.
            message = (
                "Review could not run the tests because this sandbox has no "
                "compiler or test runner. Retrying here will not install one — "
                "set the repository's sandbox profile to an image that has the "
                "toolchain, then run the job again. The branch is already pushed."
            )
        elif arrival_stage == "review_retries_exhausted":
            message = f"Review requested changes and the retry budget is exhausted ({retry_count} attempts)"
        elif _over_budget(state):
            # The developer node routes here on a soft budget overrun before
            # any review runs: "Review failed" was wrong for it.
            from embry0.orchestration.budget import resolve_job_cap

            message = (
                f"The job reached its budget (${float(state.get('total_cost_usd') or 0):.2f} of "
                f"${resolve_job_cap(state):.2f})"
            )
        elif retry_count:
            message = f"Review failed after {retry_count} attempts"
        else:
            message = f"Pipeline paused at stage {arrival_stage or 'unknown'}"
        if arrival_stage != "dev_run_unfinished":
            # Salvage (spec 2026-10-25): a budget or question stop that kept
            # the coding run's work says where it is.
            from embry0.workflows.issue_to_pr.nodes._salvage import pause_note

            message += pause_note(state.get("unfinished_work"))
        reason = "max_retries"
        if arrival_stage == "review_toolchain_missing":
            options = ["abandon"]
        else:
            options = ["continue_retrying", "merge_as_is", "abandon"]

    summary = {
        "message": message,
        "reason": reason,
        "pr_url": pr_url,
        "retry_count": retry_count,
        "options": options,
    }
    # A job over its cap: continuing it lets it spend one more cap, so the
    # pause says how much (the person deciding is the one paying).
    over_budget = check_budget(state) == "over_budget"
    ceiling = 0.0
    if over_budget:
        ceiling, _mode = await load_budget_policy(config)
        summary["budget"] = {
            "spent": round(float(state.get("total_cost_usd") or 0.0), 2),
            "cap": round(resolve_job_cap(state, ceiling=ceiling), 2),
            "continue_adds": round(resolve_job_cap({**state, "budget_allowance_usd": 0.0}, ceiling=ceiling), 2),
        }

    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if review_outputs:
        # RAV-1260: an errored review's output is truncated investigation
        # narration, not a verdict — present the error instead of passing
        # narration off as "the latest review".
        if review_outputs[-1].get("is_error"):
            summary["review_error"] = (review_outputs[-1].get("error_message") or "")[:500]
        else:
            summary["latest_review"] = review_outputs[-1].get("output", "")[:1000]

    writer(events.interrupt(node="max_retries", reason="max_retries", summary=summary))
    logger.info("max_retries_reached", retry_count=retry_count)

    user_choice = _nodes.interrupt(summary)

    extra_budget: float | None = None
    if isinstance(user_choice, str):
        choice = user_choice
        guidance = ""
    elif isinstance(user_choice, dict):
        choice = user_choice.get("choice", "abandon")
        guidance = user_choice.get("guidance", "")
        raw_extra = user_choice.get("extra_budget_usd")
        if isinstance(raw_extra, int | float) and raw_extra > 0:
            extra_budget = float(raw_extra)
    else:
        choice = "abandon"
        guidance = ""

    # Refuse continue_retrying when it cannot change the outcome: the
    # ask_user cap is already tripped, or the sandbox is missing the
    # compiler and another developer round runs in that same sandbox.
    if state.get("current_stage") == "review_toolchain_missing" and choice == "continue_retrying":
        logger.warning(
            "continue_retrying_rejected_toolchain_missing",
            job_id=state.get("job_id"),
        )
        choice = "abandon"
    if exhausted and choice == "continue_retrying":
        logger.warning(
            "continue_retrying_rejected_agent_questions_exhausted",
            job_id=state.get("job_id"),
        )
        choice = "abandon"

    if choice == "continue_retrying":
        user_rounds = int(state.get("user_retry_rounds", 0) or 0)
        max_user_retries = USER_RETRY_ROUNDS_CAP
        if user_rounds >= max_user_retries:
            logger.warning("user_retry_rounds_exceeded", job_id=state.get("job_id"))
            return {
                "current_stage": "abandoned",
                "errors": [f"Exceeded {max_user_retries} user-initiated retries"],
            }
        updates: dict[str, Any] = {
            "retry_count": 0,
            "current_stage": "developer_retry",
            "user_retry_rounds": user_rounds + 1,
        }
        if over_budget or extra_budget:
            # Continuing past the cap is the person's call: the job may spend
            # what it has spent plus one more cap (or what they named).
            if not over_budget:
                ceiling, _mode = await load_budget_policy(config)
            allowance = allowance_to_continue(state, ceiling=ceiling, extra=extra_budget)
            updates["budget_allowance_usd"] = allowance
            logger.info(
                "budget_raised_on_continue",
                job_id=state.get("job_id"),
                spent=state.get("total_cost_usd"),
                allowance=allowance,
                cap=resolve_job_cap({**state, "budget_allowance_usd": allowance}, ceiling=ceiling),
            )
        if guidance:
            existing = state.get("additional_context", "") or ""
            updates["additional_context"] = existing + f"\n\nUser guidance:\n{guidance}"
            # EMB-35: also carry the guidance as a one-shot field — the
            # delta prompt sends just this on a resumed session instead of
            # re-parsing it out of the accumulated additional_context.
            updates["user_retry_guidance"] = guidance
        return updates
    elif choice == "merge_as_is":
        return {"current_stage": "completed"}
    else:
        return {"current_stage": "abandoned", "errors": [f"Abandoned after {retry_count} retries"]}


# Resume target per asking node — answers must re-enter the node that asked
# so it sees them in its rebuilt context (RAV-1271: the old static edge sent
# every resume to the developer, so a triage QA-escalation answer never
# reached triage). Only these three nodes emit ask_user questions;
# generic_agent has no ask path by design.
_RESUME_TARGETS: dict[str, str] = {
    "triage": "triage",
    "developer": "developer",
    "review": "review",
}


async def ask_user_interrupt(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    """Pause the pipeline — raise an interrupt with the pending questions.

    When resumed via Command(resume=answers), the pending questions are
    cleared, the user's answers are stored on state, and the graph re-enters
    the ASKING node (triage / developer / review) so its next invocation can
    include the answers in its prompt.
    """
    writer = _nodes.get_stream_writer()

    questions = state.get("pending_agent_questions", []) or []

    # RAV-1271 defense-in-depth: an interrupt with nothing to ask is an
    # upstream routing bug. Raising it anyway pauses the job on a question
    # nobody can see (no input row, nothing on any channel) and the executor
    # then ends the job in a corrupt completed-without-finished_at state.
    # Fail loudly instead.
    if not questions:
        from embry0.safety.error_codes import ErrorCode

        logger.error(
            "ask_user_interrupt_zero_questions",
            job_id=state.get("job_id"),
            current_stage=state.get("current_stage"),
        )
        writer(
            events.error(
                message="ask_user_interrupt reached with zero pending questions — failing instead of pausing on nothing",
                node="ask_user_interrupt",
            )
        )
        return Command(
            goto=END,
            update={
                "current_stage": "failed",
                "error_code": ErrorCode.EMPTY_INTERRUPT.value,
                "errors": ["ask_user_interrupt reached with zero pending questions (upstream routing bug)"],
            },
        )

    asking_node = questions[0].get("asking_node", "developer")

    writer(events.agent_paused_for_input(node=asking_node, count=len(questions)))
    logger.info("agent_ask_user_interrupt", node=asking_node, count=len(questions))

    # _nodes.interrupt() raises; the graph halts here until Command(resume=...) arrives.
    answers = _nodes.interrupt(
        {
            "asking_node": asking_node,
            "questions": questions,
            "reason": "agent_needs_info",
        }
    )

    return Command(
        goto=_RESUME_TARGETS.get(asking_node, "developer"),
        update={
            "pending_agent_questions": [],
            "user_answers": answers,
        },
    )
