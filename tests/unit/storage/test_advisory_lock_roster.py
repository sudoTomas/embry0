"""Every Postgres advisory lock embry0 takes is named in the migration runner's roster.

Advisory-lock keys share one database-wide namespace, so a new lock must take a key no other takes, and the roster
beside ``MIGRATIONS_ADVISORY_LOCK_KEY`` is where a reader checks which are taken. Each module that takes one is
listed here with the key it takes: a module that starts taking one fails until it is listed here and in the roster.
"""

from __future__ import annotations

from pathlib import Path

import embry0
from embry0.storage.migrations import runner
from embry0.storage.repositories import progression_packs

PACKAGE = Path(embry0.__file__).parent

#: Each module that takes an advisory lock, by its path under ``embry0/``: the key as the roster names it, and the
#: text of the key in the module's own source.
LOCKS: dict[str, tuple[str, str]] = {
    "storage/repositories/qa_image_tags.py": ("`qa_image_tags:<repo>`", '"qa_image_tags:'),
    "storage/repositories/org_needs.py": ("`org_needs:<owner>`", '"org_needs:'),
    "storage/repositories/standards.py": ("`standards:<owner>:<kind>`", '"standards:'),
    "storage/repositories/org_repos.py": ("`org_discovery:<owner>`", '"org_discovery:'),
    "storage/repositories/credentials.py": (
        "`credential_bindings:<scope_kind>:<scope_value>`",
        '"credential_bindings:',
    ),
    "storage/repositories/settings_documents.py": ("`settings_documents:<scope>`", "'settings_documents:'"),
    "storage/repositories/asks.py": ("`asks:hand_out`", '"asks:hand_out"'),
    "storage/repositories/local_model_leases.py": ("`local_model_slots`", '"local_model_slots"'),
    "storage/repositories/progression_rollup.py": ("`progression_rollup`", '"progression_rollup"'),
    "storage/repositories/progression_packs.py": ("0x65307061636B", "WRITE_LOCK_KEY"),
}


def _roster() -> str:
    """The comment right above ``MIGRATIONS_ADVISORY_LOCK_KEY``, joined into one line."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    above = source[: source.index("\nMIGRATIONS_ADVISORY_LOCK_KEY = ")].splitlines()
    comment: list[str] = []
    while above and above[-1].startswith("#"):
        comment.insert(0, above.pop().lstrip("#").strip())
    return " ".join(comment)


def test_every_module_that_takes_an_advisory_lock_is_listed() -> None:
    taking = {
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if "pg_advisory" in path.read_text(encoding="utf-8") and path != Path(runner.__file__)
    }
    assert taking, "the walk found no advisory lock at all: it read the wrong tree"
    assert taking == set(LOCKS), (
        f"modules that take an advisory lock and are not listed: {sorted(taking - set(LOCKS))}; "
        f"listed but taking none: {sorted(set(LOCKS) - taking)}"
    )


def test_the_roster_names_every_key_taken() -> None:
    roster = _roster()
    assert "embry0" in roster, f"the roster was not found above MIGRATIONS_ADVISORY_LOCK_KEY: {roster[:200]!r}"
    unnamed = [named for named, _code in LOCKS.values() if named not in roster]
    assert unnamed == [], f"keys the runner's advisory-lock roster does not name: {unnamed}"


def test_each_module_takes_the_key_the_roster_names() -> None:
    differ = [
        module for module, (_named, code) in LOCKS.items() if code not in (PACKAGE / module).read_text(encoding="utf-8")
    ]
    assert differ == [], f"modules whose key is not the one listed: {differ}"
    assert progression_packs.WRITE_LOCK_KEY == 0x65307061636B != runner.MIGRATIONS_ADVISORY_LOCK_KEY
