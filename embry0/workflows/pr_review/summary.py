"""The review's summary body and inline comment bodies (#30).

Everything the agent wrote is redacted before it is posted: the sandbox may
print a credential into a check's output, and the pull request's page may be
public. The footer names the reviewed sha, links the job, says how to ask for
another review — mid-line, so the body never reads as the command itself —
and ends with the hidden marker that keeps embry0 from triggering on its own
comments.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from embry0.safety.redaction import redact_bounded
from embry0.workflows.pr_review.verdict import Finding, Verdict

BODY_LIMIT = 60000  # GitHub caps a review body at 65536 characters
SUMMARY_CHARS = 8000
COMMENT_CHARS = 4000
OUTPUT_TAIL_CHARS = 1500
_LABEL = {"critical": "Critical", "important": "Important", "suggestion": "Suggestion"}
_TRUNCATED = "\n\n… (cut to fit the forge's size limit; the full verdict is on the job)"
_CLOSE_FENCE = "\n  ~~~"


@dataclass(frozen=True)
class SummaryContext:
    job_id: str
    head_sha: str
    job_url: str
    from_fork: bool
    depth: str


def review_marker(job_id: str, head_sha: str) -> str:
    return f"<!-- embry0-review job={job_id} sha={head_sha} -->"


def _code(text: str) -> str:
    return "`" + text.replace("`", "'") + "`"


def _where(file: str, line: int | None) -> str:
    if not file:
        return "(general)"
    return f"{file}:{line}" if line else file


def inline_body(finding: Finding, *, marker: str) -> str:
    label = _LABEL.get(finding.severity, "Suggestion")
    return f"**{label}** — {redact_bounded(finding.comment, COMMENT_CHARS)}\n\n{marker}"


def _counts(verdict: Verdict, inline_count: int, listed: int) -> str:
    if not verdict.findings:
        return "No findings."
    counts = Counter(f.severity for f in verdict.findings)
    return (
        f"**Findings:** {counts['critical']} critical · {counts['important']} important · "
        f"{counts['suggestion']} suggestion — {inline_count} inline, {listed} listed below."
    )


def _checks(verdict: Verdict, ctx: SummaryContext) -> str:
    if ctx.from_fork:
        return "### Checks\nChecks skipped: this pull request comes from a fork."
    if ctx.depth == "read_only":
        return "### Checks\nChecks skipped: this repository's review depth is read-only."
    if not verdict.checks_run:
        return "### Checks\nNo checks were run."
    lines = ["### Checks"]
    for check in verdict.checks_run:
        label = check.name or check.command
        entry = f"- **{check.status}** {_code(label)}"
        if check.command and check.command != label:
            entry += f": {_code(check.command)}"
        lines.append(entry)
        if check.status != "passed" and check.output_tail:
            tail = redact_bounded(check.output_tail, OUTPUT_TAIL_CHARS).replace("~~~", "~ ~ ~")
            lines.append("\n  ~~~\n" + "\n".join(f"  {t}" for t in tail.splitlines()) + "\n  ~~~")
    return "\n".join(lines)


def _previous(verdict: Verdict) -> str:
    lines = ["### Earlier findings"]
    for p in verdict.previous_findings:
        state = "resolved" if p.status == "resolved" else "still open"
        lines.append(f"- {state} — {_code(_where(p.file, p.line))} {redact_bounded(p.comment, COMMENT_CHARS)}")
    return "\n".join(lines)


def _elsewhere(listed: Sequence[Finding]) -> str:
    lines = ["### Elsewhere in the code"]
    for f in listed:
        label = _LABEL.get(f.severity, "Suggestion")
        lines.append(f"- **{label}** {_code(_where(f.file, f.line))} — {redact_bounded(f.comment, COMMENT_CHARS)}")
    return "\n".join(lines)


def _suggested(verdict: Verdict) -> str:
    block = json.dumps(
        [{"name": s.name, "paths": list(s.paths), "command": s.command} for s in verdict.suggested_dev_checks],
        indent=2,
    )
    return (
        "### Suggested `dev_checks`\n"
        "This repository configures no dev checks, so the reviewer worked out these commands. "
        "To make them the repository's checks, add them under Dev checks in its embry0 preferences:\n\n"
        f"~~~json\n{block}\n~~~"
    )


def _footer(ctx: SummaryContext) -> str:
    return (
        f"---\nReviewed {_code(ctx.head_sha[:12])} · [job {ctx.job_id}]({ctx.job_url}) · "
        "For another review, comment with a line that reads `/embry0 review`.\n"
        f"{review_marker(ctx.job_id, ctx.head_sha)}"
    )


def render_summary(verdict: Verdict, *, inline_count: int, listed: Sequence[Finding], ctx: SummaryContext) -> str:
    parts = [
        "## embry0 review",
        redact_bounded(verdict.summary, SUMMARY_CHARS),
        _counts(verdict, inline_count, len(listed)),
        _checks(verdict, ctx),
    ]
    if verdict.previous_findings:
        parts.append(_previous(verdict))
    if listed:
        parts.append(_elsewhere(listed))
    if verdict.suggested_dev_checks:
        parts.append(_suggested(verdict))
    footer = _footer(ctx)
    body = "\n\n".join(parts)
    room = BODY_LIMIT - len(footer) - 2
    if len(body) > room:
        body = body[: room - len(_TRUNCATED) - len(_CLOSE_FENCE)]
        if sum(1 for line in body.splitlines() if line.lstrip().startswith("~~~")) % 2:
            body += _CLOSE_FENCE  # the cut landed inside a check's output or the dev_checks block
        body += _TRUNCATED
    return f"{body}\n\n{footer}"
