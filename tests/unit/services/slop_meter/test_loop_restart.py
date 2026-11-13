"""The Slop-O-Meter's place in each read and each credential's turn outlive a restart (#396).

Against the migrated schema: the meter is built anew, as a restart builds it,
before every run, over the same database. A read the budget stopped goes on
from its stored place, and the round-robin from the credential's stored turn.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.slop_meter import SlopMeterRepository
from tests.unit.services.slop_meter.test_loop import FakeForge, _crowd_loop

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.mark.parametrize(("repos", "budget", "bound"), [(70, 200, 4), (12, 5, 24)], ids=["70 on 200", "12 on 5"])
async def test_a_restart_before_every_run_still_scores_every_repository_within_the_bound(
    db_with_migrations: DatabasePool, repos: int, budget: int, bound: int
) -> None:
    """A whole read is at most 7 calls: ceil(7 / (200 // 70)) = 4 runs; 12 * ceil(7 / 5) = 24 runs."""
    names = [f"acme/app-{i:02d}" for i in range(repos)]
    forges: dict[str, Any] = {name: FakeForge(repo=name) for name in names}
    store = SlopMeterRepository(db_with_migrations)
    for _ in range(bound):
        # A fresh meter each run, as after a restart: nothing it keeps in memory survives.
        assert await _crowd_loop(store, forges, names, budget=budget).tick() is False, "no catch-up for budget"
    scored = {str(row["repo"]) for row in await store.scores()}
    unscored = sorted(set(names) - scored)
    assert not unscored, f"{len(unscored)} of {repos} were not scored within {bound} runs, restarted before each"
