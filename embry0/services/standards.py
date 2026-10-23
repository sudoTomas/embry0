"""Organization standards (ADR 0014): one versioned standard per forge owner and kind.

A standard has two halves. The **schema** is the shape a kind of standard
takes: for ``design``, the token roles a design names (a page background,
a body font, a table row height), its type scale and its component rules.
The **values** are one organization's fill of those roles: which colour,
which font, which height. embry0 ships the vocabulary and the checks, never
an organization's values; those are configuration, stored per owner.

The owner is the forge owner, the same key as an organization (ADR 0010):
the repositories a standard applies to are the repositories under it.

This module holds the vocabulary, the validation every write goes through,
the task an audit job carries, and the parser that reads an audit's score
and findings back out of its report. Storage is
``embry0/storage/repositories/standards.py``; the routes are
``embry0/api/v1/standards.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

#: The kinds of standard embry0 knows. Growing it is a code change and a
#: migration (the tables CHECK it), plus an audit template in AUDIT_TEMPLATES.
KINDS = ("design",)
STATUSES = ("draft", "active", "superseded")
#: off: embry0 does nothing for the repository; advise and enforce allow audits.
MODES = ("off", "advise", "enforce")
DEFAULT_MODE = "off"

#: The pipeline template an audit of each kind runs on. Seeded with the
#: built-in agents, not by this module: an audit is refused while it is absent.
AUDIT_TEMPLATES = {"design": "design-audit"}

#: The keys a schema holds. Closed, so a misspelt key is refused instead of
#: stored and silently ignored by every check that reads the schema.
SCHEMA_KEYS = ("roles", "type_scale", "rules")
#: Each of schema, values (as JSON) and prose. Kept small: all three ride an audit's task.
MAX_DOCUMENT_CHARS = 32_000
MAX_REFERENCES = 50
MAX_REFERENCE_CHARS = 500

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}\Z")
_OWNER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}\Z")

#: An audit report is cut to this many characters before it is stored (the
#: report deliverable's own cap), so the task asks for less.
REPORT_TARGET_CHARS = 8_000
MAX_FINDING_ROWS = 40


@dataclass
class Standard:
    id: str
    owner: str
    kind: str
    version: int
    status: str
    schema: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    prose: str = ""
    references: list[str] = field(default_factory=list)
    created_by: str | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "kind": self.kind,
            "version": self.version,
            "status": self.status,
            "schema": self.schema,
            "values": self.values,
            "prose": self.prose,
            "references": list(self.references),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class RepoStandardState:
    """A repository's place under its owner's standard of one kind.

    ``score`` and ``findings_open`` come from the newest audit that completed,
    at ``last_audit_at``. ``last_audit_job_id`` is the newest audit started,
    which may still be running or may have failed.
    """

    repo: str
    owner: str
    kind: str
    mode: str = DEFAULT_MODE
    last_audit_at: datetime | None = None
    last_audit_job_id: str | None = None
    score: int | None = None
    findings_open: int = 0
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "owner": self.owner,
            "kind": self.kind,
            "mode": self.mode,
            "last_audit_at": self.last_audit_at.isoformat() if self.last_audit_at else None,
            "last_audit_job_id": self.last_audit_job_id,
            "score": self.score,
            "findings_open": self.findings_open,
            "exceptions": [dict(e) for e in self.exceptions],
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def normalize_owner(raw: str) -> str:
    """The owner as stored: lowercase, one path segment. Raises ValueError otherwise."""
    owner = raw.strip().lower()
    if "/" in owner:
        raise ValueError("a standard's owner is a forge owner, with no slash")
    if not _OWNER.match(owner):
        raise ValueError(f"{raw!r} is not a forge owner")
    return owner


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown standard kind {kind!r}; known kinds: {', '.join(KINDS)}")
    return kind


def owner_of(repo: str) -> str:
    """The forge owner a repository sits under: its first path segment, lowercase."""
    return repo.strip().split("/", 1)[0].lower()


def default_state(repo: str, kind: str) -> RepoStandardState:
    """What a repository with no stored row holds: mode off, never audited."""
    return RepoStandardState(repo=repo.strip().lower(), owner=owner_of(repo), kind=kind)


def _check_names(where: str, names: Any, problems: list[str]) -> None:
    for name in names:
        if not isinstance(name, str) or not _NAME.match(name):
            problems.append(f"{where}: {name!r} is not a name (lowercase letters, digits, '.', '_' and '-')")


def validate_standard(schema: Any, values: Any, prose: str = "", references: Any = None) -> list[str]:
    """Every reason this standard cannot be stored, in a fixed order; empty when it can.

    The schema is an object of ``roles`` (role name to an object describing
    the role), ``type_scale`` (step name to an object) and ``rules`` (a list
    of objects, each with an ``id`` and a ``text``), all optional. The values
    are an object whose every key is a role the schema declares; a role with
    no value is allowed.
    """
    problems: list[str] = []
    if not isinstance(schema, dict):
        problems.append("schema must be an object")
    if not isinstance(values, dict):
        problems.append("values must be an object")
    if problems:
        return problems

    unknown = sorted(str(k) for k in schema if k not in SCHEMA_KEYS)
    if unknown:
        problems.append(f"schema has unknown keys {unknown}; a schema holds {list(SCHEMA_KEYS)}")

    roles = schema.get("roles", {})
    if not isinstance(roles, dict):
        problems.append("schema.roles must be an object of role name to an object describing the role")
        roles = {}
    _check_names("schema.roles", roles, problems)
    for name, spec in roles.items():
        if not isinstance(spec, dict):
            problems.append(f"schema.roles.{name} must be an object")

    type_scale = schema.get("type_scale", {})
    if not isinstance(type_scale, dict):
        problems.append("schema.type_scale must be an object of step name to an object")
    else:
        _check_names("schema.type_scale", type_scale, problems)
        for name, spec in type_scale.items():
            if not isinstance(spec, dict):
                problems.append(f"schema.type_scale.{name} must be an object")

    rules = schema.get("rules", [])
    if not isinstance(rules, list):
        problems.append("schema.rules must be a list of objects, each with an id and a text")
    else:
        seen: set[str] = set()
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                problems.append(f"schema.rules[{index}] must be an object with an id and a text")
                continue
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not _NAME.match(rule_id):
                problems.append(f"schema.rules[{index}].id must be a name (lowercase letters, digits, '.', '_', '-')")
            elif rule_id in seen:
                problems.append(f"schema.rules[{index}].id {rule_id!r} is used twice")
            else:
                seen.add(rule_id)
            text = rule.get("text")
            if not isinstance(text, str) or not text.strip():
                problems.append(f"schema.rules[{index}].text must be a sentence")

    for key in values:
        if key not in roles:
            problems.append(f"values.{key} names no role the schema declares")
        elif values[key] is None:
            problems.append(f"values.{key} is null; leave a role without a value out")

    for name, document in (("schema", schema), ("values", values)):
        if len(json.dumps(document, separators=(",", ":"))) > MAX_DOCUMENT_CHARS:
            problems.append(f"{name} is longer than {MAX_DOCUMENT_CHARS} characters as JSON")
    if len(prose) > MAX_DOCUMENT_CHARS:
        problems.append(f"prose is longer than {MAX_DOCUMENT_CHARS} characters")

    refs = [] if references is None else references
    if not isinstance(refs, list):
        problems.append("references must be a list of repository paths or URLs")
    else:
        if len(refs) > MAX_REFERENCES:
            problems.append(f"references holds more than {MAX_REFERENCES} entries")
        for index, ref in enumerate(refs):
            if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_REFERENCE_CHARS:
                problems.append(
                    f"references[{index}] must be a repository path or a URL of at most {MAX_REFERENCE_CHARS} characters"
                )
    return problems


def _json_block(document: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(document, indent=2, sort_keys=True) + "\n```"


def audit_task(standard: Standard, repo: str, exceptions: list[dict[str, Any]]) -> str:
    """The task an audit job carries: what to do, the report contract, and the whole active standard.

    The first line is the job's title. The report contract is what
    :func:`parse_audit_report` reads back: a ``Score: N`` line first (the
    conformance score), then a findings table under a ``Findings`` heading.
    The ``Hygiene: M`` line between them is read by people; it is not stored.
    """
    lines = [
        f"{standard.kind.capitalize()} audit of {repo} against the {standard.owner} {standard.kind} standard, "
        f"version {standard.version}",
        "",
        "Audit this repository against the organization's standard below and report how far it holds it. "
        "Read only: change no file, open no pull request, push nothing.",
        "",
        "Measure before you judge. Every finding rests on something you read in the code, at a file and line: "
        "a raw value where the standard names a role for it, a font family or size the standard does not name, "
        "an accent the rules do not allow, a component the rules ask to be built another way.",
        "",
        "## Report contract",
        "",
        f"Your final message is the report, in markdown, at most {REPORT_TARGET_CHARS:,} characters:",
        "",
        "1. First line: `Score: N`, the conformance score: a whole number from 0 (the interface does not "
        "follow the standard) to 100 (it renders the standard throughout).",
        "2. Second line: `Hygiene: M`, the hygiene score: a whole number from 0 to 100 for the drift the "
        "code carries beside it (raw values outside the tokens, stray sizes, radii and font families).",
        "3. A `## Findings` heading, then one markdown table with the columns "
        "`Severity | Rule | Location | Evidence | Proposed fix`, one row per finding, most severe first, "
        f"at most {MAX_FINDING_ROWS} rows. Severity is `high`, `medium` or `low`; Location is `path:line`. "
        "With nothing to report, write `No findings.` under the heading and no table.",
        "4. Anything else after the table.",
        "",
        "## Exceptions this repository holds",
        "",
    ]
    if exceptions:
        for exc in exceptions:
            paths = exc.get("paths") or []
            where = f" (in {', '.join(str(p) for p in paths)})" if paths else ""
            lines.append(f"- `{exc.get('rule', '')}`{where}: {exc.get('reason', '')}")
        lines += ["", "What an exception covers is not a finding."]
    else:
        lines.append("None.")
    lines += [
        "",
        f"## The standard: {standard.owner} {standard.kind}, version {standard.version}",
        "",
        "### Schema (the roles, the type scale and the rules)",
        "",
        _json_block(standard.schema),
        "",
        "### Values (this organization's fill of the roles)",
        "",
        _json_block(standard.values),
    ]
    if standard.prose.strip():
        lines += ["", "### Prose", "", standard.prose.strip()]
    if standard.references:
        lines += ["", "### References (where the standard is held best)", ""]
        lines += [f"- {ref}" for ref in standard.references]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class AuditResult:
    score: int | None
    findings_open: int


# "Score" as a word, then any separator, then N, with "/100" allowed and any
# text after it: "Score: 72", "Score - 72/100, commit abc1234". A longer number
# ("Score: 1000") is no score.
_SCORE_LINE = re.compile(r"^score\b\W*(\d{1,3})(?:\s*/\s*100)?(?!\d)", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_DELIMITER_CELL = re.compile(r"^:?-+:?\Z")
_EMPTY_CELLS = frozenset({"", "-", "—", "none", "n/a", "no findings", "no findings."})


def _plain(line: str) -> str:
    """A line without markdown emphasis, code ticks or a heading or quote marker."""
    return re.sub(r"[*_`]", "", line).strip().lstrip("#>").strip()


def parse_score(report: str) -> int | None:
    """The first line that starts with ``Score`` and a number: N, when it is 0 to 100; None when there is none."""
    for line in report.splitlines():
        match = _SCORE_LINE.match(_plain(line))
        if match:
            score = int(match.group(1))
            return score if 0 <= score <= 100 else None
    return None


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _tables(lines: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """Every markdown table in ``lines``: its header cells and its body rows' cells."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    i = 0
    while i + 1 < len(lines):
        header, delimiter = lines[i].strip(), lines[i + 1].strip()
        if header.startswith("|") and delimiter.startswith("|"):
            cells = _cells(delimiter)
            if cells and all(_DELIMITER_CELL.match(c) for c in cells):
                rows: list[list[str]] = []
                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    rows.append(_cells(lines[j]))
                    j += 1
                tables.append((_cells(header), rows))
                i = j
                continue
        i += 1
    return tables


