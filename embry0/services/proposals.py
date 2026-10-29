"""Owner proposals (spec 2026-10-24): work embry0 asks a repository's owners to start.

Three kinds are read from rows other features write: a watcher case at
stage ``tracked`` (accepting it runs the case's acceptance), an issue that is
open with no job (accepting it sends it to triage), and one row of the
findings table of a repository's newest context audit that finished its work,
none once that audit's pull request merged, or another audit's after it
started (accepting it starts a fix job for that file, which opens a pull
request; that job is no audit, so the audit's other findings are still asked
while it runs and after its pull request merges). The rest are leads, work
embry0's finders found by itself (spec 2026-10-25,
``embry0.services.leads``): a backlog ticket, failing CI, an alert, a flaky
check, unanswered review threads. An owner answers each with
accept, decline or snooze; the newest answer decides whether it is still
asked.

Owners are the repository's ``owners`` preference, else the users its
``CODEOWNERS`` names for every file. Nothing here messages anyone: an
assistant reads the feed and asks.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from embry0.orchestration import dispatch_actors
from embry0.services import lead_kinds
from embry0.services.areas import Area, area_of_labels, area_of_logs, area_of_path
from embry0.services.leads import LEAD_KINDS
from embry0.services.slop import is_slop, parse_work_nature

#: Every kind of owner proposal (:mod:`embry0.services.lead_kinds`): case, issue, finding and the leads.
KINDS: tuple[str, ...] = lead_kinds.ids(proposal=True)
DECISIONS: tuple[str, ...] = ("accept", "decline", "snooze")
#: Who an assistant's answer is recorded under when it names nobody, and who
#: dispatches a finding's fix job, which the context audit therefore reads as
#: no audit (``context_audit.FIX_ACTOR``).
ACTOR = dispatch_actors.PROPOSALS

#: The columns of a context audit's findings table, as the command in its task fixes them.
FINDING_COLUMNS = ("severity", "location", "claim", "evidence", "fix")
_LOCATION = re.compile(r"^`?(?P<path>[^`:\s]+)(?::(?P<line>\d+)(?:-\d+)?)?`?$")
#: Where CODEOWNERS may live, in the order GitHub reads them.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
_LOGIN = re.compile(r"^@([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})$")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


# ---- findings ----


@dataclass(frozen=True)
class AuditFinding:
    severity: str
    path: str
    line: int | None
    claim: str
    evidence: str
    fix: str


def _cells(row: str) -> list[str]:
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def parse_findings(report: str | None) -> list[AuditFinding]:
    """The rows of a report's ``## Findings`` table; a row without a file in its Location is skipped."""
    if not report:
        return []
    lines = report.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().lower().startswith("## findings"))
    except StopIteration:
        return []
    header: list[str] | None = None
    findings: list[AuditFinding] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if header is not None and findings:
                break
            continue
        cells = _cells(stripped)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        row = dict(zip(header, cells, strict=False))
        location = _LOCATION.match(row.get("location", "").strip())
        if not location:
            continue
        line_no = location.group("line")
        findings.append(
            AuditFinding(
                severity=row.get("severity", "").strip("` ").lower(),
                path=location.group("path").strip(),
                line=int(line_no) if line_no else None,
                claim=row.get("claim", ""),
                evidence=row.get("evidence", ""),
                fix=row.get("fix", ""),
            )
        )
    return findings


def finding_id(repo: str, path: str, claim: str) -> str:
    """Stable across audits: the next audit finding the same claim in the same file asks nothing new."""
    digest = hashlib.sha256(f"{repo.lower()}\n{path}\n{' '.join(claim.split()).lower()}".encode()).hexdigest()
    return f"finding:{digest[:16]}"


# ---- owners ----


def codeowners_for_all(text: str | None) -> list[str]:
    """The users ``CODEOWNERS`` names for every file: the last ``*`` rule's ``@login`` entries.

    Teams (``@org/team``) and email addresses are skipped: a team would need
    resolving to people, which this version does not do.
    """
    owners: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *entries = line.split()
        if pattern in ("*", "/*", "/**", "**"):
            owners = [m.group(1) for e in entries if (m := _LOGIN.match(e))]
    return owners


# ---- the feed ----


