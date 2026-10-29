"""Leads (spec 2026-10-25): work embry0 finds by itself and offers to a repository's owners.

A finder reads one source for one repository and returns what it sees as
:class:`Lead` objects: a backlog ticket nobody labelled for embry0, a check
failing on the default branch, a dependency or code-scanning alert, a check
that failed and then passed on the same commit, a pull request whose review
threads nobody answered. The loop (:mod:`embry0.services.leads.loop`) stores
them, one row per lead keyed by a hash of the repository and what the finder
knows it by, and the owner proposals feed offers every open one. An owner's
yes starts the work (:mod:`embry0.api.v1.leads`).

Every lead carries what a person needs to decide and what a game-like view
needs to draw it: a severity, a rough effort, the files or labels that place
it in an area, and when it was first and last seen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from embry0.services import lead_kinds

#: The kinds stored as leads (:mod:`embry0.services.lead_kinds`): one per finder, and
#: ``crack`` and ``weed``, which come from the scout loop's finished jobs
#: (:mod:`embry0.services.scout`) and knowledge maps, not from a finder.
LEAD_KINDS: tuple[str, ...] = lead_kinds.ids(lead=True)
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
EFFORTS: tuple[str, ...] = ("small", "medium", "large")
#: Who a lead's start is recorded under when the person who said yes is not known.
ACTOR = "embry0:leads"


@dataclass(frozen=True)
class Lead:
    kind: str
    repo: str
    #: What the finder knows it by, unique within the repository and kind: ``linear:ABC-1``, ``check:build``.
    source_key: str
    title: str
    #: A sentence a chat message can carry.
    summary: str
    details: str
    #: What accepting starts, in words.
    action: str
    url: str | None = None
    severity: str = "medium"
    effort: str = "medium"
    #: Files it is about, which place it in an area.
    paths: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    #: What starting it needs: the tracker and key, a check name and commit, an alert number, a pull request.
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return lead_id(self.kind, self.repo, self.source_key)

    def __post_init__(self) -> None:
        if self.kind not in LEAD_KINDS:
            raise ValueError(f"lead kind {self.kind!r} is not one of {', '.join(LEAD_KINDS)}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} is not one of {', '.join(SEVERITIES)}")
        if self.effort not in EFFORTS:
            raise ValueError(f"effort {self.effort!r} is not one of {', '.join(EFFORTS)}")


def lead_id(kind: str, repo: str, source_key: str) -> str:
    """Stable across ticks: the same thing seen again is the same lead, and answered once."""
    digest = hashlib.sha256(f"{repo.lower()}\n{source_key}".encode()).hexdigest()
    return f"{kind}:{digest[:16]}"


def severity_rank(severity: str) -> int:
    """Higher is worse; what a capped finder keeps first."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else 1


def by_severity(leads: Sequence[Lead]) -> list[Lead]:
    return sorted(leads, key=lambda lead: -severity_rank(lead.severity))


@dataclass(frozen=True)
class FinderResult:
    """What one finder saw on one repository.

    ``complete`` is whether it read its whole source: only a complete read
    marks the leads it no longer sees as gone, so a failed or partial read
    never closes anything. ``skipped`` says why it read nothing, once (a
    source the credential cannot read, a forge it does not speak).
    """

    leads: list[Lead]
    complete: bool = True
    skipped: str | None = None


def skipped(reason: str) -> FinderResult:
    return FinderResult([], complete=False, skipped=reason)


def as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    from datetime import UTC

    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
