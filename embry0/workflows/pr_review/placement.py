"""Which findings become inline comments (#30).

A finding goes inline only when its file and line are a commentable line of
the diff (``diff_positions``). Inline comments are capped at
:data:`INLINE_CAP`, worst severity first; everything else is listed in the
summary under "Elsewhere in the code".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from embry0.workflows.pr_review.diff_positions import FileIndex, normalize_finding_path
from embry0.workflows.pr_review.verdict import Finding

INLINE_CAP = 30
_RANK = {"critical": 0, "important": 1, "suggestion": 2}


@dataclass(frozen=True)
class InlineComment:
    finding: Finding
    path: str
    old_path: str
    line: int
    old_line: int | None
    kind: str  # "added" | "context"


@dataclass(frozen=True)
class Placement:
    inline: tuple[InlineComment, ...]
    elsewhere: tuple[Finding, ...]  # not on a commentable line
    overflow: tuple[Finding, ...]  # commentable, but past the cap

    @property
    def listed(self) -> tuple[Finding, ...]:
        return self.elsewhere + self.overflow


def by_severity(findings: Iterable[Finding]) -> list[Finding]:
    """Worst first; the agent's order within a severity."""
    return sorted(findings, key=lambda f: _RANK.get(f.severity, len(_RANK)))


def _locate(finding: Finding, index: Mapping[str, FileIndex]) -> InlineComment | None:
    if finding.line is None:  # lines.get(None) misses too; this narrows the type for InlineComment.line
        return None
    path = normalize_finding_path(finding.file)
    entry = index.get(path)
    if entry is None and path[:2] in ("a/", "b/"):  # a path copied from a git diff header
        path = path[2:]
        entry = index.get(path)
    if entry is None:
        return None
    new_line = entry.lines.get(finding.line)
    if new_line is None:
        return None
    return InlineComment(
        finding=finding,
        path=path,
        old_path=entry.old_path,
        line=finding.line,
        old_line=new_line.old_line,
        kind=new_line.kind,
    )


def place_findings(findings: Iterable[Finding], index: Mapping[str, FileIndex], *, cap: int = INLINE_CAP) -> Placement:
    inline: list[InlineComment] = []
    elsewhere: list[Finding] = []
    overflow: list[Finding] = []
    for finding in by_severity(findings):
        spot = _locate(finding, index)
        if spot is None:
            elsewhere.append(finding)
        elif len(inline) < cap:
            inline.append(spot)
        else:
            overflow.append(finding)
    return Placement(inline=tuple(inline), elsewhere=tuple(elsewhere), overflow=tuple(overflow))
