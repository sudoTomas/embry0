"""A map-areas job's areas document, read from its report (spec 2026-11-10, ADR 0019).

The area-mapper ends its report with the whole areas document in a ``yaml``
fenced block under an ``## Areas file`` heading. The output step reads that
block and the rest of the report (its notes) into the job's state; the
executor then stores them on the map's proposal, where a person applies or
rejects them. Nothing is written to the repository and no pull request is
opened.

The document is read from the report, so what a person reviews is what the
agent wrote. A report with no block leaves the document out (``None``), and
the proposal says the map found no document.
"""

from __future__ import annotations

import re
from typing import Any

#: The pipeline template whose output step this is.
TEMPLATE = "map-areas"

_HEADING = re.compile(r"^#{2,3}[ \t]+Areas file\b.*$", re.MULTILINE)
_YAML_BLOCK = re.compile(r"^(?P<fence>`{3,})ya?ml[^\n]*\n(?P<body>.*?)\n(?P=fence)[ \t]*$", re.MULTILINE | re.DOTALL)


def report_file(report: str | None) -> str | None:
    """The document the report carries: the first ``yaml`` block under ``## Areas file``, else None."""
    if not report:
        return None
    heading = _HEADING.search(report)
    if heading is None:
        return None
    match = _YAML_BLOCK.search(report, heading.end())
    if match is None:
        return None
    body = match.group("body").strip("\n")
    return body + "\n" if body.strip() else None


def report_notes(report: str) -> str:
    """The report without its document: the table and the notes a person reads beside the proposal."""
    heading = _HEADING.search(report)
    text = report[: heading.start()] if heading else report
    rest = report[heading.end() :] if heading else ""
    after = _YAML_BLOCK.search(rest)
    if after is not None:
        text += rest[after.end() :]
    return text.strip()


def map_outcome(report: str) -> dict[str, Any]:
    """What a map-areas job's output step adds to its state: ``areas_document`` (the block, or None) and
    ``areas_notes``, for the executor to store on the map's proposal."""
    return {"areas_document": report_file(report), "areas_notes": report_notes(report)}
