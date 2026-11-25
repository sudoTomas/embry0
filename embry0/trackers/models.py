"""Normalized tracker domain models (#160).

Before this module, GitHub issues stayed raw webhook ``dict``s end to end,
Linear had two unrelated frozen dataclasses for one concept, and label
extraction was written twice with different defensiveness. These models are
the single normalized shape the sync services parse *into*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from embry0.orchestration.dispatch_actors import is_reserved


@dataclass(frozen=True)
class TrackerIssue:
    """One issue as a tracker reports it, normalized across trackers.

    ``key`` is the human-facing identifier *within* the tracker: GitHub's
    issue number, GitLab's iid, Linear's ``ENG-123``. It is a string on
    purpose — the DB's generic ``tracker_key`` column (migration 60) is TEXT
    for the same reason. ``native_id`` is the tracker's internal id when it
    differs from the key (Linear's UUID, which ``commentCreate`` wants);
    empty when the key is the only id.
    """

    tracker: str
    key: str
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=list)
    native_id: str = ""
    repo: str = ""
    state: str = ""


#: Pages of 100 issues a forge's backlog read takes per repository, newest-updated first: a repository with more
#: open issues is read in part (not whole).
BACKLOG_ISSUE_PAGES = 3


@dataclass(frozen=True)
class BacklogItem:
    """An open ticket of a tracker's backlog that nobody holds, for the backlog intake (leads, #181).

    ``tracker`` and ``key`` are as :class:`TrackerIssue` names them; ``repo`` is the repository the ticket is
    about (a Linear ticket's resolves through its project). ``priority`` is the tracker's own, 0 when it has
    none (Linear: 1 urgent .. 4 low); ``estimate`` its points, if any.
    """

    tracker: str
    repo: str
    key: str
    title: str
    body: str
    url: str | None
    labels: tuple[str, ...] = ()
    priority: int = 0
    estimate: float | None = None


@dataclass(frozen=True)
class BacklogRead:
    """What one tracker's backlog read found: its items, the repositories it read whole, and why it did not read
    the others it was asked about (``unread``, by repository, when it can say).

    A repository the read named but did not read whole (a page cap, a refused request) is not in ``whole``:
    the intake closes none of that tracker's leads there, since what it did not see may still be open.
    """

    items: list[BacklogItem] = field(default_factory=list)
    whole: frozenset[str] = frozenset()
    unread: Mapping[str, str] = field(default_factory=dict)


#: Why a backlog read stopped short of a repository's whole backlog.
BACKLOG_CUT_SHORT = "the read stopped before the end (its page cap, or a page refused)"
#: Why a backlog was not read for a repository with no client of its organization's own.
BACKLOG_NO_CLIENT = "no client with the organization's credential"


def read_failed(exc: BaseException) -> str:
    """Why a read that raised read nothing: its error's type, never its text (a URL, a token's owner)."""
    return f"the read failed ({type(exc).__name__})"


@dataclass(frozen=True)
class TrackerIssueRef:
    """The address of an issue for outbound write-back.

    GitHub and GitLab address by ``repo`` + ``key``; Linear addresses by
    ``native_id`` alone. Carrying all three lets one ref shape serve every
    tracker without per-tracker call signatures.
    """

    repo: str = ""
    key: str = ""
    native_id: str = ""


def github_number_of(issue: Mapping[str, Any] | None) -> int | None:
    """The GitHub-shaped integer issue number of an issue row, or None.

    #161 dropped the ``github_number`` column; the number now lives in
    ``tracker_key`` (TEXT) under ``tracker='github'``. Everything that is
    genuinely GitHub-shaped — ``Closes #N`` bodies, PR-dedupe ticket
    matching, the GitHub comment channel — reads through here, so the
    "is this a GitHub issue with a number" decision exists exactly once.
    """
    if not issue or issue.get("tracker") != "github":
        return None
    key = str(issue.get("tracker_key") or "")
    return int(key) if key.isdigit() else None


def forge_number_of(issue: Mapping[str, Any] | None) -> int | None:
    """The forge-native integer issue number (GitHub number / GitLab iid), or None.

    This is the number ``Closes #N`` keywords resolve against on the repo's
    own forge — meaningful only when the issue lives on the forge that hosts
    the change request (tracker github|gitlab). Linear issues have no
    forge-side number, so a Linear-dispatched job returns None (#213).
    """
    if not issue or issue.get("tracker") not in ("github", "gitlab"):
        return None
    key = str(issue.get("tracker_key") or "")
    return int(key) if key.isdigit() else None


def linear_identifier_of(issue: Mapping[str, Any] | None) -> str | None:
    """The Linear human identifier (``ENG-123``) of an issue row, or None."""
    if not issue or issue.get("tracker") != "linear":
        return None
    return str(issue.get("tracker_key") or "") or None


def linear_native_id_of(issue: Mapping[str, Any] | None) -> str | None:
    """The Linear UUID of an issue row (what ``commentCreate`` wants), or None."""
    if not issue or issue.get("tracker") != "linear":
        return None
    return str(issue.get("tracker_native_id") or "") or None


def actor_of(payload: Mapping[str, Any] | None, tracker: str) -> str:
    """The human who triggered this delivery, as the tracker names them.

    One extractor for the same reason ``extract_labels`` is one: every forge
    puts the acting user somewhere else — GitHub under ``sender.login``,
    GitLab under ``user.username``, Linear under ``actor`` (email preferred,
    display name as fallback). Empty string when the payload carries no
    identifiable actor; callers treat that as "nobody asked" rather than
    inventing a bucket (#246).
    """
    if not isinstance(payload, Mapping):
        return ""
    holder: Any
    keys: tuple[str, ...]
    if tracker == "github":
        holder, keys = payload.get("sender"), ("login",)
    elif tracker == "gitlab":
        holder, keys = payload.get("user"), ("username",)
    elif tracker == "linear":
        holder, keys = payload.get("actor"), ("email", "name")
    else:
        return ""
    if not isinstance(holder, Mapping):
        return ""
    for key in keys:
        value = holder.get(key)
        if isinstance(value, str) and value:
            # #281: `embry0:` is the service-actor namespace; some of those
            # actors skip the per-author rate limit. A display name must not
            # be able to claim one — treat it as "nobody identifiable".
            return "" if is_reserved(value) else value
    return ""


def extract_labels(raw: object) -> list[str]:
    """Label names from any tracker's ``labels`` payload field.

    One defensive extractor replacing the per-tracker copies: GitHub keys
    the name as ``name``, GitLab as ``title`` (with some payload shapes
    sending bare strings). A malformed entry is skipped — it must never
    take the delivery down.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("title") or item.get("name")
            if isinstance(name, str) and name:
                out.append(name)
        elif isinstance(item, str) and item:
            out.append(item)
    return out
