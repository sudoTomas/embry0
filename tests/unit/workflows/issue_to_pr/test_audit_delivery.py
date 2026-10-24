"""A context audit's checked patch becomes a pull request when its organization asks for one (ADR 0022)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.branch_names import job_push_branch
from embry0.services.context_audit import audit_task
from embry0.workflows.issue_to_pr import audit_delivery
from embry0.workflows.issue_to_pr.audit_delivery import (
    PATCH_PATH,
    deliver_audit_patch,
    open_audit_pr,
    report_findings,
    report_patch,
)

PATCH = (
    "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -12,3 +12,3 @@\n ```bash\n-old line\n+new line\n ```\n"
    "@@ -40 +40 @@\n-two backends\n+every backend\n"
)
REPORT = (
    "I'll audit the steering files at the named commit first.\n\n"
    "Stale: 2\nFiles: 1\n\n## Findings\n\n| Severity | Location | Claim | Evidence | Fix |\n|---|---|---|---|---|\n"
    "| misleads | CLAUDE.md:12 | old line | src/x.py:3 | new line |\n\n"
    "Coverage: checked 40.\n\n## Pull\n\n`git apply --check` exited 0.\n\n"
    f"````diff\n{PATCH}````\n\n### Unverifiable\n\nNone.\n"
)


class FakeDocker:
    def __init__(self, fail: str | None = None) -> None:
        self.copied: list[tuple[str, bytes, str]] = []
        self.commands: list[list[str]] = []
        self.fail = fail

    def build_exec_cmd(self, container: str, command: list[str], **_kw: Any) -> list[str]:
        return ["docker", "exec", container, *command]

    async def copy_bytes_into(self, container: str, data: bytes, dst_path: str) -> None:
        self.copied.append((container, data, dst_path))

    async def run_cmd(self, cmd: list[str], timeout: int = 0) -> str:
        self.commands.append(cmd)
        if self.fail:
            raise RuntimeError(self.fail)
        return ""


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-3f2a9c1e5b7d",
        "issue_id": None,
        "task": "Audit the files that steer a coding agent in acme/web for statements that are stale or wrong.",
        "context": {"type": "git", "repo": "acme/web"},
        "sandbox_container_id": "C1",
        "open_pr": True,
        **extra,
    }


def _config(docker: Any) -> dict[str, Any]:
    return {"configurable": {"docker": docker}}


# ---- reading the report ----


def test_the_patch_is_the_diff_block_under_the_pull_heading_whatever_its_fence() -> None:
    assert report_patch(REPORT) == PATCH, "a ``` line inside the diff does not close a four-backtick fence"
    assert report_patch(REPORT.replace("````", "```")) == PATCH, "every diff line has a prefix, so none closes it"
    early = "```diff\n-a\n+b\n```\n\n" + REPORT
    assert report_patch(early) == PATCH, "the block under ## Pull wins over an earlier one"
    assert report_patch("Stale: 0\nFiles: 1\n\n## Findings\n") is None
    assert report_patch(None) is None and report_patch("") is None


def test_the_body_is_the_findings_without_the_agents_opening_or_the_patch() -> None:
    findings = report_findings(REPORT)
    assert findings.startswith("Stale: 2\nFiles: 1")
    assert "| misleads | CLAUDE.md:12 |" in findings and "Coverage: checked 40." in findings
    assert "I'll audit" not in findings and "+new line" not in findings and "Unverifiable" not in findings


def test_an_earlier_audits_open_pr_is_found_by_its_branch() -> None:
    task = _state()["task"]
    earlier_branch = job_push_branch(issue_id=None, task=task, job_id="job-aaaaaaaaaaaa")
    prs = [
        {"number": 4, "head_ref": "embry0/iss-1234-fix-the-thing"},
        {"number": 7, "head_ref": earlier_branch},
    ]
    assert open_audit_pr(prs, task) == prs[1]
    assert open_audit_pr(prs[:1], task) is None and open_audit_pr(None, task) is None


def test_every_audit_of_any_repository_shares_the_branch_stem() -> None:
    """The open-PR check reads the stem every audit's task derives; a change to
    the task's first words that broke that would let a second PR stack up."""
    from embry0.services.context_audit import RepoContext

    tasks = [
        audit_task(RepoContext(repo=r, head_sha="1" * 40, files=("CLAUDE.md",), fingerprint="f"))
        for r in ("a/b", "c/d")
    ]
    stems = {job_push_branch(issue_id=None, task=t, job_id="job-0").rsplit("-", 1)[0] for t in tasks}
    assert len(stems) == 1, stems


# ---- delivering ----