@dataclass(frozen=True)
class Proposal:
    id: str
    kind: str
    repo: str
    title: str
    #: A sentence a chat message can carry.
    summary: str
    #: The longer evidence.
    details: str
    #: What accepting does, in words.
    action: str
    created_at: datetime
    owners: tuple[str, ...] = ()
    url: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    #: The repository's area it is about (``.embry0/areas.yml``), None for a repository without areas.
    area: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "repo": self.repo,
            "owners": list(self.owners),
            "area": self.area,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
            "action": self.action,
            "url": self.url,
            "created_at": _iso(self.created_at),
            **dict(self.extra),
        }


def case_proposal(row: Mapping[str, Any]) -> Proposal:
    seen = int(row.get("occurrence_count") or 0)
    severity = str(row.get("severity") or "unrated")
    title = str(row.get("title") or "A recurring error")
    since = row.get("detected_at")
    details = [
        f"Severity {severity}, seen {seen} times" + (f" since {since:%Y-%m-%d}" if isinstance(since, datetime) else ""),
    ]
    if row.get("signature"):
        details.append(f"Matching log lines: {row['signature']}")
    if row.get("tracker_url"):
        details.append(f"Draft ticket: {row['tracker_url']}")
    extra: dict[str, Any] = {"severity": severity, "occurrences": seen, "signature": row.get("signature")}
    # Slop dungeon (spec 2026-10-25): a finding the watcher judged slop reads
    # as a cleanup, so the owner is not asked to "fix" what is shape, not a bug.
    verdict = parse_work_nature(row.get("work_nature"))
    if verdict is not None:
        extra.update(nature=verdict["nature"], slop_score=verdict["slop_score"], slop_kinds=verdict["kinds"])
    if is_slop(verdict):
        kinds = ", ".join(k.replace("_", " ") for k in verdict["kinds"]) if verdict else ""
        details.insert(0, f"Slop, not a bug: slop score {extra['slop_score']}/100" + (f" ({kinds})" if kinds else ""))
        summary = f"The watcher found slop in production ({seen}x, {severity}), a cleanup, not a fix: {title}"
        action = "Run the Slopmaster's cleanup on the draft ticket"
    else:
        summary = f"The watcher keeps seeing this in production ({seen}x, {severity}): {title}"
        action = "Run the fix pipeline on the draft ticket"
    return Proposal(
        id=f"case:{row['id']}",
        kind="case",
        repo=str(row["repo"]),
        title=title,
        summary=summary,
        details="\n".join(details),
        action=action,
        created_at=row.get("tracked_at") or row.get("detected_at") or row["created_at"],
        url=row.get("tracker_url"),
        extra=extra,
    )


def issue_proposal(row: Mapping[str, Any]) -> Proposal:
    title = str(row.get("title") or "An open issue")
    body = str(row.get("body") or "").strip()
    return Proposal(
        id=f"issue:{row['id']}",
        kind="issue",
        repo=str(row["repo"]),
        title=title,
        summary=f"Open and not started: {title}",
        details=body[:2000],
        action="Send it to triage, which starts its pipeline",
        created_at=row["created_at"],
        url=row.get("tracker_url"),
        extra={"labels": [str(label) for label in row.get("labels") or [] if isinstance(label, str)]},
    )


def finding_proposals(repo: str, job_id: str, finished_at: datetime, report: str | None) -> list[Proposal]:
    out: list[Proposal] = []
    for f in parse_findings(report):
        where = f"{f.path}:{f.line}" if f.line else f.path
        out.append(
            Proposal(
                id=finding_id(repo, f.path, f.claim),
                kind="finding",
                repo=repo,
                title=f"{where}: {f.claim}"[:200],
                summary=f'We found this in {where}: "{f.claim}". Is that what you want it to say?',
                details="\n".join(
                    part
                    for part in (
                        f"Severity: {f.severity}" if f.severity else "",
                        f"Evidence: {f.evidence}" if f.evidence else "",
                        f"Proposed fix: {f.fix}" if f.fix else "",
                    )
                    if part
                ),
                action=f"Open a pull request fixing {f.path}",
                created_at=finished_at,
                extra={"path": f.path, "line": f.line, "claim": f.claim, "fix": f.fix, "audit_job_id": job_id},
            )
        )
    return out


