"""Open-PR awareness for the developer/reviewer path (RAV-1079).

Before a pipeline job runs the developer, the executor fetches the target
repo's OPEN pull requests and (a) deterministically skips the job when an
open PR already addresses the same ticket (skip+link policy — the job
completes linking the existing PR instead of opening a duplicate), and
(b) injects the list into the triage/developer/reviewer prompts so the
agents can catch semantic duplicates the exact-match heuristic misses.

The watcher path has its own dedup (recent_proposal_titles + fingerprint,
RAV-1073) — this module is specifically about the post-accept pipeline.

Everything here is fail-open: a GitHub API failure yields an empty list
and the pipeline proceeds exactly as it did before RAV-1079.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from embry0.execution.github_client import GitHubClient

logger = structlog.get_logger(__name__)


# Tracker-style issue ids (RAV-1079, EMB-45, …). Deliberately loose — tokens
# like "phase-2" can match, but they are only ever compared for equality
# against the job's KNOWN ticket identifiers, so false tokens are inert.
_TRACKER_ID_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9}-\d{1,6})\b")

# Explicit GitHub closing markers in a PR body: "Fixes #12", "Closes RAV-34",
# "resolved: #56". Only these count as a body-level claim on an issue — a
# bare mention ("relates to RAV-34") is context, not ownership.
_CLOSING_MARKER_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+(?:#(\d+)|([A-Za-z][A-Za-z0-9]{1,9}-\d{1,6}))",
    re.IGNORECASE,
)


def parse_embry0_branch_issue_prefix(head_ref: str) -> str | None:
    """Issue-id prefix from the pipeline's own branch convention.

    Branches are ``embry0/{issue_id[:12]}-{slug}`` where the id segment is
    ``iss-`` + 8 hex chars — it CONTAINS a dash, so a ``split("-", 1)[0]``
    parse yields the useless literal "iss" (the bug that silently killed
    branch-based PR-linking pre-RAV-1092). Pre-"iss-" era branches were
    ``embry0/{hex}-{slug}``. Returns None for any other branch shape.

    Shared by the webhook PR-linking matcher, comment-dispatch donor lookup,
    and the open-PR duplicate heuristic — one parser, one bug surface.
    """
    if not head_ref.startswith("embry0/"):
        return None
    segment = head_ref.split("/", 1)[1]
    if segment.startswith("iss-"):
        return segment[:12] or None
    head = segment.split("-", 1)[0]
    return f"iss-{head}" if head else None


def extract_pr_issue_refs(head_ref: str, title: str, body: str) -> dict[str, Any]:
    """Issue references a PR claims, from its branch, title, and body.

    Returns ``{"issue_refs": [str], "closes_numbers": [int], "issue_prefix": str|None}``.

    - ``issue_refs``: uppercased tracker ids from the head branch and title
      (both are high-precision — embry0 and humans put the ticket id there),
      plus explicit Fixes/Closes/Resolves markers in the body. Bare body
      mentions are deliberately NOT collected.
    - ``closes_numbers``: GitHub issue numbers from body closing markers
      ("Closes #12" — the shape embry0's own developer prompt mandates).
    - ``issue_prefix``: the embry0 branch-convention issue-id prefix, if any.
    """
    refs: set[str] = set()
    closes: set[int] = set()

    for text in (head_ref, title):
        for m in _TRACKER_ID_RE.finditer(text or ""):
            refs.add(m.group(1).upper())

    for m in _CLOSING_MARKER_RE.finditer(body or ""):
        number, tracker_id = m.group(1), m.group(2)
        if number:
            closes.add(int(number))
        if tracker_id:
            refs.add(tracker_id.upper())

    return {
        "issue_refs": sorted(refs),
        "closes_numbers": sorted(closes),
        "issue_prefix": parse_embry0_branch_issue_prefix(head_ref or ""),
    }


def find_duplicate_pr(
    open_prs: list[dict[str, Any]],
    *,
    linear_identifier: str | None = None,
    github_number: int | None = None,
    issue_id: str | None = None,
) -> dict[str, Any] | None:
    """First open PR that already claims this job's ticket, or None.

    Matches (all exact, high-precision):
    - the issue's Linear identifier (e.g. "RAV-1066") against the PR's
      branch/title/body-marker refs;
    - the issue's GitHub number against body "Closes #N" markers;
    - the issue's id prefix against the embry0 branch convention (a
      re-labeled ticket whose earlier job already pushed a PR).

    Returns the matched PR entry with a ``matched_by`` key added.
    """
    ticket = (linear_identifier or "").upper() or None
    prefix = issue_id[:12] if issue_id else None
    for pr in open_prs:
        matched_by: str | None = None
        if ticket and ticket in (pr.get("issue_refs") or []):
            matched_by = f"ticket {ticket}"
        elif github_number is not None and github_number in (pr.get("closes_numbers") or []):
            matched_by = f"closes #{github_number}"
        elif prefix and pr.get("issue_prefix") == prefix:
            matched_by = f"branch issue prefix {prefix}"
        if matched_by:
            return {**pr, "matched_by": matched_by}
    return None


def format_open_prs_block(open_prs: list[dict[str, Any]], max_entries: int = 20) -> str:
    """Bounded prompt block listing open PRs; empty string when there are none."""
    if not open_prs:
        return ""
    lines = ["Existing OPEN pull requests on this repository (do not duplicate them):"]
    for pr in open_prs[:max_entries]:
        entry = f"- #{pr.get('number')} {str(pr.get('title') or '')[:100]} (head {pr.get('head_ref')}"
        refs = pr.get("issue_refs") or []
        if refs:
            entry += f"; refs {', '.join(refs[:5])}"
        entry += ")"
        lines.append(entry)
    remaining = len(open_prs) - max_entries
    if remaining > 0:
        lines.append(f"…and {remaining} more open PRs.")
    return "\n".join(lines)


def forge_said(exc: BaseException) -> str:
    """What a forge answered, for a person: its status and message when it sent one, else the error itself."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)[:300]
    message = ""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON is read as text
        payload = None
    if isinstance(payload, dict):
        message = str(payload.get("message") or "")
        details = [
            str(e.get("message") or e.get("code") or "") for e in payload.get("errors") or [] if isinstance(e, dict)
        ]
        details = [d for d in details if d]
        if details:
            message = f"{message} ({'; '.join(details)})" if message else "; ".join(details)
    if not message:
        message = (getattr(response, "text", "") or "")[:200]
    return f"{response.status_code} {message}".strip()[:300]