@pytest.mark.asyncio
async def test_the_patch_is_committed_on_the_jobs_own_branch_and_pushed() -> None:
    docker = FakeDocker()
    state = _state()
    out = await deliver_audit_patch(state, _config(docker), REPORT)
    branch = job_push_branch(issue_id=None, task=state["task"], job_id=state["job_id"], context=state["context"])
    assert out["branch_name"] == branch, "the one branch the sandbox's git-proxy enrolment lets it push"
    assert docker.copied == [("C1", PATCH.encode(), PATCH_PATH)]
    script = docker.commands[0][-1]
    assert f"git checkout -q -B {branch}" in script
    assert f"git apply --index {PATCH_PATH}" in script and "--unidiff-zero" in script
    assert f"git push -q origin HEAD:refs/heads/{branch}" in script
    assert out["pr_title"] == "docs: correct 2 outdated claims in agent instruction files"
    assert "found 2 outdated claims" in out["developer_summary"]
    assert "| misleads | CLAUDE.md:12 |" in out["developer_summary"]
    assert "+new line" not in out["developer_summary"], "the PR's diff shows the patch; the body does not repeat it"
    assert "git apply --check" in out["pr_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report", "extra", "reason"),
    [
        ("Stale: 0\nFiles: 3\n\n## Findings\n", {}, "nothing stale"),
        (REPORT.split("## Pull")[0], {}, "no patch"),
        (
            REPORT,
            {"open_prs": [{"number": 7, "head_ref": "embry0/unknown-audit-the-files-that-steer-a-c-aaaaaaaa"}]},
            "earlier audit's PR open",
        ),
        (REPORT, {"sandbox_container_id": None}, "no sandbox"),
    ],
)
async def test_nothing_is_pushed_without_a_patch_or_while_an_earlier_pr_is_open(
    report: str, extra: dict[str, Any], reason: str
) -> None:
    docker = FakeDocker()
    with patch.object(audit_delivery, "logger") as log:
        assert await deliver_audit_patch(_state(**extra), _config(docker), report) == {}
    assert docker.commands == [] and docker.copied == []
    logged = [c.kwargs.get("reason") for c in log.info.call_args_list + log.warning.call_args_list]
    assert reason in logged, logged


@pytest.mark.asyncio
async def test_a_patch_that_no_longer_applies_opens_nothing_and_fails_nothing() -> None:
    docker = FakeDocker(fail="error: patch failed: CLAUDE.md:12")
    assert await deliver_audit_patch(_state(), _config(docker), REPORT) == {}
    assert len(docker.commands) == 1


# ---- the output step asks for it only when the job does ----


@pytest.mark.asyncio
@pytest.mark.parametrize("open_pr", [True, None])
async def test_the_output_step_delivers_only_for_a_job_that_asks(open_pr: bool | None) -> None:
    from embry0.workflows.issue_to_pr.plan_route import finalize_output_node

    deliver = AsyncMock(
        return_value={"branch_name": "embry0/x", "pr_title": "t", "pr_note": "n", "developer_summary": "b"}
    )
    state = _state(
        open_pr=open_pr, agent_outputs=[{"agent_type": "context-auditor", "is_error": False, "output": REPORT}]
    )
    with (
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.audit_delivery.deliver_audit_patch", new=deliver),
    ):
        out = await finalize_output_node(state, {"configurable": {}})
    if open_pr:
        deliver.assert_awaited_once()
        assert deliver.await_args.args[2] == REPORT.strip(), "the whole report, not the truncated summary"
        assert out["branch_name"] == "embry0/x" and out["pr_title"] == "t"
    else:
        deliver.assert_not_awaited()
        assert "branch_name" not in out


# ---- the executor opens the PR with the audit's title and note ----


def _executor() -> Any:
    from embry0.services.issue_executor import IssueExecutor

    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value={"repo": "acme/web", "task": _state()["task"], "issue_number": None})
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._github_token = "t"
    ex._repo_prefs = None
    return ex


@pytest.mark.asyncio
async def test_the_executor_opens_the_pr_with_the_audits_title_body_and_note() -> None:
    ex = _executor()
    opened = AsyncMock(return_value="https://github.com/acme/web/pull/12")
    result = {
        "triage_decision": {"action": "proceed"},
        "current_stage": "output_finalized",
        "branch_name": "embry0/unknown-audit-the-files-that-steer-a-c-3f2a9c1e",
        "developer_summary": "The context audit found 2 outdated claims.",
        "pr_title": "docs: correct 2 outdated claims in agent instruction files",
        "pr_note": "Opened by the embry0 orchestrator from the patch the context audit checked.",
        "result_summary": REPORT,
        "agent_outputs": [{"agent_type": "context-auditor", "is_error": False, "output": REPORT}],
    }
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._open_change_request", new=opened),
        patch("embry0.services._executor_outcomes._base_branch_for_change_request", new=AsyncMock(return_value="main")),
    ):
        await ex._handle_workflow_result(None, "job-3f2a9c1e5b7d", result)
    opened.assert_awaited_once()
    kwargs = opened.await_args.kwargs
    assert kwargs["title"] == result["pr_title"], "not the task's first line"
    assert kwargs["branch"] == result["branch_name"] and kwargs["labels"] == ["embry0"]
    assert result["developer_summary"] in kwargs["body"] and result["pr_note"] in kwargs["body"]
    assert "the developer pushed" not in kwargs["body"]
    assert ex._jobs.update.await_args.kwargs["pr_url"] == "https://github.com/acme/web/pull/12"