def lead_proposal(row: Mapping[str, Any]) -> Proposal:
    """An open lead (``LeadsRepository.open_leads``), asked like every other proposal."""
    data = dict(row.get("data") or {})
    return Proposal(
        id=str(row["id"]),
        kind=str(row["kind"]),
        repo=str(row["repo"]),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or row.get("title") or ""),
        details=str(row.get("details") or ""),
        action=str(row.get("action") or ""),
        created_at=row["first_seen"],
        url=row.get("url"),
        area=row.get("area"),
        extra={
            "severity": row.get("severity"),
            "effort": row.get("effort"),
            "last_seen": _iso(row.get("last_seen")),
            "paths": list(data.get("paths") or []),
            "labels": list(data.get("labels") or []),
            "source": {k: v for k, v in data.items() if k not in ("paths", "labels")},
        },
    )


@dataclass(frozen=True)
class Decision:
    proposal_id: str
    decision: str
    decided_by: str
    created_at: datetime
    until: datetime | None = None
    reason: str = ""
    job_id: str | None = None


def still_open(proposal_id: str, decisions: Mapping[str, Decision], now: datetime) -> bool:
    """Asked until answered: an accept or decline closes it, a snooze until it ends."""
    last = decisions.get(proposal_id)
    if last is None:
        return True
    if last.decision == "snooze":
        return last.until is not None and last.until <= now
    return False


def with_owners(
    proposals: Iterable[Proposal],
    owners_of: Mapping[str, Sequence[str]],
    areas_of: Mapping[str, Sequence[Area]] | None = None,
) -> list[Proposal]:
    """Each proposal stamped with its area and its owners: the area's when it names some, else the repository's.

    A finding is in the area of its file, a case in the area its log
    signature or title names, an issue in the area a label of it names
    (``.embry0/areas.yml``). A lead keeps the area its finder stored, else
    takes the one its files or labels name.
    """
    out = []
    for p in proposals:
        areas = (areas_of or {}).get(p.repo.lower(), ())
        area: Area | None = None
        if p.kind in LEAD_KINDS:
            area = next((a for a in areas if a.name == p.area), None) if p.area else None
            area = (
                area
                or area_of_paths_any(areas, p.extra.get("paths") or ())
                or area_of_labels(areas, p.extra.get("labels") or ())
            )
            if area is None and p.area:
                # The file changed since: keep the stored name, with the repository's owners.
                out.append(Proposal(**{**p.__dict__, "owners": tuple(owners_of.get(p.repo.lower(), ()))}))
                continue
        elif p.kind == "finding" and p.extra.get("path"):
            area = area_of_path(areas, str(p.extra["path"]))
        elif p.kind == "case":
            area = area_of_logs(areas, p.extra.get("signature"), p.title)
        elif p.kind == "issue":
            area = area_of_labels(areas, p.extra.get("labels") or ())
        owners = area.owners if area and area.owners else tuple(owners_of.get(p.repo.lower(), ()))
        out.append(Proposal(**{**p.__dict__, "owners": tuple(owners), "area": area.name if area else None}))
    return out


def area_of_paths_any(areas: Sequence[Area], paths: Iterable[str]) -> Area | None:
    """The area of the first of ``paths`` that falls in one."""
    return next((area for path in paths if (area := area_of_path(areas, path)) is not None), None)


def for_owner(proposals: Iterable[Proposal], owner: str | None) -> list[Proposal]:
    if not owner:
        return list(proposals)
    login = owner.strip().removeprefix("@").lower()
    return [p for p in proposals if login in (o.lower() for o in p.owners)]


def finding_task(repo: str, head_sha: str, proposal: Proposal) -> str:
    """The fix job's task: the one file and the one claim an owner asked to fix."""
    extra = proposal.extra
    where = f"{extra['path']}:{extra['line']}" if extra.get("line") else str(extra["path"])
    lines = [
        f"Fix one outdated claim in {repo}, which its owner confirmed should change.",
        "",
        f"Read at commit {head_sha} of the default branch.",
        "",
        f"Instruction files (1):\n- {extra['path']}",
        "",
        f"The claim at {where}: {extra['claim']}",
    ]
    if extra.get("fix"):
        lines.append(f"The fix the audit proposed: {extra['fix']}")
    lines += ["", "Change only this claim; leave every other line of the file as it is."]
    return "\n".join(lines)
