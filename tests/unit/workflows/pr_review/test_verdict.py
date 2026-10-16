"""Reading the pull request reviewer's verdict (#30)."""

from __future__ import annotations

import json

import pytest

from embry0.workflows.pr_review.verdict import Finding, Verdict, parse_verdict

VERDICT = {
    "summary": "Solid change; one real bug.",
    "review_comments": [
        {"file": "src/app.py", "line": 12, "severity": "critical", "comment": "Off by one."},
        {"file": "README.md", "line": 3, "severity": "suggestion", "comment": "Typo."},
    ],
    "checks_run": [{"name": "unit", "command": "pytest -q", "status": "failed", "output_tail": "1 failed"}],
    "suggested_dev_checks": [{"name": "unit", "paths": ["src"], "command": "pytest -q"}],
    "previous_findings": [{"file": "src/app.py", "line": 3, "comment": "Unused import.", "status": "resolved"}],
}


def test_bare_json_is_read() -> None:
    verdict = parse_verdict(json.dumps(VERDICT))
    assert verdict is not None
    assert verdict.summary == "Solid change; one real bug."
    assert verdict.findings[0] == Finding(file="src/app.py", line=12, severity="critical", comment="Off by one.")
    assert verdict.checks_run[0].status == "failed" and verdict.suggested_dev_checks[0].paths == ("src",)
    assert verdict.previous_findings[0].status == "resolved"


def test_a_fenced_block_after_prose_is_read() -> None:
    text = "I ran the tests.\n\n```json\n" + json.dumps(VERDICT) + "\n```"
    assert parse_verdict(text) is not None


def test_a_bare_object_after_prose_is_read_even_without_a_decision_key() -> None:
    """_review_payload's own last-object fallback looks for "decision", which this verdict has no reason to carry."""
    text = 'Example: {"summary": "x", "review_comments": []}\nNow my review.\n' + json.dumps(VERDICT)
    verdict = parse_verdict(text)
    assert verdict is not None and verdict.summary == VERDICT["summary"], (
        "the last verdict object after prose was not read"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I could not finish the review.",
        '{"summary": "cut off", "review_comments": [',
        json.dumps({"review_comments": []}),
        json.dumps({"summary": "   ", "review_comments": []}),
        json.dumps({"summary": "x", "review_comments": "none"}),
        None,
    ],
)
def test_an_unreadable_verdict_is_none(text) -> None:
    assert parse_verdict(text) is None, f"{text!r} must not read as a verdict"


@pytest.mark.parametrize(
    ("line", "expected"),
    [(42, 42), ("42", 42), (" 7 ", 7), (0, None), (-3, None), (True, None), (None, None), ("12a", None), (4.5, None)],
)
def test_line_values_are_read_or_dropped(line, expected) -> None:
    verdict = parse_verdict(
        json.dumps({"summary": "s", "review_comments": [{"file": "a.py", "line": line, "comment": "c"}]})
    )
    assert verdict is not None and verdict.findings[0].line == expected, f"line {line!r}"


def test_malformed_entries_are_dropped_and_severities_normalised() -> None:
    raw = {
        "summary": "s",
        "review_comments": [
            "not an object",
            {"file": "a.py", "line": 1},  # no comment
            {"file": "a.py", "line": 1, "severity": "CRITICAL", "comment": "upper"},
            {"file": "a.py", "line": 2, "severity": "blocker", "comment": "unknown severity"},
        ],
        "checks_run": [{"name": "x", "command": "y", "status": "weird"}, {}],
        "previous_findings": [{"file": "a.py", "comment": "c", "status": "maybe"}],
    }
    verdict = parse_verdict(json.dumps(raw))
    assert verdict is not None
    assert [(f.severity, f.comment) for f in verdict.findings] == [
        ("critical", "upper"),
        ("suggestion", "unknown severity"),
    ]
    assert [c.status for c in verdict.checks_run] == ["error"]
    assert verdict.previous_findings == ()


def test_a_verdict_round_trips_through_its_dict() -> None:
    verdict = parse_verdict(json.dumps(VERDICT))
    assert verdict is not None
    assert Verdict.from_dict(verdict.to_dict()) == verdict
    assert Verdict.from_dict(None) is None and Verdict.from_dict({"summary": ""}) is None
