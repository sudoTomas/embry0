"""Orchestrator-side GitLab merge-request creation (#167).

The GitLab counterpart to :func:`embry0.services.github_prs.ensure_pr_for_branch`,
and it exists for the same reason: a turn-capped or JSON-less developer can
finish with the branch pushed but no MR, stranding the work. The orchestrator
holds the token natively, so the fallback never depends on sandbox turns.

Two GitLab specifics beyond the client's own (`PRIVATE-TOKEN`, `%2F`-encoded
project paths):

* The target branch is **not** assumed to be ``main``. `shop`'s is
  ``develop``; it is read from the project record.
* GitLab calls it ``source_branch``/``target_branch``, and a duplicate create
  returns 409 rather than GitHub's 422 — so an existing MR is looked up first
  either way.

Never raises: any failure logs and returns None, and the job completes exactly
as it would have, just without an MR.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.execution.gitlab_client import GitLabClient

logger = structlog.get_logger(__name__)


async def find_open_mr_for_branch(client: GitLabClient, repo: str, branch: str) -> str | None:
    """URL of an open MR whose source branch is ``branch``, if one exists."""
    found = await _open_mr_for_branch(client, repo, branch)
    return None if found is None else str(found.get("web_url") or "") or None


async def _open_mr_for_branch(client: GitLabClient, repo: str, branch: str) -> dict[str, Any] | None:
    """The open MR whose source branch is ``branch``, as GitLab lists it, if one exists."""
    resp = await client.get(
        client.project_path(repo, "/merge_requests"),
        repo=repo,
        params={"state": "opened", "source_branch": branch},
    )
    if resp.status_code != 200:
        return None
    body = resp.json()
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return body[0]
    return None


async def default_branch_for(client: GitLabClient, repo: str) -> str:
    """Project's default branch, falling back to ``main``.

    Read rather than assumed: `acme/widgets/shop` defaults to
    ``develop``, and targeting a branch that does not exist fails the
    create with a message that does not obviously say so.
    """
    project = await client.get_project(repo)
    if project:
        branch = project.get("default_branch")
        if isinstance(branch, str) and branch:
            return branch
    return "main"


async def ensure_mr_for_branch(
    *,
    repo: str,
    branch: str,
    title: str,
    body: str,
    client: GitLabClient,
    labels: list[str] | None = None,
    target_branch: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str | None:
    """Open (or find) a merge request for ``branch``. Returns its URL or None.

    Checks for an existing open MR on the same source branch first — a
    developer that opened one but failed to report the URL must not produce a
    duplicate, and GitLab would 409 the create anyway; the lookup recovers the
    URL instead.

    ``target_branch`` (Spec 1A §3.7) is the job's resolved base branch. When
    given it is used verbatim: the resolver already answered, and a second
    round trip could only disagree with the branch the clone actually
    fetched. Without it the project's own default is read, as before.

    ``diagnostics``, when given, receives ``change_request_error``: why no
    merge request came of it, in words a person reads (#459).

    ``labels`` go on the merge request it opens or finds, as on a pull
    request (#477); when GitLab will not take them the merge request still
    counts, and :func:`_labels_or_say_so` says so on it and in
    ``diagnostics``.
    """

    def _why(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["change_request_error"] = reason[:300]

    if not repo or "/" not in repo or not branch:
        _why("the job has no repository or no branch to open a merge request from")
        return None
    # Nothing to merge into itself. With an explicit target that comparison is
    # exact; without one the resolved default is only known after an API round
    # trip, so the historical literals stay as the cheap pre-check.
    if branch == target_branch or (target_branch is None and branch in ("main", "master")):
        _why(f"the branch {branch!r} is the base branch, so there is nothing to merge")
        return None

    try:
        found = await _open_mr_for_branch(client, repo, branch)
        existing = None if found is None else str(found.get("web_url") or "") or None
        if found is not None and existing:
            logger.info("mr_fallback_found_existing", repo=repo, branch=branch, url=existing)
            await _labels_or_say_so(client, repo=repo, iid=found.get("iid"), labels=labels, diagnostics=diagnostics)
            return existing

        target = target_branch or await default_branch_for(client, repo)
        if branch == target:
            # Nothing to merge into itself; also guards a repo whose default
            # branch happens to match the pipeline's branch name.
            logger.info("mr_fallback_skipped_same_branch", repo=repo, branch=branch)
            _why(f"the branch {branch!r} is the project's default branch, so there is nothing to merge")
            return None

        resp = await client.post(
            client.project_path(repo, "/merge_requests"),
            repo=repo,
            json={
                "source_branch": branch,
                "target_branch": target,
                "title": title,
                "description": body,
                "remove_source_branch": False,
            },
        )
        if resp.status_code in (200, 201):
            created: dict[str, Any] = resp.json()
            url = str(created.get("web_url") or "") or None
            logger.info("mr_fallback_created", repo=repo, branch=branch, target=target, url=url)
            if url:
                await _labels_or_say_so(
                    client, repo=repo, iid=created.get("iid"), labels=labels, diagnostics=diagnostics
                )
            return url

        # 409 means someone created it between our lookup and our create.
        if resp.status_code == 409:
            again = await _open_mr_for_branch(client, repo, branch)
            recovered = None if again is None else str(again.get("web_url") or "") or None
            if again is not None and recovered:
                logger.info("mr_fallback_recovered_after_conflict", repo=repo, branch=branch)
                await _labels_or_say_so(client, repo=repo, iid=again.get("iid"), labels=labels, diagnostics=diagnostics)
                return recovered

        logger.warning(
            "mr_fallback_failed",
            repo=repo,
            branch=branch,
            status=resp.status_code,
            body=resp.text[:200],
        )
        _why(f"GitLab refused it: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as exc:
        logger.warning("mr_fallback_failed", repo=repo, branch=branch, error=str(exc))
        _why(f"GitLab refused it: {exc}"[:300])
        return None


async def _apply_mr_labels(client: GitLabClient, *, repo: str, iid: Any, labels: list[str] | None) -> str | None:
    """Labels on a merge request, beside the ones it has.

    None when they are on it, or there is nothing to put on; otherwise what
    GitLab said, redacted and bounded. Adding labels twice changes nothing, so
    the client's own retries are safe here, as for a pull request (#472).
    """
    if not labels:
        return None
    from embry0.safety.redaction import redact_bounded
    from embry0.services.github_prs import forge_said

    if not isinstance(iid, int) or isinstance(iid, bool):
        return "GitLab named no merge request number to put them on"
    try:
        resp = await client.put(
            client.project_path(repo, f"/merge_requests/{iid}"),
            repo=repo,
            json={"add_labels": ",".join(labels)},
        )
        resp.raise_for_status()
        return None
    except Exception as exc:
        logger.warning("mr_label_failed", repo=repo, iid=iid, labels=labels, error=str(exc))
        return redact_bounded(forge_said(exc), 300)


async def _labels_or_say_so(
    client: GitLabClient,
    *,
    repo: str,
    iid: Any,
    labels: list[str] | None,
    diagnostics: dict[str, Any] | None,
) -> None:
    """Put ``labels`` on the merge request; when they will not go on, say so on it and in ``diagnostics`` (#477).

    The GitLab twin of :func:`embry0.services.github_prs._labels_or_say_so`:
    the merge request still counts, but a missing ``human-merge-only`` is what
    tells a person, and anything keyed on the label, not to merge it. So the
    merge request gets one note naming the labels (one per merge request,
    :data:`~embry0.services.github_prs.LABELS_NOTE_MARKER`: a later outcome
    edits it), and ``diagnostics`` gets ``labels_missing``, ``labels_error``
    and ``labels_noted``, for the job to record.
    """
    said = await _apply_mr_labels(client, repo=repo, iid=iid, labels=labels)
    if said is None or not labels:
        return
    from embry0.services.github_prs import LABELS_NOTE_MARKER
    from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

    named = ", ".join(f"`{label}`" for label in labels)
    body = f"embry0 could not add the labels {named} to this merge request ({said})."
    if HUMAN_MERGE_LABEL in labels:
        body += (
            f" It should carry `{HUMAN_MERGE_LABEL}`: only a person should merge it, after reviewing it. "
            "Please add the label by hand."
        )
    noted = False
    if isinstance(iid, int) and not isinstance(iid, bool):
        try:
            await client.upsert_sticky_mr_note(repo, iid, f"{LABELS_NOTE_MARKER}\n\n{body}", marker=LABELS_NOTE_MARKER)
            noted = True
        except Exception as exc:
            logger.warning("mr_label_note_failed", repo=repo, iid=iid, error=str(exc))
    if diagnostics is not None:
        diagnostics["labels_missing"] = list(labels)
        diagnostics["labels_error"] = said
        diagnostics["labels_noted"] = noted
