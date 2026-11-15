"""Issue-tracker abstraction (#160).

One Protocol + registry + normalized models for the systems issues arrive
from and are written back to — GitHub Issues, GitLab Issues, Linear. See
``docs/glossary.md`` ("Tracker", "Forge") for the vocabulary.

The Protocol covers what tracker *consumers* actually need: the webhook
routes call ``handle_webhook_event`` uniformly, and outbound write-back
(the #163 channel work) calls ``post_issue_comment`` uniformly. Everything
else a service exposes (Linear's GraphQL fetch, GitHub's push sync) stays
tracker-specific surface by design — forcing it onto the Protocol would be
ceremony, not abstraction.
"""

from embry0.trackers.author_association import (
    DEFAULT_AUTHOR_ROLES,
    check_comment_author,
    parse_author_roles,
)
from embry0.trackers.base import IssueTracker, TriggerContext
from embry0.trackers.dispatch import create_tracked_issue, dispatch_open_issue, end_waits, start_triage
from embry0.trackers.models import (
    TrackerIssue,
    TrackerIssueRef,
    actor_of,
    extract_labels,
    forge_number_of,
    github_number_of,
    linear_identifier_of,
    linear_native_id_of,
)
from embry0.trackers.registry import TrackerRegistry

__all__ = [
    "DEFAULT_AUTHOR_ROLES",
    "IssueTracker",
    "TrackerIssue",
    "TrackerIssueRef",
    "TrackerRegistry",
    "TriggerContext",
    "actor_of",
    "check_comment_author",
    "create_tracked_issue",
    "dispatch_open_issue",
    "end_waits",
    "extract_labels",
    "forge_number_of",
    "github_number_of",
    "linear_identifier_of",
    "linear_native_id_of",
    "parse_author_roles",
    "start_triage",
]
