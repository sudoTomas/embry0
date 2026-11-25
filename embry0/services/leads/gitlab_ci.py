"""Failing and flaky CI on a GitLab project's default branch (leads, #181): pipelines and their jobs.

The GitLab twins of :mod:`embry0.services.leads.ci` and :mod:`embry0.services.leads.flaky`, over a
:class:`~embry0.execution.gitlab_client.GitLabClient` with the organization's credential:

- ``ci``: the newest pipeline of the default branch; each job that failed (and may not fail) is one lead, keyed by
  the job's name as a check is (``check:<name>``), so the same job failing in the next pipeline is the same lead;
- ``flaky``: the pipelines of the default branch's newest commits; a job that failed and then passed in one
  pipeline (a retried job) is one lead, keyed ``flaky:<name>``.

A job allowed to fail is not a failure here, as GitLab does not count it against the pipeline.
"""

from __future__ import annotations

from typing import Any

from embry0.services.leads import FinderResult, Lead, skipped
from embry0.services.leads.ci import KIND as CI_KIND
from embry0.services.leads.flaky import DEFAULT_COMMITS
from embry0.services.leads.flaky import KIND as FLAKY_KIND

#: Pages of 100 jobs read per pipeline.
JOB_PAGES = 5
#: Why a finder read nothing when the default branch's pipelines could not be listed; nothing of it closes.
PIPELINES_UNREAD = "the default branch's pipelines could not be read"


async def default_branch(client: Any, repo: str) -> str | None:
    """The project's default branch; None when the project cannot be read."""
    project = await client.get_project(repo)
    branch = project.get("default_branch") if project else None
    return str(branch) if isinstance(branch, str) and branch else None


async def _pipelines(client: Any, repo: str, branch: str, count: int) -> list[dict[str, Any]] | None:
    """The newest ``count`` pipelines of ``branch``, newest first; None when they cannot be read."""
    resp = await client.get(
        client.project_path(repo, "/pipelines"),
        repo=repo,
        params={"ref": branch, "order_by": "id", "sort": "desc", "per_page": count},
    )
    body = resp.json() if resp.status_code == 200 else None
    if not isinstance(body, list):
        return None
    return [p for p in body if isinstance(p, dict) and p.get("id")]


async def _jobs(client: Any, repo: str, pipeline_id: Any, *, retried: bool) -> tuple[list[dict[str, Any]], bool]:
    """A pipeline's jobs (every run of each when ``retried``) and whether the listing was whole."""
    params: dict[str, Any] = {"include_retried": "true"} if retried else {}
    listing = await client.paginate_listing(
        client.project_path(repo, f"/pipelines/{pipeline_id}/jobs"),
        repo=repo,
        params=params,
        per_page=100,
        max_pages=JOB_PAGES,
    )
    return [j for j in listing.items if isinstance(j, dict) and j.get("name")], listing.complete


async def find_failing_jobs(client: Any, repo: str) -> FinderResult:
    """Every job failing in the newest pipeline of the default branch."""
    branch = await default_branch(client, repo)
    if branch is None:
        return skipped("the default branch could not be read")
    pipelines = await _pipelines(client, repo, branch, 1)
    if pipelines is None:
        return skipped(PIPELINES_UNREAD)
    if not pipelines:
        return FinderResult([], complete=True)
    pipeline = pipelines[0]
    sha = str(pipeline.get("sha") or "")
    jobs, complete = await _jobs(client, repo, pipeline["id"], retried=False)
    leads: dict[str, Lead] = {}
    for job in jobs:
        name = str(job["name"])
        if name in leads or job.get("status") != "failed" or job.get("allow_failure"):
            continue
        why = str(job.get("failure_reason") or "").replace("_", " ")
        leads[name] = Lead(
            kind=CI_KIND,
            repo=repo,
            source_key=f"check:{name}",
            title=f"{name} fails on {branch}",
            summary=f"{name} is failing on {branch} of {repo} (commit {sha[:7]}).",
            details="\n".join(part for part in (f"Commit {sha} of {branch}, pipeline {pipeline['id']}.", why) if part),
            action=f"Start a job that finds why {name} fails on {branch} and fixes it",
            url=job.get("web_url"),
            severity="high",
            effort="medium",
            data={"check": name, "sha": sha, "branch": branch, "source": "gitlab-job"},
        )
    return FinderResult(list(leads.values()), complete=complete)


async def find_flaky_jobs(client: Any, repo: str, *, commits: int = DEFAULT_COMMITS) -> FinderResult:
    """Every job that failed and then passed in one of the default branch's newest pipelines."""
    branch = await default_branch(client, repo)
    if branch is None:
        return skipped("the default branch could not be read")
    pipelines = await _pipelines(client, repo, branch, commits)
    if pipelines is None:
        return skipped(PIPELINES_UNREAD)
    complete = True
    flaky: dict[str, dict[str, Any]] = {}
    for pipeline in pipelines:
        jobs, whole = await _jobs(client, repo, pipeline["id"], retried=True)
        complete = complete and whole
        outcomes: dict[str, set[str]] = {}
        urls: dict[str, str] = {}
        for job in jobs:
            name = str(job["name"])
            status = str(job.get("status") or "")
            if status == "failed" and job.get("allow_failure"):
                continue
            outcomes.setdefault(name, set()).add(status)
            if status == "success" and job.get("web_url"):
                urls[name] = str(job["web_url"])
        for name, seen in outcomes.items():
            if {"failed", "success"} <= seen:
                entry = flaky.setdefault(name, {"shas": [], "url": urls.get(name)})
                entry["shas"].append(str(pipeline.get("sha") or ""))
    leads = [
        Lead(
            kind=FLAKY_KIND,
            repo=repo,
            source_key=f"flaky:{name}",
            title=f"{name} is flaky on {branch}",
            summary=(
                f"{name} failed and then passed in the same pipeline {len(entry['shas'])} "
                f"{'time' if len(entry['shas']) == 1 else 'times'} in the last {len(pipelines)} pipelines of {branch}."
            ),
            details="Commits where it failed, then passed:\n" + "\n".join(f"- {sha}" for sha in entry["shas"]),
            action=f"Start a job that finds what makes {name} flaky and fixes it",
            url=entry["url"],
            severity="high" if len(entry["shas"]) >= 3 else "medium",
            effort="medium",
            data={"check": name, "shas": entry["shas"], "branch": branch},
        )
        for name, entry in flaky.items()
    ]
    return FinderResult(leads, complete=complete)