def _findings_sections(lines: list[str]) -> list[list[str]]:
    """The lines under each heading that names findings, up to the next heading of its level or higher."""
    sections: list[list[str]] = []
    for index, line in enumerate(lines):
        heading = _HEADING.match(line.strip())
        if not heading or "finding" not in heading.group(2).lower():
            continue
        level = len(heading.group(1))
        end = len(lines)
        for later in range(index + 1, len(lines)):
            nxt = _HEADING.match(lines[later].strip())
            if nxt and len(nxt.group(1)) <= level:
                end = later
                break
        sections.append(lines[index + 1 : end])
    return sections


def _is_findings_header(header: list[str]) -> bool:
    return any(_plain(cell).lower() in ("severity", "location") for cell in header)


def count_findings(report: str) -> int:
    """The body rows of the report's findings table.

    The findings table is the first table with a ``Severity`` or ``Location``
    column under a heading that names findings, else the first table under
    such a heading. A report with no such heading uses its first table with
    a ``Severity`` or ``Location`` column. A row whose every cell is empty,
    a dash or "none" is not a finding, and no table is zero findings.
    """
    lines = report.splitlines()
    sections = _findings_sections(lines)
    chosen: tuple[list[str], list[list[str]]] | None = None
    if sections:
        under = [table for section in sections for table in _tables(section)]
        chosen = next((t for t in under if _is_findings_header(t[0])), under[0] if under else None)
    else:
        chosen = next((t for t in _tables(lines) if _is_findings_header(t[0])), None)
    if chosen is None:
        return 0
    return sum(1 for row in chosen[1] if any(_plain(cell).lower() not in _EMPTY_CELLS for cell in row))


