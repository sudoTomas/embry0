"""Every table in the repository's Markdown renders as the table it was written as.

Two ways a table goes wrong, and neither shows in the source:

- **A stray row.** GitHub's Markdown keeps a table going until a blank line
  or the start of another block: a heading, a quote, a list, a fence, HTML.
  A sentence put directly under the last row, meant as the next paragraph,
  is read as one more row instead and lands in the table's first cell. This
  pins every table's end on a blank line or a block of its own.
- **No header.** A table starts only at a header row with a delimiter row
  (``|---|---|``) right under it. Rows without them are not a table at all:
  GitHub renders them as one paragraph of literal ``|`` text, joined to any
  sentence directly above. This pins every run of ``|`` rows to a table.
"""

from __future__ import annotations

import re

from tests.unit.docs.test_markdown_links import ROOT, _markdown_files, _outside_fences

_DELIMITER = re.compile(r"^\s{0,3}\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# A line that opens a block of its own, and so ends a table rather than joining it.
_OPENS_A_BLOCK = re.compile(r"^\s{0,3}(#{1,6}(\s|$)|>|<|[-*+]\s|\d{1,9}[.)]\s|([-*_]\s*){3,}$)")
# A line written as a table row: a cell separator first, as every table here writes it.
_ROW = re.compile(r"^\s{0,3}\|")


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


def headless_rows(text: str) -> list[tuple[int, int]]:
    """First and last line numbers of each run of ``|`` rows that no header and delimiter row start."""
    lines = _outside_fences(text, strip_code=False)
    runs: list[tuple[int, int]] = []
    in_table = False
    for i, (n, line) in enumerate(lines):
        follows = i > 0 and lines[i - 1][0] == n - 1
        if in_table and (not follows or not line.strip() or _OPENS_A_BLOCK.match(line)):
            in_table = False
        if in_table or not _ROW.match(line):
            continue
        below = lines[i + 1] if i + 1 < len(lines) else None
        if below is not None and below[0] == n + 1 and _DELIMITER.match(below[1]):
            in_table = True  # a header row: the delimiter and the rows under it are the table
        elif runs and follows and runs[-1][1] == n - 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def test_rows_without_a_header_are_found() -> None:
    """The check below passes only if it can fail."""
    text = (
        "Prose.\n\n| a | 1 |\n| b | 2 |\n\n"  # no header after a blank line: lines 3-4
        "A sentence.\n| c | 3 |\n\n"  # no header, glued under a sentence: line 7
        "| h | i |\n|---|---|\n| d | 4 |\n\n"  # a table
        "| h |\n|:-:|\n| e |\n\n| f |\n\n"  # a table, then a row the blank line cut off from it: line 17
        "```\n| g |\n```\n"  # in a fence
        "    | indented code |\n"
    )
    assert headless_rows(text) == [(3, 4), (7, 7), (17, 17)]


def test_every_row_belongs_to_a_table_with_a_header() -> None:
    found = [
        f"{src.relative_to(ROOT)}:{first}-{last}"
        for src in _markdown_files(ROOT)
        for first, last in headless_rows(src.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "these | rows have no header and delimiter row above them, so they render as literal | text; "
        "add a header row and a |---| delimiter row:\n" + "\n".join(found)
    )


def test_no_sentence_renders_as_a_table_row() -> None:
    found = [
        f"{src.relative_to(ROOT)}:{n}"
        for src in _markdown_files(ROOT)
        for n in stray_rows(src.read_text(encoding="utf-8"))
    ]
    assert not found, "a line right under a table renders as one more row; put a blank line before it:\n" + (
        "\n".join(found)
    )
