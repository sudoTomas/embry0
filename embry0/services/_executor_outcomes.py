"""Outcome/notification internals of IssueExecutor (RAV-1235 split).

Mixin methods annotate ``self: IssueExecutor``. Behavior unchanged from the
pre-split module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from embry0.services.issue_executor import IssueExecutor

from embry0.orchestration import dispatch_actors
from embry0.safety.error_codes import ErrorCode
from embry0.services._cost import resolve_job_total_cost
from embry0.services.healing.recorder import record_case_event
from embry0.services.job_events import broadcast_job_event, emit_job_finished
from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.trackers import forge_number_of, linear_identifier_of
from embry0.workflows.issue_to_pr.plan_route import build_notification_summary, job_title_from_task

logger = structlog.get_logger(__name__)


def person_who_started(job: Mapping[str, Any] | None) -> str | None:
    """Whom to tell about a job: the person who started it, when a person did. A key or a trigger is nobody to message."""
    by = str((job or {}).get("dispatched_by") or "")
    return by if "@" in by else None


def _developer_summary(result: dict[str, Any]) -> str:
    """The developer's own description of the change, for the fallback PR body.

    The sandbox cannot open a pull request, so the orchestrator does. The
    body a person reads has to say what changed. The canned "reported no PR"
    sentence does not.

    #488: the developer node records it as ``developer_summary``. ``result``
    is ``_process_stream``'s merge of node UPDATES, so its ``agent_outputs``
    hold only the last node's fragment, normally the review's. The scan below
    finds the developer's own entry only when the developer ran last, or for a
    checkpoint from before that field existed.
    """
    from embry0.orchestration.agent_json import last_object_with

    recorded = result.get("developer_summary")
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip()[:4000]
    for entry in reversed(result.get("agent_outputs") or []):
        if not isinstance(entry, dict) or entry.get("agent_type") != "developer" or entry.get("is_error"):
            continue
        text = entry.get("output")
        if not isinstance(text, str) or not text.strip():
            continue
        summary = (last_object_with(text, "summary") or {}).get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:4000]
        stripped = text.strip()
        if stripped and stripped != "done":
            return stripped[:4000]
    return ""


def _fold_auto_answers_into_context(enriched_questions: list[dict[str, Any]]) -> str:
    """Build a Q&A context block from auto-answered triage questions.

    ``enriched_questions`` is the list of dicts produced by ``_handle_needs_info``
    after DB insertion — each dict has ``question``, ``importance``, and
    ``auto_answer`` keys.

    Returns a formatted block suitable for prepending to ``additional_context``
    in the next triage call, so the triage agent sees the answers and can
    proceed without re-asking.
    """
    lines: list[str] = ["Triage auto-answers (questions the system can answer automatically):"]
    for q in enriched_questions:
        if q.get("auto_answer") is not None:
            lines.append(f"\nQ: {q['question']}\nA: {q['auto_answer']}")
    if len(lines) == 1:
        # No auto-answers found — return empty so caller doesn't set context
        return ""
    return "\n".join(lines)


def _tracker_channel_for(self: IssueExecutor, issue: dict[str, Any] | None) -> Any | None:
    """The comment channel for the issue's originating tracker, or None.

    getattr: several unit tests build the executor via ``__new__`` and set
    only the attributes their path touches — this lookup must not add a new
    construction requirement to that pattern."""
    channels: dict[str, Any] = getattr(self, "_tracker_channels", None) or {}
    if not issue:
        return None
    return channels.get(str(issue.get("tracker") or ""))


async def _brief_title(self: Any, job_id: str) -> str:
    """The job's brief title when its current brief is written, else ``""``; never raises."""
    db = getattr(self, "_db", None)
    if db is None:
        return ""
    try:
        from embry0.storage.repositories.job_briefs import JobBriefsRepository

        row = (await JobBriefsRepository(db).current([job_id])).get(job_id)
    except Exception:
        logger.warning("job_brief_title_unread", job_id=job_id, exc_info=True)
        return ""
    return str((row or {}).get("title") or "").strip()


async def _post_tracker_outcome(self: IssueExecutor, issue_id: str | None, job_id: str) -> None:
    """Write-back (#163, née EMB-47): comment the terminal job outcome on the
    issue's originating tracker (Linear, GitLab). No-op unless a channel is
    wired for that tracker AND the job reached a terminal status (paused /
    awaiting-input runs get their comment when they eventually finish)."""
    if not issue_id:
        return
    try:
        issue = await self._issues.get(issue_id)
        channel = _tracker_channel_for(self, issue)
        if channel is None:
            return
        job = await self._jobs.get(job_id)
        if not job or job.get("status") in ("running", "pending", "paused", "awaiting_input"):
            return
        await channel.post_outcome(issue, job)
    except Exception:
        logger.warning("tracker_outcome_post_failed", job_id=job_id, exc_info=True)


async def _post_tracker_questions(
    self: IssueExecutor,
    issue: dict[str, Any] | None,
    job_id: str,
    asking_node: str,
    blocking_questions: list[dict[str, Any]],
) -> None:
    """RAV-1076: comment the blocking agent questions on the issue's
    originating tracker so the ticket carries the full decision trail
    (answers are posted by ``answer_recorder.record_answer`` when they
    arrive). Skipped when the issue's ``notification_channels`` explicitly
    name the tracker — ``dispatch_questions`` already posted there."""
    channel = _tracker_channel_for(self, issue)
    if channel is None or not blocking_questions:
        return
    if channel.name in ((issue or {}).get("notification_channels") or []):
        return
    try:
        enriched = [
            {**bq, "job_id": job_id, "asking_node": bq.get("asking_node") or asking_node} for bq in blocking_questions
        ]
        await channel.dispatch(issue or {}, enriched)
    except Exception:
        logger.warning("tracker_question_post_failed", job_id=job_id, exc_info=True)


async def _persist_deliverables(self: IssueExecutor, job_id: str, result: dict[str, Any]) -> int:
    """Write deliverables rows for a completed job (RAV-603). Returns the
    number of rows persisted.

    ``state["deliverables"]`` (built by finalize_output_node) is the
    primary source. Uniform synthesis on top: a ``pr`` row from
    ``pr_url`` (code kinds usually end without an ``output`` step, so
    the workflow never builds one), and — when the workflow produced
    nothing at all — a ``report`` row from ``result_summary`` so
    pre-RAV-603-template jobs still get a deliverable.
    """
    if self._db is None:
        return 0
    from embry0.storage.repositories.deliverables import DeliverablesRepository

    rows = [dict(d) for d in (result.get("deliverables") or []) if isinstance(d, dict)]
    pr_url = result.get("pr_url")
    if pr_url and not any(r.get("type") == "pr" for r in rows):
        rows.insert(0, {"type": "pr", "title": "Pull request", "url": str(pr_url)})
    summary = str(result.get("result_summary") or "").strip()
    if not rows and summary:
        rows.append({"type": "report", "title": f"{result.get('job_kind') or 'job'} report", "content": summary})
    if not rows:
        return 0

    persisted = 0
    repo = DeliverablesRepository(self._db)
    allowed = {
        "type",
        "title",
        "content",
        "url",
        "storage_bucket",
        "storage_key",
        "media_type",
        "size_bytes",
        "metadata",
    }
    for row in rows:
        fields = {k: v for k, v in row.items() if k in allowed}
        try:
            await repo.create(job_id=job_id, **fields)
            persisted += 1
        except Exception:
            # Per-row isolation: one malformed entry must not drop the rest.
            logger.warning(
                "deliverable_row_persist_failed",
                job_id=job_id,
                type=row.get("type"),
                exc_info=True,
            )
    return persisted


async def _record_standard_audit(self: IssueExecutor, job_id: str, result: dict[str, Any]) -> None:
    """ADR 0014: a completed audit's score and finding count land on its repository's standards state.

    Only the job the audit route named as a repository's newest audit writes
    anything; any other job costs one indexed lookup. Never raises: the job
    row is already written and nothing here may flip it.
    """
    if self._db is None:
        return
    try:
        from embry0.services.standards import record_audit_completion
        from embry0.storage.repositories.standards import RepoStandardStateRepository

        await record_audit_completion(RepoStandardStateRepository(self._db), job_id, result)
    except Exception:
        logger.warning("standard_audit_record_failed", job_id=job_id, exc_info=True)


