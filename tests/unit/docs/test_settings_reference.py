"""The settings reference in docs/configuration.md is the one the sections render (spec 2026-10-28).

A section field can never go undocumented: every field must have a
description to register, and the committed reference must equal what the
catalog renders, so adding a field without regenerating the docs fails here.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import Field

from embry0.settings.reference import COMMAND, END, START, committed_block, main, render, splice
from embry0.settings.registry import Catalog, Section, SectionModel
from embry0.settings.sections import CATALOG
from tests.fixtures.settings_sections import SAMPLE, TEST_CATALOG

CONFIGURATION = Path(__file__).resolve().parents[3] / "docs" / "configuration.md"


def test_the_committed_reference_is_what_the_sections_render() -> None:
    committed = committed_block(CONFIGURATION.read_text(encoding="utf-8"))
    assert committed == render(CATALOG), f"docs/configuration.md's settings reference is out of date; run {COMMAND}"


def test_the_check_fails_when_a_section_is_missing_from_the_reference(tmp_path: Path, monkeypatch) -> None:
    """The comparison above passes only if it can fail."""
    stale = tmp_path / "configuration.md"
    stale.write_text("intro\n\n" + render(Catalog(())) + "\n\nmore\n", encoding="utf-8")
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    assert main([str(stale), "--check"]) == 1
    assert main([str(stale), "--write"]) == 0
    assert main([str(stale), "--check"]) == 0
    text = stale.read_text(encoding="utf-8")
    assert text.startswith("intro\n\n") and text.endswith("\n\nmore\n"), "only the block between the markers changes"


def test_every_field_is_in_its_section_table_with_its_description_and_variable() -> None:
    block = render(TEST_CATALOG)
    for key in SAMPLE.fields:
        row = next((line for line in block.splitlines() if line.startswith(f"| `{key}` |")), None)
        assert row is not None, f"sample.{key} has no row"
        assert (SAMPLE.model.model_fields[key].description or "") in row
    per_tick = next(line for line in block.splitlines() if line.startswith("| `per_tick` |"))
    assert per_tick == (
        "| `per_tick` | whole number | `3` | ≥ 1, ≤ 20 | organization | `PROPOSAL_BRIEFS_PER_TICK` "
        "| Items the sample loop handles each tick. |"
    )


def test_splice_needs_both_markers() -> None:
    with pytest.raises(ValueError, match="markers"):
        splice(f"only {START} here", render(CATALOG))
    assert splice(f"a {START} old {END} b", "NEW") == "a NEW b"


class _Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class _Paint(SectionModel):
    color: _Color = Field(default=_Color.RED, description="The color.")
    maybe: _Color | None = Field(default=None, description="Maybe a color.")


def test_an_enum_field_is_described_as_text_with_its_values() -> None:
    block = render(Catalog((Section(name="paint", model=_Paint, summary="Paint."),)))
    rows = {line.split(" | ")[0]: line for line in block.splitlines() if line.startswith("| `")}
    assert rows["| `color`"] == '| `color` | text | `"red"` | one of `"red"`, `"blue"` |  |  | The color. |', (
        "an Enum is a $ref in the schema and must be resolved, not shown as a mapping with no values"
    )
    assert rows["| `maybe`"].startswith('| `maybe` | text or null | `null` | one of `"red"`, `"blue"` |')


class _Palette(SectionModel):
    colors: list[_Color] = Field(default_factory=list, description="Colors.")


def test_a_list_of_enum_values_is_described_with_its_values() -> None:
    block = render(Catalog((Section(name="palette", model=_Palette, summary="Palette."),)))
    row = next(line for line in block.splitlines() if line.startswith("| `colors`"))
    assert 'each one of `"red"`, `"blue"`' in row, f"a list of an Enum shows no allowed values: {row}"
