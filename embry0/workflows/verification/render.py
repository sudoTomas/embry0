"""The verification comment embry0 posts on the pull request (#298, #299).

Read by colleagues who are not engineers, under a human's GitHub identity:
every paragraph says what happened, whether anyone has to act, and what to
do. No command output, file paths, exception text or internal vocabulary —
test names are the one technical detail, folded away, because the person
fixing a failure needs them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from embry0.workflows.verification.scrub import REDACTED

COMMENT_MARKER = "<!-- embry0:verification -->"
MAX_NAMES_SHOWN = 10

TESTS_TEXT: dict[str, str] = {
    "passed": (
        "**Automated tests: passed.** embry0 ran this repository's tests on the code as it was before this "
        "change and again with this change. Nothing that passed before fails now. No action needed."
    ),
    "new_failures": (
        "**Automated tests: this change breaks tests that passed before it.** embry0 ran the tests without "
        "this change and with it; the tests listed below only fail with it. embry0 has stopped working on "
        "this pull request. What to do: fix the listed tests on this branch, or close the pull request."
    ),
    "unstable": (
        "**Automated tests: no new failures.** Some tests fail, but they fail the same way without this "
        "change, or they passed when run a second time, so this change did not cause them. They are listed "
        "below for whoever looks after the test suite. This does not block the pull request."
    ),
    "not_configured": (
        "**Automated tests: not run.** No test command is set up in embry0 for this repository, so embry0 "
        "did not run the tests itself. This does not block the pull request. What to do: review and test it "
        "as you normally would. An embry0 administrator can add the test command in the repository's "
        "embry0 settings."
    ),
    "no_matching_targets": (
        "**Automated tests: not run.** None of the test commands set up in embry0 for this repository "
        "covers the files this change touches. This does not block the pull request. What to do: review "
        "and test it as you normally would."
    ),
    "error": (
        "**Automated tests: could not be run.** embry0 hit a problem while running the tests, so there is "
        "no result either way. This does not block the pull request. What to do: check the tests yourself "
        "before merging."
    ),
}
BASE_RED_UNVERIFIED_TEXT = (
    "Some tests already fail without this change, so embry0 cannot be certain this change adds nothing on "
    "top of them. What to do: check the test results yourself before merging."
)
BROWSER_TEXT: dict[str, str] = {
    "neutral_only": "**Browser check: not needed.** This change only touches documentation.",
    "backend_only": (
        "**Browser check: not needed.** This change does not touch the user interface, so no browser test was run."
    ),
    "branch_stack": (
        "**Browser check: runs next**, against a copy of the application built from this branch. Its "
        "result is posted in a separate comment."
    ),
    "no_branch_stack": (
        "**Browser check: not run.** This change touches the user interface. embry0 cannot yet start a "
        "copy of the application from this branch, and testing the live site would test the code that is "
        "already released, not this change. What to do: a person needs to try the change in a browser "
        "before merging."
    ),
    "qa_not_requested": (
        "**Browser check: not run.** This change touches the user interface, but no browser test was "
        "requested for it. What to do: a person needs to try the change in a browser before merging."
    ),
}
HUMAN_MERGE_TEXT = (
    "**Merging: by a person only.** embry0 will not merge this pull request automatically (label `human-merge-only`)."
)


def listed_names(names: Iterable[str]) -> list[str]:
    """Test names from every target in one sorted list, one entry per test.

    A name the scrubber left whole is listed once, however many targets name
    it: equal text is the same test. A name it redacted part of is listed
    each time it appears, since two different tests can scrub to the same
    text; the report names each test once per target already.
    """
    seen = list(names)
    return sorted([*{n for n in seen if REDACTED not in n}, *(n for n in seen if REDACTED in n)])


def _names_block(title: str, names: list[str]) -> list[str]:
    if not names:
        return []
    shown = names[:MAX_NAMES_SHOWN]
    lines = ["<details>", f"<summary>{title} ({len(names)})</summary>", ""]
    lines += [f"- `{name.replace('`', '')}`" for name in shown]
    if len(names) > len(shown):
        lines.append(f"- and {len(names) - len(shown)} more")
    return [*lines, "", "</details>"]


def render_verification_comment(result: dict[str, Any]) -> str:
    gate = result.get("gate") or {}
    verdict = str(gate.get("verdict") or "error")
    targets = [t for t in gate.get("targets") or [] if isinstance(t, dict)]
    reasons = list(result.get("human_merge_reasons") or [])

    lines = [COMMENT_MARKER, "### embry0 verification", "", TESTS_TEXT.get(verdict, TESTS_TEXT["error"])]
    if "gate_base_red_unverified" in reasons:
        lines += ["", BASE_RED_UNVERIFIED_TEXT]

    new = listed_names(n for t in targets for n in t.get("new_failures") or [])
    old = listed_names(n for t in targets for n in [*(t.get("preexisting") or []), *(t.get("flaky") or [])])
    unnamed = sorted(
        str(t.get("target")) for t in targets if t.get("status") == "new_failures" and not t.get("new_failures")
    )
    blocks = (
        _names_block("Tests that fail only with this change", new)
        + _names_block("Test groups that fail only with this change", unnamed)
        + _names_block("Tests that also fail without this change, or only sometimes", old)
    )
    if blocks:
        lines += ["", *blocks]

    reason_code = str((result.get("browser_qa") or {}).get("reason_code") or "")
    if reason_code in BROWSER_TEXT:
        lines += ["", BROWSER_TEXT[reason_code]]
    if result.get("human_merge_only"):
        lines += ["", HUMAN_MERGE_TEXT]
    return "\n".join(lines)
