"""Failing and flaky CI on a GitLab project's default branch (#181): its pipelines and their jobs, read over the real
client with GitLab faked by its answers."""

from __future__ import annotations

from typing import Any

from embry0.services.leads.gitlab_ci import find_failing_jobs, find_flaky_jobs
from tests.unit.services.leads.fakes import fake_gitlab

REPO = "acme/sub/web"
P = "/projects/acme%2Fsub%2Fweb"


def _job(name: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "web_url": f"https://gitlab.example.test/j/{name}-{status}", **extra}


async def test_each_job_failing_in_the_newest_pipeline_is_one_lead_keyed_as_a_check():
    asked: list[str] = []
    client = fake_gitlab(
        {
            P: {"default_branch": "trunk"},
            f"{P}/pipelines": [{"id": 9, "sha": "a" * 40}, {"id": 8, "sha": "b" * 40}],
            f"{P}/pipelines/9/jobs": [
                _job("build", "failed", failure_reason="script_failure"),
                _job("build", "failed"),
                _job("lint", "success"),
                _job("audit", "failed", allow_failure=True),
                _job("deploy", "running"),
            ],
        },
        asked,
    )
    result = await find_failing_jobs(client, REPO)
    assert [(lead.source_key, lead.kind) for lead in result.leads] == [("check:build", "ci")]
    lead = result.leads[0]
    assert lead.title == "build fails on trunk" and "script failure" in lead.details and result.complete
    assert lead.data["sha"] == "a" * 40 and lead.data["source"] == "gitlab-job"
    assert f"{P}/pipelines/8/jobs" not in asked, "only the newest pipeline is read"


async def test_a_project_that_cannot_be_read_is_skipped_and_one_with_no_pipeline_has_nothing_failing():
    assert (await find_failing_jobs(fake_gitlab({}), REPO)).skipped == "the default branch could not be read"
    none = await find_failing_jobs(fake_gitlab({P: {"default_branch": "main"}, f"{P}/pipelines": []}), REPO)
    assert none.leads == [] and none.complete
    unread = await find_failing_jobs(fake_gitlab({P: {"default_branch": "main"}}), REPO)
    assert unread.leads == [] and not unread.complete, "pipelines that cannot be read close nothing"
    assert unread.skipped == "the default branch's pipelines could not be read", "and say so"


async def test_a_job_that_failed_then_passed_in_one_pipeline_is_flaky():
    client = fake_gitlab(
        {
            P: {"default_branch": "main"},
            f"{P}/pipelines": [{"id": 3, "sha": "c" * 40}, {"id": 2, "sha": "d" * 40}],
            f"{P}/pipelines/3/jobs": [_job("test", "failed"), _job("test", "success"), _job("lint", "failed")],
            f"{P}/pipelines/2/jobs": [
                _job("test", "success"),
                _job("audit", "failed", allow_failure=True),
                _job("audit", "success"),
            ],
        }
    )
    result = await find_flaky_jobs(client, REPO, commits=2)
    assert [(lead.source_key, lead.data["shas"]) for lead in result.leads] == [("flaky:test", ["c" * 40])]
    assert result.leads[0].severity == "medium" and result.complete
    assert "in the last 2 pipelines of main" in result.leads[0].summary
