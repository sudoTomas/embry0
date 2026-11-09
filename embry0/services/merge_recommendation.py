"""What the "merge this PR?" ask tells a human: a summary and a recommendation.

Pure functions over facts embry0 already holds — no I/O, no model call.

The summary is the reviewer agent's own assessment (its ``summary`` field),
falling back to the PR body and then the title. The recommendation is a
fixed-rule verdict — ``merge`` / ``review_first`` / ``dont_merge`` — with the
reasons that produced it. It is deliberately NOT a model's opinion: the PR's
text is agent-written and could argue a model into "merge it", while CI, the
gate and the changed paths cannot be argued with. The same facts always give
the same verdict, and every downgrade names its cause.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from embry0.json_text import json_object

Verdict = Literal["merge", "review_first", "dont_merge"]

#: Lines changed / files touched above which a diff is "large" — a human
#: should read it rather than take the verdict on trust.
LARGE_DIFF_LINES = 800
LARGE_DIFF_FILES = 30

#: Longest summary the ask carries; the reviewer's field can run to a page.
SUMMARY_CHARS = 420

# Gate reasons (``human_merge_reasons``) that mean "nothing deterministic ran",
# which on today's repos is nearly every PR. Counting them as a downgrade
# would make every verdict "review first" and the verdict meaningless; they
# are reported as information instead. Anything else the gate says downgrades.
_INFO_ONLY_GATE_REASONS = frozenset({"gate_not_configured", "gate_no_matching_targets", "neutral_only", "backend_only"})

_GATE_REASON_TEXT = {
    "gate_error": "the test gate errored",
    "gate_new_failures": "the test gate found new failures",
    "gate_base_red_unverified": "main was already red, so the gate could not show nothing new broke",
    "no_branch_stack": "frontend change with no browser QA against the branch",
    "qa_not_requested": "frontend change with browser QA not requested",
}


@dataclass(frozen=True)
class ReviewFacts:
    """The parts of the reviewer agent's JSON the ask uses."""

    decision: str = ""
    validation: dict[str, str] = field(default_factory=dict)
    critical: int = 0
    important: int = 0
    addresses_failure: str = ""
    duplicate_pr: int | None = None
    summary: str = ""