async def _forge_for(self: IssueExecutor, repo: str) -> str:
    """The repo's forge, through the one shared resolver (Spec 1A §3.8).

    This used to be a hand-rolled copy of ``resolve_forge`` that caught
    ``Exception`` — ``UnregisteredRepoError`` included — and answered
    "github", which is precisely the guess §3.8 removed. It decides both the
    MR-vs-PR branch and, via ``_base_branch_for_change_request``, which forge
    client resolves the base branch, so a wrong answer here opens a pull
    request against a repo path that only exists on the other forge.

    Raises what the resolver raises: ``UnregisteredRepoError`` on a
    deployment that refuses to guess, and the underlying error when a
    registry outage leaves it with no configured default to degrade to. Both
    call sites below decide what that means for them.
    """
    from embry0.services.forge_resolver import resolve_forge

    return await resolve_forge(getattr(self, "_repos", None), repo)


async def _base_branch_for_change_request(self: IssueExecutor, repo: str, result: dict[str, Any]) -> str:
    """The branch this job's PR/MR fallback must target (Spec 1A §3.7).

    ``result`` is ``_process_stream``'s merge of node UPDATES, so it carries
    ``base_branch`` only when ``init_node`` ran in THIS stream, or when a
    resume carried it over from its checkpoint (#482, ``_RESUME_CARRIED_KEYS``).
    A checkpoint whose init left it empty still arrives without the key — and
    an absent value would reach the forge helpers as ``target_branch=None``,
    whose GitHub side then resolves its own base through
    ``get_repo_default_branch``, which answers "main" on
    a 404, a rate limit or a network blip. On a repo whose integration branch
    is not main that is a silently wrong target (§3.8). So re-resolve it here
    through the same resolver ``init_node`` used: it honours the repo
    preference first and logs which step answered. Never returns empty.
    """
    from_state = str(result.get("base_branch") or "")
    if from_state:
        return from_state
    logger.info("change_request_base_branch_absent", repo=repo)
    return await _repo_base_branch(self, repo, caller="change_request")


async def _repo_base_branch(self: IssueExecutor, repo: str, *, caller: str) -> str:
    """``repo``'s base branch: its preference, else the forge default, else "main".

    The executor-side counterpart of ``init_node``'s lookup, for callers that
    have no workflow state to read it from: the change-request fallback
    above, and an onboard job started without a branch (#29). ``caller``
    names the path in the ``repo_base_branch_lookup`` log event, which is
    where the resolved forge is logged. Never raises and never returns empty.
    """
    from embry0.services.base_branch import resolve_base_branch

    config = getattr(self, "_config", None)
    try:
        forge = await _forge_for(self, repo)
    except Exception:
        # Deliberate (Spec 1A §3.8): an unresolvable forge must not decide
        # which forge API is asked for a default branch — that is how a
        # GitLab repo ends up targeting a GitHub project's "main". Skip the
        # forge step entirely; the preference (or "main") still answers, and
        # _open_change_request below refuses the request anyway (as an
        # onboard job's init node fails an unregistered repo), so this
        # value is never actually used on that path. Raising here instead
        # would abort _handle_workflow_result before the terminal job status
        # is written — the run would hang in "running" forever.
        logger.warning("repo_base_branch_forge_unresolved", repo=repo, exc_info=True)
        forge = ""
    logger.info("repo_base_branch_lookup", repo=repo, forge=forge, caller=caller)
    gitlab_client: Any = None
    github_client: Any = None
    resolver = getattr(self, "_credential_resolver", None)
    # getattr: unit tests build the executor via __new__ and set only the
    # attributes their path touches.
    github_default = str(getattr(self, "_github_token", "") or "")
    has_token = False
    if forge in ("gitlab", "github"):
        from embry0.services.credentials.resolver import forge_token

        default = str(getattr(config, "gitlab_token", "") or "") if forge == "gitlab" else github_default
        try:
            # #424: the same gate as the change-request fallback — a registry
            # or per-owner/namespace env token counts, not only the default.
            has_token = bool(await forge_token(resolver, repo, forge, default))
        except Exception:
            logger.warning("repo_base_branch_token_lookup_failed", repo=repo, exc_info=True)
    if forge == "gitlab":
        if has_token:
            # Never a raw GitLabClient: the factory is what loads the private
            # CA bundle (Spec 1A §3.2).
            from embry0.execution.gitlab_client import make_gitlab_client

            gitlab_client = make_gitlab_client(config, credential_resolver=resolver)
    elif forge == "github":
        if has_token:
            from embry0.execution.github_client import GitHubClient

            github_client = GitHubClient(default_token=github_default, credential_resolver=resolver)

    return await resolve_base_branch(
        repo=repo,
        forge=forge,
        prefs_repo=getattr(self, "_repo_prefs", None),
        gitlab_client=gitlab_client,
        github_client=github_client,
    )


