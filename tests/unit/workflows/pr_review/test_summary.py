"""The review's summary body (#30)."""

from __future__ import annotations

from embry0.services.pr_review.triggers import has_review_command
from embry0.workflows.pr_review.summary import (
    BODY_LIMIT,
    SummaryContext,
    inline_body,
    render_summary,
    review_marker,
)
from embry0.workflows.pr_review.verdict import CheckRun, Finding, PreviousFinding, SuggestedCheck, Verdict

SHA = "a" * 40
CTX = SummaryContext(
    job_id="job-1", head_sha=SHA, job_url="https://embry0.example.com/jobs/job-1", from_fork=False, depth="full"
)


def _verdict(**overrides) -> Verdict:
    base = dict(
        summary="Careful change. One bug in the retry loop.",
        findings=(
            Finding("src/app.py", 12, "critical", "Off by one."),
            Finding("src/app.py", 30, "important", "Unhandled error."),
            Finding("README.md", None, "suggestion", "Typo."),
        ),
        checks_run=(
            CheckRun("unit", "pytest -q", "passed", ""),
            CheckRun("lint", "ruff check .", "failed", "E501 line too long"),
        ),
        suggested_dev_checks=(),
        previous_findings=(),
    )
    base.update(overrides)
    return Verdict(**base)


def test_the_summary_has_the_assessment_counts_checks_and_footer() -> None:
    body = render_summary(
        _verdict(), inline_count=2, listed=[Finding("README.md", None, "suggestion", "Typo.")], ctx=CTX
    )
    assert "Careful change. One bug in the retry loop." in body
    assert "1 critical · 1 important · 1 suggestion" in body
    assert "**passed** `unit`: `pytest -q`" in body
    assert "**failed** `lint`: `ruff check .`" in body and "E501 line too long" in body
    assert "### Elsewhere in the code" in body and "`README.md` — Typo." in body
    assert f"Reviewed `{SHA[:12]}`" in body and "https://embry0.example.com/jobs/job-1" in body
    assert body.rstrip().endswith(review_marker("job-1", SHA))
    assert review_marker("job-1", SHA) == f"<!-- embry0-review job=job-1 sha={SHA} -->"


def test_a_fork_says_its_checks_were_skipped() -> None:
    ctx = SummaryContext(job_id="job-1", head_sha=SHA, job_url="u", from_fork=True, depth="read_only")
    body = render_summary(_verdict(checks_run=()), inline_count=0, listed=[], ctx=ctx)
    assert "Checks skipped: this pull request comes from a fork." in body


def test_read_only_depth_says_why_nothing_ran() -> None:
    ctx = SummaryContext(job_id="job-1", head_sha=SHA, job_url="u", from_fork=False, depth="read_only")
    assert "review depth is read-only" in render_summary(_verdict(checks_run=()), inline_count=0, listed=[], ctx=ctx)


def test_a_re_review_lists_the_earlier_findings() -> None:
    verdict = _verdict(
        previous_findings=(
            PreviousFinding("src/app.py", 3, "Unused import.", "resolved"),
            PreviousFinding("src/app.py", 9, "Missing test.", "still_open"),
        )
    )
    body = render_summary(verdict, inline_count=0, listed=[], ctx=CTX)
    assert "### Earlier findings" in body
    assert "resolved — `src/app.py:3` Unused import." in body
    assert "still open — `src/app.py:9` Missing test." in body


def test_discovered_checks_are_offered_as_a_paste_ready_block() -> None:
    verdict = _verdict(suggested_dev_checks=(SuggestedCheck("unit", ("src",), "pytest -q"),))
    body = render_summary(verdict, inline_count=0, listed=[], ctx=CTX)
    assert "### Suggested `dev_checks`" in body
    assert '"command": "pytest -q"' in body and '"paths": [\n      "src"\n    ]' in body


def test_no_findings_reads_as_such() -> None:
    body = render_summary(_verdict(findings=()), inline_count=0, listed=[], ctx=CTX)
    assert "No findings." in body


def test_the_summary_never_reads_as_a_command() -> None:
    body = render_summary(_verdict(), inline_count=0, listed=[], ctx=CTX)
    assert "/embry0 review" in body, "the footer must tell people how to ask again"
    assert has_review_command(body) is False, "embry0's own summary would ask for another review"
    # Not only because of the marker: the footer's own wording must not read as the command.
    assert has_review_command(body.replace(review_marker("job-1", SHA), "")) is False, (
        "the footer starts a line with the command"
    )


def test_secrets_in_agent_text_are_redacted() -> None:
    token = "ghp_" + "A" * 36
    verdict = _verdict(
        summary=f"Found {token} in the logs.", checks_run=(CheckRun("x", "y", "failed", f"token={token}"),)
    )
    body = render_summary(verdict, inline_count=0, listed=[], ctx=CTX)
    assert token not in body


def test_a_huge_body_is_cut_to_the_limit_and_keeps_its_footer() -> None:
    listed = [Finding(f"f{n}.py", n, "suggestion", "x" * 900) for n in range(200)]
    body = render_summary(_verdict(), inline_count=0, listed=listed, ctx=CTX)
    assert len(body) <= BODY_LIMIT
    assert body.rstrip().endswith(review_marker("job-1", SHA))


def test_an_inline_comment_starts_with_its_severity_and_carries_the_marker() -> None:
    body = inline_body(Finding("a.py", 1, "important", "Unhandled error."), marker=review_marker("job-1", SHA))
    assert body.startswith("**Important** — Unhandled error.")
    assert body.endswith(review_marker("job-1", SHA))


def test_a_cut_inside_a_check_output_still_closes_its_fence() -> None:
    """Otherwise the truncation note and the footer render as code."""
    checks = tuple(CheckRun(f"c{n}", f"run {n}", "failed", "y" * 1400) for n in range(100))
    body = render_summary(_verdict(checks_run=checks), inline_count=0, listed=[], ctx=CTX)
    assert len(body) <= BODY_LIMIT
    fences = [line for line in body.splitlines() if line.lstrip().startswith("~~~")]
    assert len(fences) % 2 == 0, "the cut left a code fence open over the footer"