def parse_audit_report(report: str) -> AuditResult:
    return AuditResult(score=parse_score(report), findings_open=count_findings(report))


def audit_report_text(result: dict[str, Any]) -> str:
    """The audit's report: its ``report`` deliverable, else the job's result summary."""
    for deliverable in result.get("deliverables") or []:
        if isinstance(deliverable, dict) and deliverable.get("type") == "report" and deliverable.get("content"):
            return str(deliverable["content"])
    return str(result.get("result_summary") or "")


class AuditStateStore(Protocol):
    async def record_audit(
        self, job_id: str, *, score: int | None, findings_open: int, finished_at: datetime
    ) -> RepoStandardState | None: ...


async def record_audit_completion(
    states: AuditStateStore, job_id: str, result: dict[str, Any], *, now: datetime | None = None
) -> RepoStandardState | None:
    """Write a completed audit's score and finding count onto its repository's state.

    Only the repository whose newest audit is ``job_id`` is written, so an
    older audit that finishes late, or any job that is not an audit, writes
    nothing. Returns the state written, or None.
    """
    parsed = parse_audit_report(audit_report_text(result))
    state = await states.record_audit(
        job_id, score=parsed.score, findings_open=parsed.findings_open, finished_at=now or datetime.now(UTC)
    )
    if state is not None:
        logger.info(
            "standard_audit_recorded",
            job_id=job_id,
            repo=state.repo,
            kind=state.kind,
            score=parsed.score,
            findings_open=parsed.findings_open,
        )
        if parsed.score is None:
            logger.warning("standard_audit_without_score", job_id=job_id, repo=state.repo, kind=state.kind)
    return state
