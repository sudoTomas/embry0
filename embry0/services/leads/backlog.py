"""The backlog intake (leads, spec 2026-10-25): tickets nobody sent to embry0 that it could do.

Until now a ticket reached embry0 only when a person put the trigger label
on it. This finder reads the backlog itself: Linear tickets nobody started
(unassigned, or assigned to the account embry0's key acts as) whose project
resolves to the repository, and the repository's open GitHub issues nobody
holds. A ticket that already carries the trigger label, or that embry0
already tracks, is left to the paths that have it.

What it offers is judged first by a cheap reading of the ticket in its
organization's words (:class:`~embry0.services.leads.readiness.Readiness`,
which the loop builds for each repository from the ``leads`` section): a
description with something to act on (file paths, error text, what done
looks like) and a label that says chore or bug reads as ready; an epic, a
question or a one-line title does not. Only tickets at or above the minimum
score (``leads.offer_min``, read the same way) become leads. A second,
model-based reading is not part of this version.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from embry0.services.leads import FinderResult, Lead
from embry0.services.leads.readiness import Readiness, paths_in
from embry0.services.trigger_labels import normalize_labels

KIND = "ticket"
#: Pages of 100 GitHub issues read per repository per pass, newest-updated first.
GITHUB_ISSUE_PAGES = 3


def _summary(title: str, score: int, why: Sequence[str]) -> str:
    reasons = ", ".join(why[:3]) if why else "little to go on"
    return f"A backlog ticket embry0 could take ({score}/100 ready: {reasons}): {title}"


def ticket_lead(
    *,
    repo: str,
    tracker: str,
    key: str,
    title: str,
    body: str,
    url: str | None,
    labels: Sequence[str],
    priority: int = 0,
    estimate: float | None = None,
    min_score: int,
    readiness: Readiness,
) -> Lead | None:
    """The lead for one ticket, or None when it does not read as ready in ``readiness``'s words."""
    score, why = readiness.score(title, body, labels, estimate)
    if score < min_score:
        return None
    action = (
        "Put the trigger label on the ticket and start its pipeline"
        if tracker == "linear"
        else "Bring the issue into embry0 and start its pipeline"
    )
    return Lead(
        kind=KIND,
        repo=repo,
        source_key=f"{tracker}:{key}",
        title=title or key,
        summary=_summary(title or key, score, why),
        details="\n".join(
            part
            for part in (f"Readiness {score}/100: {', '.join(why) or 'little to go on'}.", (body or "").strip()[:2000])
            if part
        ),
        action=action,
        url=url,
        severity=readiness.severity(labels, priority),
        effort=readiness.effort(labels, estimate, body),
        paths=paths_in(f"{title}\n{body}"),
        labels=tuple(labels),
        data={"tracker": tracker, "key": key, "readiness": score},
    )


#: Whether embry0 already tracks a ticket: ``(tracker, repo, key)``.
Tracked = Callable[[str, str, str], Awaitable[bool]]


async def linear_leads(
    repo: str,
    tickets: Sequence[Any],
    *,
    trigger_labels: Iterable[str],
    tracked: Tracked,
    min_score: int,
    readiness: Readiness,
) -> list[Lead]:
    """The leads among Linear backlog tickets (``LinearSyncService.list_backlog_tickets``) for ``repo``, read in
    its organization's words (``readiness``)."""
    triggers = normalize_labels(trigger_labels)
    out: list[Lead] = []
    for ticket in tickets:
        issue = ticket.issue
        if ticket.repo.lower() != repo.lower() or (ticket.assigned and not ticket.mine):
            continue
        if normalize_labels(issue.labels) & triggers or await tracked("linear", ticket.repo, issue.key):
            continue
        lead = ticket_lead(
            repo=ticket.repo,
            tracker="linear",
            key=issue.key,
            title=issue.title,
            body=issue.body,
            url=issue.url,
            labels=list(issue.labels),
            priority=ticket.priority,
            estimate=ticket.estimate,
            min_score=min_score,
            readiness=readiness,
        )
        if lead is not None:
            out.append(lead)
    return out


async def find_github_tickets(
    client: Any,
    repo: str,
    *,
    trigger_labels: Iterable[str],
    tracked: Tracked,
    min_score: int,
    readiness: Readiness,
) -> FinderResult:
    """The repository's open GitHub issues nobody holds, not labelled for embry0 and not yet tracked, that read as ready
    in its organization's words (``readiness``)."""
    listing = await client.paginate_listing(
        f"/repos/{repo}/issues",
        repo=repo,
        params={"state": "open", "sort": "updated", "direction": "desc"},
        per_page=100,
        max_pages=GITHUB_ISSUE_PAGES,
    )
    triggers = normalize_labels(trigger_labels)
    out: list[Lead] = []
    for issue in listing.items:
        if not isinstance(issue, dict) or "pull_request" in issue or issue.get("assignees"):
            continue
        labels = [str(label.get("name")) for label in issue.get("labels") or [] if isinstance(label, dict)]
        key = str(issue.get("number") or "")
        if not key or normalize_labels(labels) & triggers or await tracked("github", repo, key):
            continue
        lead = ticket_lead(
            repo=repo,
            tracker="github",
            key=key,
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            url=issue.get("html_url"),
            labels=labels,
            min_score=min_score,
            readiness=readiness,
        )
        if lead is not None:
            out.append(lead)
    # A repository with more open issues than the pages hold is read in part, and closes nothing.
    return FinderResult(out, complete=listing.complete)