async def _open_change_request(
    self: IssueExecutor,
    *,
    repo: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    target_branch: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> str | None:
    """Open (or find) the change request for ``branch`` on the repo's forge.

    GitHub gets a pull request, GitLab a merge request. Both helpers are
    fail-open by contract: any failure logs and returns None, and the job
    completes exactly as before, just without a linked change request.

    That contract now covers the forge lookup too, but fail-open here means
    *skip the change request*, not *assume github* (Spec 1A §3.8): on a
    tenant that refuses to guess, opening a PR against ``github.com/<path>``
    is a request on somebody else's repo. The job itself still completes —
    this runs after the work is done, and raising would abort
    ``_handle_workflow_result`` before the terminal status is written.

    ``target_branch`` is the job's resolved base branch (state["base_branch"],
    Spec 1A §3.7). Empty means "ask the forge", which is the pre-3.7
    behaviour — it must NOT become the empty branch name.

    ``diagnostics``, when given, receives ``change_request_error`` when no
    change request came of it: what the forge, or embry0 before it, said
    (#459). It is still None that comes back, so every caller that does not
    ask goes on exactly as before.
    """
    from embry0.storage.repositories.repos import UnregisteredRepoError

    def _why(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["change_request_error"] = reason[:300]

    try:
        forge = await _forge_for(self, repo)
    except UnregisteredRepoError:
        logger.warning("change_request_skipped_unregistered_repo", repo=repo, branch=branch)
        _why(f"{repo} is not registered in embry0, so it does not know which forge to ask")
        return None
    except Exception as exc:
        logger.warning("change_request_skipped_forge_lookup_failed", repo=repo, branch=branch, exc_info=True)
        _why(f"embry0 could not look up {repo}'s forge: {exc}")
        return None

    target = target_branch or None

    resolver = getattr(self, "_credential_resolver", None)
    if forge == "gitlab":
        config = getattr(self, "_config", None)
        from embry0.services.credentials.resolver import forge_token

        # #424: the registry, or GITLAB_TOKEN__<NS>, counts — not only the
        # plain GITLAB_TOKEN.
        try:
            token = await forge_token(resolver, repo, "gitlab", getattr(config, "gitlab_token", "") or "")
        except Exception as exc:
            # Fail-open (see docstring): a pinned credential that is
            # unavailable skips the MR, it must not abort the job's outcome.
            logger.warning("mr_fallback_token_lookup_failed", repo=repo, exc_info=True)
            _why(f"embry0 could not read a GitLab token for {repo}: {exc}")
            return None
        if not token:
            logger.warning("mr_fallback_skipped_no_token", repo=repo)
            _why(f"embry0 has no GitLab token for {repo}")
            return None
        from embry0.execution.gitlab_client import make_gitlab_client
        from embry0.services.gitlab_mrs import ensure_mr_for_branch

        return await ensure_mr_for_branch(
            repo=repo,
            branch=branch,
            title=title,
            body=body,
            client=make_gitlab_client(config, credential_resolver=resolver),
            target_branch=target,
            diagnostics=diagnostics,
        )

    from embry0.services.github_prs import ensure_pr_for_branch

    # ensure_pr_for_branch resolves the per-owner token (GITHUB_TOKEN__<OWNER>)
    # internally; the default passed here is only its final fallback.
    return await ensure_pr_for_branch(
        repo=repo,
        branch=branch,
        title=title,
        body=body,
        labels=labels,
        github_token=getattr(self, "_github_token", None) or "",
        target_branch=target,
        credential_resolver=resolver,
        diagnostics=diagnostics,
    )


async def _record_missing_labels(self: IssueExecutor, job_id: str, url: str, diagnostics: dict[str, Any]) -> None:
    """Record on the job that its change request opened without its labels (#472).

    The job still completes with the URL, but a missing ``human-merge-only``
    must be visible where a person reads the job, not only in the logs.
    """
    from embry0.execution import events
    from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

    missing = [str(label) for label in diagnostics.get("labels_missing") or []]
    labels = ", ".join(missing)
    said = str(diagnostics.get("labels_error") or "the forge did not take them").rstrip(". ")
    message = f"The change request {url} opened without its labels ({labels}): {said}."
    if HUMAN_MERGE_LABEL in missing:
        message += " Only a person should merge it."
    message += " A comment on it says so." if diagnostics.get("labels_noted") else " Add them on it by hand."
    logger.warning("change_request_labels_missing", job_id=job_id, url=url, labels=labels)
    try:
        await self._broadcast_event(job_id, events.system(message=message))
    except Exception:
        logger.warning("change_request_labels_event_failed", job_id=job_id, exc_info=True)


#: How much of the forge's answer a refused "merge as is" keeps (#459).
MERGE_AS_IS_REASON_CHARS = 300


async def _deliver_as_is(self: IssueExecutor, state: dict[str, Any]) -> tuple[str | None, str]:
    """Open the change request of a job a person chose to merge as is (#459): its URL, or why there is none.

    The graph's ``deliver_as_is_node`` calls it with the job's state, so a
    refusal can send the job back to the pause it came from instead of ending
    it ``completed`` with nothing delivered and its sandbox destroyed. The
    change request is the one ``_open_fallback_pr`` opens after any run that
    did not deliver; the reason is what the forge, or embry0 before it, said.
    """
    from embry0.safety.redaction import redact_bounded

    diagnostics: dict[str, Any] = {}
    url, _job_row = await _open_fallback_pr(
        self, state.get("issue_id"), str(state.get("job_id") or ""), state, error_code=None, diagnostics=diagnostics
    )
    if url:
        return url, ""
    # The forge's answer goes into the pause, the card's line, an event, the checkpoint and the logs: whatever a
    # proxy page echoed back (a header, a token) is redacted here, once, and the answer bounded.
    reason = str(diagnostics.get("change_request_error") or "no change request was opened")
    return None, redact_bounded(reason, MERGE_AS_IS_REASON_CHARS)


async def _finish_credential_uses(self: IssueExecutor, job_id: str, *, error_code: str | None) -> None:
    """Stamp the job's credential_uses rows (spec §4). Best-effort: the ledger never changes a job outcome."""
    repo = getattr(self, "_credentials_repo", None)
    if repo is None:
        return
    try:
        await repo.finish_uses(job_id, error_code or "ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("credential_uses_finish_failed", job_id=job_id, error=str(exc)[:200])


async def _publish_verification(self: IssueExecutor, job_id: str, pr_url: Any) -> None:
    """Best-effort hand-off to ``publish_verification`` — never raises."""
    # getattr: unit tests build the executor via __new__ and set only what
    # their path touches.
    db = getattr(self, "_db", None)
    if db is None:
        return
    try:
        from embry0.services.verification_publish import publish_verification
        from embry0.storage.repositories.deliverables import DeliverablesRepository
        from embry0.storage.repositories.job_verifications import JobVerificationsRepository

        if not pr_url:
            pr_url = ((await self._jobs.get(job_id)) or {}).get("pr_url")
        await publish_verification(
            job_id=job_id,
            pr_url=str(pr_url) if pr_url else None,
            verifications_repo=JobVerificationsRepository(db),
            deliverables_repo=DeliverablesRepository(db),
            github_token=getattr(self, "_github_token", None) or "",
            config=getattr(self, "_config", None),
            credential_resolver=getattr(self, "_credential_resolver", None),
        )
    except Exception:
        logger.warning("verification_publish_failed", job_id=job_id, exc_info=True)


# #349: why a failed job stopped, in words a person reviewing its PR can act
# on. Never the ERR_ code: the body is for the reader, not for /stats.
_STOPPED_BECAUSE: dict[str, str] = {
    "infra": "embry0's own machinery failed before it could finish (the sandbox, storage or an upstream service)",
    "agent": "the agent run did not finish: it ran out of turns or time, crashed, or returned output embry0 could not use",
    "verification": "the change did not pass review or QA",
    "budget": "a spending limit stopped it",
    "config": "its configuration needs an operator's change",
    "cancelled": "it was stopped before it finished",
    "unknown": "it failed for a reason embry0 could not classify",
}


#: A code whose own words say more than its category's.
_STOPPED_BECAUSE_CODES: dict[str, str] = {
    # #182: done_when.on_unmet fail. "Did not pass review or QA" would be
    # wrong: neither ran; the coding runs' own report fell short.
    ErrorCode.DONE_WHEN_UNMET.value: "its coding runs did not meet every item of its done-when list",
}


def _not_finished_note(why: str) -> str:
    """The note a change request of a job that did not finish carries (#349), giving ``why``."""
    return (
        f"**embry0 did not finish this job:** {why}. "
        "The work it pushed is here so it is not lost. Review it before merging; "
        "close it unmerged to let a later run start over."
    )


def _unfinished_delivery(
    state: Mapping[str, Any], job_row: Mapping[str, Any] | None, error_code: str | None
) -> tuple[list[str], bool]:
    """What a job's change request must say about work it delivers unfinished (#182), and whether only a person
    may merge it.

    - A failed job whose last done-when report left items unmet (its
      ``done_when.on_unmet`` is ``fail``, or it stopped for another reason):
      the items not met. The "did not finish" note and ``human-merge-only``
      come with every failed job's.
    - A job a person completed with "merge as is" at a pause where salvage
      had committed its coding run's work: the branch ends in embry0's "Do
      not merge it as it is" commit, so the note says so, and only a person
      may merge it.
    - A job that went on to review with items unmet (``done_when.on_unmet``
      ``review``, marked on its report) and was delivered: the items, for
      whoever merges it.

    The list is read from the job row first: a resumed job's state holds only
    what ran after the pause.
    """
    from embry0.services.done_when import as_list, unmet_block
    from embry0.workflows.issue_to_pr.nodes._salvage import committed_unfinished

    listed = as_list((job_row or {}).get("done_when")) or as_list(state.get("done_when"))
    report = (listed or {}).get("report")
    if error_code:
        # Whatever stopped it, the items its last report left unmet are what a
        # person picking the work up needs.
        block = unmet_block(listed)
        return ([block] if block else []), False
    parts: list[str] = []
    record = state.get("unfinished_work")
    salvaged = committed_unfinished(record)
    if salvaged and isinstance(record, Mapping):
        agent = record.get("agent") or "developer"
        reason = record.get("reason") or "before it finished"
        parts.append(
            _not_finished_note(
                f"its {agent} stopped ({reason}) before it committed, embry0 committed what it left, and a person "
                "chose to deliver the branch as it is, ending in that unfinished commit"
            )
        )
    if isinstance(report, Mapping) and report.get("on_unmet") == "review":
        block = unmet_block(listed)
        if block:
            parts.append(block)
    return parts, salvaged


def _existing_note(parts: list[str], error_code: str | None) -> list[str]:
    """``parts`` for a change request the job did not open now: a failed job's comment leads with why it stopped."""
    return [_not_finished_note(_stopped_because(error_code)), *parts] if error_code else parts


def _finished(state: Mapping[str, Any], error_code: str | None) -> bool:
    """Whether the job finished its work: it completed, and not because a person answered "merge as is" at a pause
    (``merged_as_is``, set by ``max_retries_node``), which delivers what stood without finishing it."""
    return not error_code and not state.get("merged_as_is")


def _forge_access(self: IssueExecutor) -> Any:
    from embry0.services.unfinished_note import ForgeAccess

    # getattr: unit tests build the executor via __new__ and set only what their path touches.
    return ForgeAccess(
        github_token=getattr(self, "_github_token", None) or "",
        config=getattr(self, "_config", None),
        credential_resolver=getattr(self, "_credential_resolver", None),
    )


async def _note_on_existing_change_request(
    self: IssueExecutor,
    job_id: str,
    pr_url: str,
    parts: list[str],
    *,
    error_code: str | None,
    salvaged: bool,
    settle: bool,
) -> None:
    """Keep the sticky note on a change request the job did not open now (#182; ``embry0.services.unfinished_note``).

    With ``parts``, say them there, labelled ``human-merge-only`` when only a
    person may merge it (a failed job, or a salvaged "merge as is"). Without,
    and with ``settle`` (:func:`_finished`), edit a note an earlier job left
    there to say the work is finished; a change request with no such note
    gets nothing. Never raises.
    """
    from embry0.services.unfinished_note import post_unfinished_note, settle_unfinished_note

    access = _forge_access(self)
    try:
        if parts:
            human_merge_only = salvaged or bool(error_code)
            noted = await post_unfinished_note(
                pr_url, _existing_note(parts, error_code), human_merge_only=human_merge_only, access=access
            )
            logger.info(
                "unfinished_note_posted", job_id=job_id, pr_url=pr_url, noted=noted, human_merge_only=human_merge_only
            )
        elif settle:
            settled = await settle_unfinished_note(pr_url, job_id, access=access)
            if settled:
                logger.info("unfinished_note_settled", job_id=job_id, pr_url=pr_url)
    except Exception:
        logger.warning("unfinished_note_failed", job_id=job_id, pr_url=pr_url, exc_info=True)


def _stopped_because(error_code: str | None) -> str:
    """The plain-words reason a failed job's PR body gives (#349)."""
    from embry0.safety.error_codes import ACCOUNT_FAILURE_CODES, category_for_code

    if error_code in ACCOUNT_FAILURE_CODES:
        return "the LLM account it ran on refused it (out of usage, rate-limited, or its token was rejected)"
    if error_code in _STOPPED_BECAUSE_CODES:
        return _STOPPED_BECAUSE_CODES[error_code]
    return _STOPPED_BECAUSE.get(category_for_code(error_code).value, _STOPPED_BECAUSE["unknown"])


async def _area_of_branch(self: IssueExecutor, repo: str, base: str, branch: str) -> Any | None:
    """The area (the repository's areas document, spec 2026-11-10) most of the files ``branch`` changes from
    ``base`` fall in.

    GitHub only, and best-effort: None whenever the repository names no
    areas or this cannot read them, and the pull request opens as before.
    """
    from embry0.services.areas import area_of_change

    # getattr: unit tests build the executor via __new__; without a store
    # they open their PRs without asking for areas.
    store = getattr(self, "_areas", None)
    if store is None:
        return None
    try:
        areas = await store.areas_for(repo)
        if not areas or await _forge_for(self, repo) != "github":
            return None
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(
            default_token=str(getattr(self, "_github_token", "") or ""),
            credential_resolver=getattr(self, "_credential_resolver", None),
        )
        return await area_of_change(lambda url: client.get(url, repo=repo), repo, base, branch, areas)
    except Exception:
        logger.info("pr_area_unread", repo=repo, branch=branch, exc_info=True)
        return None


async def finish_areas_map(
    self: IssueExecutor, job_id: str, proposal_id: str | None, result: Mapping[str, Any] | None, failure: str | None
) -> None:
    """Store how a map of a repository's areas ended on its proposal (spec 2026-11-10): the document its report
    carries, for a person to apply, or why there is none. Best-effort: the job's own outcome is recorded either
    way, and a proposal left ``running`` is settled when it is next read."""
    from embry0.services.areas_proposals import finish_map

    proposals = getattr(self, "_areas_proposals", None)
    store = getattr(self, "_areas", None)
    if not proposal_id or proposals is None or store is None:
        return
    try:
        job = await self._jobs.get(job_id)
        await finish_map(
            store,
            proposals,
            str(proposal_id),
            job_id=job_id,
            repo=str((job or {}).get("repo") or ""),
            document=(result or {}).get("areas_document"),
            notes=str((result or {}).get("areas_notes") or ""),
            failure=failure,
        )
    except Exception:
        logger.warning("areas_map_not_recorded", job_id=job_id, proposal_id=proposal_id, exc_info=True)


async def _open_fallback_pr(
    self: IssueExecutor,
    issue_id: str | None,
    job_id: str,
    state: dict[str, Any],
    *,
    error_code: str | None,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Open (or find) the PR for a branch the job pushed and did not deliver.

    RAV-1247: a developer that pushed its branch but never opened a PR (turn
    cap, missing final JSON, or the sandbox policy blocking PR creation —
    RAV-1282) used to complete with the work stranded on the branch. #349: so
    did every job that FAILED after pushing, on both terminal paths: a workflow
    result, and a run that raised (a hard budget stop, a sandbox that is gone).

    ``state`` is the job's merged state: ``_handle_workflow_result``'s result,
    or the last streamed state of a run that raised. ``error_code`` is None for
    a completed job. A failed job's PR says why it stopped and carries
    ``human-merge-only``, so nobody takes it for a clean result. A change the
    verification gate blocked (#299) keeps its own body; the verdict comment
    is published onto it afterwards.

    Returns the PR URL (None when there was nothing to open or the forge said
    no) and the job row it read. A PR that opened without its labels is still
    the job's PR, and the job's events say which labels it is missing (#472).
    Never raises: it also runs on failure paths that still have to record the
    job's status.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

    job_row: dict[str, Any] | None = None
    # The opener's own record of what went wrong (#459), and of labels that did not go on (#472).
    diagnostics = {} if diagnostics is None else diagnostics
    existing = state.get("pr_url")
    if not existing and not state.get("branch_name"):
        diagnostics["change_request_error"] = "the job has no branch to open a change request from"
        return None, job_row
    try:
        if existing:
            # #182: the job delivers onto a change request it already had (a
            # follow-up's, or an earlier failed job's it continued): say there
            # what is unfinished, labelled when only a person may merge it, or
            # that a later job finished what an earlier one left.
            parts, salvaged = _unfinished_delivery(state, None, error_code)
            settle = not parts and _finished(state, error_code)
            if settle:
                # A clean finish settles a note an earlier job left, which only
                # a change request the job had from its start can carry: the one
                # on its row (a follow-up's, or an earlier failed job's).
                try:
                    job_row = await self._jobs.get(job_id)
                except Exception:
                    logger.warning("pr_fallback_job_fetch_failed", job_id=job_id, exc_info=True)
                settle = (job_row or {}).get("pr_url") == existing
            if parts or settle:
                await _note_on_existing_change_request(
                    self, job_id, str(existing), parts, error_code=error_code, salvaged=salvaged, settle=settle
                )
            return None, job_row
        # RAV-1282: repo/task/issue_number live on the JOB ROW — the state is a
        # merge of node UPDATES, and no node echoes the initial state back.
        try:
            job_row = await self._jobs.get(job_id)
        except Exception:
            logger.warning("pr_fallback_job_fetch_failed", job_id=job_id, exc_info=True)
        repo_for_pr = str(state.get("repo") or (job_row or {}).get("repo") or "")
        if not repo_for_pr or "/" not in repo_for_pr:
            diagnostics["change_request_error"] = "the job's repository is not known"
            return None, job_row
        task_text = str(state.get("task") or (job_row or {}).get("task") or "").strip()
        # A step other than the developer that pushed the branch (the context
        # audit's, ADR 0022) names the PR and its closing note itself.
        pr_title = (
            str(state.get("pr_title") or "")[:100]
            or (task_text.splitlines()[0][:100] if task_text else "")
            or f"embry0: {state['branch_name']}"
        )
        issue_number = state.get("issue_number") or (job_row or {}).get("issue_number")
        if issue_number is None and issue_id:
            # #213: jobs.issue_number is GitHub-only (#161), so GitLab jobs
            # reached here with nothing to close — the merged MR left its issue
            # open. Derive the forge-native number from the issue row instead;
            # GitLab honors the same keyword.
            try:
                issue_number = forge_number_of(await self._issues.get(issue_id))
            except Exception:
                logger.warning("pr_fallback_issue_fetch_failed", issue_id=issue_id, exc_info=True)
        gate_blocked = error_code == ErrorCode.VERIFY_NEW_TEST_FAILURES.value
        body_parts = [f"Closes #{issue_number}"] if issue_number else []
        unfinished, salvaged = _unfinished_delivery(state, job_row, error_code)
        if gate_blocked:
            body_parts.append(
                "Opened by the embry0 orchestrator: automated tests on this branch fail "
                "in a way they do not fail without it, so embry0 stopped and left the "
                "work here for a person to pick up. The verification comment below says "
                "which tests."
            )
        else:
            if error_code:
                body_parts.append(_not_finished_note(_stopped_because(error_code)))
            # #182: the items a job's finish line still has unmet, and a branch
            # that ends in salvage's unfinished commit (_unfinished_delivery).
            body_parts += unfinished
            # The sandbox cannot open the PR (no token, and the safety policy
            # blocks the credential helper). Say what the developer said the
            # change is, then one line for why the body was written here.
            summary = _developer_summary(state)
            if summary:
                body_parts.append(summary)
            body_parts.append(
                str(state.get("pr_note") or "")
                or "Opened by the embry0 orchestrator from the branch the developer pushed."
            )
        # #163: mark orchestrator-opened PRs. A failed job's also says a person
        # must merge it; a gate-blocked one gets that label from its verdict.
        labels = ["embry0"] if not error_code or gate_blocked else ["embry0", HUMAN_MERGE_LABEL]
        if salvaged and HUMAN_MERGE_LABEL not in labels:
            labels.append(HUMAN_MERGE_LABEL)
        # Spec 1A §3.7: the branch init_node resolved and the clone actually
        # fetched. Never empty — see §3.8 and _base_branch_for_change_request.
        target_branch = await _base_branch_for_change_request(self, repo_for_pr, state)
        # In a monorepo the PR says which part it changes (spec 2026-10-25).
        area = await _area_of_branch(self, repo_for_pr, target_branch, str(state["branch_name"]))
        if area is not None:
            from embry0.services.areas import titled

            pr_title = titled(pr_title, area)[:100]
            labels.append(f"area:{area.slug}")
        url = await _open_change_request(
            self,
            repo=repo_for_pr,
            branch=str(state["branch_name"]),
            title=pr_title,
            body="\n\n".join(body_parts),
            labels=labels,
            target_branch=target_branch,
            diagnostics=diagnostics,
        )
        if url and diagnostics.get("labels_missing"):
            await _record_missing_labels(self, job_id, url, diagnostics)
        if url and area is not None:
            try:
                await self._jobs.update(job_id, area=area.name)
            except Exception:
                logger.warning("pr_area_record_failed", job_id=job_id, exc_info=True)
        if url and (job_row or {}).get("pr_url") == url:
            # The forge found the job's change request from before (a resumed
            # follow-up's state no longer names it): its body is not this one.
            await _note_on_existing_change_request(
                self,
                job_id,
                url,
                unfinished,
                error_code=error_code,
                salvaged=salvaged,
                settle=_finished(state, error_code),
            )
        return url, job_row
    except Exception as exc:
        logger.warning("pr_fallback_failed", job_id=job_id, exc_info=True)
        diagnostics["change_request_error"] = f"embry0 failed while opening it: {exc}"[:300]
        return None, job_row


async def _record_pr_opened(
    self: IssueExecutor,
    issue_id: str | None,
    job_id: str,
    pr_url: str,
    job_row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Tell the case ledger the job's change request exists (#278); returns the job row it used.

    Called once the job row is written: every terminal path of a job that
    opened a PR, including a run that raised after the fallback opened one
    (#349). record_case_event is guarded, so nothing here can flip the outcome.
    """
    if job_row is None:
        try:
            job_row = await self._jobs.get(job_id)
        except Exception:
            logger.warning("case_ledger_job_fetch_failed", job_id=job_id, exc_info=True)
    await record_case_event(
        self,
        "pr_opened",
        log_ids={"job_id": job_id, "issue_id": issue_id},
        issue_id=issue_id,
        job_id=job_id,
        pr_url=pr_url,
        # The PR was opened while this job ran, so its start is a bound that is
        # never later than the forge's open time — what the ledger falls back
        # to when the forge cannot say.
        job_started_at=(job_row or {}).get("created_at"),
    )
    return job_row


def _flagged_duplicate_pr(result: dict[str, Any]) -> dict[str, Any] | None:
    """The open PR triage's ``duplicate_of_pr`` flag names, from the open-PR
    list the executor put on state. triage_node only routes to
    ``skipped_duplicate`` after matching the number against that list."""
    decision = result.get("triage_decision")
    flag = decision.get("duplicate_of_pr") if isinstance(decision, dict) else None
    if not isinstance(flag, dict):
        return None
    return next((p for p in result.get("open_prs") or [] if p.get("number") == flag.get("pr_number")), None)


async def _end_triage_duplicate(self: IssueExecutor, issue_id: str | None, job_id: str, result: dict[str, Any]) -> None:
    """Triage judged the task a duplicate of an open PR (#474): the job ends
    ``skipped`` like the executor's pre-graph ticket match, with what triage
    spent."""
    duplicate = _flagged_duplicate_pr(result)
    if duplicate is None:
        # Cannot happen while triage_node validates the flag; fail loudly
        # rather than end a job skipped against a PR nobody can name.
        raise RuntimeError("triage ended the job as a duplicate but its PR is not in the open-PR list")
    job_row: dict[str, Any] | None = None
    try:
        job_row = await self._jobs.get(job_id)
    except Exception:
        logger.warning("duplicate_skip_job_fetch_failed", job_id=job_id, exc_info=True)
    await self._skip_as_duplicate(
        issue_id,
        job_id,
        str(result.get("repo") or (job_row or {}).get("repo") or ""),
        {**duplicate, "matched_by": "triage"},
        task=str(result.get("task") or (job_row or {}).get("task") or "") or None,
        summary=str(result.get("result_summary") or "") or None,
        total_cost_usd=await resolve_job_total_cost(
            getattr(self, "_traces", None), job_id, float(result.get("total_cost_usd", 0.0) or 0.0)
        ),
        kind=result.get("job_kind") or (job_row or {}).get("job_kind"),
    )
    from embry0.orchestration.checkpoint import purge_thread

    try:
        await purge_thread(self._database_url, job_id)
    except Exception:
        logger.warning("checkpoint_purge_on_skip_failed", job_id=job_id, exc_info=True)


async def _handle_workflow_result(
    self: IssueExecutor, issue_id: str | None, job_id: str, result: dict[str, Any]
) -> None:
    """Process the workflow result and update issue/job status."""
    # action lives in triage_decision (D4: state["pipeline_config"] is now the
    # flat PipelineConfig dict; it has no "action" key).
    triage_decision = result.get("triage_decision", {})
    action = triage_decision.get("action", "proceed") if isinstance(triage_decision, dict) else "proceed"
    current_stage = result.get("current_stage", "")

    logger.info(
        "workflow_result",
        issue_id=issue_id,
        job_id=job_id,
        action=action,
        stage=current_stage,
    )

    if current_stage == "awaiting_input":
        triage_decision = result.get("triage_decision", {})
        await self._handle_needs_info(issue_id, job_id, triage_decision)
        return

    if current_stage == "skipped_duplicate":
        await _end_triage_duplicate(self, issue_id, job_id, result)
        return

    if action == "split":
        await self._handle_split(issue_id, job_id, triage_decision)
    elif action == "needs_info":
        await self._handle_needs_info(issue_id, job_id, triage_decision)
    else:
        # action == "proceed" — workflow ran through the full pipeline
        from datetime import UTC, datetime

        # Pipeline ends at review_complete (approved) or abandoned (user gave up)
        failed_stages = {"abandoned", "failed", "developer_retry"}
        # RAV-1035: judge the run by its TERMINAL state, not by history.
        # state["errors"] is a reducer-append list, so an error from a
        # RECOVERED attempt (developer retry after max-turns, resumed
        # runs) persists for the job's whole life — a bare
        # bool(result["errors"]) misclassified fully-recovered pilots as
        # failed. A run ends in error when its stage is a failed stage, a
        # node set a terminal error_code, its last agent output errored,
        # or errors were appended without any agent ever running.
        outputs = result.get("agent_outputs") or []
        if outputs:
            ends_in_error = bool(outputs[-1].get("is_error"))
        else:
            ends_in_error = bool(result.get("errors"))
        final_status = (
            "failed"
            if current_stage in failed_stages
            or current_stage.endswith("_failed")
            or bool(result.get("error_code"))
            or ends_in_error
            else "completed"
        )
        # Classify the failure: graph nodes may have set a specific error_code
        # in state (e.g. ERR_TRIAGE_MALFORMED); otherwise bucket as UNKNOWN.
        from embry0.safety.error_codes import ErrorCode

        error_code: str | None = None
        error_message: str | None = None
        # The terminal output's ledger provider. None quarantines every llm
        # use on the job; a named provider quarantines only that one.
        provider: str | None = None
        # #466: the provider's reset of a spent usage window, when the job's
        # code is the terminal output's ERR_PROVIDER_QUOTA_EXHAUSTED.
        provider_resets_at: datetime | None = None
        # An xAI 403 that named its condition quarantines with that as the reason.
        quarantine_reason: str | None = None
        if final_status == "failed":
            from embry0.execution.agent_runner import aware_datetime
            from embry0.safety.error_codes import ACCOUNT_FAILURE_CODES, classify_failure

            # #304: no node-level code — fall back to the runner's diagnosis
            # on the terminal output (stale sandbox image, stalled exec,
            # turn cap, CLI crash). #281: and when neither exists, classify
            # from the terminal agent's error text, the collected errors and
            # the stage — never a bare ERR_UNKNOWN.
            terminal = outputs[-1] if outputs and ends_in_error else {}
            terminal_code = terminal.get("error_code")
            if terminal_code in ACCOUNT_FAILURE_CODES:
                # #466: the run that ended the job died because the account
                # could not serve it (spent window, rate limit, refused
                # token). Whatever a node concluded afterwards — "triage
                # output malformed" — is a symptom of that, and the account
                # code is what the quarantine below and an operator act on.
                error_code = terminal_code
            else:
                error_code = (
                    result.get("error_code")
                    or terminal_code
                    or classify_failure(
                        stage=current_stage,
                        agent_error=terminal.get("error_message"),
                        errors=[str(e) for e in (result.get("errors") or [])],
                    ).value
                )
            raw_provider = terminal.get("provider") if isinstance(terminal, dict) else None
            if isinstance(raw_provider, str) and raw_provider:
                provider = raw_provider
            if terminal_code is not None and terminal_code == error_code:
                provider_resets_at = aware_datetime(terminal.get("provider_resets_at"))
            # Never leave a failed job opaque (GET /jobs/{id} must surface a
            # reason): prefer the workflow summary, else the collected errors,
            # else the stage it died at.
            error_message = result.get("result_summary")
            if not error_message:
                errs = result.get("errors") or []
                error_message = "; ".join(str(e) for e in errs) if errs else f"failed at stage {current_stage!r}"
            # #466: an account failure's code must not arrive without the
            # provider's words ("You're out of extra usage · resets ...").
            provider_text = str(terminal.get("error_message") or "")
            account_failure = terminal_code in ACCOUNT_FAILURE_CODES and error_code == terminal_code
            if account_failure and provider_text and provider_text not in error_message:
                error_message = f"{error_message}; {terminal.get('agent_type') or 'agent'}: {provider_text}"
            if account_failure:
                from embry0.services.credentials.health import xai_quarantine_reason

                quarantine_reason = xai_quarantine_reason(provider, error_code, provider_text)
        # RAV-1247 / #349: open (or find) the PR for a branch the job pushed and
        # did not deliver, for a completed job and for a failed one. Not for an
        # abandoned job: a person said stop (#410). repo/task/issue_number live
        # on the JOB ROW (RAV-1282); the row is shared with the webhook payload
        # below, so it is fetched at most once.
        job_row: dict[str, Any] | None = None
        # #459: a merge as is opened its change request inside the graph (deliver_as_is_node), where a refusal sends
        # the job back to its pause; opening it again here would only note it twice.
        if error_code != ErrorCode.ABANDONED.value and not result.get("merge_as_is_delivered"):
            fallback_url, job_row = await _open_fallback_pr(self, issue_id, job_id, result, error_code=error_code)
            if fallback_url:
                result["pr_url"] = fallback_url
        # Spec 2026-11-10: a map's document waits on its proposal, recorded before the job ends so a reader
        # never finds the job over and its proposal still running.
        await finish_areas_map(
            self,
            job_id,
            result.get("areas_proposal_id"),
            result,
            (error_message or "the job failed") if final_status == "failed" else None,
        )

        await self._jobs.update(
            job_id,
            status=final_status,
            pr_url=result.get("pr_url"),
            error_message=error_message,
            error_code=error_code,
            # Denormalized convenience copy for list views + completion
            # comments; the deliverables table (RAV-603) is the real model.
            result_summary=(result.get("result_summary") if final_status == "completed" else None),
            # The state total omits QA entirely (its fan-out subgraph never
            # propagates cost to the parent), so resolve against trace rows.
            total_cost_usd=await resolve_job_total_cost(
                getattr(self, "_traces", None), job_id, float(result.get("total_cost_usd", 0.0) or 0.0)
            ),
            # #246: `soft` mode records the crossing on state and lets the run
            # finish, so this is the only place that figure can reach the
            # column the API already exposes. It stayed 0.0 for every job.
            budget_overrun_usd=float(result.get("budget_overrun_usd") or 0.0),
            finished_at=datetime.now(UTC),
        )
        await self._finish_credential_uses(job_id, error_code=error_code)
        await self._announce_finished(job_id)
        # Spec §3.6: a rate limit, a spent quota, or a refused token
        # quarantines the account that served this job. The job row is
        # already written. A quarantine failure must not flip it.
        health = getattr(self, "_credential_health", None)
        if health is not None and error_code in {
            ErrorCode.PROVIDER_RATE_LIMITED.value,
            ErrorCode.PROVIDER_QUOTA_EXHAUSTED.value,
            ErrorCode.AUTH_REJECTED.value,
        }:
            try:
                await health.report_job(
                    job_id, error_code, provider, resets_at=provider_resets_at, reason=quarantine_reason
                )
            except Exception as exc:
                logger.warning(
                    "credential_quarantine_failed",
                    job_id=job_id,
                    error_type=type(exc).__name__,
                )
        # #278: this is where a job's change request is first persisted —
        # the developer's own PR and the orchestrator's fallback both arrive
        # as result["pr_url"]. A failed job can still have opened one. Guarded
        # like every other post-terminal step here: the job row is already
        # written and nothing after it may flip the outcome.
        if result.get("pr_url"):
            job_row = await _record_pr_opened(self, issue_id, job_id, str(result["pr_url"]), job_row)
        elif error_code == ErrorCode.ABANDONED.value:
            # #410: the other terminal shape of an accepted finding. A person
            # answered "abandon" at the retry pause and no change request came
            # of it, so the case has nothing left to wait for — close it as
            # abandoned instead of leaving it at `accepted`, where nothing
            # could move it and the dead-man alert paged forever. Narrow by
            # design: every other failure keeps its case open, because the
            # acceptance stands and an undelivered acceptance is exactly what
            # that alert is for. Guarded like the PR report above — the job
            # row is written and no bookkeeping may flip the outcome.
            await record_case_event(
                self,
                "job_abandoned",
                log_ids={"job_id": job_id, "issue_id": issue_id},
                issue_id=issue_id,
                job_id=job_id,
                note=error_message,
            )
        # RAV-603: persist the job's deliverables. Best-effort — the job
        # status is already written; a persistence failure must not flip
        # a completed job to failed.
        deliverable_count = 0
        if final_status == "completed":
            try:
                deliverable_count = await self._persist_deliverables(job_id, result)
            except Exception:
                logger.warning("deliverables_persist_failed", job_id=job_id, exc_info=True)
            await _record_standard_audit(self, job_id, result)
        # #298/#299: the verify node's result goes on the PR and into the
        # deliverables for completed AND failed jobs (a job the gate stopped
        # is exactly the one whose PR needs the explanation). After the PR
        # fallback above, so an orchestrator-opened PR gets it too.
        await _publish_verification(self, job_id, result.get("pr_url"))
        # RAV-1052: outbound webhook — fire-and-forget after the terminal
        # status is persisted; delivery failures never touch job state.
        if self._webhook_notifier is not None:
            if job_row is None:
                try:
                    job_row = await self._jobs.get(job_id)
                except Exception:
                    logger.warning("webhook_job_fetch_failed", job_id=job_id, exc_info=True)
        notify_row: dict[str, Any] = job_row or {}
        # The watcher's own analysis jobs are not news: one an hour, delivered
        # as a raw-JSON "job completed" nobody could act on. They are counted
        # in the daily digest instead (embry0/services/digest.py).
        if self._webhook_notifier is not None and notify_row.get("dispatched_by") != dispatch_actors.WATCHER:
            # RAV-1074 gave consumers a clean summary but still nothing to
            # identify the job BY: every notification opened with a job id, so
            # they all read alike and the reader had to open the dashboard to
            # learn what the job even was. Its brief's plain title when one is
            # written (spec 2026-10-26, job briefs), else the first line of the
            # task, the title a human wrote.
            job_title = await _brief_title(self, job_id) or job_title_from_task(
                str(result.get("task") or notify_row.get("task") or "")
            )
            self._webhook_notifier.emit_soon(
                f"job.{final_status}",
                {
                    "job_id": job_id,
                    "issue_id": issue_id,
                    "title": job_title,
                    "kind": result.get("job_kind") or notify_row.get("job_kind"),
                    "repo": result.get("repo") or notify_row.get("repo"),
                    "pr_url": result.get("pr_url"),
                    # RAV-1074: clean, deliverable-derived one-liner for the
                    # notification. result_summary stays raw for consumers
                    # that need the full text (the watcher's parse_proposal).
                    "summary": build_notification_summary(result),
                    "result_summary": str(result.get("result_summary") or "")[:1000],
                    "deliverable_count": deliverable_count,
                    "total_cost_usd": result.get("total_cost_usd", 0.0),
                    "error_code": error_code,
                    "error_message": error_message,
                    "started_by": person_who_started(notify_row),
                },
            )
        # #319 phase 2: a completed job's PR on an enabled repo is asked about
        # in the operator's approval chat. Scheduled, not awaited — it reads
        # the PR from GitHub, and nothing here may wait on that or be failed
        # by it.
        merge_approvals = getattr(self, "_merge_approvals", None)
        if merge_approvals is not None and final_status == "completed" and result.get("pr_url"):
            try:
                merge_approvals.schedule_ask(job_id=job_id, pr_url=str(result["pr_url"]))
            except Exception:
                logger.warning("merge_approval_schedule_failed", job_id=job_id, exc_info=True)
        # Purge LangGraph checkpoint state for completed/failed jobs.
        # Best-effort: job status is already written; failure here does not
        # affect the job outcome.
        from embry0.orchestration.checkpoint import purge_thread

        try:
            await purge_thread(self._database_url, job_id)
        except Exception:
            logger.warning(
                "checkpoint_purge_on_complete_failed",
                job_id=job_id,
                status=final_status,
                exc_info=True,
            )
        # A job with no issue (every log-watcher analysis job) has no status
        # to change: a row here would record a transition that never happened.
        if issue_id is not None:
            issue_status = "closed" if final_status == "completed" else "open"
            await self._issues.update(issue_id, status=issue_status)
            await self._issues.update_parent_status(issue_id)
            await self._emit_audit(
                self._db,
                "issue.status_changed",
                actor="system",
                details={
                    "old_status": "triaging",
                    "new_status": issue_status,
                    "job_status": final_status,
                },
                audit_log_path=self._audit_log_path,
                issue_id=issue_id,
            )


async def _handle_split(self: IssueExecutor, issue_id: str | None, job_id: str, decision: dict[str, Any]) -> None:
    """Decompose issue into child issues based on triage split decision."""
    sub_tasks = decision.get("sub_tasks", [])
    if not sub_tasks:
        logger.warning("split_no_subtasks", issue_id=issue_id)
        await self._issues.update(issue_id, status="open")
        return

    issue = await self._issues.get(issue_id)
    child_ids: list[str] = []

    for sub in sub_tasks:
        child_id = await self._issues.create(
            title=sub.get("task", sub.get("title", "Untitled subtask")),
            body=sub.get("description", sub.get("body", "")),
            priority=issue.get("priority", "medium") if issue else "medium",
            labels=issue.get("labels", []) if issue else [],
            repo=issue.get("repo") if issue else None,
            parent_issue_id=issue_id,
            created_by="triage_agent",
        )
        child_ids.append(child_id)

    await self._jobs.update(job_id, status="completed")
    await self._finish_credential_uses(job_id, error_code=None)
    await self._announce_finished(job_id)
    await self._issues.update(issue_id, status="open")
    await self._issues.update_parent_status(issue_id)

    await self._emit_audit(
        self._db,
        "issue.decomposed",
        actor="triage_agent",
        details={
            "child_issue_ids": child_ids,
            "count": len(child_ids),
            "reasoning": decision.get("reasoning", ""),
        },
        audit_log_path=self._audit_log_path,
        issue_id=issue_id,
    )

    await self._emit_audit(
        self._db,
        "issue.triaged",
        actor="triage_agent",
        details={
            "action": "split",
            "confidence": decision.get("confidence"),
            "reasoning": decision.get("reasoning", ""),
        },
        audit_log_path=self._audit_log_path,
        issue_id=issue_id,
    )

    logger.info("issue_decomposed", issue_id=issue_id, children=child_ids)


async def _broadcast_event(self: IssueExecutor, job_id: str, event: dict[str, Any]) -> None:
    """Forward event to WebSocket subscribers and persist to job_logs (embry0.services.job_events)."""
    await broadcast_job_event(
        self._jobs, getattr(self, "_event_bus", None), getattr(self, "_metrics", None), job_id, event
    )


async def _announce_finished(self: IssueExecutor, job_id: str) -> None:
    """spec 3.5: every terminal transition ends with job_finished. Called right
    after the terminal status write at every site (a guard test enforces it)."""
    await emit_job_finished(self._jobs, getattr(self, "_event_bus", None), getattr(self, "_metrics", None), job_id)


def _stable_input_id(job_id: str, asking_node: str, question: str, answer: Any) -> str:
    digest = hashlib.sha256(json.dumps([job_id, asking_node, question, answer], default=str).encode()).hexdigest()
    return f"inp-{digest[:12]}"


async def _record_auto_answers(
    self: IssueExecutor, issue_id: str | None, job_id: str, questions: list[dict[str, Any]]
) -> None:
    """Store each question an agent asked and answered itself, as an ``auto_answered`` input of the job (#410).

    ``_enforce_ask_user_cap`` splits such questions (``importance="auto_answerable"``) from the blocking ones
    and puts them on the node's update as ``auto_answered_agent_questions``, each with the ``input_id`` its row
    takes; the workflow goes on without waiting. Each becomes a row of the job's inputs, with the answer the
    agent suggested, under its job and the job's issue, so the dashboard shows it with the job's other inputs
    and a person can override it (the answer routes accept an ``auto_answered`` row). Audited as
    ``issue.input_created``, as a blocking question is, when the row is new: a node's update replayed on a
    resume carries the same ids and writes nothing. Best-effort: a write that fails is logged and never fails
    the workflow, and the questions after it are still tried.
    """
    inputs = getattr(self, "_inputs", None)
    if inputs is None or not questions:
        return
    for question in questions:
        text = str(question.get("question") or "")
        if not text:
            continue
        asking_node = str(question.get("asking_node") or "developer")
        suggested = question.get("auto_answer")
        # An update built before questions carried an id (one replayed from a checkpoint written by an older
        # release) gets one made from what it says, so a second replay still writes nothing.
        input_id = str(question.get("input_id") or _stable_input_id(job_id, asking_node, text, suggested))
        try:
            written = await inputs.record_auto_answer(
                input_id=input_id,
                issue_id=issue_id,
                job_id=job_id,
                question=text,
                asking_node=asking_node,
                auto_answer=None if suggested is None else str(suggested),
            )
            if written:
                await self._emit_audit(
                    self._db,
                    "issue.input_created",
                    actor=asking_node,
                    details={"input_id": input_id, "question": text, "importance": "auto_answerable"},
                    audit_log_path=getattr(self, "_audit_log_path", None),
                    issue_id=issue_id,
                )
        except Exception:
            logger.warning("auto_answer_persist_failed", job_id=job_id, input_id=input_id, exc_info=True)


async def _handle_needs_info(self: IssueExecutor, issue_id: str | None, job_id: str, decision: dict[str, Any]) -> None:
    """Create input records, dispatch notifications, and pause for blocking questions.

    Atomicity: the INSERTs for all input rows and the status UPDATEs for the
    job + issue are wrapped in a single DB transaction. Either all persist
    or none do — no orphan input rows if the orchestrator crashes mid-flow.

    Audit events and notification dispatch happen AFTER the commit, because:
    - Audit is additive (can emit a few extra records; no correctness impact).
    - Notifications are best-effort (a missed Telegram ping is not a data issue).
    """
    import uuid

    from embry0.notifications.dispatcher import dispatch_questions

    questions = decision.get("questions", [])
    asking_node = decision.get("asking_node", "triage") if "asking_node" in decision else "triage"

    # Normalize questions to dicts and compute rollout details.
    normalized: list[dict[str, Any]] = []
    for q in questions:
        if isinstance(q, str):
            q = {"question": q, "importance": "blocking"}
        importance = q.get("importance", "blocking")
        auto_answer = q.get("suggested_answer") if importance == "auto_answerable" else None
        normalized.append(
            {
                "question": q["question"],
                "importance": importance,
                "auto_answer": auto_answer,
            }
        )

    # RAV-1271: an agent_needs_info interrupt with zero questions used to fall
    # into the all-auto-answerable branch below — marking the job completed
    # (without finished_at) and spawning a pointless retriage. It means a
    # workflow-side routing bug; fail the job loudly instead.
    if not normalized:
        from datetime import UTC, datetime

        from embry0.safety.error_codes import ErrorCode

        logger.error("needs_info_zero_questions", job_id=job_id, issue_id=issue_id)
        await self._jobs.update(
            job_id,
            status="failed",
            error_message="agent_needs_info interrupt carried zero questions (workflow routing bug)",
            error_code=ErrorCode.EMPTY_INTERRUPT.value,
            finished_at=datetime.now(UTC),
        )
        await self._finish_credential_uses(job_id, error_code=ErrorCode.EMPTY_INTERRUPT.value)
        await self._announce_finished(job_id)
        await self._issues.update(issue_id, status="open")
        return

    has_blocking = any(n["importance"] == "blocking" for n in normalized)

    # Transactional writes: inputs + status updates as one unit.
    if self._db is None:
        raise RuntimeError("IssueExecutor._db is None — cannot write inputs")
    enriched_questions: list[dict[str, Any]] = []
    try:
        async with self._db.transaction() as conn:
            for n in normalized:
                input_id = f"inp-{uuid.uuid4().hex[:12]}"
                status = (
                    "auto_answered"
                    if n["auto_answer"] is not None and n["importance"] == "auto_answerable"
                    else "pending"
                )
                await conn.execute(
                    """
                    INSERT INTO issue_inputs (
                        id, issue_id, job_id, asking_node, question,
                        importance, auto_answer, status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    input_id,
                    issue_id,
                    job_id,
                    asking_node,
                    n["question"],
                    n["importance"],
                    n["auto_answer"],
                    status,
                )
                enriched_questions.append(
                    {
                        "question": n["question"],
                        "importance": n["importance"],
                        "auto_answer": n["auto_answer"],
                        "input_id": input_id,
                    }
                )

            if has_blocking:
                # RAV-1063: CAS transitions on the transaction's connection
                # — previously raw UPDATEs that bypassed the transition
                # machine. A False return means a concurrent writer (e.g. a
                # cancel racing this interrupt) moved the job/issue out of
                # the expected state: abort the whole transaction so no
                # input rows land on a job that is no longer waiting.
                ok = await self._jobs.cas_transition_on_conn(conn, job_id, "awaiting_input", {"running"})
                if ok and issue_id is not None:
                    ok = await self._issues.cas_transition_on_conn(
                        conn, issue_id, "awaiting_input", {"triaging", "in_progress"}
                    )
                if not ok:
                    raise StatusTransitionConflict(f"needs_info status race on job {job_id}")
    except StatusTransitionConflict:
        logger.warning(
            "needs_info_aborted_status_race",
            job_id=job_id,
            issue_id=issue_id,
            question_count=len(normalized),
        )
        return

    # Audit (post-commit, additive)
    for eq in enriched_questions:
        await self._emit_audit(
            self._db,
            "issue.input_created",
            actor=asking_node,
            details={
                "input_id": eq["input_id"],
                "question": eq["question"],
                "importance": eq["importance"],
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

    if has_blocking:
        await self._emit_audit(
            self._db,
            "issue.pipeline_paused",
            actor="system",
            details={
                "job_id": job_id,
                "pending_count": sum(1 for eq in enriched_questions if eq["importance"] == "blocking"),
            },
            audit_log_path=self._audit_log_path,
            issue_id=issue_id,
        )

        # Fetched once for the webhook payload, the channel dispatch, and
        # the Linear question comment below. None for issue-less API jobs.
        issue = await self._issues.get(issue_id) if issue_id else None

        # RAV-1052: outbound webhook per blocking question — global
        # config-driven (unlike the per-issue channels below), so
        # issue-less API jobs emit too. Fire-and-forget.
        if self._webhook_notifier is not None:
            dashboard_base = (getattr(self._config, "dashboard_public_url", "") or "http://localhost:8200").rstrip("/")
            answer_url = f"{dashboard_base}/issues/{issue_id}" if issue_id else f"{dashboard_base}/jobs/{job_id}"
            started_by = person_who_started(await self._jobs.get(job_id))
            for eq in enriched_questions:
                if eq["importance"] != "blocking":
                    continue
                self._webhook_notifier.emit_soon(
                    "agent.question",
                    {
                        "job_id": job_id,
                        "issue_id": issue_id,
                        "input_id": eq["input_id"],
                        "question": eq["question"],
                        "asking_node": asking_node,
                        "answer_url": answer_url,
                        "linear_identifier": linear_identifier_of(issue),
                        "started_by": started_by,
                    },
                )

        blocking_questions = [
            {
                "input_id": eq["input_id"],
                "question": eq["question"],
                "asking_node": asking_node,
                "importance": eq["importance"],
            }
            for eq in enriched_questions
            if eq["importance"] == "blocking"
        ]

        # Best-effort notification dispatch
        if issue and self._config:
            # The GitHub-comment channel is injected by the API lifespan
            # via app.state; the always-on dashboard no-op channel runs
            # by default.
            await dispatch_questions(
                blocking_questions,
                issue,
                github_comment_channel=self._github_comment_channel,
                telegram_channel=self._telegram_channel,
                slack_channel=self._slack_channel,
                tracker_comment_channels=getattr(self, "_tracker_channels", None),
            )

        # RAV-1076: the decision trail starts on the ticket — post the
        # blocking questions as a Linear comment; answers follow via
        # answer_recorder.record_answer from whichever channel they land.
        await self._post_tracker_questions(issue, job_id, asking_node, blocking_questions)
    else:
        # All auto-answerable — re-run triage with the answers folded into
        # additional_context. Previously this called execute(issue_id) with
        # no context, causing the new triage agent to re-ask the same
        # questions. Now the agent sees the Q&A block and can proceed.
        qa_block = _fold_auto_answers_into_context(enriched_questions)
        await self._jobs.update(job_id, status="completed")
        await self._finish_credential_uses(job_id, error_code=None)
        await self._announce_finished(job_id)
        if issue_id is None:
            # Issue-less API jobs have no issue row to re-triage — the
            # auto-answer re-run path is issue-driven. Surfaced by the
            # str|None sweep; previously execute(None) raised and was
            # swallowed by the except below. Make the skip explicit.
            logger.warning("auto_answer_retriage_skipped_issueless", job_id=job_id)
            return
        await self._issues.update(issue_id, status="triaging")
        try:
            new_job_id = await self.execute(
                issue_id, additional_context=qa_block, dispatched_by=dispatch_actors.AUTO_RETRIAGE
            )
            logger.info(
                "auto_answer_retriage",
                issue_id=issue_id,
                new_job_id=new_job_id,
                qa_block_len=len(qa_block),
            )
        except Exception:
            logger.warning("auto_answer_retriage_failed", issue_id=issue_id, exc_info=True)
            await self._issues.update(issue_id, status="open")

    logger.info("needs_info_handled", issue_id=issue_id, blocking=has_blocking)
