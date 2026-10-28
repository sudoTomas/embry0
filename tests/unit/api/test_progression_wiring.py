"""Production builds the progression service over the database, the stored pack and the rollup, and mounts its routes behind auth.

Read from the AST of ``app.py`` because that wiring needs Docker and a
database; see test_merge_approvals_wiring.py for the same approach.
"""

from __future__ import annotations

import ast
import pathlib

import embry0.api.app as app_module

SOURCE = pathlib.Path(app_module.__file__).read_text()
TREE = ast.parse(SOURCE)


def _assigned(target: str) -> set[str]:
    return {
        ast.unparse(node.value)
        for node in ast.walk(TREE)
        if isinstance(node, ast.Assign) and any(ast.unparse(t) == target for t in node.targets)
    }


def test_the_service_reads_the_database_through_its_repository() -> None:
    assert _assigned("app.state.progression") == {
        "ProgressionService(ProgressionRepository(db), packs=app.state.progression_packs, "
        "rollups=ProgressionRollupRepository(db))"
    }, "production reads the database, the stored content pack and the rollup tables (#176)"


def test_shutdown_stops_a_rollup_write_still_running() -> None:
    """A rollup write left running would hold a pool connection while the pool closes."""
    assert "await progression_service.aclose()" in SOURCE, "the lifespan's shutdown no longer stops the rollup write"


def test_the_active_pack_is_the_stored_one_not_the_shipped_default() -> None:
    """``packs`` is required, so a missing source fails at once; a wrong one (``ShippedPack()``) would not.

    With the shipped source wired, every write of the pack API would succeed
    and nothing anyone sees would ever change.
    """
    assert _assigned("app.state.progression_packs") == {"PackStore(ProgressionPacksRepository(db))"}, (
        "production must read the active pack from the database (#176)"
    )
    assert "ShippedPack()" not in SOURCE, "the shipped-only source is for tests; production reads the stored pack"


def test_the_lifespan_upgrades_a_stored_pack_after_the_migrations() -> None:
    lifespan = next(
        node for node in ast.walk(TREE) if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    calls = [ast.unparse(node) for node in ast.walk(lifespan) if isinstance(node, ast.Call)]
    assert "upgrade_stored_packs(ProgressionPacksRepository(db))" in calls, "a stored older pack is upgraded at boot"
    body = ast.unparse(lifespan)
    assert body.index("run_migrations(db)") < body.index("upgrade_stored_packs("), "after the tables exist"


def test_the_routers_are_mounted_behind_auth() -> None:
    for router in ("progression", "progression_pack"):
        assert (
            f'app.include_router({router}.router, prefix="/api/v1", tags=["progression"], dependencies=auth_deps)'
            in SOURCE
        ), router
