"""Unanswered review comments (leads, spec 2026-10-25).

Reads the open pull requests of the repository with their review threads
(GitHub's GraphQL API: the REST API does not say whether a thread is
resolved). A thread is unanswered when it is neither resolved nor outdated,
a reviewer started it, and its newest comment, not by the pull request's
author, is older than the staleness window. Each pull request with one or
more is one lead; accepting it starts a follow-up job on its branch. Drafts
and pull requests from forks are left out: a follow-up cannot push to a fork.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from embry0.services.leads import FinderResult, Lead

KIND = "review"
#: Days a thread waits for an answer before it counts.
DEFAULT_STALE_DAYS = 3
#: Open pull requests read per pass, most recently updated first.
PULLS = 30

_QUERY = """
query OpenReviewThreads($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number title url isDraft isCrossRepository headRefName
        author { login }
        reviewThreads(first: 50) {
          nodes {
            isResolved isOutdated path line
            opened: comments(first: 1) { nodes { author { login } body createdAt } }
            latest: comments(last: 1) { nodes { author { login } createdAt } }
          }
        }
      }
    }
  }
}
"""


def _parse_time(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(node: Any) -> str:
    return str(((node or {}).get("author") or {}).get("login") or "")


def unanswered(pull: dict[str, Any], *, now: datetime, stale_days: int) -> list[dict[str, Any]]:
    """The review threads of ``pull`` a reviewer started that nobody answered for ``stale_days``."""
    author = _login(pull)
    out: list[dict[str, Any]] = []
    for thread in ((pull.get("reviewThreads") or {}).get("nodes")) or []:
        if not isinstance(thread, dict) or thread.get("isResolved") or thread.get("isOutdated"):
            continue
        opened = (((thread.get("opened") or {}).get("nodes")) or [None])[0] or {}
        latest = (((thread.get("latest") or {}).get("nodes")) or [None])[0] or {}
        reviewer = _login(opened)
        if not reviewer or reviewer == author or _login(latest) == author:
            continue
        when = _parse_time(latest.get("createdAt"))
        if when is None or now - when < timedelta(days=stale_days):
            continue
        out.append(
            {
                "path": thread.get("path"),
                "line": thread.get("line"),
                "reviewer": reviewer,
                "excerpt": " ".join(str(opened.get("body") or "").split())[:300],
            }
        )
    return out


def review_lead(repo: str, pull: dict[str, Any], threads: list[dict[str, Any]]) -> Lead:
    number = pull["number"]
    paths = tuple(dict.fromkeys(str(t["path"]) for t in threads if t.get("path")))
    return Lead(
        kind=KIND,
        repo=repo,
        source_key=f"pr:{number}",
        title=f"#{number} {pull.get('title') or ''}".strip(),
        summary=(
            f"{len(threads)} review {'thread waits' if len(threads) == 1 else 'threads wait'} for an answer "
            f"on #{number}: {pull.get('title') or ''}".strip()
        ),
        details="\n".join(
            f"- {t.get('path') or 'the pull request'}{':' + str(t['line']) if t.get('line') else ''} "
            f"({t['reviewer']}): {t['excerpt']}"
            for t in threads
        ),
        action="Start a follow-up job on the pull request's branch that answers its review threads",
        url=pull.get("url"),
        severity="medium" if len(threads) >= 3 else "low",
        effort="small",
        paths=paths,
        data={"number": number, "head_ref": pull.get("headRefName"), "threads": threads},
    )


async def find_unanswered_reviews(
    client: Any, repo: str, *, now: datetime, stale_days: int = DEFAULT_STALE_DAYS
) -> FinderResult:
    """Every open, same-repository, non-draft pull request with review threads nobody answered."""
    owner, name = repo.split("/", 1)
    resp = await client.post(
        "/graphql", repo=repo, json={"query": _QUERY, "variables": {"owner": owner, "name": name, "first": PULLS}}
    )
    body = resp.json() if resp.status_code == 200 else None
    if not isinstance(body, dict) or body.get("errors") or not (body.get("data") or {}).get("repository"):
        return FinderResult([], complete=False)
    pulls = body["data"]["repository"]["pullRequests"] or {}
    leads = []
    for pull in pulls.get("nodes") or []:
        if (
            not isinstance(pull, dict)
            or pull.get("isDraft")
            or pull.get("isCrossRepository")
            or pull.get("number") is None
        ):
            continue
        threads = unanswered(pull, now=now, stale_days=stale_days)
        if threads:
            leads.append(review_lead(repo, pull, threads))
    return FinderResult(leads, complete=int(pulls.get("totalCount") or 0) <= PULLS)