async def ensure_pr_for_branch(
    *,
    repo: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    github_token: str = "",
    http_client: httpx.AsyncClient | None = None,
    target_branch: str | None = None,
    credential_resolver: Any | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str | None:
    """Open (or find) a PR for ``branch`` orchestrator-side (RAV-1247).

    A turn-capped or JSON-less developer can complete with the branch pushed
    but ``pr_url`` null, stranding the work. The executor calls this at job
    completion; the orchestrator holds the token natively, so the fallback
    never depends on sandbox turns.

    Checks for an existing open PR with the same head first — a developer
    that opened the PR but failed to report ``pr_url`` must not produce a
    duplicate (GitHub would 422 the create anyway; the lookup recovers the
    URL instead). NEVER raises: any failure logs and returns None, and the
    job completes exactly as before, just without a PR.

    ``labels`` are applied to the PR best-effort (#163) — labeling used to be
    delegated to the sandbox agent via a prompt addendum, which contradicted
    the developer prompt's no-GitHub-access rule and so never worked. A PR is
    an issue to the labels API, and adding is idempotent, so found-existing
    PRs are labeled too.

    ``target_branch`` (Spec 1A §3.7) is the job's resolved base branch; when
    given it is used verbatim instead of re-reading the repo record, so the
    PR targets the same branch the clone fetched.

    ``diagnostics``, when given, receives ``change_request_error``: why no PR
    came of it, in words a person reads (#459). The return stays None.
    """

    def _why(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["change_request_error"] = reason[:300]

    if not repo or "/" not in repo or not branch:
        _why("the job has no repository or no branch to open a pull request from")
        return None
    # Same reasoning as gitlab_mrs.ensure_mr_for_branch: an explicit target is
    # compared exactly; without one the literals are the cheap pre-check for a
    # default we would otherwise have to fetch.
    if branch == target_branch or (target_branch is None and branch in ("main", "master")):
        _why(f"the branch {branch!r} is the base branch, so there is nothing to merge")
        return None
    from embry0.services.credentials.resolver import forge_token

    try:
        token = await forge_token(credential_resolver, repo, "github", github_token)
    except Exception as exc:
        # NEVER raises (see docstring): a pinned credential that is
        # unavailable skips the PR.
        logger.warning("pr_fallback_token_lookup_failed", repo=repo, exc_info=True)
        _why(f"embry0 could not read a GitHub token for {repo}: {exc}")
        return None
    if not token:
        _why(f"embry0 has no GitHub token for {repo}")
        return None
    owner = repo.split("/")[0]
    client = GitHubClient(default_token=github_token, http_client=http_client, credential_resolver=credential_resolver)
    try:
        resp = await client.get(
            f"/repos/{repo}/pulls",
            repo=repo,
            params={"state": "open", "head": f"{owner}:{branch}"},
        )
        resp.raise_for_status()
        existing = resp.json()
        if isinstance(existing, list) and existing:
            url = str((existing[0] or {}).get("html_url") or "")
            if url:
                logger.info("pr_fallback_found_existing", repo=repo, branch=branch, url=url)
                await _labels_or_say_so(
                    client,
                    repo=repo,
                    pr_number=(existing[0] or {}).get("number"),
                    labels=labels,
                    diagnostics=diagnostics,
                )
                return url

        base = target_branch or await client.get_repo_default_branch(repo)
        if branch == base:
            # Nothing to merge into itself — the repo's default happens to be
            # the branch the developer pushed.
            logger.info("pr_fallback_skipped_same_branch", repo=repo, branch=branch)
            _why(f"the branch {branch!r} is the repository's default branch, so there is nothing to merge")
            return None

        create = await client.post(
            f"/repos/{repo}/pulls",
            repo=repo,
            json={"title": title, "head": branch, "base": base, "body": body},
        )
        create.raise_for_status()
        created = create.json() or {}
        created_url = str(created.get("html_url") or "") or None
        logger.info("pr_fallback_created", repo=repo, branch=branch, url=created_url)
        await _labels_or_say_so(
            client, repo=repo, pr_number=created.get("number"), labels=labels, diagnostics=diagnostics
        )
        return created_url
    except Exception as exc:
        # GitHubClient owns the httpx lifecycle: it closes a client it created
        # and leaves an injected one alone, so there is no `finally` here.
        logger.warning("pr_fallback_failed", repo=repo, branch=branch, error=str(exc))
        _why(f"GitHub refused it: {forge_said(exc)}")
        return None


async def _apply_pr_labels(
    client: GitHubClient,
    *,
    repo: str,
    pr_number: Any,
    labels: list[str] | None,
) -> str | None:
    """Labels on a PR (a PR is an issue to the labels API).

    None when they are on it, or there is nothing to put on; otherwise what
    GitHub said, redacted and bounded. The forge client already sends the call
    again on an answer a retry can change (429, 5xx, a dropped connection);
    adding labels twice changes nothing, so that is safe here (#472).
    """
    if not labels or not isinstance(pr_number, int):
        return None
    from embry0.safety.redaction import redact_bounded

    try:
        resp = await client.post(
            f"/repos/{repo}/issues/{pr_number}/labels",
            repo=repo,
            json={"labels": list(labels)},
        )
        resp.raise_for_status()
        return None
    except Exception as exc:
        logger.warning("pr_label_failed", repo=repo, pr_number=pr_number, labels=labels, error=str(exc))
        return redact_bounded(forge_said(exc), 300)


async def _labels_or_say_so(
    client: GitHubClient,
    *,
    repo: str,
    pr_number: Any,
    labels: list[str] | None,
    diagnostics: dict[str, Any] | None,
) -> None:
    """Put ``labels`` on the PR; when they will not go on, say so on the PR and in ``diagnostics`` (#472).

    The PR still counts: it is open and the job has its URL. But a missing
    ``human-merge-only`` is what tells a person, and anything that keys on the
    label, not to merge it, so the PR gets a comment naming the labels, and
    ``diagnostics`` gets ``labels_missing`` (the labels), ``labels_error``
    (what GitHub said) and ``labels_noted`` (whether the comment went on), for
    the job to record.
    """
    said = await _apply_pr_labels(client, repo=repo, pr_number=pr_number, labels=labels)
    if said is None or not labels:
        return
    named = ", ".join(f"`{label}`" for label in labels)
    body = f"embry0 could not add the labels {named} to this pull request ({said})."
    from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

    if HUMAN_MERGE_LABEL in labels:
        body += (
            f" It should carry `{HUMAN_MERGE_LABEL}`: only a person should merge it, after reviewing it. "
            "Please add the label by hand."
        )
    noted = False
    try:
        # Sent once: a comment the forge took but answered 5xx would be posted twice.
        resp = await client.post(
            f"/repos/{repo}/issues/{pr_number}/comments", repo=repo, json={"body": body}, retry_transient=False
        )
        resp.raise_for_status()
        noted = True
    except Exception as exc:
        logger.warning("pr_label_note_failed", repo=repo, pr_number=pr_number, error=str(exc))
    if diagnostics is not None:
        diagnostics["labels_missing"] = list(labels)
        diagnostics["labels_error"] = said
        diagnostics["labels_noted"] = noted


class OpenPRListError(RuntimeError):
    """The open-PR listing failed, so nobody can say which PRs exist (#305).

    An empty list would read as "no open PRs" and let a job open a duplicate
    of one that does exist; the caller fails closed on this instead.
    """


class OpenPRService:
    """Lists a repo's open PRs with pre-extracted issue references.

    Fetches orchestrator-side (per-repo token via the credential resolver
    when one is wired, else ``resolve_for_repo``, same as every other GitHub
    client here) — the sandbox never needs to
    query PRs itself. ``list_open_prs`` raises :class:`OpenPRListError` when
    the forge cannot answer (#305): "could not list" is not "none open".
    """

    def __init__(
        self,
        *,
        github_token: str = "",
        http_client: httpx.AsyncClient | None = None,
        credential_resolver: Any | None = None,
    ) -> None:
        self._token = github_token
        self._http = http_client
        self._credential_resolver = credential_resolver

    async def list_open_prs(self, repo: str, limit: int = 30) -> list[dict[str, Any]]:
        if not repo or "/" not in repo:
            return []
        client = GitHubClient(
            default_token=self._token, http_client=self._http, credential_resolver=self._credential_resolver
        )
        try:
            resp = await client.get(
                f"/repos/{repo}/pulls",
                repo=repo,
                params={
                    "state": "open",
                    "per_page": max(1, min(int(limit), 100)),
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            logger.warning("open_pr_list_failed", repo=repo, error=str(exc))
            # Only the failure's type reaches the message: it lands on the
            # job row, and an HTTP error's text carries the request URL.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else type(exc).__name__
            raise OpenPRListError(
                f"could not list the open pull requests of {repo} ({detail}); "
                "the duplicate-PR check cannot run, so the job stopped rather than risk a second PR"
            ) from exc
        if not isinstance(raw, list):
            logger.warning("open_pr_list_malformed", repo=repo, payload_type=type(raw).__name__)
            raise OpenPRListError(
                f"the open pull request listing of {repo} was not a list; "
                "the duplicate-PR check cannot run, so the job stopped rather than risk a second PR"
            )

        prs: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            head_ref = ((item.get("head") or {}).get("ref")) or ""
            title = item.get("title") or ""
            entry: dict[str, Any] = {
                "number": item.get("number"),
                "title": title[:200],
                "head_ref": head_ref,
                "url": item.get("html_url") or "",
                "author": ((item.get("user") or {}).get("login")) or "",
            }
            entry.update(extract_pr_issue_refs(head_ref, title, item.get("body") or ""))
            prs.append(entry)
        logger.info("open_pr_list_fetched", repo=repo, count=len(prs))
        return prs
