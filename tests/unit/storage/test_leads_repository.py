"""LeadsRepository (spec 2026-10-25): upsert keeps first_seen, a whole read closes what it no longer sees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.leads import Lead
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.leads import LeadsRepository

T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)


def _lead(name: str, severity: str = "high") -> Lead:
    return Lead(
        kind="ci",
        repo="acme/web",
        source_key=f"check:{name}",
        title=f"{name} fails on main",
        summary="s",
        details="d",
        action="a",
        severity=severity,
        paths=("src/app.py",),
        data={"check": name},
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_leads_are_kept_up_to_date_closed_and_reopened(db_with_migrations: DatabasePool) -> None:
    repo = LeadsRepository(db_with_migrations)
    build, lint = _lead("build"), _lead("lint", "medium")
    await repo.upsert([build, lint], now=T0, areas={build.id: "Web"})
    row = await repo.get(build.id)
    assert (
        row is not None
        and row["area"] == "Web"
        and row["data"] == {"check": "build", "paths": ["src/app.py"], "labels": []}
    )
    assert await repo.new_since("acme/web", "ci", T0 - timedelta(hours=1)) == 2
    assert await repo.open_sources("acme/web", "ci") == {build.id: "check:build", lint.id: "check:lint"}

    assert await repo.mark_gone("acme/web", "ci", {build.id}, now=T0 + timedelta(hours=1)) == 1
    assert [r["id"] for r in await repo.open_leads()] == [build.id]
    assert {r["id"] for r in await repo.list(state="gone")} == {lint.id}

    await repo.upsert([lint], now=T0 + timedelta(hours=2))
    back = await repo.get(lint.id)
    assert back is not None and back["state"] == "open" and back["first_seen"] == T0 and back["gone_at"] is None
    assert await repo.known_ids("acme/web", "ci") == {build.id, lint.id}
    assert [r["id"] for r in await repo.list(repo="ACME/web", kind="ci", state=None, limit=1)] == [lint.id]
