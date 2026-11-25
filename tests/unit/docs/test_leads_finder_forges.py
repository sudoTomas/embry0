"""docs/leads.md's finder table names the forges each finder reads as the loop declares them (FINDER_FORGES)."""

from __future__ import annotations

import re
from pathlib import Path

from embry0.services.leads.loop import FINDER_FORGES

ROOT = Path(__file__).resolve().parents[3]
FORGES = {"GitHub": "github", "GitLab": "gitlab"}


def _documented() -> dict[str, frozenset[str]]:
    """Each finder kind of the table under "Where it looks", with the forges its Forges column names before any
    semicolon (``GitHub, GitLab; Linear for any`` is GitHub and GitLab: Linear is a tracker, read for any)."""
    text = (ROOT / "docs/leads.md").read_text()
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    header = next(line for line in text.splitlines() if line.startswith("| Kind |"))
    column = [cell.strip() for cell in header.strip("|").split("|")].index("Forges")
    documented: dict[str, frozenset[str]] = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        kind = cells[0].strip("`")
        if kind in FINDER_FORGES:
            named = cells[column].split(";", 1)[0]
            documented[kind] = frozenset(forge for word, forge in FORGES.items() if re.search(rf"\b{word}\b", named))
    return documented


def test_the_finder_table_names_the_forges_each_finder_reads() -> None:
    assert _documented() == FINDER_FORGES, "docs/leads.md's Forges column and FINDER_FORGES disagree"
