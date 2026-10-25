"""A map-areas job's file, committed on the job's own branch (spec 2026-10-25, map areas).

The area-mapper ends its report with the whole ``.embry0/areas.yml`` in a
``yaml`` fenced block under an ``## Areas file`` heading. The output step
writes that block to the file in the workspace, commits it on the one branch
the sandbox may push, and pushes it through the git proxy; the executor then
opens the pull request at completion, as it does for any branch a job pushed
and did not deliver (and names its area in the title, as for any other).

The file is read from the report, so what a person reviews is what lands.
Nothing here fails the job: a report with no file, a file that names no area
with a path, an earlier map's pull request still open, or a file the same as
the one on the branch each leaves the report as it is and opens nothing.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from embry0.orchestration.branch_names import job_push_branch
from embry0.services.areas import AREAS_PATH, parse_areas
from embry0.workflows.issue_to_pr.audit_delivery import WORKSPACE, open_audit_pr

logger = structlog.get_logger(__name__)

#: The pipeline template whose output step this is.
TEMPLATE = "map-areas"
#: Where the file is written in the sandbox before it is copied into the workspace.
FILE_PATH = "/tmp/embry0-areas.yml"
#: What the push prints when the file on the branch already says the same.
UNCHANGED = "embry0-areas-unchanged"

_HEADING = re.compile(r"^#{2,3}[ \t]+Areas file\b.*$", re.MULTILINE)
_YAML_BLOCK = re.compile(r"^(?P<fence>`{3,})ya?ml[^\n]*\n(?P<body>.*?)\n(?P=fence)[ \t]*$", re.MULTILINE | re.DOTALL)

_TITLE = "chore(embry0): map this repository's areas"
_NOTE = (
    "Opened by the embry0 orchestrator from the file its area mapper wrote. embry0 reads "
    f"`{AREAS_PATH}` from the default branch to name the part of the repository each proposal, "
    "pull request and job is about, and to ask that part's owners first. Edit it like any other file."
)


def report_file(report: str | None) -> str | None:
    """The file the report carries: the first ``yaml`` block under ``## Areas file``, else None."""
    if not report:
        return None
    heading = _HEADING.search(report)
    if heading is None:
        return None
    match = _YAML_BLOCK.search(report, heading.end())
    if match is None:
        return None
    body = match.group("body").strip("\n")
    return body + "\n" if body.strip() else None


def _summary(report: str) -> str:
    """The report without its file: the table and the notes a pull request's body shows."""
    heading = _HEADING.search(report)
    text = report[: heading.start()] if heading else report
    rest = report[heading.end() :] if heading else ""
    after = _YAML_BLOCK.search(rest)
    if after is not None:
        text += rest[after.end() :]
    return text.strip()


async def deliver_areas_file(state: dict[str, Any], config: RunnableConfig, report: str) -> dict[str, Any]:
    """Commit and push the report's areas file on the job's branch; the state the executor opens the PR from.

    Returns ``branch_name``, ``developer_summary``, ``pr_title`` and
    ``pr_note``, or ``{}`` when nothing was pushed. Never raises.
    """
    job_id = state.get("job_id")
    text = report_file(report)
    areas = [a for a in parse_areas(text) if a.paths]
    if text is None or not areas:
        logger.info("map_areas_pr_skipped", job_id=job_id, reason="no file" if text is None else "no area with a path")
        return {}
    earlier = open_audit_pr(state.get("open_prs"), state.get("task"))
    if earlier is not None:
        logger.info("map_areas_pr_skipped", job_id=job_id, reason="earlier map's PR open", pr=earlier.get("number"))
        return {}
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    container = state.get("sandbox_container_id")
    if docker is None or not container:
        logger.warning("map_areas_pr_skipped", job_id=job_id, reason="no sandbox")
        return {}
    branch = job_push_branch(
        issue_id=state.get("issue_id"), task=state.get("task"), job_id=job_id, context=state.get("context")
    )
    # The workspace is the job's own clone; tracked files go back to its commit
    # first, so only the file lands. A file the branch already has as written
    # commits nothing and opens nothing.
    script = " && ".join(
        [
            "set -e",
            f"cd {WORKSPACE}",
            "git reset -q --hard HEAD",
            f"git checkout -q -B {shlex.quote(branch)}",
            f"mkdir -p {shlex.quote(AREAS_PATH.rsplit('/', 1)[0])}",
            f"cp {FILE_PATH} {shlex.quote(AREAS_PATH)}",
            f"git add {shlex.quote(AREAS_PATH)}",
            f"if git diff --cached --quiet; then echo {UNCHANGED}; else "
            f"git commit -q -m {shlex.quote(_TITLE)} && git push -q origin {shlex.quote('HEAD:refs/heads/' + branch)}; fi",
        ]
    )
    try:
        await docker.copy_bytes_into(container, text.encode(), FILE_PATH)
        out = await docker.run_cmd(docker.build_exec_cmd(container, ["bash", "-c", script]), timeout=180)
    except Exception as exc:  # noqa: BLE001 - the file stays in the report
        logger.warning("map_areas_pr_not_pushed", job_id=job_id, branch=branch, error=str(exc)[:300])
        return {}
    if UNCHANGED in str(out or ""):
        logger.info("map_areas_pr_skipped", job_id=job_id, reason="file unchanged")
        return {}
    logger.info("map_areas_pr_pushed", job_id=job_id, branch=branch, areas=len(areas))
    names = ", ".join(a.name for a in areas)
    return {
        "branch_name": branch,
        "developer_summary": f"Names {len(areas)} {'area' if len(areas) == 1 else 'areas'} in `{AREAS_PATH}`: "
        f"{names}.\n\n{_summary(report)}",
        "pr_title": _TITLE,
        "pr_note": _NOTE,
    }
