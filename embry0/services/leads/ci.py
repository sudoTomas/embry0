"""Failing CI on the default branch (leads, spec 2026-10-25).

Reads the check runs and commit statuses on the newest commit of the
repository's default branch. Each check that finished failing is one lead,
keyed by the check's name, so the same check failing on the next commit is
the same lead seen again; once the head's checks pass, the lead is gone.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from embry0.services.leads import FinderResult, Lead, skipped

KIND = "ci"
#: Check-run conclusions that mean the check failed.
FAILED = frozenset({"failure", "timed_out", "startup_failure"})
#: Commit-status states that mean the check failed.
FAILED_STATES = frozenset({"failure", "error"})


async def default_head(client: Any, repo: str) -> tuple[str, str, str] | None:
    """The default branch, its newest commit and that commit's first line; None when unreadable."""
    record = await client.get_repo(repo)
    if not record:
        return None
    branch = str(record.get("default_branch") or "main")
    resp = await client.get(f"/repos/{repo}/commits/{quote(branch, safe='')}", repo=repo)
    if resp.status_code != 200:
        return None
    body = resp.json()
    if not isinstance(body, dict) or not body.get("sha"):
        return None
    message = str(((body.get("commit") or {}).get("message")) or "").splitlines()
    return branch, str(body["sha"]), message[0] if message else ""


def _lead(repo: str, branch: str, sha: str, name: str, url: str | None, why: str, source: str) -> Lead:
    return Lead(
        kind=KIND,
        repo=repo,
        source_key=f"check:{name}",
        title=f"{name} fails on {branch}",
        summary=f"{name} is failing on {branch} of {repo} (commit {sha[:7]}).",
        details="\n".join(part for part in (f"Commit {sha} of {branch}.", why) if part),
        action=f"Start a job that finds why {name} fails on {branch} and fixes it",
        url=url,
        severity="high",
        effort="medium",
        data={"check": name, "sha": sha, "branch": branch, "source": source},
    )


async def find_failing_checks(client: Any, repo: str) -> FinderResult:
    """Every check failing on the default branch's newest commit."""
    head = await default_head(client, repo)
    if head is None:
        return skipped("the default branch could not be read")
    branch, sha, _subject = head
    leads: dict[str, Lead] = {}
    complete = True
    runs = await client.get_check_runs(repo, sha)
    if runs is None:
        complete = False
    else:
        listed = [r for r in runs.get("check_runs") or [] if isinstance(r, dict)]
        complete = int(runs.get("total_count") or 0) <= len(listed)
        for run in listed:
            name = str(run.get("name") or "")
            if not name or name in leads or run.get("status") != "completed" or run.get("conclusion") not in FAILED:
                continue
            output = run.get("output") or {}
            why = "\n".join(str(output.get(k) or "").strip()[:1200] for k in ("title", "summary") if output.get(k))
            leads[name] = _lead(
                repo, branch, sha, name, run.get("html_url") or run.get("details_url"), why, "check-run"
            )
    statuses = await client.get_combined_status(repo, sha)
    if statuses is None:
        complete = False
    else:
        for status in statuses.get("statuses") or []:
            name = str((status or {}).get("context") or "")
            if not name or name in leads or (status or {}).get("state") not in FAILED_STATES:
                continue
            leads[name] = _lead(
                repo, branch, sha, name, status.get("target_url"), str(status.get("description") or ""), "status"
            )
    return FinderResult(list(leads.values()), complete=complete)
