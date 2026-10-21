"""No line of the repository's Markdown renders as a stray table row.

GitHub's Markdown keeps a table going until a blank line or the start of
another block: a heading, a quote, a list, a fence, HTML. A sentence put
directly under the last row, meant as the next paragraph, is read as one
more row instead and lands in the table's first cell. This pins every
table's end on a blank line or a block of its own.
"""

from __future__ import annotations

import re

from tests.unit.docs.test_markdown_links import ROOT, _markdown_files, _outside_fences

_DELIMITER = re.compile(r"^\s{0,3}\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# A line that opens a block of its own, and so ends a table rather than joining it.
_OPENS_A_BLOCK = re.compile(r"^\s{0,3}(#{1,6}(\s|$)|>|<|[-*+]\s|\d{1,9}[.)]\s|([-*_]\s*){3,}$)")


def stray_rows(text: str) -> list[int]:
    """Line numbers of lines with no cell separator that a table above swallows as a row."""
    stray: list[int] = []
    in_table = False
    previous: tuple[int, str] | None = None
    for n, line in _outside_fences(text, strip_code=False):
        follows = previous is not None and previous[0] == n - 1
        if in_table and (not follows or not line.strip() or _OPENS_A_BLOCK.match(line)):
            in_table = False
        elif in_table and "|" not in line:
            stray.append(n)
            in_table = False
        elif not in_table and follows and previous is not None and "|" in previous[1] and _DELIMITER.match(line):
            in_table = True
        previous = (n, line)
    return stray


def test_a_sentence_glued_under_a_table_is_found() -> None:
    """The check below passes only if it can fail."""
    text = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n See `x`.\n\n"  # glued: line 4
        "| a |\n|---|\n| 1 |\n<!-- end -->\n\n"  # HTML ends a table
        "| a |\n|---|\n\nAfter a blank line.\n\n"
        "```\n| a |\n|---|\nin a fence\n```\n"
    )
    assert stray_rows(text) == [4]


def test_no_sentence_renders_as_a_table_row() -> None:
    found = [
        f"{src.relative_to(ROOT)}:{n}"
        for src in _markdown_files(ROOT)
        for n in stray_rows(src.read_text(encoding="utf-8"))
    ]
    assert not found, "a line right under a table renders as one more row; put a blank line before it:\n" + (
        "\n".join(found)
    )
