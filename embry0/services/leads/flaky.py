"""Flaky checks (leads, spec 2026-10-25): a check that failed and then passed on the same commit.

Reads every run of every check (not only the latest) on the newest commits of
the default branch. A check that both failed and succeeded on one commit was
re-run until it passed: one lead per such check, keyed by its name, saying
on how many of the commits read it happened.
"""

from __future__ import annotations

from typing import Any

from embry0.services.leads import FinderResult, Lead, skipped
from embry0.services.leads.ci import FAILED, default_head

KIND = "flaky"
#: Commits of the default branch read per pass, newest first.
DEFAULT_COMMITS = 10


async def find_flaky_checks(client: Any, repo: str, *, commits: int = DEFAULT_COMMITS) -> FinderResult:
    """Every check that failed and passed on one of the default branch's newest commits."""
    head = await default_head(client, repo)
    if head is None:
        return skipped("the default branch could not be read")
    branch = head[0]
    resp = await client.get(f"/repos/{repo}/commits", repo=repo, params={"sha": branch, "per_page": commits})
    if resp.status_code != 200 or not isinstance(resp.json(), list):
        return FinderResult([], complete=False)
    shas = [str(c.get("sha")) for c in resp.json() if isinstance(c, dict) and c.get("sha")]
    complete = True
    flaky: dict[str, dict[str, Any]] = {}
    for sha in shas:
        runs = await client.get(
            f"/repos/{repo}/commits/{sha}/check-runs", repo=repo, params={"filter": "all", "per_page": 100}
        )
        if runs.status_code != 200 or not isinstance(runs.json(), dict):
            complete = False
            continue
        outcomes: dict[str, set[str]] = {}
        urls: dict[str, str] = {}
        for run in runs.json().get("check_runs") or []:
            if not isinstance(run, dict) or run.get("status") != "completed" or not run.get("name"):
                continue
            name = str(run["name"])
            outcomes.setdefault(name, set()).add(str(run.get("conclusion") or ""))
            if run.get("conclusion") == "success" and run.get("html_url"):
                urls[name] = str(run["html_url"])
        for name, seen in outcomes.items():
            if seen & FAILED and "success" in seen:
                entry = flaky.setdefault(name, {"shas": [], "url": urls.get(name)})
                entry["shas"].append(sha)
    leads = [
        Lead(
            kind=KIND,
            repo=repo,
            source_key=f"flaky:{name}",
            title=f"{name} is flaky on {branch}",
            summary=(
                f"{name} failed and then passed on the same commit {len(entry['shas'])} "
                f"{'time' if len(entry['shas']) == 1 else 'times'} in the last {len(shas)} commits of {branch}."
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
