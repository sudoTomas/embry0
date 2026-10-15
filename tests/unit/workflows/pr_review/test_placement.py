"""Which findings go inline, and which into the summary (#30)."""

from __future__ import annotations

from embry0.workflows.pr_review.diff_positions import FileIndex, NewLine
from embry0.workflows.pr_review.placement import INLINE_CAP, by_severity, place_findings
from embry0.workflows.pr_review.verdict import Finding

INDEX = {
    "src/app.py": FileIndex(
        old_path="src/app.py",
        lines={10: NewLine("added", None), 11: NewLine("context", 9), 12: NewLine("added", None)},
    ),
    "src/new.py": FileIndex(old_path="src/old.py", lines={1: NewLine("added", None)}),
}


def _f(file: str, line: int | None, severity: str = "important", comment: str = "c") -> Finding:
    return Finding(file=file, line=line, severity=severity, comment=comment)


def test_a_finding_on_a_diff_line_goes_inline_with_its_positions() -> None:
    placement = place_findings([_f("src/app.py", 11)], INDEX)
    assert len(placement.inline) == 1
    comment = placement.inline[0]
    assert (comment.path, comment.old_path, comment.line, comment.old_line, comment.kind) == (
        "src/app.py",
        "src/app.py",
        11,
        9,
        "context",
    )
    assert placement.listed == ()


def test_a_line_just_outside_a_hunk_goes_elsewhere() -> None:
    placement = place_findings([_f("src/app.py", 13), _f("src/app.py", 9)], INDEX)
    assert placement.inline == () and len(placement.elsewhere) == 2


def test_files_outside_the_diff_and_findings_without_a_line_go_elsewhere() -> None:
    # Line 10 is commentable in src/app.py, so only the file can keep docs/guide.md:10 out.
    placement = place_findings([_f("docs/guide.md", 10), _f("src/app.py", None), _f("", None)], INDEX)
    assert placement.inline == (), "a file outside the diff, or a finding without a line, went inline"
    assert len(placement.elsewhere) == 3


def test_paths_are_normalised_before_placement() -> None:
    findings = [_f("./src/app.py", 10), _f("/workspace/src/app.py", 12), _f("b/src/new.py", 1)]
    placement = place_findings(findings, INDEX)
    assert [(c.path, c.line) for c in placement.inline] == [("src/app.py", 10), ("src/app.py", 12), ("src/new.py", 1)]
    assert placement.inline[2].old_path == "src/old.py", "a renamed file keeps its old path for GitLab"


def test_the_cap_keeps_the_worst_findings_inline() -> None:
    index = {"a.py": FileIndex(old_path="a.py", lines={n: NewLine("added", None) for n in range(1, 41)})}
    findings = [_f("a.py", n, "suggestion") for n in range(1, 31)] + [_f("a.py", n, "critical") for n in range(31, 36)]
    placement = place_findings(findings, index)
    assert len(placement.inline) == INLINE_CAP == 30
    assert [c.finding.severity for c in placement.inline[:5]] == ["critical"] * 5, (
        "critical findings must go inline first"
    )
    assert len(placement.overflow) == 5 and {f.severity for f in placement.overflow} == {"suggestion"}
    assert placement.listed == placement.elsewhere + placement.overflow


def test_severity_order_is_stable() -> None:
    ordered = by_severity(
        [_f("a", 1, "suggestion", "s1"), _f("a", 2, "critical", "c1"), _f("a", 3, "suggestion", "s2")]
    )
    assert [f.comment for f in ordered] == ["c1", "s1", "s2"]
