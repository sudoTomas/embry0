"""IssueTracker Protocol and the per-delivery trigger context (#160).

The Protocol is deliberately narrow: it names exactly the surfaces
tracker consumers share. Webhook routes call ``handle_webhook_event`` with
one signature regardless of forge; outbound write-back calls
``post_issue_comment`` with one ref shape; the backlog intake (leads)
calls ``list_backlog`` with the repositories it reads. Webhook *verification* is
deliberately absent — #159 centralized the three per-forge signature
shapes in ``embry0/api/webhook_ingress.py``, and a ``verify_signature``
method here would re-fragment what that unified.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from embry0.trackers.models import BacklogRead, TrackerIssueRef


@dataclass
class TriggerContext:
    """Everything a tracker needs to act on one webhook delivery.

    ``event_type`` and ``action`` carry GitHub's header-borne event
    envelope (``X-GitHub-Event`` + the payload's ``action``); Linear and
    GitLab carry their event kind in the body and leave these empty.

    ``actor`` is the human the delivery names as having acted (``actor_of``).
    It rides to ``jobs.dispatched_by`` and is what the dispatch rate limit
    buckets on (#246); empty when the payload names nobody.

    ``jobs_repo`` lets a tracker judge whether a ticket it already tracks may
    be dispatched again (``embry0.trackers.redispatch``, #284). Optional: a
    context without it keeps the permanent dedupe. ``dispatch_ledger`` is the
    durable per-ticket memory (``DispatchLedgerRepository``): the claims behind
    the once-per-reason "not dispatched" comment (``embry0.trackers.notices``)
    and the count of dispatches that raised. Without it those reasons are only
    logged and failures are not counted.

    ``auto_retry`` says a reconciler in ``on`` mode will look at the ticket
    again by itself; ticket comments use it to give the right advice ("nothing
    to do" versus "re-apply the label").
    """

    issues_repo: Any
    issue_executor: Any
    jobs_repo: Any = None
    dispatch_ledger: Any = None
    auto_retry: bool = False
    trigger_labels: set[str] | None = None
    dashboard_base_url: str = ""
    event_type: str = ""
    action: str = ""
    actor: str = ""
    #: The model and key a person chose when accepting a draft
    #: (``embry0.services.run_with.RunWith``); None for a webhook delivery.
    run_with: Any = None


@runtime_checkable
class IssueTracker(Protocol):
    """One issue tracker (GitHub Issues, GitLab Issues, Linear).

    ``name`` is the registry key and the DB ``tracker`` discriminator
    value ("github", "gitlab", "linear").
    """

    @property
    def name(self) -> str: ...

    async def handle_webhook_event(
        self,
        payload: dict[str, Any],
        ctx: TriggerContext,
    ) -> dict[str, Any]:
        """Process one inbound webhook delivery. Returns the route response."""
        ...

    async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
        """Best-effort comment on the tracker's issue. Never raises."""
        ...

    async def list_backlog(
        self, repos: Sequence[str], *, client_for: Callable[[str], Awaitable[Any]] | None = None
    ) -> BacklogRead:
        """The open tickets nobody holds about ``repos`` (the backlog intake, leads #181), and which of them were
        read whole. GitHub and GitLab read each repository's issues, with the client ``client_for`` gives for it
        when given (none: not read), their own otherwise; Linear reads its backlog once, with its own key, and
        keeps the tickets whose project resolves to one of ``repos``. Raises when nothing could be read."""
        ...
