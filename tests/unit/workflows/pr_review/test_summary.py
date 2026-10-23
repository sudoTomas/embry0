"""The review's summary body (#30)."""

from __future__ import annotations

import re

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


def test_secrets_in_every_agent_field_are_redacted() -> None:
    token = "ghp_" + "B" * 36
    verdict = _verdict(
        checks_run=(CheckRun(f"probe {token}", f"curl -H {token}", "failed", ""),),
        suggested_dev_checks=(SuggestedCheck(f"name-{token}", (f"src/{token}",), f"echo {token}"),),
        previous_findings=(PreviousFinding(f"src/{token}.py", 3, "c", "resolved"),),
    )
    listed = [Finding(f"src/{token}.py", 4, "suggestion", "c")]
    body = render_summary(verdict, inline_count=0, listed=listed, ctx=CTX)
    assert body.count(token) == 0, f"the token appears {body.count(token)} times in the summary"
    inline = inline_body(
        Finding(f"src/{token}.py", 1, "important", f"uses {token}"), marker=review_marker("job-1", SHA)
    )
    assert token not in inline, "the inline comment carries the token"


def _fence_open_at(body: str, needle: str) -> bool:
    """Whether the line holding ``needle`` sits inside a fenced code block (CommonMark rules)."""
    fence: tuple[str, int] | None = None
    for line in body.splitlines():
        if needle in line and fence is None:
            return False
        if needle in line:
            return True
        m = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if not m:
            continue
        run, rest = m.group(1), m.group(2)
        if fence is None:
            if not (run[0] == "`" and "`" in rest):
                fence = (run[0], len(run))
        elif run[0] == fence[0] and len(run) >= fence[1] and not rest.strip():
            fence = None
    raise AssertionError(f"{needle!r} is not in the body")


def test_an_unclosed_fence_in_agent_prose_is_closed_where_it_ends() -> None:
    verdict = _verdict(summary="Look at this:\n```python\nx = 1")
    listed = [Finding("a.py", 1, "suggestion", "~~~\nstray")]
    body = render_summary(verdict, inline_count=0, listed=listed + [Finding("b.py", 2, "important", "next")], ctx=CTX)
    assert not _fence_open_at(body, "### Checks"), "the summary's fence swallowed the checks"
    assert not _fence_open_at(body, "### Elsewhere in the code"), "the summary's fence swallowed the findings"
    assert not _fence_open_at(body, "`b.py:2`"), "a comment's fence swallowed the next finding"
    assert not _fence_open_at(body, "Reviewed `"), "a fence swallowed the footer"
    opening = render_summary(_verdict(summary="```\nstarts with a fence"), inline_count=0, listed=[], ctx=CTX)
    assert not _fence_open_at(opening, "### Checks"), "a summary that opens with a fence swallowed the checks"
    inline = inline_body(Finding("a.py", 1, "important", "```\ncode"), marker="<!-- m -->")
    assert not _fence_open_at(inline, "<!-- m -->"), "the inline comment's fence swallowed its marker"


def test_mentions_in_agent_prose_do_not_notify() -> None:
    verdict = _verdict(summary="Ask @octocat.", previous_findings=(PreviousFinding("a.py", 1, "cc @jdoe", "resolved"),))
    body = render_summary(verdict, inline_count=0, listed=[Finding("a.py", 2, "suggestion", "@acme/team?")], ctx=CTX)
    for handle in ("@octocat", "@jdoe", "@acme"):
        assert handle not in body, f"{handle} would notify"
    assert "@octocat" not in inline_body(Finding("a.py", 1, "important", "@octocat look"), marker="m")


def test_agent_prose_cannot_open_an_html_comment() -> None:
    marker = review_marker("job-1", SHA)
    verdict = _verdict(summary="Before <!-- hidden")
    body = render_summary(verdict, inline_count=0, listed=[Finding("a.py", 2, "suggestion", "x <!-- y")], ctx=CTX)
    assert body.count("<!--") == 1 and body.endswith(marker), "agent prose opened an HTML comment"
    inline = inline_body(Finding("a.py", 1, "important", "<!-- gone"), marker=marker)
    assert inline.count("<!--") == 1, "agent prose opened an HTML comment inline"


def test_a_newline_in_a_code_span_does_not_end_it() -> None:
    verdict = _verdict(checks_run=(CheckRun("unit\n# Pwned", "pytest\n\n-q", "failed", ""),))
    body = render_summary(verdict, inline_count=0, listed=[Finding("a.py\n## Heading", 2, "suggestion", "c")], ctx=CTX)
    assert "`unit # Pwned`: `pytest -q`" in body, "a newline in a check broke its code span"
    assert "`a.py ## Heading:2`" in body, "a newline in a file broke its code span"


def test_a_cut_inside_a_backtick_fence_in_a_comment_still_closes_it() -> None:
    listed = [Finding(f"f{n}.py", n, "suggestion", "~~~\nstray\n```\n" + "z\n" * 600 + "```") for n in range(100)]
    body = render_summary(_verdict(), inline_count=0, listed=listed, ctx=CTX)
    assert len(body) <= BODY_LIMIT
    assert not _fence_open_at(body, "Reviewed `"), "the cut left a fence open over the footer"


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


def test_without_a_public_dashboard_url_the_footer_links_nothing() -> None:
    ctx = SummaryContext(job_id="job-1", head_sha=SHA, job_url="", from_fork=False, depth="full")
    body = render_summary(_verdict(), inline_count=0, listed=[], ctx=ctx)
    footer = body[body.rindex("\n---\n") :]
    assert "](" not in footer and "localhost" not in footer, f"the footer links somewhere: {footer!r}"
    assert "job job-1" in footer and body.rstrip().endswith(review_marker("job-1", SHA))
