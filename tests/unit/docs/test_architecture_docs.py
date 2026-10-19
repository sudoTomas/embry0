"""docs/architecture.md describes the runner; these tests keep the two in step.

The Migrations Reference table listed 1 to 75 while the runner defined up
to 100 (#79), and nothing noticed for twenty-five migrations. The table's
numbers are read from the document and compared with the runner's, so the
next migration without a row fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from embry0.storage.migrations.runner import MIGRATIONS

ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURE = ROOT / "docs" / "architecture.md"


def _migrations_reference_rows() -> list[int]:
    text = ARCHITECTURE.read_text()
    heading = "### Migrations Reference"
    assert heading in text, f"no {heading!r} heading in {ARCHITECTURE}"
    section = text.split(heading, 1)[1]
    next_heading = re.search(r"^#{1,3} ", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    return [int(m.group(1)) for m in re.finditer(r"^\| (\d+) \|", section, re.MULTILINE)]


def test_migrations_reference_lists_every_migration_once() -> None:
    documented = _migrations_reference_rows()
    defined = [number for number, _, _ in MIGRATIONS]
    assert sorted(documented) == sorted(set(documented)), "a migration has two rows"
    assert sorted(documented) == sorted(defined), (
        f"undocumented: {sorted(set(defined) - set(documented))}; "
        f"documented but not defined: {sorted(set(documented) - set(defined))}"
    )


def test_migrations_reference_is_in_order() -> None:
    documented = _migrations_reference_rows()
    assert documented == sorted(documented)
