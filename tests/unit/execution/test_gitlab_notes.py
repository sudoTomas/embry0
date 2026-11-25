"""A note embry0 posts to GitLab carries words, never a quick action."""

from __future__ import annotations

import re

import pytest

from embry0.execution.gitlab_notes import ZERO_WIDTH_SPACE, without_quick_actions

#: GitLab's own test for a quick action: a slash at the very start of a line, then the command's name.
QUICK_ACTION = re.compile(r"^/[a-z_]+", re.MULTILINE)


@pytest.mark.parametrize(
    "body",
    [
        "/merge",
        "Looks good.\n/approve\n/merge",
        "Done.\r\n/close",
        "  /assign @someone",
        "\t/label ~ready",
        "```\n/merge\n```",
        "\r/merge",
        "Not wanted.\n\r/merge\n/approve",
        "Not wanted.\n \r\t/merge",
    ],
)
def test_no_line_starts_a_quick_action(body: str) -> None:
    """Checked as GitLab reads a note: carriage returns deleted first, and spaces or tabs before the slash
    ignored."""
    neutral = without_quick_actions(body)
    assert QUICK_ACTION.search(neutral.replace("\r", "").replace(" ", "").replace("\t", "")) is None, repr(neutral)
    assert neutral.replace(ZERO_WIDTH_SPACE, "") == body, "the note reads the same"


@pytest.mark.parametrize(
    "body", ["Not wanted.", "See https://example.test/a/b and a/b.", "Run `/merge` by hand", "", "x /approve"]
)
def test_a_note_without_a_line_start_slash_is_unchanged(body: str) -> None:
    assert without_quick_actions(body) == body


def test_it_can_be_applied_twice() -> None:
    once = without_quick_actions("a\n/merge\n  /approve")
    assert without_quick_actions(once) == once
