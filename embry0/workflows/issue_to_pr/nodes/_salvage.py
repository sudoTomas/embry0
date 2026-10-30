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

The commit's subject is the job's ``salvage.commit_subject`` template (the
settings document, as the job read it when it started; #182), filled with the
agent and the reason, ``wip: unfinished — <agent> stopped (<reason>)`` unless
an operator set another. A subject past ``MAX_SUBJECT_CHARS`` has only its
reason shortened.
"""

from __future__ import annotations

import math
from typing import Any

import structlog

import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.services.progression_templates import Field, parse, render
from embry0.settings.sections.salvage import MAX_SUBJECT_CHARS

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


def commit_subject(agent: str, reason: str, *, template: str) -> str:
    """The salvage commit's subject: the job's ``salvage.commit_subject`` ``template`` with ``agent`` and ``reason``.

    A subject longer than ``MAX_SUBJECT_CHARS`` has only its reason
    shortened, ending in ``…``, where the template names it; a shorter one
    is never touched.
    """
    subject = render(template, {"agent": agent, "reason": reason})
    over = len(subject) - MAX_SUBJECT_CHARS
    named = sum(1 for part in parse(template) if isinstance(part, Field) and part.name == "reason")
    if over <= 0 or not named or not reason:
        return subject
    keep = max(len(reason) - math.ceil(over / named) - 1, 0)
    return render(template, {"agent": agent, "reason": reason[:keep].rstrip() + "…"})


def commit_message(agent: str, reason: str, *, template: str) -> str:
    """The salvage commit's message: plainly unfinished, plainly embry0's."""
    return (
        f"{commit_subject(agent, reason, template=template)}\n\n"
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
    subject_template: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Commit what a stopped run left uncommitted and push the branch.

    ``subject_template`` is the job's ``salvage.commit_subject``.

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
            message=commit_message(agent, reason, template=subject_template),
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


def _subject_of(record: dict[str, Any], template: str) -> str:
    """The subject the salvage commit of ``record`` was made with."""
    return commit_subject(str(record.get("agent") or "developer"), str(record.get("reason") or ""), template=template)


def prompt_section(record: dict[str, Any] | None, *, subject_template: str) -> str | None:
    """What the next coding attempt is told about the unfinished commit; None without one.

    ``subject_template`` is the job's ``salvage.commit_subject``, which the
    commit was made with: the prompt quotes the commit's subject, filled from
    the record exactly as the commit's was.
    """
    if not record or not record.get("committed"):
        return None
    branch = record.get("branch") or "the job's branch"
    subject = _subject_of(record, subject_template)
    return (
        f"\nUnfinished work from your earlier run:\nThat run stopped ({record.get('reason') or 'before it finished'}) "
        f"before it committed. embry0 committed what it left in the working tree as `{subject}` "
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
