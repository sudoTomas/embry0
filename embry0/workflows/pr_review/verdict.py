"""The pull request reviewer's verdict: its shape and how it is read (#30).

The agent's final message is a JSON object (see ``prompts.VERDICT_CONTRACT``).
It is read with the existing reviewer parser, ``_review_payload`` (whole text,
then a fenced block). That parser's last fallback looks for an object with a
``decision`` key, which this verdict does not have, so the last object with a
``summary`` is tried after it. Anything else is no verdict: the job fails and
nothing is posted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from embry0.orchestration.agent_json import last_object_with
from embry0.orchestration.routing.conditions import _review_payload

SEVERITIES: tuple[str, ...] = ("critical", "important", "suggestion")
CHECK_STATUSES: tuple[str, ...] = ("passed", "failed", "error")
PREVIOUS_STATUSES: tuple[str, ...] = ("resolved", "still_open")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int | None
    severity: str
    comment: str


@dataclass(frozen=True)
class CheckRun:
    name: str
    command: str
    status: str
    output_tail: str


@dataclass(frozen=True)
class SuggestedCheck:
    name: str
    paths: tuple[str, ...]
    command: str


@dataclass(frozen=True)
class PreviousFinding:
    file: str
    line: int | None
    comment: str
    status: str


@dataclass(frozen=True)
class Verdict:
    summary: str
    findings: tuple[Finding, ...]
    checks_run: tuple[CheckRun, ...]
    suggested_dev_checks: tuple[SuggestedCheck, ...]
    previous_findings: tuple[PreviousFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        """The agent's JSON shape, which is also what ``pr_reviews.verdict`` stores."""
        return {
            "summary": self.summary,
            "review_comments": [
                {"file": f.file, "line": f.line, "severity": f.severity, "comment": f.comment} for f in self.findings
            ],
            "checks_run": [
                {"name": c.name, "command": c.command, "status": c.status, "output_tail": c.output_tail}
                for c in self.checks_run
            ],
            "suggested_dev_checks": [
                {"name": s.name, "paths": list(s.paths), "command": s.command} for s in self.suggested_dev_checks
            ],
            "previous_findings": [
                {"file": p.file, "line": p.line, "comment": p.comment, "status": p.status}
                for p in self.previous_findings
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Verdict | None:
        return verdict_from_mapping(data) if isinstance(data, Mapping) else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _line(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _finding(raw: Any) -> Finding | None:
    if not isinstance(raw, Mapping):
        return None
    comment = _text(raw.get("comment"))
    if not comment:
        return None
    severity = _text(raw.get("severity")).lower()
    return Finding(
        file=_text(raw.get("file")),
        line=_line(raw.get("line")),
        severity=severity if severity in SEVERITIES else "suggestion",
        comment=comment,
    )


def _check(raw: Any) -> CheckRun | None:
    if not isinstance(raw, Mapping):
        return None
    name, command = _text(raw.get("name")), _text(raw.get("command"))
    if not (name or command):
        return None
    status = _text(raw.get("status")).lower()
    return CheckRun(
        name=name,
        command=command,
        status=status if status in CHECK_STATUSES else "error",
        output_tail=_text(raw.get("output_tail")),
    )


def _suggested(raw: Any) -> SuggestedCheck | None:
    if not isinstance(raw, Mapping):
        return None
    command = _text(raw.get("command"))
    if not command:
        return None
    paths = tuple(p.strip().strip("/") for p in _list(raw, "paths") if isinstance(p, str) and p.strip().strip("/"))
    return SuggestedCheck(name=_text(raw.get("name")) or "check", paths=paths, command=command)


def _previous(raw: Any) -> PreviousFinding | None:
    if not isinstance(raw, Mapping):
        return None
    status = _text(raw.get("status")).lower()
    comment = _text(raw.get("comment"))
    if status not in PREVIOUS_STATUSES or not comment:
        return None
    return PreviousFinding(file=_text(raw.get("file")), line=_line(raw.get("line")), comment=comment, status=status)


def verdict_from_mapping(data: Mapping[str, Any]) -> Verdict | None:
    summary = _text(data.get("summary"))
    if not summary:
        return None
    if "review_comments" in data and not isinstance(data["review_comments"], list):
        return None
    return Verdict(
        summary=summary,
        findings=tuple(f for f in (_finding(r) for r in _list(data, "review_comments")) if f is not None),
        checks_run=tuple(c for c in (_check(r) for r in _list(data, "checks_run")) if c is not None),
        suggested_dev_checks=tuple(
            s for s in (_suggested(r) for r in _list(data, "suggested_dev_checks")) if s is not None
        ),
        previous_findings=tuple(p for p in (_previous(r) for r in _list(data, "previous_findings")) if p is not None),
    )


def parse_verdict(text: Any) -> Verdict | None:
    raw = text if isinstance(text, str) else ""
    data = _review_payload(raw)
    verdict = verdict_from_mapping(data) if data is not None else None
    if verdict is not None:
        return verdict
    fallback = last_object_with(raw, "summary")
    return verdict_from_mapping(fallback) if fallback is not None else None
