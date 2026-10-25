"""Linear trigger + write-back (EMB-47).

Inbound: the Linear webhook posts Issue events to ``POST /api/v1/webhook/linear``;
when an issue carries the trigger label (default ``embry0``) and its project
resolves to a repo, embry0 creates a tracked issue and auto-runs the pipeline —
the Linear sibling of :class:`embry0.services.github_sync.GitHubSyncService`.

Repo resolution (RAV-1397): the project carries a machine-readable
``repo: owner/name`` stamp in its summary (GraphQL ``Project.description``;
falls back to the project document, GraphQL ``Project.content``); the static
``LINEAR_REPO_MAP`` env map is the last-resort fallback. Project = routing,
label = trigger. No stamp and no map entry → skip with a logged reason, never
guess.

Outbound: lifecycle comments (dispatched / completed with PR + QA verdict) are
posted back to the source Linear issue via GraphQL. Credentials stay
orchestrator-side only — ``LINEAR_API_KEY`` never enters a sandbox.

The one Linear GraphQL client (#163) — the CLI in
:mod:`embry0.services.linear_dispatch` drives this service too.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

from embry0.orchestration.budget_gate import DispatchDecision
from embry0.safety.error_codes import ErrorCode
from embry0.services import linear_notice_texts as texts
from embry0.services.linear_dispatch import LINEAR_GRAPHQL_URL, compose_additional_context
from embry0.services.trigger_labels import matching_triggers, normalize_labels
from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.trackers import (
    TrackerIssue,
    TrackerIssueRef,
    TriggerContext,
    create_tracked_issue,
)
from embry0.trackers.keyed_lock import KeyedLock
from embry0.trackers.notices import DispatchNoticeBoard
from embry0.trackers.redispatch import dispatch_eligibility

logger = structlog.get_logger(__name__)

# Fallback only. The webhook route passes the resolved set (DB row over env)
# via handle_webhook_event's trigger_labels kwarg; this constant now covers
# only callers that construct the service without one — chiefly tests.
DEFAULT_TRIGGER_LABEL = "embry0"
# Linear posts API comments under the key's owner — a person. Every comment
# opens with this so nobody reads a machine's sentence as that person's words.
AUTOMATED_PREFIX = "embry0 (automated):"
# Label → per-agent model override applied to the dispatched job. Kept tiny and
# explicit; extend when more provider labels exist.
MODEL_LABELS: dict[str, dict[str, str]] = {
    "grok": {"triage": "grok-4.7", "developer": "grok-4.7", "review": "grok-4.7", "qa": "grok-4.7"},
}

# Jobs read per ticket when judging a re-dispatch — far above what one ticket
# accumulates, and the live-job / open-PR checks must see every one of them.
_JOB_HISTORY_LIMIT = 200

# Shared by every LinearSyncService instance: the provider rebuilds the service
# when credentials change, and a lock that died with the old instance would
# not protect a delivery already in flight on it.
_TICKET_LOCKS = KeyedLock()

# Tickets this process just put the trigger label on for someone accepting a
# draft (#460), by identifier and by UUID, with the monotonic time it expires.
# Linear answers that write with an ordinary label webhook, and nothing in the
# delivery says embry0 was the one who labelled: it would be read as a person
# asking for a job — a second "did not start" comment under every acceptance,
# and a second job when the first one has already failed by the time the
# delivery arrives. So the delivery is expected here and consumed once.
# Per process, like the ticket lock, and deliberately forgetful: without the
# memo (a restart, a second orchestrator process) the delivery falls back to
# the ordinary dedupe on the tracked ticket; with a stale one, at worst the
# next hand-applied label inside the window is ignored once.
_OWN_LABEL_ECHO_SECONDS = 900.0
_OWN_LABELS: dict[str, float] = {}

_TRIGGER_ISSUE_FIELDS = """\
    id
    identifier
    title
    description
    url
    labels { nodes { name } }
    state { type }
    project { id name description content }
    team { key }
