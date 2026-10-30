"""The backlog intake (leads, spec 2026-10-25): tickets nobody sent to embry0 that it could do.

Until now a ticket reached embry0 only when a person put the trigger label
on it. This finder reads the backlog itself: Linear tickets nobody started
(unassigned, or assigned to the account embry0's key acts as) whose project
resolves to the repository, and the repository's open GitHub issues nobody
holds. A ticket that already carries the trigger label, or that embry0
already tracks, is left to the paths that have it.

What it offers is judged first by a cheap reading of the ticket
(:func:`readiness`): a description with something to act on (file paths,
error text, what done looks like) and a label that says chore or bug reads
as ready; an epic, a question or a one-line title does not. Only tickets at
or above the minimum score (``leads.offer_min`` in the settings document,
read by the loop for each repository's organization) become leads. A second,
model-based reading is not part of this version.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from embry0.services.leads import FinderResult, Lead
from embry0.services.trigger_labels import normalize_labels

KIND = "ticket"
#: Pages of 100 GitHub issues read per repository per pass, newest-updated first.
GITHUB_ISSUE_PAGES = 3

_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w{1,6}|[\w-]+\.(?:py|ts|tsx|js|jsx|java|kt|go|rs|rb|php|cs|sql|ya?ml|json|toml|md))(?![\w/])"
)
_ERROR = re.compile(
    r"(Traceback|Exception\b|Error:|\bERROR\b|stack ?trace|\bHTTP [45]\d\d\b|\b[45]\d\d (?:error|response)|panic:|segfault|NullPointer)",
    re.IGNORECASE,
)
_DONE = re.compile(
    r"(acceptance criteria|done when|definition of done|expected (?:behaviou?r|result)|steps to reproduce|should\b|- \[ \])",
    re.IGNORECASE,
)
_READY_LABELS = frozenset(
    {
        "bug",
        "chore",
        "tech-debt",
        "tech debt",
        "refactor",
        "test",
        "tests",
        "docs",
        "documentation",
        "dependencies",
        "maintenance",
    }
)
_EASY_LABELS = frozenset(
    {"good first issue", "good-first-issue", "easy", "small", "xs", "s", "size:xs", "size:s", "size/xs", "size/s"}
)
_LARGE_LABELS = frozenset({"large", "l", "xl", "size:l", "size:xl", "size/l", "size/xl"})
_NOT_READY_LABELS = frozenset(
    {
        "epic",
        "design",
        "discussion",
        "question",
        "spike",
        "research",
        "needs design",
        "needs-design",
        "needs discussion",
        "rfc",
        "idea",
        "wontfix",
        "blocked",
    }
)
#: A title that says the ticket is an epic ("Epic: ...", "[Epic] ...", "epic(scope): ..."), labelled or not.
_EPIC_TITLE = re.compile(r"^\s*(?:\[\s*epic\s*\]|epic\s*(?:\([^)]*\))?\s*[:\-\u2014\u2013])", re.IGNORECASE)
_URGENT_LABELS = frozenset({"critical", "urgent", "p0", "sev1", "security"})
_HIGH_LABELS = frozenset({"high", "p1", "sev2", "regression"})


def paths_in(text: str) -> tuple[str, ...]:
    """The file paths a ticket names, in order, each once."""
    seen: dict[str, None] = {}
    for match in _PATH.finditer(text or ""):
        seen.setdefault(match.group(1).strip("./"), None)
    return tuple(seen)


def readiness(title: str, body: str, labels: Iterable[str], estimate: float | None = None) -> tuple[int, list[str]]:
    """A 0 to 100 reading of whether an agent could start on the ticket as written, and why.

    Cheap on purpose: it decides what a person is asked about, not what runs.
    """
    names = normalize_labels(labels)
    text = body or ""
    score = 0
    why: list[str] = []
    if len(text) >= 400:
        score, why = score + 25, [*why, "a full description"]
    elif len(text) >= 80:
        score, why = score + 20, [*why, "a description"]
    elif text.strip():
        score += 5
    if paths_in(f"{title}\n{text}"):
        score, why = score + 20, [*why, "names files"]
    if _ERROR.search(f"{title}\n{text}"):
        score, why = score + 10, [*why, "quotes an error"]
    if _DONE.search(text):
        score, why = score + 15, [*why, "says what done looks like"]
    if names & _READY_LABELS:
        score, why = score + 10, [*why, f"labelled {sorted(names & _READY_LABELS)[0]}"]
    if names & _EASY_LABELS or (estimate is not None and estimate <= 2):
        score, why = score + 10, [*why, "small"]
    if names & _LARGE_LABELS or (estimate is not None and estimate >= 5):
        score, why = score - 10, [*why, "large"]
    if names & _NOT_READY_LABELS:
        score, why = score - 30, [*why, f"labelled {sorted(names & _NOT_READY_LABELS)[0]}"]
    elif _EPIC_TITLE.match(title or ""):
        score, why = score - 30, [*why, "an epic by its title"]
    if len(title.split()) <= 3:
        score, why = score - 10, [*why, "a short title"]
    return max(0, min(100, score)), why


def effort_of(labels: Iterable[str], estimate: float | None, body: str) -> str:
    names = normalize_labels(labels)
    if names & _LARGE_LABELS or (estimate is not None and estimate >= 5):
        return "large"
    if names & _EASY_LABELS or (estimate is not None and estimate <= 2):
        return "small"
    return "medium" if len(body or "") >= 80 else "small"


def severity_of(labels: Iterable[str], priority: int = 0) -> str:
    """Linear's priority when set (1 urgent .. 4 low), else what the labels say."""
    if priority in (1, 2, 3, 4):
        return {1: "critical", 2: "high", 3: "medium", 4: "low"}[priority]
    names = normalize_labels(labels)
    if names & _URGENT_LABELS:
        return "critical"
    if names & _HIGH_LABELS:
        return "high"
    return "medium"


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
) -> Lead | None:
    """The lead for one ticket, or None when it does not read as ready."""
    score, why = readiness(title, body, labels, estimate)
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
        severity=severity_of(labels, priority),
        effort=effort_of(labels, estimate, body),
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
) -> list[Lead]:
    """The leads among Linear backlog tickets (``LinearSyncService.list_backlog_tickets``) for ``repo``."""
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
) -> FinderResult:
    """The repository's open GitHub issues nobody holds, not labelled for embry0 and not yet tracked, that read as ready."""
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
        )
        if lead is not None:
            out.append(lead)
    # A repository with more open issues than the pages hold is read in part, and closes nothing.
    return FinderResult(out, complete=listing.complete)
