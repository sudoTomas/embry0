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


class DefaultBranchUnread(Exception):
    """The forge did not say which branch is a repository's default (#546); the message says what it answered."""


async def _repo_default_branch(client: GitHubClient, repo: str) -> str:
    """The repository's default branch, as GitHub reads it now. Raises :class:`DefaultBranchUnread` when it does not.

    No guess stands in for it (#546): a pull request into a guessed ``main``
    on a repository whose default is another branch is another pull request,
    or a create GitHub refuses, and the job would say neither.
    """
    try:
        resp = await client.get(f"/repos/{repo}", repo=repo)
        resp.raise_for_status()
        record = resp.json()
    except Exception as exc:
        raise DefaultBranchUnread(forge_said(exc)) from exc
    branch = record.get("default_branch") if isinstance(record, dict) else None
    if not isinstance(branch, str) or not branch:
        raise DefaultBranchUnread("GitHub named no default branch")
    return branch


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
    status = str(response.status_code)
    # GitLab's message is often the status line itself ("403 Forbidden"): say the number once.
    if message.split(" ", 1)[0] == status:
        return message.strip()[:300]
    return f"{status} {message}".strip()[:300]


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

    Checks for an existing open PR from the same head into the base first —
    a developer that opened the PR but failed to report ``pr_url`` must not
    produce a duplicate (GitHub would 422 the create anyway; the lookup
    recovers the URL instead). The base is resolved before that lookup, and a
    PR from the branch into another base is not the job's (#545), as on
    GitLab (#524). A PR opened between that lookup and the create gets that
    422, "A pull request already exists": the branch's open PR into the base
    is looked up again and is the job's, labelled like any other, as GitLab's
    opener does after a 409 (#525). NEVER raises: any failure logs and
    returns None, and the job completes exactly as before, just without a PR.

    ``labels`` are applied to the PR best-effort (#163) — labeling used to be
    delegated to the sandbox agent via a prompt addendum, which contradicted
    the developer prompt's no-GitHub-access rule and so never worked. A PR is
    an issue to the labels API, and adding is idempotent, so found-existing
    PRs are labeled too.

    ``target_branch`` (Spec 1A §3.7) is the job's resolved base branch; when
    given it is used verbatim instead of re-reading the repo record, so the
    PR targets the same branch the clone fetched. Without it the repository's
    default is read, and when GitHub will not say what it is, no PR is opened
    and ``change_request_error`` says why (#546): never one into a guess.

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
        # The base comes first: the job's PR is the branch's open PR into it, not one into another base (#545).
        base = target_branch or await _repo_default_branch(client, repo)
        if branch == base:
            # Nothing to merge into itself — the repo's default happens to be
            # the branch the developer pushed.
            logger.info("pr_fallback_skipped_same_branch", repo=repo, branch=branch)
            _why(f"the branch {branch!r} is the repository's default branch, so there is nothing to merge")
            return None

        existing = await _open_pr_into(client, repo=repo, owner=owner, branch=branch, base=base)
        url = str((existing or {}).get("html_url") or "")
        if existing is not None and url:
            logger.info("pr_fallback_found_existing", repo=repo, branch=branch, base=base, url=url)
            await _labels_or_say_so(
                client, repo=repo, pr_number=existing.get("number"), labels=labels, diagnostics=diagnostics
            )
            return url

        # Safe to send again (#45): GitHub refuses a second open request for the same head and base (the 422
        # below), and the opener then finds the one the first request made. Sent once, a create GitHub made but
        # answered 502 would read as no pull request.
        create = await client.post(
            f"/repos/{repo}/pulls",
            repo=repo,
            json={"title": title, "head": branch, "base": base, "body": body},
            retry_transient=True,
        )
        if create.status_code == 422 and _says_already_exists(create):
            # Opened between the lookup and the create (the developer, a person, another outcome of the job):
            # GitHub keys that 422 on head and base, so the one it means is the branch's open PR into ``base``.
            try:
                again = await _open_pr_into(client, repo=repo, owner=owner, branch=branch, base=base)
            except Exception as exc:
                # The create's 422 says why there is no PR; that is what the job records.
                logger.warning("pr_fallback_relookup_failed", repo=repo, branch=branch, base=base, error=str(exc))
                again = None
            recovered = str((again or {}).get("html_url") or "") or None
            if again is not None and recovered:
                logger.info("pr_fallback_recovered_after_conflict", repo=repo, branch=branch, base=base, url=recovered)
                await _labels_or_say_so(
                    client, repo=repo, pr_number=again.get("number"), labels=labels, diagnostics=diagnostics
                )
                return recovered
        create.raise_for_status()
        created = create.json() or {}
        created_url = str(created.get("html_url") or "") or None
        logger.info("pr_fallback_created", repo=repo, branch=branch, url=created_url)
        await _labels_or_say_so(
            client, repo=repo, pr_number=created.get("number"), labels=labels, diagnostics=diagnostics, opened=True
        )
        return created_url
    except DefaultBranchUnread as exc:
        # Without the base, a pull request would go into a guess (#546): none is opened, and the job says why,
        # as for any pull request that could not be opened (#459).
        logger.warning("pr_fallback_default_branch_unread", repo=repo, branch=branch, error=str(exc))
        _why(
            f"embry0 could not read the default branch of {repo} from GitHub ({exc}), "
            "so it opened no pull request into a guessed branch"
        )
        return None
    except Exception as exc:
        # GitHubClient owns the httpx lifecycle: it closes a client it created
        # and leaves an injected one alone, so there is no `finally` here.
        logger.warning("pr_fallback_failed", repo=repo, branch=branch, error=str(exc))
        _why(f"GitHub refused it: {forge_said(exc)}")
        return None


def _says_already_exists(resp: httpx.Response) -> bool:
    """Whether GitHub's 422 to a create says a pull request for that head and base is already open (#525).

    Only that 422: another (no commits between the branches, a head that does
    not exist) is a refusal, and the opener says so as before.
    """
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON says nothing of which 422 it is
        return False
    if not isinstance(payload, dict):
        return False
    said = [str(payload.get("message") or "")]
    said += [str(e.get("message") or "") for e in payload.get("errors") or [] if isinstance(e, dict)]
    return any("pull request already exists" in text.lower() for text in said)


async def _open_pr_into(
    client: GitHubClient, *, repo: str, owner: str, branch: str, base: str
) -> dict[str, Any] | None:
    """The open pull request from ``branch`` into ``base``, or None (#525, #545).

    ``base`` is a filter of GitHub's own: the branch may have an open pull
    request into another base, and that one is not the job's. Raises when
    GitHub will not answer, so a failed read is never taken for "none open".
    """
    resp = await client.get(
        f"/repos/{repo}/pulls",
        repo=repo,
        params={"state": "open", "head": f"{owner}:{branch}", "base": base},
    )
    resp.raise_for_status()
    found = resp.json()
    if not isinstance(found, list):
        return None
    for pr in found:
        if not isinstance(pr, dict):
            continue
        into, head = pr.get("base"), pr.get("head")
        # Belt and braces: GitHub's filters already ask for an open pull request from the branch into ``base``;
        # one that says otherwise is not the job's, filter or none.
        if pr.get("state") not in (None, "open"):
            continue
        if isinstance(into, dict) and into.get("ref") not in (None, base):
            continue
        if isinstance(head, dict) and (
            head.get("ref") not in (None, branch) or not _from(head.get("label"), owner, branch)
        ):
            continue
        return pr
    return None


def _from(label: Any, owner: str, branch: str) -> bool:
    """Whether a pull request's ``head.label`` (``<login>:<ref>``) names ``branch`` of ``owner``, or names nothing.

    The login is compared without case, as GitHub matches it: a repository
    stored as ``acme/widgets`` is GitHub's ``Acme``, whose labels carry the
    login's own case. The ref is compared exactly: git refs are
    case-sensitive.
    """
    if label is None:
        return True
    login, colon, ref = str(label).partition(":")
    return bool(colon) and login.casefold() == owner.casefold() and ref == branch


async def _apply_pr_labels(
    client: GitHubClient,
    *,
    repo: str,
    pr_number: Any,
    labels: list[str] | None,
) -> tuple[list[str] | None, str | None]:
    """Labels on a PR (a PR is an issue to the labels API): the labels it still lacks, and what GitHub said.

    Nothing missing (``[]``) when they are on it, or there is nothing to put
    on. GitHub answers with the labels the PR has after the call, so that
    answer is read back, as GitLab's is (#477): a label it does not name is
    missing too, whatever the status said. An answer that names no labels
    says neither, so ``None`` comes back for "missing": nothing claims they
    are on. The forge client already sends the call again on an answer a
    retry can change (429, 5xx, a dropped connection); adding labels twice
    changes nothing, so that is safe here (#472).
    """
    if not labels or not isinstance(pr_number, int) or isinstance(pr_number, bool):
        return None, None
    from embry0.safety.redaction import redact_bounded

    try:
        resp = await client.post(
            f"/repos/{repo}/issues/{pr_number}/labels",
            repo=repo,
            json={"labels": list(labels)},
            retry_transient=True,  # safe to send again (#45): a label it already has is not added twice
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("pr_label_failed", repo=repo, pr_number=pr_number, labels=labels, error=str(exc))
        return list(labels), redact_bounded(forge_said(exc), 300)
    try:
        carried = resp.json()
    except Exception:  # noqa: BLE001 - an answer that is not a list of labels says nothing of them
        carried = None
    if not isinstance(carried, list):
        return None, None
    # GitHub matches a label's name without case: adding `embry0` to a repository that has `Embry0` puts that on.
    names = {str(label.get("name") if isinstance(label, dict) else label).casefold() for label in carried}
    missing = [label for label in labels if label.casefold() not in names]
    if missing:
        logger.warning("pr_label_not_applied", repo=repo, pr_number=pr_number, labels=missing)
        return missing, "GitHub answered 200 and left them off"
    return [], None


#: Marks embry0's one comment on a PR that says its labels did not go on (#472).
LABELS_NOTE_MARKER = "<!-- embry0:labels-missing -->"


def labels_missing_note(missing: list[str], said: str | None, *, noun: str) -> str:
    """The marked note on a change request (``noun``: "pull request", "merge request") whose labels did not go on.

    It names the labels in ``missing`` and what the forge ``said``; for
    ``human-merge-only`` it also says why that one matters. Both forges post
    it, so a pull request and a merge request read alike (#472, #477).
    """
    from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

    named = ", ".join(f"`{label}`" for label in missing)
    body = f"embry0 could not add the labels {named} to this {noun} ({said})."
    if HUMAN_MERGE_LABEL in missing:
        body += (
            f" It should carry `{HUMAN_MERGE_LABEL}`: only a person should merge it, after reviewing it. "
            "Please add the label by hand."
        )
    return f"{LABELS_NOTE_MARKER}\n\n{body}"


def labels_on_note(labels: list[str], *, noun: str) -> str:
    """What the marked note says once a later outcome has put ``labels`` on the change request (#477, #530)."""
    named = ", ".join(f"`{label}`" for label in labels)
    return f"{LABELS_NOTE_MARKER}\n\nThe labels {named} are on this {noun} now."


async def _labels_or_say_so(
    client: GitHubClient,
    *,
    repo: str,
    pr_number: Any,
    labels: list[str] | None,
    diagnostics: dict[str, Any] | None,
    opened: bool = False,
) -> None:
    """Put ``labels`` on the PR; when they will not go on, say so on the PR and in ``diagnostics`` (#472).

    The PR still counts: it is open and the job has its URL. But a missing
    ``human-merge-only`` is what tells a person, and anything that keys on the
    label, not to merge it, so the PR gets a comment naming the missing
    labels, and ``diagnostics`` gets ``labels_missing`` (the labels),
    ``labels_error`` (what GitHub said) and ``labels_noted`` (whether the
    comment went on), for the job to record.

    The comment is one per PR (:data:`LABELS_NOTE_MARKER`): a later outcome
    that finds the same PR open with its labels still refused edits that
    comment rather than posting another, and one that does put them on edits
    it to say so (#530), as GitLab's twin does
    (:func:`embry0.services.gitlab_mrs._labels_or_say_so`). A PR embry0 has
    just ``opened`` has no comment to edit, so none is looked for.
    """
    if not labels:
        return
    missing, said = await _apply_pr_labels(client, repo=repo, pr_number=pr_number, labels=labels)
    from embry0.notifications.qa_comment import edit_sticky_comment

    if not missing:
        if missing is not None and not opened:
            done = labels_on_note(labels, noun="pull request")
            try:
                await edit_sticky_comment(
                    client, repo=repo, issue_number=pr_number, body=done, marker=LABELS_NOTE_MARKER
                )
            except Exception as exc:  # noqa: BLE001 - the labels are on; only the old comment stays as it was
                logger.warning("pr_label_note_edit_failed", repo=repo, pr_number=pr_number, error=str(exc))
        return
    body = labels_missing_note(missing, said, noun="pull request")
    noted = False
    try:
        if not await edit_sticky_comment(
            client, repo=repo, issue_number=pr_number, body=body, marker=LABELS_NOTE_MARKER
        ):
            # Sent once: a comment the forge took but answered 5xx would be posted twice.
            resp = await client.post(
                f"/repos/{repo}/issues/{pr_number}/comments", repo=repo, json={"body": body}, retry_transient=False
            )
            resp.raise_for_status()
        noted = True
    except Exception as exc:
        logger.warning("pr_label_note_failed", repo=repo, pr_number=pr_number, error=str(exc))
    if diagnostics is not None:
        diagnostics["labels_missing"] = missing
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
            head, base = item.get("head") or {}, item.get("base") or {}
            head_ref = head.get("ref") or ""
            title = item.get("title") or ""
            # A fork: any head repository other than the base, a deleted one (head.repo null) included, as the
            # pull-request read decides it (execution.pull_requests.GitHubPullRequests.read).
            head_repo = str((head.get("repo") or {}).get("full_name") or "")
            base_repo = str((base.get("repo") or {}).get("full_name") or "") or repo
            entry: dict[str, Any] = {
                "number": item.get("number"),
                "title": title[:200],
                "head_ref": head_ref,
                "url": item.get("html_url") or "",
                "author": ((item.get("user") or {}).get("login")) or "",
                "from_fork": not head_repo or head_repo.lower() != base_repo.lower(),
                "default_branch": str((base.get("repo") or {}).get("default_branch") or "") or None,
            }
            entry.update(extract_pr_issue_refs(head_ref, title, item.get("body") or ""))
            prs.append(entry)
        logger.info("open_pr_list_fetched", repo=repo, count=len(prs))
        return prs
