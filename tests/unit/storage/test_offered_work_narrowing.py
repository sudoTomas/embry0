"""The leads, hand-offs and asks queries narrowed to the repositories a key covers, on a real database.

``GET /leads``, ``GET /handoffs?for=all`` and ``GET /asks`` page in SQL. A key with an
allowlist narrows the query to the slugs it covers (``covered_slugs``)
before the limit, so its page holds its own rows even when newer rows
belong to repositories it does not cover. A row stored under a repository's
old slug is selected through its current one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.leads import Lead
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from embry0.storage.repositories.handoffs import HandoffsRepository
from embry0.storage.repositories.leads import LeadsRepository

pytestmark = pytest.mark.requires_postgres

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
#: What covered_slugs gives a key covering acme/web, which was acme/old-web.
COVERED = ["acme/old-web", "acme/web"]


def _lead(repo: str, name: str) -> Lead:
    return Lead(
        kind="ci",
        repo=repo,
        source_key=f"check:{name}",
        title=f"{name} fails on main",
        summary="s",
        details="d",
        action="a",
        severity="high",
        paths=("src/app.py",),
        data={"check": name},
    )


async def test_a_page_of_leads_holds_the_covered_repositories_rows_before_newer_ones(
    db_with_migrations: DatabasePool,
) -> None:
    leads = LeadsRepository(db_with_migrations)
    await leads.upsert([_lead("acme/web", "build")], now=NOW - timedelta(hours=3))
    await leads.upsert([_lead("acme/old-web", "lint")], now=NOW - timedelta(hours=2))
    await leads.upsert([_lead("globex/gizmos", "deploy")], now=NOW - timedelta(hours=1))

    page = await leads.list(limit=2, repos=COVERED)
    assert [r["repo"] for r in page] == ["acme/old-web", "acme/web"], (
        f"the newest lead is globex's, yet a page of two narrowed to acme/web holds both of its leads, the one "
        f"stored under the old slug too: {[r['repo'] for r in page]}"
    )
    assert [r["repo"] for r in await leads.list(limit=2)] == ["globex/gizmos", "acme/old-web"], "unnarrowed, as before"
    assert await leads.list(repos=[]) == [], "an empty allowlist selects nothing"


async def test_a_page_of_hand_offs_holds_the_covered_repositories_rows_before_newer_ones(
    db_with_migrations: DatabasePool,
) -> None:
    handoffs = HandoffsRepository(db_with_migrations)
    for i, repo in enumerate(("acme/web", "acme/old-web", "globex/gizmos")):
        await handoffs.send(
            decision_id=f"offer:issue:{i}",
            repo=repo,
            title=f"Decision {i}",
            from_actor="bo@example.com",
            to_email="ada@example.com",
            note="",
            now=NOW + timedelta(minutes=i),
        )

    page = await handoffs.recent(limit=2, repos=COVERED)
    assert [r["repo"] for r in page] == ["acme/old-web", "acme/web"], (
        f"the newest hand-off is globex's, yet a page of two narrowed to acme/web holds both of its own: "
        f"{[r['repo'] for r in page]}"
    )
    assert [r["repo"] for r in await handoffs.recent(limit=2)] == ["globex/gizmos", "acme/old-web"], "unnarrowed"
    assert await handoffs.recent(repos=[]) == [], "an empty allowlist selects nothing"


async def test_a_page_of_asks_holds_the_asks_about_the_named_decisions_before_newer_ones(
    db_with_migrations: DatabasePool,
) -> None:
    """``GET /asks`` through a key with an allowlist narrows to the asks about open decisions in its repositories."""
    for i, decision_id in enumerate(("offer:issue:mine-1", "offer:issue:mine-2", "offer:issue:other-1")):
        await db_with_migrations.execute(
            "INSERT INTO asks (id, decision_id, recipient, due_at, created_at) VALUES ($1, $2, $3, $4, $4)",
            f"ask-{i}",
            decision_id,
            "ada@example.com",
            NOW + timedelta(minutes=i),
        )
    asks = AsksRepository(db_with_migrations)
    page = await asks.recent(limit=2, repos=[], decision_ids=["offer:issue:mine-1", "offer:issue:mine-2"])
    assert [r["decision_id"] for r in page] == ["offer:issue:mine-2", "offer:issue:mine-1"], (
        f"the newest ask is about another decision, yet a page of two narrowed to the two named holds both of "
        f"theirs: {[r['decision_id'] for r in page]}"
    )
    assert [r["id"] for r in await asks.recent(limit=2)] == ["ask-2", "ask-1"], "unnarrowed, as before"
    assert await asks.recent(repos=[], decision_ids=[]) == [], "no repository and no decision named selects nothing"


async def _seed_decision_sources(db: DatabasePool) -> None:
    """One closed decision of each kind in acme/web, the repository covered, and one of each in globex/gizmos."""
    for repo, tag in (("acme/web", "web"), ("globex/gizmos", "gizmos")):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status) VALUES ($1, $2, 'Fix it', 'pr_merged')", f"job-{tag}", repo
        )
        await db.execute(
            "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Widgets break', $2, 'closed')",
            f"iss-{tag}",
            repo,
        )
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, title) VALUES ($1, $2, 'watcher', 'Null sender')",
            f"hc-{tag}",
            repo,
        )
        await db.execute(
            "INSERT INTO leads (id, kind, repo, source_key, title, state) VALUES ($1, 'ticket', $2, $3, 'A ticket', 'gone')",
            f"ticket:{tag}",
            repo,
            f"key-{tag}",
        )
        await db.execute(
            "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by) "
            "VALUES ($1, $2, 'finding', 'decline', 'ada')",
            f"finding:{tag}",
            repo,
        )


#: Each kind of decision, by its id in a repository's tag: a finished job, an issue, a watcher case, a lead, a finding.
KINDS = ("done:job-{}", "offer:issue:iss-{}", "offer:case:hc-{}", "offer:ticket:{}", "offer:finding:{}")


async def test_a_closed_decision_s_repository_is_read_back_from_the_row_it_came_from(
    db_with_migrations: DatabasePool,
) -> None:
    await _seed_decision_sources(db_with_migrations)
    asks = AsksRepository(db_with_migrations)
    ids = [kind.format(tag) for kind in KINDS for tag in ("web", "gizmos")] + ["offer:finding:never-answered"]
    repos = await asks.decision_repos(ids)
    assert repos == {
        kind.format(tag): repo for kind in KINDS for tag, repo in (("web", "acme/web"), ("gizmos", "globex/gizmos"))
    }, (
        f"a finished job's, an issue's, a watcher case's, a lead's and an answered finding's repository are read "
        f"back; a finding no one answered has none to read: {repos}"
    )

    for i, decision_id in enumerate(ids):
        await db_with_migrations.execute(
            "INSERT INTO asks (id, decision_id, recipient, due_at, created_at, state) "
            "VALUES ($1, $2, 'ada@example.com', $3, $3, 'answered')",
            f"ask-{i:02d}",
            decision_id,
            NOW + timedelta(minutes=i),
        )
    page = await asks.recent(limit=50, repos=["acme/web"], decision_ids=["offer:finding:never-answered"])
    assert sorted(r["decision_id"] for r in page) == sorted(
        [kind.format("web") for kind in KINDS] + ["offer:finding:never-answered"]
    ), (
        f"the asks about acme/web's decisions, closed ones included, and the one named by id: {[r['decision_id'] for r in page]}"
    )
    assert {r["decision_repo"] for r in page} == {"acme/web", None}, "each with the repository read back"
    assert len(await asks.recent(limit=3, repos=["acme/web"])) == 3, "narrowed before the limit"
