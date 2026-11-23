"""The readiness table in docs/leads.md is the one the ``leads`` section's defaults render (#168).

The page says what a ticket's readiness reads before any document says
otherwise: each signal's points, each role's labels, the done phrases and the
epic title words. The committed table must equal what the defaults render, so
changing a default without regenerating the page fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from embry0.services.leads.readiness_reference import COMMAND, END, START, main, render
from embry0.settings.reference import committed_block
from embry0.settings.sections.leads import LABEL_ROLES, SIGNALS, LeadsSettings

LEADS_PAGE = Path(__file__).resolve().parents[3] / "docs" / "leads.md"


def test_the_committed_table_is_what_the_defaults_render() -> None:
    committed = committed_block(LEADS_PAGE.read_text(encoding="utf-8"), markers=(START, END))
    assert committed == render(), f"docs/leads.md's readiness table is out of date; run {COMMAND}"


def test_the_check_fails_when_a_default_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison above passes only if it can fail."""
    page = tmp_path / "leads.md"
    page.write_text(f"intro\n\n{render()}\n\nmore\n", encoding="utf-8")
    assert main([str(page), "--check"]) == 0
    changed = LeadsSettings.model_validate({"readiness_weights": {"done": 25}}, strict=False)
    monkeypatch.setattr("embry0.services.leads.readiness_reference.LeadsSettings", lambda: changed)
    assert main([str(page), "--check"]) == 1, "a default that changed fails the check"
    assert main([str(page), "--write"]) == 0
    assert main([str(page), "--check"]) == 0
    text = page.read_text(encoding="utf-8")
    assert "| `done` | +25 |" in text, "the table says the new points"
    assert text.startswith("intro\n\n") and text.endswith("\n\nmore\n"), "only the block between the markers changes"


def test_every_signal_and_role_has_a_row_with_its_default() -> None:
    block = render()
    defaults = LeadsSettings()
    for signal in SIGNALS:
        assert f"| `{signal}` | {defaults.readiness_weights[signal]:+d} |" in block, f"{signal} has its row"
    roles = block.split("| Role |", 1)[1].splitlines()
    for role in LABEL_ROLES:
        row = next((line for line in roles if line.startswith(f"| `{role}` |")), None)
        assert row is not None and all(f"`{label}`" in row for label in defaults.readiness_labels[role]), role
    assert all(f"`{phrase}`" in block for phrase in defaults.done_phrases), "every default done phrase is listed"