def parse_review(result_summary: str | None) -> ReviewFacts | None:
    """The reviewer's verdict from a job's ``result_summary``, or None when it
    holds no reviewer JSON (a PR no embry0 job reviewed)."""
    text = str(result_summary or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    data = json_object(candidate)
    if data is None or "decision" not in data:
        return None

    validation: dict[str, str] = {}
    for name, entry in (data.get("validation") or {}).items():
        status = entry.get("status") if isinstance(entry, dict) else entry
        if isinstance(status, str):
            validation[str(name)] = status.lower()
    comments = [c for c in data.get("review_comments") or [] if isinstance(c, dict)]
    severity = [str(c.get("severity") or "").lower() for c in comments]
    failure = data.get("addresses_logged_failure")
    duplicate = data.get("duplicates_open_pr")
    return ReviewFacts(
        decision=str(data.get("decision") or "").lower(),
        validation=validation,
        critical=severity.count("critical"),
        important=severity.count("important"),
        addresses_failure=str(failure.get("verdict") or "").lower() if isinstance(failure, dict) else "",
        duplicate_pr=int(duplicate["number"]) if isinstance(duplicate, dict) and duplicate.get("number") else None,
        summary=str(data.get("summary") or "").strip(),
    )


def _clip(text: str, limit: int = SUMMARY_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # End on a sentence when one ends reasonably late; otherwise on a word.
    stop = max(cut.rfind(". "), cut.rfind("; "))
    if stop >= limit * 0.6:
        return cut[: stop + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"


#: PR bodies embry0 writes itself that say nothing about the change.
#: Issue references ("Fixes #12") are boilerplate; "Fixes the rounding bug" is not.
_BOILERPLATE = re.compile(
    r"^(opened by the embry0 orchestrator|(closes|fixes|resolves)\s+[\w./-]*#\d+\S*\s*$|refs?:)", re.I
)


def summarize(review: ReviewFacts | None, pr_body: str | None, title: str) -> tuple[str, str]:
    """``(summary, source)`` — source is ``review``, ``pr_body`` or ``title``."""
    if review and review.summary:
        return _clip(review.summary), "review"
    for paragraph in re.split(r"\n\s*\n", str(pr_body or "")):
        # Drop headings, HTML comments, checklists and images; keep prose.
        lines = [
            ln.strip()
            for ln in paragraph.splitlines()
            if ln.strip() and not re.match(r"^(#|<!--|- \[[ x]\]|!\[|\|)", ln.strip())
        ]
        prose = " ".join(lines)
        if len(prose) >= 20 and not _BOILERPLATE.match(prose):
            return _clip(prose), "pr_body"
    return title.strip(), "title"


@dataclass(frozen=True)
class Recommendation:
    verdict: Verdict
    reasons: list[str]
    positives: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommend(
    *,
    checks_state: str,
    checks_text: str,
    checks_passed: int,
    mergeable: bool | None,
    denylisted: dict[str, Any],
    files_complete: bool,
    additions: int,
    deletions: int,
    changed_files: int,
    verification: dict[str, Any],
    review: ReviewFacts | None,
) -> Recommendation:
    """The fixed-rule verdict. Any ``dont_merge`` reason wins over any
    ``review_first`` one; ``merge`` needs neither."""
    stop: list[str] = []
    caution: list[str] = []
    positives: list[str] = []
    notes: list[str] = []

    # -- CI -------------------------------------------------------------
    if checks_state == "red":
        stop.append(f"CI failed — {checks_text}")
    elif checks_state == "green":
        positives.append(f"CI green ({checks_passed} checks)")
    else:
        caution.append(f"CI not settled — {checks_text}")

    if mergeable is False:
        stop.append("conflicts with the base branch")

    # -- verification gate ------------------------------------------------
    if verification.get("present"):
        if verification.get("blocking") and verification.get("for_this_head"):
            stop.append("the test gate found new failures on this commit")
        for reason in verification.get("human_merge_reasons") or []:
            if reason in _INFO_ONLY_GATE_REASONS:
                notes.append("no deterministic test gate is configured for this change")
            else:
                caution.append(_GATE_REASON_TEXT.get(reason, f"verification flagged `{reason}`"))
    else:
        notes.append("no verification recorded for this PR")

    # -- the reviewer agent -----------------------------------------------
    if review is None:
        caution.append("no embry0 review — this PR was not opened by an embry0 job")
    else:
        if review.decision == "changes_requested":
            stop.append("the reviewer requested changes")
        elif review.decision == "approved":
            positives.append("reviewer approved")
        failed = sorted(k for k, v in review.validation.items() if v == "fail")
        if failed:
            stop.append("the reviewer saw " + ", ".join(failed) + " fail")
        if review.validation.get("tests") == "skipped":
            caution.append("the reviewer did not run tests")
        passed = sorted(k for k, v in review.validation.items() if v == "pass")
        if passed and not failed:
            positives.append(", ".join(passed) + " pass")
        if review.critical:
            stop.append(f"{review.critical} critical review comment(s)")
        if review.important:
            caution.append(f"{review.important} important review comment(s)")
        if review.addresses_failure in ("no", "partial", "unclear"):
            caution.append(f"the reviewer is not sure it fixes the logged failure ({review.addresses_failure})")
        elif review.addresses_failure == "yes":
            positives.append("addresses the logged failure")
        if review.duplicate_pr:
            caution.append(f"overlaps open PR #{review.duplicate_pr}")

    # -- the diff itself --------------------------------------------------
    if denylisted:
        caution.append("touches " + ", ".join(sorted(denylisted)))
    if not files_complete:
        caution.append("the changed-file list was incomplete")
    if additions + deletions > LARGE_DIFF_LINES or changed_files > LARGE_DIFF_FILES:
        caution.append(f"large diff ({additions + deletions} lines in {changed_files} files)")

    if stop:
        return Recommendation("dont_merge", stop + caution, positives, notes)
    if caution:
        return Recommendation("review_first", caution, positives, notes)
    return Recommendation("merge", [], positives, notes)
