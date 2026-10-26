"""Salvage: a coding run that stops before it pushes keeps its work (spec 2026-10-25, salvage unfinished work).

#358 kept a turn-cap death's work: embry0 committed what the run left in the
worktree, pushed it, and paused the job as unfinished. Every other stop still
lost it. An error ending (a proxy's request cap, a timeout, a crash) skipped
the commit, and a run that took the job over its budget went to the pause
before the push check ran. This module widens #358 to every stop: what a
stopped run left uncommitted is committed on the job's branch, marked
unfinished, and pushed the way the rescue always pushes, so the next attempt
continues from it instead of starting over.

A clean finish is not a stop: a run that ended without an error keeps #358's
behaviour, where an empty branch means the agent produced nothing.
"""

from __future__ import annotations

from typing import Any

import structlog

import embry0.workflows.issue_to_pr.nodes as _nodes

logger = structlog.get_logger(__name__)

#: The agents the developer node runs; their outputs are the coding run's.
CODING_AGENTS = frozenset({"developer", "slopmaster"})

BUDGET = "budget reached"
QUESTIONS = "question rounds used up"


def _last_coding_output(result: dict[str, Any]) -> dict[str, Any] | None:
    for out in reversed(result.get("agent_outputs") or []):
        if isinstance(out, dict) and out.get("agent_type") in CODING_AGENTS:
            return dict(out)
    return None


def coding_agent(result: dict[str, Any], state: dict[str, Any]) -> str:
    """The agent the run was: its output's type, else the state's coding agent, else the developer."""
    out = _last_coding_output(result)
    return str((out or {}).get("agent_type") or state.get("coding_agent") or "developer")


def stop_reason(result: dict[str, Any]) -> str | None:
    """Why the coding run stopped, in a few words; None for a clean finish."""
    from embry0.agents.session import is_max_turns_error

    out = _last_coding_output(result)
    if not out or not out.get("is_error"):
        return None
    if out.get("error_code") == "ERR_AGENT_MAX_TURNS" or is_max_turns_error(out.get("error_message")):
        return "turn cap"
    code = out.get("error_code")
    return f"error {code}" if code else "error"


def commit_message(agent: str, reason: str) -> str:
    """The salvage commit's message: plainly unfinished, plainly embry0's."""
    return (
        f"wip: unfinished — {agent} stopped ({reason})\n\n"
        f"Committed by embry0, not by the agent. The {agent} run stopped ({reason})\n"
        "before it committed, so this change is incomplete and was never\n"
        "self-reviewed. It is kept here so the work is not lost: the next run\n"
        "continues from this commit. Do not merge it as it is."
    )


async def preserve_unfinished_work(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
    job_id: str | None,
    agent: str,
    reason: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Commit what a stopped run left uncommitted and push the branch.

    The record (``{branch, agent, reason, committed}``) when the branch is on
    origin with the run's work, ``committed`` saying whether embry0 made a
    commit for it; None when there was nothing to push or the push failed.
    Never raises: a failure is logged and the job takes its old path.
    """
    try:
        committed = await _nodes._commit_unfinished_work(
            docker=docker,
            container_id=container_id,
            branch=branch,
            job_id=job_id,
            message=commit_message(agent, reason),
        )
        pushed = await _nodes._rescue_push_branch(
            docker=docker, container_id=container_id, branch=branch, diagnostics=diagnostics
        )
    except Exception as exc:  # noqa: BLE001 - salvage never fails the job
        logger.warning("dev_salvage_failed", branch=branch, job_id=job_id, error=str(exc))
        return None
    if not pushed:
        return None
    logger.info("dev_salvage_pushed", branch=branch, job_id=job_id, agent=agent, reason=reason, committed=committed)
    return {"branch": branch, "agent": agent, "reason": reason, "committed": committed}


def prompt_section(record: dict[str, Any] | None) -> str | None:
    """What the next coding attempt is told about the unfinished commit; None without one."""
    if not record or not record.get("committed"):
        return None
    branch = record.get("branch") or "the job's branch"
    return (
        f"\nUnfinished work from your earlier run:\nThat run stopped ({record.get('reason') or 'before it finished'}) "
        f"before it committed. embry0 committed what it left in the working tree as `wip: unfinished …` "
        f"on {branch} and pushed it, so it is checked out here. Do not start over: review that commit "
        "against the task, finish what is missing, run the checks, then commit and push."
    )


def pause_note(record: dict[str, Any] | None) -> str:
    """The sentence a pause adds when salvage kept the work; empty without it."""
    if not record or not record.get("committed"):
        return ""
    return (
        f" embry0 kept the {record.get('agent') or 'developer'}'s UNFINISHED work on "
        f"`{record.get('branch')}`; continue from that branch rather than starting over."
    )
