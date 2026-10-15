"""GitHub, GitLab, Linear and Alertmanager webhook routes.

Verification, smee unwrapping, and body parsing live in
:mod:`embry0.api.webhook_ingress` so every forge shares one policy; this
module is routing and per-event handling only.

Alertmanager is not a forge — it is a detection source (#296). Its route and
the ``workflow_run`` handler both translate their payload with
:mod:`embry0.services.healing.signals` and then write through one function,
:func:`_record_signal`, so every non-watcher source agrees about what "the
same problem, again" means.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, Header, Request

from embry0.api.trigger_context import adopt_linear_sync, linear_trigger_context, resolve_trigger_labels
from embry0.api.webhook_ingress import SignatureScheme, parse_webhook_body, verify_webhook
from embry0.notifications.inbound_dispatch import apply_inbound_directives
from embry0.notifications.inbound_parser import parse_answer_directives
from embry0.services.answer_recorder import record_answer
from embry0.services.pr_review.triggers import has_review_command
from embry0.trackers import TriggerContext, actor_of
from embry0.trackers.author_association import check_comment_author, parse_author_roles

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/webhook/linear")
async def linear_webhook(
    request: Request,
    linear_signature: str = Header(default="", alias="Linear-Signature"),
) -> dict[str, Any]:
    """Linear webhook → embry0 pipeline trigger.

    Linear signs deliveries with ``Linear-Signature``: hex HMAC-SHA256 of the
    raw body under the webhook's signing secret (no ``sha256=`` prefix —
    that's GitHub's convention). ``WEBHOOK_DEV_MODE=true`` bypasses
    verification for smee-relay development, same rules as the GitHub hook.
    """
    config = request.app.state.config
    body = await request.body()

    # Credentials resolve per delivery (DB row over env) so editing them in
    # Settings → Integrations takes effect without an orchestrator restart.
    provider = getattr(request.app.state, "linear_provider", None)
    resolved_sync = None
    if provider is not None:
        resolved_sync, secret = await provider.resolve()
    else:
        secret = config.linear_webhook_secret

    verify_webhook(
        scheme=SignatureScheme.HMAC_SHA256_BARE,
        body=body,
        presented=linear_signature,
        secret=secret,
        webhook_dev_mode=config.webhook_dev_mode,
        forge="Linear",
    )
    payload = parse_webhook_body(body)

    linear_sync = adopt_linear_sync(request, resolved_sync)
    if linear_sync is None:
        return {"status": "ignored", "reason": "linear integration not configured"}

    logger.info("linear_webhook_received", type=payload.get("type"), action=payload.get("action"))
    result = await linear_sync.handle_webhook_event(
        payload, await linear_trigger_context(request, actor=actor_of(payload, "linear"))
    )
    return dict(result)


@router.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str = Header(default="", alias="X-Gitlab-Token"),
) -> dict[str, Any]:
    """GitLab Issue Hook → embry0 pipeline trigger (#165).

    GitLab authenticates with ``X-Gitlab-Token``: the configured secret sent
    **verbatim**, not an HMAC over the body like GitHub's or Linear's. The
    shared verifier handles all three shapes and applies one dev-mode policy.
    """
    config = request.app.state.config
    body = await request.body()

    verify_webhook(
        scheme=SignatureScheme.SHARED_SECRET,
        body=body,
        presented=x_gitlab_token,
        secret=getattr(config, "gitlab_webhook_secret", "") or "",
        webhook_dev_mode=config.webhook_dev_mode,
        forge="GitLab",
    )
    payload = parse_webhook_body(body)

    # #30: merge request and comment events are review triggers. Before this
    # they reached GitLabSyncService, which ignores everything but issues.
    # Routed ahead of the sync check: reviews are gated on registration and
    # pr_review.enabled, not on the issue integration being wired.
    kind = payload.get("object_kind")
    if kind in ("merge_request", "note"):
        method = "handle_gitlab_merge_request" if kind == "merge_request" else "handle_gitlab_note"
        review = await _pr_review(request, method, payload)
        if review is None:
            return {"status": "ignored", "reason": f"not a pull request review trigger: {kind}"}
        return _pr_review_response(review)

    gitlab_sync = getattr(request.app.state, "gitlab_sync", None)
    if gitlab_sync is None:
        return {"status": "ignored", "reason": "gitlab integration not configured"}

    logger.info(
        "gitlab_webhook_received",
        object_kind=payload.get("object_kind"),
        action=(payload.get("object_attributes") or {}).get("action"),
    )
    result = await gitlab_sync.handle_webhook_event(
        payload,
        TriggerContext(
            issues_repo=request.app.state.issues_repo,
            issue_executor=request.app.state.issue_executor,
            trigger_labels=set(await resolve_trigger_labels(request)),
            actor=actor_of(payload, "gitlab"),
        ),
    )
    return dict(result)


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, Any]:
    config = request.app.state.config
    body = await request.body()

    verify_webhook(
        scheme=SignatureScheme.HMAC_SHA256_PREFIXED,
        body=body,
        presented=x_hub_signature_256,
        secret=config.github_webhook_secret,
        webhook_dev_mode=config.webhook_dev_mode,
        forge="GitHub",
    )
    payload = parse_webhook_body(body)

    action = payload.get("action", "")
    event_type = x_github_event
    logger.info("webhook_received", event_type=event_type, action=action)

    handler = _GITHUB_EVENT_HANDLERS.get(event_type)
    if handler is None:
        return {"status": "ignored", "event": event_type, "action": action}
    return await handler(request, payload, action)


async def _pr_review(request: Request, method: str, *args: Any) -> dict[str, Any] | None:
    """Hand a delivery to the pull request reviewer (#30); None when it is not a review trigger.

    Never raises: a review that cannot start must not fail the delivery, or
    the merge-sync bookkeeping that rides the same event.
    """
    dispatcher = getattr(request.app.state, "pr_review_dispatcher", None)
    if dispatcher is None:
        return None
    try:
        result = await getattr(dispatcher, method)(*args)
    except Exception:
        logger.warning("pr_review_dispatch_failed", method=method, exc_info=True)
        return {"dispatched": False, "reason": "dispatch_error"}
    return None if result is None else dict(result.as_dict())


def _pr_review_response(review: dict[str, Any]) -> dict[str, Any]:
    return {"status": "accepted" if review.get("dispatched") else "ignored", "action": "pr_review", **review}


# ---------------------------------------------------------------------------
# GitHub event handlers
#
# A dispatch table, not an if/elif chain: adding a forge or an event should be
# a registry entry, not another branch in a 250-line function (#159). Each
# handler owns its own action filtering, because "which actions matter" is a
# property of the event, not of the router.
# ---------------------------------------------------------------------------

_ISSUE_ACTIONS = ("opened", "edited", "closed", "reopened", "labeled", "unlabeled")


async def _handle_issues(request: Request, payload: dict[str, Any], action: str) -> dict[str, Any]:
    if action not in _ISSUE_ACTIONS:
        return {"status": "ignored", "event": "issues", "action": action}
    issues_repo = request.app.state.issues_repo
    github_sync = request.app.state.github_sync
    if not (issues_repo and github_sync):
        return {"status": "ignored", "event": "issues", "action": action}
    result = await github_sync.handle_webhook_event(
        payload,
        TriggerContext(
            issues_repo=issues_repo,
            issue_executor=request.app.state.issue_executor,
            trigger_labels=set(await resolve_trigger_labels(request)),
            event_type="issues",
            action=action,
            # #246: the sender is the person who labelled/opened the issue —
            # the identity the dispatch rate limit buckets on. `created_by`
            # on the issue row is a provenance constant, never a human.
            actor=actor_of(payload, "github"),
        ),
    )
    return dict(result)


async def _handle_issue_comment(request: Request, payload: dict[str, Any], action: str) -> dict[str, Any]:
    """Answers to input questions via `/answer <ref>:` directives, and tagged
    PR comments that dispatch a pr-followup job.

    Answering unblocks a paused pipeline, so it is gated on the same
    ``author_association`` allowlist as comment-dispatch (#253) — on a public
    repo "can comment" is otherwise "can steer someone else's agent".

    NOTE: receiving these requires "Issue comments" in the GitHub webhook
    event subscription at https://github.com/<org>/<repo>/settings/hooks.
    """
    if action != "created":
        return {"status": "ignored", "event": "issue_comment", "action": action}

    comment = payload.get("comment") or {}
    comment_body = comment.get("body") or ""
    if not comment_body:
        return {"ok": True, "applied": 0, "reason": "empty body"}

    # #30: a /embry0 review comment on a pull request asks for a review and
    # nothing else — never a follow-up, never an /answer — even when the
    # reviewer is not wired.
    if (payload.get("issue") or {}).get("pull_request") and has_review_command(comment_body):
        review = await _pr_review(request, "handle_github_comment", payload)
        return _pr_review_response(review or {"dispatched": False, "reason": "pr_review_not_configured"})

    # RAV-1092: a tagged comment on a PULL REQUEST dispatches a pr-followup
    # job on the PR's branch (session-adopting). Checked before the /answer
    # directives — PR comments are never tracked-issue answers, and the
    # service's own gate (enabled/tag/author role/dedup) makes this a no-op
    # for everything else.
    if (payload.get("issue") or {}).get("pull_request"):
        dispatch_service = getattr(request.app.state, "comment_dispatch", None)
        if dispatch_service is not None:
            result = await dispatch_service.handle_pr_comment(payload)
            if result.get("dispatched"):
                return {"status": "accepted", "action": "comment_dispatch", **result}

    directives = parse_answer_directives(comment_body)
    if not directives:
        return {"ok": True, "applied": 0, "reason": "no directives"}

    repo_name = (payload.get("repository") or {}).get("full_name") or ""

    # #253: authorship gate. Checked after parsing so ordinary chatter never
    # logs a rejection, and before any lookup so an outsider's directive costs
    # nothing. Silent by design — a rejection comment would tell a drive-by
    # commenter which issues have an agent waiting on them.
    config = getattr(request.app.state, "config", None)
    allowed_roles = parse_author_roles(getattr(config, "comment_dispatch_author_roles", None))
    trusted, gate_reason = check_comment_author(comment, allowed_roles)
    if not trusted:
        logger.warning(
            "answer_rejected_author",
            reason=gate_reason,
            association=(comment.get("author_association") or "").upper() or None,
            repo=repo_name or None,
            issue_number=(payload.get("issue") or {}).get("number"),
            commenter=(comment.get("user") or {}).get("login"),
        )
        return {"ok": True, "applied": 0, "reason": "author not allowed"}

    gh_number = (payload.get("issue") or {}).get("number")
    if not repo_name:
        logger.warning("issue_comment_missing_repo", payload_keys=list(payload.keys()))
        return {"ok": True, "applied": 0, "reason": "missing repo"}
    if gh_number is None:
        logger.warning("issue_comment_missing_issue_number", repo=repo_name)
        return {"ok": True, "applied": 0, "reason": "missing issue number"}

    issues_repo = request.app.state.issues_repo
    inputs_repo = getattr(request.app.state, "inputs_repo", None)
    executor = getattr(request.app.state, "issue_executor", None)
    if not (issues_repo and inputs_repo and executor):
        logger.warning(
            "issue_comment_missing_state",
            has_issues=bool(issues_repo),
            has_inputs=bool(inputs_repo),
            has_executor=bool(executor),
        )
        return {"ok": True, "applied": 0, "reason": "state not initialised"}

    issue = await issues_repo.get_by_tracker_ref("github", repo_name, str(gh_number))
    if issue is None:
        return {"ok": True, "applied": 0, "reason": "issue not tracked"}

    issue_id = issue["id"]
    login = (comment.get("user") or {}).get("login") or "unknown"

    async def _resume() -> None:
        await executor.resume_for_issue(issue_id)

    async def _record(row: dict[str, Any], value: str) -> None:
        # RAV-1076: shared record path — Telegram mirror + Linear comment.
        await record_answer(
            inp=row,
            answer=value,
            answered_by=f"github:{login}",
            channel="github",
            inputs_repo=inputs_repo,
            issues_repo=issues_repo,
            tracker_channels=getattr(request.app.state, "tracker_channels", None),
            config=getattr(request.app.state, "config", None),
        )

    async def _resolve_sequence(n: int) -> dict[str, Any] | None:
        # "/answer 2:" means the 2nd question of the one question comment the
        # issue is waiting on — the list the author was reading — not the 2nd
        # row of the live pending list, which other channels mutate underneath
        # them. The repo returns None when more than one comment has questions
        # outstanding, and the author is asked for the id instead (#253).
        row: dict[str, Any] | None = await inputs_repo.get_by_github_comment_sequence(issue_id, n)
        return row

    result = await apply_inbound_directives(
        issue_id=issue_id,
        directives=directives,
        inputs_repo=inputs_repo,
        on_all_answered=_resume,
        answered_by=f"github:{login}",
        record=_record,
        resolve_sequence=_resolve_sequence,
    )
    if result.unresolved_sequence:
        await _reply_use_input_id(request, repo_name, gh_number)
    return {
        "ok": True,
        "applied": result.applied,
        "skipped": result.skipped,
        "unmatched": result.unmatched,
    }


async def _reply_use_input_id(request: Request, repo_name: str, gh_number: int) -> None:
    """Tell the author their positional ``/answer N:`` could not be resolved.

    Reached when the question comment predates #253 (no render mapping), the
    question at that position is already answered, or the issue has questions
    outstanding in more than one comment so N is ambiguous. Guessing is what
    #253 removed, so the only safe answer is to ask for the stable id — which
    the question comment now prints under each question.

    Only reachable after the authorship gate, so it cannot be used to make
    embry0 reply to a drive-by commenter. Best-effort: a failed reply must not
    fail the webhook delivery.
    """
    channel = getattr(request.app.state, "github_comment_channel", None)
    if channel is None:
        return
    # Both examples sit mid-line on purpose: this comment comes back through
    # the same webhook, and a `/answer` opening a line would parse as one.
    body = (
        "🤖 embry0 could not match that `/answer <n>:` to a pending question — "
        "the numbering it refers to is no longer current, or more than one "
        "question comment is still waiting on an answer.\n\n"
        "Re-send it addressed by the question's id — the `inp-…` code printed "
        "under each question — e.g. `/answer inp-a1b2c3d4e5f6: your answer`. "
        "You can also answer from the embry0 dashboard, using the link in the "
        "question comment."
    )
    try:
        await channel.post_comment(repo_name, gh_number, body)
    except Exception:
        logger.warning("answer_unresolved_reply_failed", repo=repo_name, issue_number=gh_number, exc_info=True)


# `synchronize` (a push to the PR's branch) carries the new head sha, which the
# healing-case ledger keeps; the other three change what became of the PR.
_PR_ACTIONS = ("opened", "reopened", "synchronize", "closed")


async def _handle_pull_request(request: Request, payload: dict[str, Any], action: str) -> dict[str, Any]:
    """Merge-sync bookkeeping, then the pull request review trigger (#30).

    The two are independent: ``opened`` is both a delivery state and a
    possible review trigger, ``ready_for_review`` is only the latter.
    """
    result = await _record_pull_request(request, payload, action)
    review = await _pr_review(request, "handle_github_pull_request", payload, action)
    if review is not None:
        result = {**result, "pr_review": review}
    return result


async def _record_pull_request(request: Request, payload: dict[str, Any], action: str) -> dict[str, Any]:
    """PR ↔ job linking and delivery state. Creates no jobs — bookkeeping only.

    All of it lives in ``services.merge_sync``: this handler only turns the
    payload into a fact and hands it over, so the webhook, the periodic sweep
    and the backfill cannot disagree about what a merged PR means (#279).

    NOTE: requires "Pull requests" in the GitHub webhook event subscription.
    A repo without one is still covered, later, by the sweep.
    """
    if action not in _PR_ACTIONS:
        return {"status": "ignored", "event": "pull_request", "action": action}

    from embry0.services.merge_sync.facts import fact_from_github

    repo_name = payload.get("repository", {}).get("full_name", "")
    fact = fact_from_github(payload.get("pull_request") or {}, repo=repo_name)
    if not repo_name or fact is None:
        return {"status": "ignored", "event": "pull_request", "action": action}

    delivery_id = request.headers.get("X-GitHub-Delivery")
    result = await _delivery_recorder(request).record(
        fact, source="webhook", event_key=f"github:{delivery_id}" if delivery_id else None
    )
    if not result.matched:
        logger.info("pr_link_no_matching_job", repo=repo_name, branch=fact.head_ref, pr_url=fact.url)
        return {"status": "ignored", "event": "pull_request", "action": action}
    return {"status": "accepted", "action": f"pr_{action}"}


def _delivery_recorder(request: Request) -> Any:
    """The app's recorder; built from the repositories when startup did not
    wire one (test apps, and a startup that degraded without it)."""
    state = request.app.state
    recorder = getattr(state, "delivery_recorder", None)
    if recorder is None:
        from embry0.services.merge_sync.recorder import DeliveryRecorder
        from embry0.storage.repositories.job_delivery import JobDeliveryRepository

        recorder = DeliveryRecorder(
            JobDeliveryRepository(state.db), state.issues_repo, getattr(state, "healing_cases", None)
        )
    return recorder


async def _handle_workflow_run(request: Request, payload: dict[str, Any], action: str) -> dict[str, Any]:
    """CI on the default branch as a detection source (#296, SH-P3.8).

    A red run on a feature branch is the pipeline working, and #305 already
    reacts to those on embry0's own PRs. A red run on `main` means the
    deployment target is broken, which is a case — and a case reaches the
    operator over MCP, where a chat message may not.
    """
    if action != "completed":
        return {"status": "ignored", "event": "workflow_run", "action": action}

    from embry0.services.healing.signals import signal_from_workflow_recovery, signal_from_workflow_run

    signal = signal_from_workflow_run(payload) or signal_from_workflow_recovery(payload)
    if signal is None:
        return {"status": "ignored", "event": "workflow_run", "action": action}
    return await _record_signal(request, signal, delivery_id=request.headers.get("X-GitHub-Delivery"))


_GITHUB_EVENT_HANDLERS: dict[str, Callable[[Request, dict[str, Any], str], Awaitable[dict[str, Any]]]] = {
    "issues": _handle_issues,
    "issue_comment": _handle_issue_comment,
    "pull_request": _handle_pull_request,
    "workflow_run": _handle_workflow_run,
}


# ---------------------------------------------------------------------------
# Monitoring signals (#296, SH-P3.8)
#
# Detection was one regex over one stack's logs. A deployment's Prometheus,
# Alertmanager, Loki and cadvisor could page a chat, but none of them could
# open a case, and a page in a chat nobody reads reaches nobody. Routing
# Alertmanager here turns a firing alert into a healing case, readable
# through case_list / case_get over MCP.
# ---------------------------------------------------------------------------


async def _record_signal(request: Request, signal: Any, *, delivery_id: str | None = None) -> dict[str, Any]:
    """Fold one Signal into the healing ledger.

    Every non-watcher source goes through this one function, so an alert, a CI
    failure and a container restart cannot disagree about what "the same
    problem, again" means. Dedupe is by the source's own fingerprint scoped to
    its origin, so an alert fingerprint can never collide with a watcher one.
    """
    from embry0.services.healing.lifecycle import ResolutionPath

    cases = getattr(request.app.state, "healing_cases", None)
    if cases is None:
        return {"status": "ignored", "reason": "healing ledger not configured"}

    event_key = f"{signal.event_key}:{delivery_id}" if delivery_id else signal.event_key
    existing = await cases.find_by_fingerprint(signal.fingerprint, origin=signal.origin, repo=signal.repo)

    if signal.resolved:
        # The condition cleared on its own. `self_resolved` is the honest path:
        # nothing fixed it, it stopped. An already-closed case is left alone
        # rather than reopened and reclosed.
        if existing is None or existing["stage"] == "closed":
            return {"status": "ignored", "reason": "resolved signal with no open case"}
        await cases.close(
            existing["id"],
            resolution_path=ResolutionPath.SELF_RESOLVED,
            note=signal.note,
            at=signal.at,
            event_key=event_key,
        )
        logger.info("signal_case_closed", case_id=existing["id"], origin=str(signal.origin), repo=signal.repo)
        return {"status": "accepted", "case_id": existing["id"], "action": "closed"}

    if existing is not None and existing["stage"] != "closed":
        await cases.record_recurrence(existing["id"], at=signal.at, event_key=event_key)
        logger.info("signal_case_recurred", case_id=existing["id"], origin=str(signal.origin), repo=signal.repo)
        return {"status": "accepted", "case_id": existing["id"], "action": "recurrence"}

    result = await cases.open_case(
        repo=signal.repo,
        origin=signal.origin,
        title=signal.title,
        fingerprint=signal.fingerprint,
        signature=signal.signature,
        severity=signal.severity,
        # A signal whose previous case was closed is a regression, and the link
        # is what makes "we already fixed this once" findable.
        parent_case_id=existing["id"] if existing is not None else None,
        note=signal.note,
        at=signal.at,
    )
    # CaseResult IS the row (a dict subclass) — there is no `.case` attribute.
    case_id = result.get("id") if result else None
    logger.info(
        "signal_case_opened",
        case_id=case_id,
        origin=str(signal.origin),
        repo=signal.repo,
        about_self=signal.about_self,
    )
    return {"status": "accepted", "case_id": case_id, "action": "opened"}


@router.post("/webhook/alertmanager")
async def alertmanager_webhook(
    request: Request,
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    """Alertmanager v4 webhook -> healing cases.

    Alertmanager cannot sign a body, so the shared secret is presented whole as
    a bearer token and compared constant-time — the same scheme as GitLab's
    token, and the same 503/401 policy as every other receiver here.
    """
    from embry0.services.healing.signals import parse_repo_map, signals_from_alertmanager

    config = request.app.state.config
    body = await request.body()

    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    verify_webhook(
        scheme=SignatureScheme.SHARED_SECRET,
        body=body,
        presented=presented,
        secret=getattr(config, "alertmanager_webhook_secret", "") or "",
        webhook_dev_mode=config.webhook_dev_mode,
        forge="Alertmanager",
    )
    payload = parse_webhook_body(body)

    signals, skipped = signals_from_alertmanager(
        payload,
        repo_map=parse_repo_map(getattr(config, "alertmanager_repo_map", "") or ""),
        default_repo=getattr(config, "watcher_repo", "") or None,
    )
    logger.info(
        "alertmanager_webhook_received",
        status=payload.get("status"),
        group_key=str(payload.get("groupKey", ""))[:120],
        alerts=len(signals),
        skipped=len(skipped),
    )
    if skipped:
        # Never silent: an alert that maps to no repo is a configuration gap,
        # and the whole point of #296 is that these stop going nowhere.
        logger.warning("alertmanager_alerts_skipped", reasons=skipped[:10])

    results = [await _record_signal(request, signal) for signal in signals]
    return {
        "status": "accepted" if results else "ignored",
        "recorded": len([r for r in results if r.get("status") == "accepted"]),
        "skipped": skipped,
    }
