"""A context audit's checked patch, committed on the job's own branch (ADR 0022).

The context-auditor ends its report with the patch it checked with
``git apply --check`` against a copy of the workspace, in a ``diff`` fenced
block under a ``## Pull`` heading. When the job's organization turned
``context_audit_open_prs`` on, the output step applies that patch to the
workspace, commits it on the one branch the sandbox may push, and pushes it
through the git proxy. The step then hands the executor the branch, a title
and a body, and the executor opens the pull request at completion the way it
opens one for any branch a job pushed and did not deliver.

The patch is read from the report rather than from a file the prompt names:
a builtin agent's prompt reaches a deployment's stored row only when the row
is first created, so a prompt change would not reach the agents already
running, and the report is what a person reviews anyway.

Nothing here fails the job: a report with no patch, an audit that found
nothing, an earlier audit's pull request still open, or a patch that no
longer applies each leaves the findings in the report and opens nothing.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from embry0.orchestration.branch_names import job_push_branch, shared_branch_name
from embry0.services.context_audit import report_counts

logger = structlog.get_logger(__name__)

#: Where the patch is written in the sandbox before it is applied.
PATCH_PATH = "/tmp/context-audit.diff"

#: The workspace the job's repository is cloned into.
WORKSPACE = "/workspace"

# The first ``diff`` fenced block: a fence of three backticks or more, closed
# by a line holding the same fence. A line inside a diff always starts with a
# space, a sign, ``@``, ``\`` or a header word, so no line of it closes the
# fence early.
_DIFF_BLOCK = re.compile(r"^(?P<fence>`{3,})diff[^\n]*\n(?P<body>.*?)\n(?P=fence)[ \t]*$", re.MULTILINE | re.DOTALL)
_PULL_HEADING = re.compile(r"^#{2,3}[ \t]+Pull\b.*$", re.MULTILINE)
_STALE_LINE = re.compile(r"^Stale:[ \t]*\d+[ \t]*$", re.MULTILINE)

_NOTE = (
    "Opened by the embry0 orchestrator from the patch the context audit checked with `git apply --check`. "
    "It changes only the files that steer coding agents. Review it before merging; close it unmerged to "
    "discard it, and the next audit starts over."
)


def report_patch(report: str | None) -> str | None:
    """The patch the report carries: the first ``diff`` block under its ``## Pull`` heading, else anywhere.

    None when the report carries no such block, such as one whose audit
    found nothing certain enough to fix, or one cut before its patch.
    """
    if not report:
        return None
    heading = _PULL_HEADING.search(report)
    match = _DIFF_BLOCK.search(report, heading.end() if heading else 0) or _DIFF_BLOCK.search(report)
    if match is None:
        return None
    body = match.group("body").strip("\n")
    return body + "\n" if body.strip() else None


def report_findings(report: str) -> str:
    """The report without the agent's opening lines and without its patch: what a pull request's body shows.

    From the ``Stale:`` line the report starts with, when it has one, to the
    ``## Pull`` heading, when it has one.
    """
    start = _STALE_LINE.search(report)
    text = report[start.start() :] if start else report
    heading = _PULL_HEADING.search(text)
    return (text[: heading.start()] if heading else text).strip()


def open_audit_pr(open_prs: list[dict[str, Any]] | None, task: str | None) -> dict[str, Any] | None:
    """An open pull request an earlier context audit of the repository opened, when the open-PR list names one.

    Every context audit's task starts the same way, so every audit derives
    the same shared branch name and pushes that name plus its own job id.
    """
    stem = shared_branch_name(issue_id=None, task=task) + "-"
    return next((pr for pr in open_prs or [] if str(pr.get("head_ref") or "").startswith(stem)), None)


def _title(stale: int) -> str:
    return f"docs: correct {stale} outdated {'claim' if stale == 1 else 'claims'} in agent instruction files"


def _body(stale: int, findings: str) -> str:
    claims = "claim" if stale == 1 else "claims"
    return (
        f"The context audit checked the files that steer coding agents in this repository against the code "
        f"and its history, and found {stale} outdated {claims}. This pull request applies the fixes it was "
        f"certain of; the table lists every finding, those left for a person included.\n\n{findings}"
    )


async def deliver_audit_patch(state: dict[str, Any], config: RunnableConfig, report: str) -> dict[str, Any]:
    """Commit and push the report's patch on the job's branch; the state the executor opens the PR from.

    Returns ``branch_name``, ``developer_summary`` (the body), ``pr_title``
    and ``pr_note``, or ``{}`` when nothing was pushed. Never raises.
    """
    job_id = state.get("job_id")
    stale, _files = report_counts(report)
    patch = report_patch(report)
    if not stale or patch is None:
        logger.info("context_audit_pr_skipped", job_id=job_id, reason="no patch" if stale else "nothing stale")
        return {}
    earlier = open_audit_pr(state.get("open_prs"), state.get("task"))
    if earlier is not None:
        logger.info(
            "context_audit_pr_skipped", job_id=job_id, reason="earlier audit's PR open", pr=earlier.get("number")
        )
        return {}
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    container = state.get("sandbox_container_id")
    if docker is None or not container:
        logger.warning("context_audit_pr_skipped", job_id=job_id, reason="no sandbox")
        return {}
    branch = job_push_branch(
        issue_id=state.get("issue_id"), task=state.get("task"), job_id=job_id, context=state.get("context")
    )
    message = _title(stale)
    # The workspace is the job's own clone at the commit the audit read. Tracked
    # files go back to that commit before the patch (the audit works on a copy,
    # so this only undoes what an agent should not have left); the untracked
    # deliverables directory stays. A zero-context hunk, which the auditor
    # emits for a one-line change, needs --unidiff-zero.
    script = " && ".join(
        [
            "set -e",
            f"cd {WORKSPACE}",
            "git reset -q --hard HEAD",
            f"git checkout -q -B {shlex.quote(branch)}",
            f"(git apply --index {PATCH_PATH} 2>/dev/null || git apply --index --unidiff-zero {PATCH_PATH})",
            f"git commit -q -m {shlex.quote(message)}",
            f"git push -q origin {shlex.quote('HEAD:refs/heads/' + branch)}",
        ]
    )
    try:
        await docker.copy_bytes_into(container, patch.encode(), PATCH_PATH)
        await docker.run_cmd(docker.build_exec_cmd(container, ["bash", "-c", script]), timeout=180)
    except Exception as exc:  # noqa: BLE001 - the findings stay in the report
        logger.warning("context_audit_pr_not_pushed", job_id=job_id, branch=branch, error=str(exc)[:300])
        return {}
    logger.info("context_audit_pr_pushed", job_id=job_id, branch=branch, stale=stale)
    return {
        "branch_name": branch,
        "developer_summary": _body(stale, report_findings(report)),
        "pr_title": message,
        "pr_note": _NOTE,
    }