"""

# Workflow-state TYPES a ticket may be dispatched from. An allowlist, not a
# "not completed/canceled" denylist: workspaces carry further types (`triage`,
# `duplicate`) and a label on a Duplicate ticket is not a request for a job.
DISPATCHABLE_STATE_TYPES: frozenset[str] = frozenset({"backlog", "unstarted", "started"})

_ISSUE_WITH_CONTEXT_QUERY = f"""\
query IssueForTrigger($id: String!) {{
  issue(id: $id) {{
{_TRIGGER_ISSUE_FIELDS}  }}
}}
"""

# The reconciler's sweep (#284): every open ticket carrying a trigger label,
# in the same shape the webhook path reads, so both feed one dispatch method.
_LABELED_ISSUES_QUERY = f"""\
query TriggerLabeledIssues($filter: IssueFilter, $first: Int, $after: String) {{
  issues(filter: $filter, first: $first, after: $after, orderBy: updatedAt) {{
    nodes {{
{_TRIGGER_ISSUE_FIELDS}    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""
_LABELED_ISSUES_PAGE_SIZE = 25
_LABELED_ISSUES_MAX_PAGES = 8

# The backlog intake (leads, spec 2026-10-25): open tickets nobody started, in
# the same shape as the trigger reads, plus who holds them and how big they are.
_BACKLOG_ISSUES_QUERY = f"""\
query BacklogIssues($filter: IssueFilter, $first: Int, $after: String) {{
  issues(filter: $filter, first: $first, after: $after, orderBy: updatedAt) {{
    nodes {{
{_TRIGGER_ISSUE_FIELDS}      assignee {{ id }}
      priority
      estimate
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""
_BACKLOG_PAGE_SIZE = 50
_VIEWER_QUERY = "query Viewer { viewer { id } }"
#: Workflow-state types a ticket nobody started is in.
BACKLOG_STATE_TYPES: frozenset[str] = frozenset({"backlog", "unstarted"})

_LABEL_HISTORY_QUERY = """\
query IssueLabelHistory($id: String!, $after: String) {
  issue(id: $id) {
    labels { nodes { id name } }
    history(first: 50, after: $after, orderBy: createdAt) {
      nodes { createdAt addedLabelIds }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
# Ordering is stated, not assumed: `orderBy: createdAt` pages newest first (the
# connection takes no direction argument; newest-first is what the live API
# returns). 5 pages = the 250 most recent changes. A relabel older than that
# is not seen and the ticket is NOT retried — the safe direction.
_LABEL_HISTORY_MAX_PAGES = 5

_COMMENT_MUTATION = """\
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) {
    success
  }
}
"""

# Accepting a draft for a person (#460) puts the trigger label on the ticket.
# A label is addressed by id, and one the ticket does not carry yet cannot be
# read off the ticket: ask the workspace for the labels of that name, and the
# ticket for its team, in one round trip.
_TRIGGER_LABEL_QUERY = """\
query TriggerLabelsForIssue($id: String!, $filter: IssueLabelFilter) {
  issue(id: $id) { id team { id } }
  issueLabels(filter: $filter, first: 50) {
    nodes { id name isGroup retiredAt team { id } }
  }
}
"""

_LABEL_ADD_MUTATION = """\
mutation IssueAddLabel($id: String!, $labelId: String!) {
  issueAddLabel(id: $id, labelId: $labelId) {
    success
  }
}
"""

_LABEL_REMOVE_MUTATION = """\
mutation IssueRemoveLabel($id: String!, $labelId: String!) {
  issueRemoveLabel(id: $id, labelId: $labelId) {
    success
  }
}
"""


@dataclass(frozen=True)
class LinearTriggerIssue(TrackerIssue):
    """A :class:`TrackerIssue` plus the routing context Linear alone needs.

    The normalized base carries the universal fields (``key`` is the human
    identifier, e.g. EMB-48 — the dedupe key; ``native_id`` is Linear's UUID,
    which ``commentCreate`` wants; ``body`` is the issue description). The
    extras exist only to feed :meth:`LinearSyncService._resolve_repo`.
    """

    project: str = ""
    team_key: str = ""
    project_id: str = ""
    # The project's short summary (GraphQL ``Project.description`` — Linear's
    # UI/MCP call it "summary"). Primary home of the ``repo:`` stamp.
    project_summary: str = ""
    # The project document (GraphQL ``Project.content``). Stamp fallback.
    project_content: str = ""
    # Workflow-state type (``backlog`` / ``started`` / ``duplicate`` …). Empty
    # means "not read", which never dispatches (``DISPATCHABLE_STATE_TYPES``).
    state_type: str = ""


@dataclass(frozen=True)
class BacklogTicket:
    """An open ticket nobody started, with the repository it resolves to (leads, spec 2026-10-25).

    ``mine`` is whether the ticket is assigned to the account this key acts
    as: a ticket a person holds is theirs, not a lead. ``priority`` is
    Linear's (0 none, 1 urgent .. 4 low); ``estimate`` its points, if any.
    """

    issue: LinearTriggerIssue
    repo: str
    assigned: bool
    mine: bool
    priority: int = 0
    estimate: float | None = None


@dataclass(frozen=True)
class AdmissionRefusal:
    """A caller's own reason not to dispatch an otherwise-eligible ticket.

    ``code`` is the stable machine code (it keys the once-per-reason comment
    and the ``skip`` field); ``detail`` is the sentence a human reads;
    ``announce`` says whether the ticket should be told — a limit that lifts by
    itself tomorrow is not worth a comment, one that needs a human is.
    """

    code: str
    detail: str
    announce: bool = False


class LinearSyncService:
    """Inbound Linear-webhook trigger + outbound issue comments."""

    def __init__(
        self,
        api_key: str,
        repo_map: dict[str, str],
        *,
        trigger_label: str = DEFAULT_TRIGGER_LABEL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._repo_map = dict(repo_map)
        self._trigger_label = trigger_label
        self._http = http_client

    @property
    def name(self) -> str:
        return "linear"

    # ---- GraphQL ----------------------------------------------------------

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Linear personal API keys go bare in Authorization — the Bearer
        # prefix is only for OAuth tokens and 401s with personal keys.
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.post(
                LINEAR_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": self._api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            # #163: a 200-with-errors response used to be indistinguishable
            # from a missing issue — GraphQL reports failures in-band.
            if body.get("errors"):
                raise ValueError(f"Linear GraphQL error: {json.dumps(body['errors'])[:300]}")
            return body
        finally:
            if self._http is None:
                await client.aclose()

    async def fetch_trigger_issue(self, issue_id: str) -> LinearTriggerIssue:
        """Fetch the issue (by UUID or identifier) with labels + project.

        Webhook payloads carry label IDs, not names, and can lag edits — the
        authoritative trigger decision always re-reads the issue.
        """
        body = await self._graphql(_ISSUE_WITH_CONTEXT_QUERY, {"id": issue_id})
        issue = (body.get("data") or {}).get("issue")
        if not issue:
            raise ValueError(f"Linear issue {issue_id!r} not found: {json.dumps(body)[:300]}")
        return _trigger_issue_from_node(issue)

    async def list_trigger_issues(self, triggers: Iterable[str]) -> list[LinearTriggerIssue]:
        """Every open Linear issue carrying one of ``triggers`` (#284).

        The reconciler's read: only tickets in a dispatchable workflow-state
        type are listed (``DISPATCHABLE_STATE_TYPES``), and the dispatch path
        checks the state again. Bounded — a workspace with more labeled open tickets than
        the page budget logs the truncation and catches up on later sweeps,
        newest-updated first. Raises on API failure so a sweep fails loudly.
        """
        labels = sorted(normalize_labels(triggers))
        if not labels:
            return []
        issue_filter = {
            "or": [{"labels": {"some": {"name": {"eqIgnoreCase": label}}}} for label in labels],
            "state": {"type": {"in": sorted(DISPATCHABLE_STATE_TYPES)}},
        }
        found: list[LinearTriggerIssue] = []
        after: str | None = None
        for _ in range(_LABELED_ISSUES_MAX_PAGES):
            body = await self._graphql(
                _LABELED_ISSUES_QUERY,
                {"filter": issue_filter, "first": _LABELED_ISSUES_PAGE_SIZE, "after": after},
            )
            page = (body.get("data") or {}).get("issues") or {}
            found.extend(_trigger_issue_from_node(node) for node in page.get("nodes") or [])
            page_info = page.get("pageInfo") or {}
            after = page_info.get("endCursor")
            if not page_info.get("hasNextPage") or not after:
                return found
        # Never silent: tickets beyond the cap are invisible to this sweep.
        logger.warning(
            "linear_trigger_list_truncated",
            returned=len(found),
            page_size=_LABELED_ISSUES_PAGE_SIZE,
            max_pages=_LABELED_ISSUES_MAX_PAGES,
        )
        return found

    async def trigger_label_added_since(self, issue_id: str, triggers: Iterable[str], since: datetime) -> bool:
        """True when a trigger label was applied to the issue after ``since``.

        The reconciler's stand-in for the webhook's ``updatedFrom.labelIds``:
        Linear's issue history records ``addedLabelIds`` per change, which is
        what separates "a human asked again after the job failed" from "the
        label was simply never removed".
        """
        after: str | None = None
        for _ in range(_LABEL_HISTORY_MAX_PAGES):
            body = await self._graphql(_LABEL_HISTORY_QUERY, {"id": issue_id, "after": after})
            issue = (body.get("data") or {}).get("issue") or {}
            wanted = normalize_labels(triggers)
            trigger_ids = {
                node["id"]
                for node in (issue.get("labels") or {}).get("nodes") or []
                if normalize_labels([node.get("name") or ""]) & wanted
            }
            history = issue.get("history") or {}
            for entry in history.get("nodes") or []:
                if trigger_ids & set(entry.get("addedLabelIds") or []) and _parse_timestamp(
                    entry.get("createdAt")
                ) > _as_utc(since):
                    return True
            page_info = history.get("pageInfo") or {}
            after = page_info.get("endCursor")
            if not page_info.get("hasNextPage") or not after:
                return False
        logger.debug(
            "linear_label_history_truncated",
            issue=issue_id,
            pages=_LABEL_HISTORY_MAX_PAGES,
            decision="no_retry",
        )
        return False

    async def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a Linear issue and return {id, identifier, url}, or None.

        RAV-657: used by the watcher to file DRAFT proposals. Deliberately
        never sets labels — applying the trigger label is the human
        acceptance step, and keeping this method label-free makes the
        watcher's human gate structural rather than behavioral.
        """
        mutation = """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        issue_input: dict[str, Any] = {"teamId": team_id, "title": title, "description": description}
        if project_id:
            issue_input["projectId"] = project_id
        try:
            resp = await self._graphql(mutation, {"input": issue_input})
            payload = (resp.get("data") or {}).get("issueCreate") or {}
            if not payload.get("success"):
                logger.warning("linear_issue_create_rejected", title=title[:80], response=json.dumps(resp)[:300])
                return None
            issue: dict[str, Any] = payload["issue"]
            logger.info("linear_issue_created", identifier=issue.get("identifier"), title=title[:80])
            return issue
        except Exception as exc:
            logger.warning("linear_issue_create_failed", title=title[:80], error=str(exc))
            return None

    async def list_open_issues(
        self,
        team_id: str,
        project_id: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Open (non-completed/canceled) issues, newest-updated first.

        RAV-1106: injected into the watcher's analysis briefing so proposals
        dedup against the real tracker, not just the watcher's own prior
        drafts. Returns [{identifier, title, state}] — raises on API failure
        so the caller can fail-open explicitly.
        """
        query = """
        query($filter: IssueFilter, $first: Int) {
          issues(filter: $filter, first: $first, orderBy: updatedAt) {
            nodes { identifier title state { name type } }
          }
        }
        """
        issue_filter: dict[str, Any] = {
            "team": {"id": {"eq": team_id}},
            "state": {"type": {"nin": ["completed", "canceled"]}},
        }
        if project_id:
            issue_filter["project"] = {"id": {"eq": project_id}}
        body = await self._graphql(query, {"filter": issue_filter, "first": limit})
        nodes = ((body.get("data") or {}).get("issues") or {}).get("nodes") or []
        return [
            {
                "identifier": n["identifier"],
                "title": n.get("title") or "",
                "state": ((n.get("state") or {}).get("name")) or "",
            }
            for n in nodes
        ]

    async def list_backlog_tickets(self, *, max_pages: int = 8) -> tuple[list[BacklogTicket], bool]:
        """Open tickets nobody started, newest-updated first, each with the repository it resolves to.

        A ticket whose project names no repository is left out: the intake
        never guesses one. Returns the tickets and whether the listing was
        whole (False when ``max_pages`` stopped it). Raises on API failure.
        """
        viewer = ((await self._graphql(_VIEWER_QUERY, {})).get("data") or {}).get("viewer") or {}
        me = str(viewer.get("id") or "")
        issue_filter = {"state": {"type": {"in": sorted(BACKLOG_STATE_TYPES)}}}
        found: list[BacklogTicket] = []
        after: str | None = None
        for _ in range(max_pages):
            body = await self._graphql(
                _BACKLOG_ISSUES_QUERY, {"filter": issue_filter, "first": _BACKLOG_PAGE_SIZE, "after": after}
            )
            issues = (body.get("data") or {}).get("issues") or {}
            for node in issues.get("nodes") or []:
                issue = _trigger_issue_from_node(node)
                repo, _source = self._resolve_repo(issue)
                if not repo:
                    continue
                assignee = str(((node.get("assignee") or {}).get("id")) or "")
                estimate = node.get("estimate")
                found.append(
                    BacklogTicket(
                        issue=issue,
                        repo=repo,
                        assigned=bool(assignee),
                        mine=bool(assignee) and assignee == me,
                        priority=int(node.get("priority") or 0),
                        estimate=float(estimate) if isinstance(estimate, int | float) else None,
                    )
                )
            page = issues.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return found, True
            after = page.get("endCursor")
        return found, False

    async def issue_state_types(self, team_id: str, identifiers: list[str]) -> dict[str, str]:
        """Map issue identifiers (e.g. 'RAV-1109') → workflow-state *type*.

        RAV-1134 (via RAV-1106): the watcher reconciles its open drafts each
        tick — a draft whose state type is completed/canceled (or which no
        longer exists: absent from the returned map) releases its cap slot.
        Raises on API failure so the caller can fail-open explicitly.
        """
        numbers = [int(m.group(1)) for i in identifiers if (m := re.search(r"-(\d+)$", i))]
        if not numbers:
            return {}
        query = """
        query($filter: IssueFilter, $first: Int) {
          issues(filter: $filter, first: $first) {
            nodes { identifier state { type } }
          }
        }
        """
        issue_filter = {"team": {"id": {"eq": team_id}}, "number": {"in": numbers}}
        body = await self._graphql(query, {"filter": issue_filter, "first": len(numbers)})
        nodes = ((body.get("data") or {}).get("issues") or {}).get("nodes") or []
        return {n["identifier"]: ((n.get("state") or {}).get("type")) or "" for n in nodes}

    async def native_ids(self, team_id: str, identifiers: list[str]) -> dict[str, str]:
        """Map issue identifiers (e.g. 'RAV-1109') → Linear's native UUID.

        `commentCreate` addresses an issue by UUID, but the healing ledger
        stores only the human identifier, because `tracker_key` is what a
        person reads on the ticket. #312 needs the translation to comment its
        verdict. Same shape as :meth:`issue_state_types` — one round trip for
        the whole batch — and it raises on API failure for the same reason, so
        the caller fails open explicitly rather than commenting into the void.
        """
        numbers = [int(m.group(1)) for i in identifiers if (m := re.search(r"-(\d+)$", i))]
        if not numbers:
            return {}
        query = """
        query($filter: IssueFilter, $first: Int) {
          issues(filter: $filter, first: $first) {
            nodes { id identifier }
          }
        }
        """
        issue_filter = {"team": {"id": {"eq": team_id}}, "number": {"in": numbers}}
        body = await self._graphql(query, {"filter": issue_filter, "first": len(numbers)})
        nodes = ((body.get("data") or {}).get("issues") or {}).get("nodes") or []
        return {n["identifier"]: n["id"] for n in nodes if n.get("id") and n.get("identifier")}

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        """Protocol adapter — Linear addresses comments by the native UUID."""
        return await self.post_comment(ref.native_id or ref.key, body)

    async def post_comment(self, linear_issue_id: str, body: str) -> bool:
        """Post a markdown comment on a Linear issue. Best-effort — never raises.

        Every comment leaves through here — notices, the dispatched line, and
        the lifecycle trail that arrives via :meth:`post_issue_comment` — so
        this is the one place the machine attribution is added, and no caller
        can forget it.
        """
        body = attributed(body)
        try:
            resp = await self._graphql(_COMMENT_MUTATION, {"issueId": linear_issue_id, "body": body})
            ok = bool(((resp.get("data") or {}).get("commentCreate") or {}).get("success"))
            if not ok:
                logger.warning("linear_comment_rejected", issue=linear_issue_id, response=json.dumps(resp)[:300])
            return ok
        except Exception as exc:
            logger.warning("linear_comment_failed", issue=linear_issue_id, error=str(exc))
            return False

    # ---- labels (#460) ----------------------------------------------------

    async def find_trigger_label(self, issue_id: str, triggers: Iterable[str]) -> tuple[str, str] | None:
        """The trigger label to put on this ticket, as ``(label id, name)``.

        Linear holds labels per workspace and per team, and a team's label
        cannot go on another team's ticket: the ticket's own team's label is
        preferred, then a workspace one, then whatever else carries the name
        (a parent team's label is inherited, and Linear is the judge of that
        when the label is applied). Group labels and retired labels cannot be
        applied and are never returned. None when Linear has no such label.
        Raises on API failure.
        """
        wanted = sorted(normalize_labels(triggers))
        if not wanted:
            return None
        label_filter = {"or": [{"name": {"eqIgnoreCase": name}} for name in wanted]}
        body = await self._graphql(_TRIGGER_LABEL_QUERY, {"id": issue_id, "filter": label_filter})
        data = body.get("data") or {}
        team_id = (((data.get("issue") or {}).get("team")) or {}).get("id") or ""
        candidates: list[tuple[int, str, str, str]] = []
        for node in (data.get("issueLabels") or {}).get("nodes") or []:
            name = str(node.get("name") or "")
            if not node.get("id") or node.get("isGroup") or node.get("retiredAt"):
                continue
            if not normalize_labels([name]) & set(wanted):
                continue
            label_team = ((node.get("team")) or {}).get("id") or ""
            rank = 0 if label_team and label_team == team_id else 1 if not label_team else 2
            candidates.append((rank, name.lower(), str(node["id"]), name))
        if not candidates:
            return None
        _, _, label_id, name = min(candidates)
        return label_id, name

    async def add_label(self, issue_id: str, label_id: str) -> None:
        """Put a label on an issue. Raises when Linear does not confirm it —
        the caller is about to dispatch on the strength of that label."""
        resp = await self._graphql(_LABEL_ADD_MUTATION, {"id": issue_id, "labelId": label_id})
        if not ((resp.get("data") or {}).get("issueAddLabel") or {}).get("success"):
            raise ValueError(f"Linear did not add the label: {json.dumps(resp)[:300]}")

    async def remove_label(self, issue_id: str, label_id: str) -> bool:
        """Take a label off an issue. Best-effort — never raises; False means
        the label is still there, and the caller must say so."""
        try:
            resp = await self._graphql(_LABEL_REMOVE_MUTATION, {"id": issue_id, "labelId": label_id})
            ok = bool(((resp.get("data") or {}).get("issueRemoveLabel") or {}).get("success"))
            if not ok:
                logger.warning("linear_label_remove_rejected", issue=issue_id, response=json.dumps(resp)[:300])
            return ok
        except Exception as exc:
            logger.warning("linear_label_remove_failed", issue=issue_id, error=str(exc))
            return False

    # ---- inbound trigger --------------------------------------------------

    def _resolve_repo(self, issue: LinearTriggerIssue) -> tuple[str, str]:
        """Resolve the target repo for a triggered issue → (repo, source).

        RAV-1397 order: ``repo:`` stamp in the project summary, then in the
        project document, then the static ``LINEAR_REPO_MAP`` (project name or
        team key). Returns ("", "") when nothing resolves — the caller skips
        with a reason; we never guess a repo.
        """
        stamp = parse_repo_stamp(issue.project_summary)
        if stamp:
            return stamp, "stamp_summary"
        stamp = parse_repo_stamp(issue.project_content)
        if stamp:
            return stamp, "stamp_content"
        mapped = self._repo_map.get(issue.project) or self._repo_map.get(issue.team_key)
        if mapped:
            return mapped, "env_map"
        return "", ""

    def model_override_for(self, labels: list[str]) -> dict[str, str] | None:
        """Merged per-agent model override from any MODEL_LABELS present."""
        merged: dict[str, str] = {}
        for label in labels:
            merged.update(MODEL_LABELS.get(label.lower(), {}))
        return merged or None

    async def handle_webhook_event(
        self,
        payload: dict[str, Any],
        ctx: TriggerContext,
    ) -> dict[str, Any]:
        """Process an inbound Linear webhook delivery.

        Trigger condition: an Issue create/update event whose issue carries the
        trigger label AND whose project resolves to a repo (``repo:`` stamp in
        the project summary/doc, else the static map — see
        :meth:`_resolve_repo`). A ticket embry0 already tracks dedupes — label
        churn and webhook redelivery are no-ops — unless this delivery
        re-applies the trigger label and the ticket is dispatchable again
        (:mod:`embry0.trackers.redispatch`, #284).
        """
        issues_repo = ctx.issues_repo
        if payload.get("type") != "Issue" or payload.get("action") not in ("create", "update"):
            return {"status": "ignored", "reason": "not an issue create/update"}
        data = payload.get("data") or {}
        identifier = data.get("identifier") or data.get("id") or ""
        if not identifier:
            return {"status": "ignored", "reason": "no issue identifier in payload"}

        triggers = ctx.trigger_labels if ctx.trigger_labels is not None else {self._trigger_label}
        relabeled = trigger_label_added(payload, triggers)
        if relabeled and _take_own_label(str(identifier), str(data.get("id") or "")):
            # The label embry0 put on for an acceptance (:meth:`label_and_dispatch`),
            # coming back. That call dispatched, or refused, by itself.
            return {"status": "ignored", "skip": "own_label", "reason": "label applied by embry0 for an acceptance"}
        # Cheap path: the overwhelming majority of deliveries for a tracked
        # ticket are unrelated edits — answer those without a Linear round trip.
        existing = await issues_repo.get_by_tracker_ref("linear", None, str(identifier))
        if existing is not None and not relabeled:
            return {"status": "ignored", "reason": "already dispatched", "issue_id": existing["id"]}

        issue = await self.fetch_trigger_issue(str(identifier))
        added_since = label_reapplied_since(self, issue.native_id or issue.key, triggers)

        async def _reapplied(failed_job: dict[str, Any]) -> bool:
            # The payload says a trigger label was added, never WHEN: a
            # redelivery, or a first delivery that arrives late, can describe
            # the very label that started the failed job (#462). Only a label
            # Linear's history dates after that job ended is a retry request
            # — the reconciler's test, asked only when the payload adds one.
            return relabeled and await added_since(failed_job)

        return await self.dispatch_trigger_issue(
            issue,
            ctx,
            created_by="linear-webhook",
            label_reapplied=_reapplied,
            # Only a delivery that applies the label (or creates the ticket
            # with it) is someone asking for a job; telling every title edit
            # "already dispatched" is noise.
            announce_dedupe=relabeled or payload.get("action") == "create",
        )

    async def dispatch_trigger_issue(
        self,
        issue: LinearTriggerIssue,
        ctx: TriggerContext,
        *,
        created_by: str,
        label_reapplied: Callable[[dict[str, Any]], Awaitable[bool]],
        announce_dedupe: bool = False,
        admit: Callable[[LinearTriggerIssue], Awaitable[AdmissionRefusal | None]] | None = None,
        dry_run: bool = False,
        assume_reopened: bool = False,
    ) -> dict[str, Any]:
        """Dispatch one freshly-read Linear issue, or say why not.

        The single dispatch path — the webhook and the periodic reconciler both
        end here, so trigger label, repo resolution and dedupe cannot drift
        between them. ``label_reapplied`` answers, for a ticket whose latest
        job failed, whether the trigger label was applied again *after* that
        job: a retry is something a human asks for, never a loop (#284).

        Every refusal is also stated on the ticket, once per reason
        (:class:`embry0.trackers.notices.DispatchNoticeBoard`). Dedupe is the
        exception: it is announced only when ``announce_dedupe`` says this
        call is a human asking for a job — a sweep re-reading a ticket that
        is simply being worked on must stay silent.

        ``admit`` is the caller's own admission policy, asked last — after the
        ticket has passed every shared check and just before anything is
        written. The reconciler bounds its spend there (daily cap, attempts
        per ticket, failure backoff); the webhook, where a human asked, has
        none. ``dry_run`` takes the whole decision and writes nothing — no
        row, no job, no ledger entry, no ticket comment — returning
        ``status: would_dispatch`` where a real call would dispatch (the
        reconciler's shadow mode). Every ``ignored`` result carries a stable
        ``skip`` code beside its human ``reason``. ``assume_reopened`` is for
        a dry run only: judge a row stuck in ``triaging`` as the ``open`` row a
        real sweep would have made of it first, so shadow reports what ``on``
        would do rather than the state it declined to change.
        """
        # One dispatch decision per ticket at a time: the webhook and the
        # reconciler can reach the same ticket in the same moment.
        async with _TICKET_LOCKS.hold(issue.key):
            return await self._dispatch_locked(
                issue,
                ctx,
                created_by=created_by,
                label_reapplied=label_reapplied,
                announce_dedupe=announce_dedupe,
                admit=admit,
                dry_run=dry_run,
                assume_reopened=assume_reopened and dry_run,
            )

    async def label_and_dispatch(
        self,
        identifier: str,
        ctx: TriggerContext,
        *,
        created_by: str,
        expected_repo: str,
    ) -> dict[str, Any]:
        """Apply the trigger label for a person, then dispatch the ticket (#460).

        What a person does in Linear to accept a ticket, done for them: the
        label goes on and the ticket is dispatched through
        :meth:`_dispatch_locked` — the decision the webhook and the reconciler
        take — so nothing about dispatching is decided here.

        That decision is taken once as a dry run *before* the ticket is
        touched, with the label assumed present. The order matters: once the
        label is on, Linear sends its label webhook, and that delivery is a
        dispatch request in its own right — taking the label off a moment
        later does not recall it. So a ticket that would be refused (its
        state, a job already on it, no repo, a cap) or that resolves to a repo
        other than ``expected_repo`` is never labelled at all.

        The label webhook this causes is expected and ignored once
        (``_OWN_LABELS``). The ticket lock is held from the dry run to the
        dispatch, so nothing else in this process — a reconciler sweep, a
        second click on Accept — dispatches the ticket in between. In another
        orchestrator process, or after a restart, neither helps: there the
        dedupe on the tracked ticket is what keeps that delivery from
        starting a second job.

        Returns the dispatch result plus ``label``, what happened on the
        ticket:

        * ``added`` — the label went on and the job was dispatched;
        * ``untouched`` — nothing was written to Linear: refused beforehand,
          or the ticket carried the label already;
        * ``unconfirmed`` — Linear did not confirm the label (it refused, or
          did not answer): nothing was dispatched, and a label that did land
          after all is an ordinary label, dispatched by its own webhook;
        * ``removed`` — the label went on, the dispatch was then refused or
          failed, and the label was taken off again;
        * ``left`` — the same, but taking it off failed: the ticket is
          labelled with no job, and the caller must say so;
        * ``kept`` — the label went on and another dispatch of the same
          ticket got there first (a second orchestrator process): a job owns
          the ticket, so the label is true and stays.

        Only ``action: dispatched`` carries a ``job_id``. Raises when the
        ticket cannot be read at all.
        """
        issue = await self.fetch_trigger_issue(identifier)
        async with _TICKET_LOCKS.hold(issue.key):
            return await self._label_and_dispatch_locked(issue, ctx, created_by=created_by, expected_repo=expected_repo)

    async def _label_and_dispatch_locked(
        self,
        issue: LinearTriggerIssue,
        ctx: TriggerContext,
        *,
        created_by: str,
        expected_repo: str,
    ) -> dict[str, Any]:
        triggers = ctx.trigger_labels if ctx.trigger_labels is not None else {self._trigger_label}
        wanted = sorted(normalize_labels(triggers))

        async def _asked(_failed_job: dict[str, Any]) -> bool:
            # The person accepting is the human asking again.
            return True

        async def _same_repo(candidate: LinearTriggerIssue) -> AdmissionRefusal | None:
            repo, source = self._resolve_repo(candidate)
            if repo == expected_repo:
                return None
            return AdmissionRefusal(
                "repo_mismatch",
                f"{candidate.key} resolves to `{repo}` in Linear ({source}), not `{expected_repo}`",
            )

        async def _decide(candidate: LinearTriggerIssue, context: TriggerContext, *, dry_run: bool) -> dict[str, Any]:
            return await self._dispatch_locked(
                candidate,
                context,
                created_by=created_by,
                label_reapplied=_asked,
                # The person who asked is answered by the caller, not on the ticket.
                announce_dedupe=False,
                admit=_same_repo,
                dry_run=dry_run,
                assume_reopened=False,
            )

        if not wanted:
            return {
                "status": "ignored",
                "skip": "trigger_label_missing",
                "reason": "no trigger label is configured",
                "label": "untouched",
            }
        present = bool(matching_triggers(issue.labels, triggers))
        assumed = issue if present else replace(issue, labels=[*issue.labels, wanted[0]])
        preview = await _decide(assumed, ctx, dry_run=True)
        if preview.get("status") != "would_dispatch":
            return {**preview, "label": "untouched"}

        label_id, label_name = "", ""
        if not present:
            try:
                found = await self.find_trigger_label(issue.native_id, triggers)
            except Exception as exc:
                logger.warning("linear_trigger_label_lookup_failed", identifier=issue.key, error=str(exc))
                return {
                    "status": "ignored",
                    "skip": "label_add_failed",
                    "reason": f"Linear could not be asked for the trigger label ({_brief(exc)})",
                    "label": "untouched",
                }
            if found is None:
                return {
                    "status": "ignored",
                    "skip": "trigger_label_missing",
                    "reason": f"Linear has no {' / '.join(f'`{name}`' for name in wanted)} label that can go on {issue.key}",
                    "label": "untouched",
                }
            label_id, label_name = found
            # Before the write, not after: the delivery can outrun the reply.
            _expect_own_label(issue.key, issue.native_id)
            try:
                await self.add_label(issue.native_id, label_id)
            except Exception as exc:
                _take_own_label(issue.key, issue.native_id)
                logger.warning("linear_label_add_failed", identifier=issue.key, label=label_name, error=str(exc))
                return {
                    "status": "ignored",
                    "skip": "label_add_failed",
                    "reason": f"Linear did not confirm the `{label_name}` label on {issue.key} ({_brief(exc)})",
                    "label": "unconfirmed",
                    "label_name": label_name,
                }
        added = bool(label_id)

        try:
            if added:
                # The authoritative read: the dispatch judges the ticket as it
                # reads now, label and all — not the copy the label was assumed on.
                issue = await self.fetch_trigger_issue(issue.native_id)
            # A label this call takes off again is not one a reconciler retries.
            result = await _decide(issue, replace(ctx, auto_retry=ctx.auto_retry and not added), dry_run=False)
        except Exception as exc:
            logger.warning("linear_label_dispatch_raised", identifier=issue.key, exc_info=True)
            result = {
                "status": "ignored",
                "skip": "dispatch_error",
                "reason": f"the dispatch raised ({_brief(exc)})",
            }

        if result.get("action") == "dispatched":
            return {**result, "label": "added" if added else "untouched"}
        if not added:
            return {**result, "label": "untouched"}
        if str(result.get("skip") or "").startswith("dedupe:"):
            return {**result, "label": "kept", "label_name": label_name}
        removed = await self.remove_label(issue.native_id, label_id)
        logger.warning(
            "linear_label_dispatch_refused",
            identifier=issue.key,
            skip=result.get("skip") or result.get("action"),
            label=label_name,
            label_removed=removed,
        )
        return {**result, "label": "removed" if removed else "left", "label_name": label_name}

    async def _dispatch_locked(
        self,
        issue: LinearTriggerIssue,
        ctx: TriggerContext,
        *,
        created_by: str,
        label_reapplied: Callable[[dict[str, Any]], Awaitable[bool]],
        announce_dedupe: bool,
        admit: Callable[[LinearTriggerIssue], Awaitable[AdmissionRefusal | None]] | None,
        dry_run: bool,
        assume_reopened: bool,
    ) -> dict[str, Any]:
        issues_repo = ctx.issues_repo
        # A board without a ledger posts nothing — exactly what a dry run needs.
        notices = DispatchNoticeBoard(None if dry_run else ctx.dispatch_ledger, self)
        ref = TrackerIssueRef(key=issue.key, native_id=issue.native_id)
        triggers = ctx.trigger_labels if ctx.trigger_labels is not None else {self._trigger_label}
        if not matching_triggers(issue.labels, triggers):
            return {
                "status": "ignored",
                "skip": "no_trigger_label",
                "reason": f"no {sorted(normalize_labels(triggers))} label",
            }

        if issue.state_type not in DISPATCHABLE_STATE_TYPES:
            if announce_dedupe:
                await notices.announce(
                    ref, f"state:{issue.state_type or 'unknown'}", texts.state_not_dispatchable(issue.state_type)
                )
            return {
                "status": "ignored",
                "skip": f"state:{issue.state_type or 'unknown'}",
                "reason": f"ticket state type {issue.state_type or 'unknown'!r} is not dispatchable",
            }

        # Payload key may have been the UUID — dedupe on the human identifier.
        existing = await issues_repo.get_by_tracker_ref("linear", None, issue.key)
        basis = "untracked"
        if existing is not None and assume_reopened and existing.get("status") == "triaging":
            existing = {**existing, "status": "open"}
        if existing is not None:
            refusal, retry_of = await self._redispatch_refusal(existing, ctx, label_reapplied)
            if refusal is not None:
                if announce_dedupe:
                    await notices.announce(
                        ref,
                        f"dedupe:{refusal['dedupe']}",
                        texts.dedupe(
                            refusal["dedupe"],
                            job_id=refusal.get("job_id", ""),
                            job_status=refusal.get("job_status", ""),
                            pr_url=refusal.get("pr_url", ""),
                            issue_status=str(existing.get("status") or ""),
                        ),
                    )
                return {
                    "status": "ignored",
                    "skip": f"dedupe:{refusal['dedupe']}",
                    "reason": "already dispatched",
                    "issue_id": existing["id"],
                    "dedupe": refusal["dedupe"],
                    "detail": refusal["detail"],
                }
            basis = "label_reapplied_after_failed_job" if retry_of is not None else "tracked_without_job"

        repo, repo_source = self._resolve_repo(issue)
        if not repo:
            announced = await notices.announce(
                ref,
                "unmapped_repo",
                texts.unmapped_repo(issue.project, auto_retry=ctx.auto_retry),
            )
            # Warn when the ticket is told, not on every look: a sweep re-reads
            # an unmapped ticket each interval and must not re-warn each time.
            _log_once(announced)(
                "linear_trigger_unmapped",
                identifier=issue.key,
                project=issue.project,
                project_id=issue.project_id,
            )
            return {
                "status": "ignored",
                "skip": "unmapped_repo",
                "reason": f"no repo: stamp on project {issue.project!r} and no LINEAR_REPO_MAP entry",
            }

        # #360: the stamp or map lives outside embry0 and drifts when a repo
        # is renamed. A repo with no registry row would run with no QA
        # config, env vars or secrets — refuse it here, where the ticket can
        # be told why. isinstance, not truthiness: test doubles answer any
        # attribute. IssueExecutor.execute refuses it again for every caller.
        unregistered = getattr(ctx.issue_executor, "unregistered_repo_refusal", _no_refusal)(repo)
        if inspect.isawaitable(unregistered):
            unregistered = await unregistered
        if isinstance(unregistered, str):
            announced = await notices.announce(
                ref,
                f"unregistered_repo:{repo}",
                texts.unregistered_repo(repo, issue.project, auto_retry=ctx.auto_retry),
            )
            _log_once(announced)(
                "linear_trigger_unregistered_repo",
                identifier=issue.key,
                repo=repo,
                repo_source=repo_source,
                project=issue.project,
            )
            return {
                "status": "ignored",
                "skip": "unregistered_repo",
                "error_code": ErrorCode.REPO_UNREGISTERED.value,
                "reason": unregistered,
                "repo": repo,
            }

        # Ask the fleet-wide gate before creating anything: a ticket that
        # cannot run today is told so once, instead of collecting a failed
        # ERR_BUDGET_REFUSED job per attempt. The runner re-checks regardless.
        refused = await _gate_refusal(ctx.issue_executor, ctx.actor or None)
        if refused is not None:
            reason = "cap" if refused.limit == "rate_limit" else "budget"
            announced = await notices.announce(
                ref, reason, texts.gate_refused(refused.limit, auto_retry=ctx.auto_retry)
            )
            _log_once(announced)("linear_trigger_refused", identifier=issue.key, limit=refused.limit)
            return {"status": "ignored", "skip": reason, "reason": refused.public_message, "limit": refused.limit}

        if admit is not None:
            denied = await admit(issue)
            if denied is not None:
                if denied.announce:
                    await notices.announce(ref, denied.code, denied.detail)
                return {"status": "ignored", "skip": denied.code, "reason": denied.detail}

        if dry_run:
            return {"status": "would_dispatch", "repo": repo, "basis": basis, "after_reopen": assume_reopened}

        body = issue.body.strip()
        body = f"{body}\n\nLinear: {issue.url}" if body else f"Linear: {issue.url}"
        title = f"[{issue.key}] {issue.title}"
        if existing is None:
            try:
                issue_id = await create_tracked_issue(
                    issues_repo,
                    replace(issue, title=title, body=body, repo=repo),
                    created_by=created_by,
                )
            except asyncpg.UniqueViolationError:
                # Another orchestrator process tracked the ticket between our
                # lookup and the insert (the ticket lock is per process). The
                # single-statement insert means no row of ours was created.
                logger.warning("linear_trigger_tracked_concurrently", identifier=issue.key)
                return {
                    "status": "ignored",
                    "skip": "dedupe:concurrent_dispatch",
                    "reason": "already dispatched",
                    "dedupe": "concurrent_dispatch",
                }
        else:
            # A retry runs against the ticket as it reads now, not as it read
            # when the failed job was dispatched — and that includes the repo
            # (#357). The job is created from this row, so leaving `repo` out
            # meant a corrected `repo:` stamp or LINEAR_REPO_MAP entry never
            # reached a ticket embry0 already tracked: the log line and the
            # ticket comment announced the new repo while the job cloned the
            # old one.
            issue_id = existing["id"]
            await issues_repo.update(issue_id, title=title, body=body, labels=list(issue.labels), repo=repo)
        try:
            await issues_repo.update(issue_id, status="triaging")
        except (ValueError, StatusTransitionConflict):
            # Lost a race with a concurrent writer — someone else owns the issue.
            logger.warning("linear_trigger_status_conflict", identifier=issue.key, issue_id=issue_id, exc_info=True)
            return {
                "status": "ignored",
                "skip": "dedupe:concurrent_dispatch",
                "reason": "already dispatched",
                "issue_id": issue_id,
                "dedupe": "concurrent_dispatch",
            }

        agent_models = self.model_override_for(issue.labels)
        try:
            job_id = await ctx.issue_executor.execute(
                issue_id,
                additional_context=compose_additional_context(issue.key),
                agent_models=agent_models,
                dispatched_by=ctx.actor or None,
                # A person's choice when accepting a draft; a webhook carries none.
                **({"run_with": ctx.run_with} if ctx.run_with is not None else {}),
            )
        except Exception:
            logger.warning("linear_trigger_execute_failed", issue_id=issue_id, exc_info=True)
            # Never strand the issue in `triaging` (parity with start_triage on
            # the GitHub/GitLab path): only `open` dispatches, so a stuck row
            # would swallow every later label and every reconciler sweep.
            try:
                await issues_repo.update(issue_id, status="open")
            except (ValueError, StatusTransitionConflict):
                logger.warning("linear_trigger_revert_failed", issue_id=issue_id, exc_info=True)
            await _ledger_call(ctx.dispatch_ledger, "record_failure", issue.key)
            await notices.announce(ref, "dispatch_failed", texts.dispatch_failed(auto_retry=ctx.auto_retry))
            return {"status": "accepted", "action": "issue_created_dispatch_failed", "issue_id": issue_id}

        # A new cycle: a later refusal or failure on this ticket is news again,
        # and the failure backoff starts over.
        await _ledger_call(ctx.dispatch_ledger, "clear", issue.key)
        logger.info(
            "linear_trigger_dispatched",
            identifier=issue.key,
            issue_id=issue_id,
            job_id=job_id,
            repo=repo,
            repo_source=repo_source,
            agent_models=agent_models or {},
            redispatch=existing is not None,
        )
        models_note = f" Agents: `{json.dumps(agent_models)}`." if agent_models else ""
        base = public_dashboard_url(ctx.dashboard_base_url)
        await self.post_comment(
            issue.native_id,
            texts.dispatched(
                job_id, repo, agents_note=models_note, dashboard_url=f"{base}/jobs/{job_id}" if base else ""
            ),
        )
        return {"status": "accepted", "action": "dispatched", "issue_id": issue_id, "job_id": job_id, "basis": basis}

    async def _redispatch_refusal(
        self,
        existing: dict[str, Any],
        ctx: TriggerContext,
        label_reapplied: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
        """Why an already-tracked ticket must not dispatch again; None when it may.

        The second item is the failed job a permitted dispatch would retry
        (None for a tracked ticket that has no job at all).

        Returns the ``dedupe`` code plus a human ``detail``. Without a jobs
        repository there is nothing to judge a retry by, so the dedupe stays
        permanent — the pre-#284 behaviour, kept for partial test contexts.
        """
        if ctx.jobs_repo is None:
            return {"dedupe": "tracked", "detail": "this ticket was already dispatched"}, None
        jobs, _ = await ctx.jobs_repo.list_all(issue_id=existing["id"], limit=_JOB_HISTORY_LIMIT, offset=0)
        verdict = dispatch_eligibility(existing, jobs)
        if not verdict.eligible:
            blocking = verdict.blocking_job or {}
            return {
                "dedupe": verdict.reason,
                "detail": verdict.detail,
                "job_id": str(blocking.get("job_id") or ""),
                "job_status": str(blocking.get("status") or ""),
                "pr_url": str(blocking.get("pr_url") or ""),
            }, None
        if verdict.retry_of is not None and not await label_reapplied(verdict.retry_of):
            job = verdict.retry_of
            return {
                "dedupe": "label_not_reapplied",
                "detail": (
                    f"the latest job `{job.get('job_id')}` ended `{job.get('status')}`; "
                    "remove and re-apply the trigger label to retry"
                ),
            }, None
        return None, verdict.retry_of


def public_dashboard_url(base_url: str) -> str:
    """``base_url`` when a colleague could open it; empty for unset or loopback.

    ``dashboard_public_url`` defaults to ``http://localhost:8200``. On a shared
    ticket that is a dead link, so comments say "the embry0 dashboard" instead.
    """
    base = (base_url or "").strip().rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    if not base or host in {"", "localhost", "0.0.0.0", "::1"} or host.startswith("127."):
        return ""
    return base


def attributed(body: str) -> str:
    """``body`` opened with :data:`AUTOMATED_PREFIX` — once, however often it is applied."""
    return body if body.startswith(AUTOMATED_PREFIX) else f"{AUTOMATED_PREFIX} {body}"


def _trigger_issue_from_node(issue: dict[str, Any]) -> LinearTriggerIssue:
    """One GraphQL issue node (``_TRIGGER_ISSUE_FIELDS``) → the trigger shape."""
    project = issue.get("project") or {}
    return LinearTriggerIssue(
        tracker="linear",
        native_id=issue["id"],
        key=issue["identifier"],
        title=issue.get("title") or "",
        body=issue.get("description") or "",
        url=issue["url"],
        labels=[n["name"] for n in (issue.get("labels") or {}).get("nodes") or []],
        project=project.get("name") or "",
        team_key=((issue.get("team") or {}).get("key")) or "",
        project_id=project.get("id") or "",
        project_summary=project.get("description") or "",
        project_content=project.get("content") or "",
        state_type=((issue.get("state") or {}).get("type")) or "",
    )


def _expect_own_label(*ticket_ids: str) -> None:
    """Remember that the next trigger-label delivery for this ticket is embry0's own."""
    now = time.monotonic()
    for stale in [key for key, expires in _OWN_LABELS.items() if expires <= now]:
        del _OWN_LABELS[stale]
    for ticket_id in ticket_ids:
        if ticket_id:
            _OWN_LABELS[ticket_id] = now + _OWN_LABEL_ECHO_SECONDS


def _take_own_label(*ticket_ids: str) -> bool:
    """True, once, when a trigger-label delivery for this ticket is expected.

    Every id of the ticket is forgotten together, so one delivery consumes
    the expectation whichever id it names.
    """
    now = time.monotonic()
    expiries = [_OWN_LABELS.pop(ticket_id, None) for ticket_id in ticket_ids if ticket_id]
    return any(expires is not None and expires > now for expires in expiries)


def _brief(exc: BaseException) -> str:
    """An exception as one short clause for an API response."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _parse_timestamp(raw: Any) -> datetime:
    """Linear's ISO-8601 ``createdAt``; an unreadable value sorts as ancient."""
    try:
        return _as_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _log_once(announced: bool) -> Callable[..., Any]:
    """``warning`` for the look that told the ticket, ``debug`` for the repeats."""
    emit: Callable[..., Any] = logger.warning if announced else logger.debug
    return emit


async def _ledger_call(ledger: Any, method: str, tracker_key: str) -> None:
    """Best-effort ledger write — bookkeeping must never fail a dispatch."""
    if ledger is None:
        return
    try:
        await getattr(ledger, method)("linear", tracker_key)
    except Exception:
        logger.warning("linear_dispatch_ledger_failed", method=method, identifier=tracker_key, exc_info=True)


async def _gate_refusal(issue_executor: Any, actor: str | None) -> DispatchDecision | None:
    """The executor's pre-dispatch refusal, tolerant of executors without one."""
    check = getattr(issue_executor, "dispatch_refusal", None)
    if check is None:
        return None
    decision = await check(actor=actor)
    # isinstance, not truthiness: test doubles answer any attribute.
    return decision if isinstance(decision, DispatchDecision) and not decision.allowed else None


async def _no_refusal(_repo: str) -> None:
    return None


def label_reapplied_since(
    sync: LinearSyncService, issue_id: str, labels: Iterable[str]
) -> Callable[[dict[str, Any]], Awaitable[bool]]:
    """``label_reapplied`` from Linear's issue history: was a trigger label
    added after the failed job ended? Shared by the webhook (#462) and the
    reconciler, so the two cannot disagree on what a retry request is."""
    wanted = set(labels)

    async def _check(failed_job: dict[str, Any]) -> bool:
        since = failed_job.get("finished_at") or failed_job.get("created_at")
        if not isinstance(since, datetime):
            # No timestamp to compare against — never guess in favour of a retry.
            return False
        since = since if since.tzinfo else since.replace(tzinfo=UTC)
        return bool(await sync.trigger_label_added_since(issue_id, wanted, since))

    return _check


def trigger_label_added(payload: dict[str, Any], triggers: Iterable[str]) -> bool:
    """True when this webhook delivery *adds* a trigger label to the issue.

    Linear's Issue update payload carries the current ``data.labelIds`` /
    ``data.labels`` and the previous ids under ``updatedFrom.labelIds`` (present
    only when the labels changed). A delivery that leaves the labels alone —
    a title edit, a state move — is not a request to dispatch again.
    """
    previous = (payload.get("updatedFrom") or {}).get("labelIds")
    if payload.get("action") != "update" or previous is None:
        return False
    data = payload.get("data") or {}
    added_ids = set(data.get("labelIds") or []) - set(previous)
    added_names = [
        str(label.get("name") or "")
        for label in data.get("labels") or []
        if isinstance(label, dict) and label.get("id") in added_ids
    ]
    return bool(matching_triggers(added_names, triggers))


# A stamp anywhere in the text: ``repo: group/name`` (RAV-1397), or a GitLab
# subgroup path like ``repo: group/subgroup/project``. The segments follow
# GitHub's charset; the first match wins. The trailing ``+`` on the group is
# what keeps a nested path whole — with a single segment the pattern matched
# ``group/subgroup`` and silently dispatched the job at a project that does
# not exist.
_REPO_STAMP_RE = re.compile(r"repo:\s*([\w.-]+(?:/[\w.-]+)+)")


def parse_repo_stamp(text: str) -> str | None:
    """Extract the ``repo: group/name`` stamp from project summary/doc text."""
    match = _REPO_STAMP_RE.search(text or "")
    return match.group(1) if match else None


def parse_repo_map(raw: str) -> dict[str, str]:
    """Parse the ``LINEAR_REPO_MAP`` config value (JSON object: project/team → repo).

    RAV-1397: this static map is the *fallback* behind the per-project
    ``repo:`` stamp (see :func:`parse_repo_stamp`) — kept as belt-and-braces
    and as in-`.env` documentation of the routing table.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"LINEAR_REPO_MAP is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise ValueError("LINEAR_REPO_MAP must be a JSON object of string → string")
    return parsed
