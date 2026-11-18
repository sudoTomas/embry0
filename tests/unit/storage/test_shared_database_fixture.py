"""Every test that migrates the test database starts from a known schema (tests/fixtures/postgres.py).

A module that connected to ``TEST_DATABASE_URL`` itself and ran the migrations on the schema as it found it
replayed every migration after a test that leaves tables and no migration ledger (a migration test that applies
migrations' SQL directly, as ``test_migration_133.py`` does), and failed at the first migration that cannot run
twice. Under ``pytest -n`` that happened whenever such a test ran just before it on the same worker; alone, it
passed. ``db_with_migrations`` and ``api_client`` start from what migrating an empty schema leaves, whatever ran
before; a module that needs a connection of its own drops the schema first, or calls ``start_clean``.
"""

from __future__ import annotations

from pathlib import Path

_TESTS = Path(__file__).resolve().parents[2]


def test_no_test_migrates_the_database_as_it_finds_it() -> None:
    """A module that reads ``TEST_DATABASE_URL`` and runs the migrations must drop the schema first or start clean.
    A file-level check: it names the module, and the fix is to take ``db_with_migrations``."""
    found = []
    for path in sorted((_TESTS / "unit").rglob("*.py")):
        text = path.read_text()
        if "run_migrations(" not in text or "TEST_DATABASE_URL" not in text:
            continue
        if "DROP SCHEMA public" in text or "start_clean(" in text:
            continue
        found.append(path.relative_to(_TESTS).as_posix())
    assert found == [], (
        f"these modules run the migrations on the test database as they find it, so a test that leaves tables and "
        f"no migration ledger before them makes them replay every migration and fail: {found}. Take "
        "db_with_migrations instead (tests/fixtures/postgres.py)."
    )
