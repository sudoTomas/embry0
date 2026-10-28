"""Production builds the progression service over the database and the active pack, and mounts its read behind auth.

Read from the AST of ``lifespan`` because that wiring needs Docker and a
database; see test_merge_approvals_wiring.py for the same approach.
"""

from __future__ import annotations

import ast
import pathlib

import embry0.api.app as app_module

SOURCE = pathlib.Path(app_module.__file__).read_text()


def test_the_service_reads_the_database_through_its_repository() -> None:
    assigned = {
        ast.unparse(node.value)
        for node in ast.walk(ast.parse(SOURCE))
        if isinstance(node, ast.Assign) and any(ast.unparse(t) == "app.state.progression" for t in node.targets)
    }
    assert assigned == {"ProgressionService(ProgressionRepository(db), packs=ShippedPack())"}, (
        f"production reads the database and the active content pack (#176): {assigned}"
    )


def test_the_router_is_mounted_behind_auth() -> None:
    assert (
        'app.include_router(progression.router, prefix="/api/v1", tags=["progression"], dependencies=auth_deps)'
        in SOURCE
    )
