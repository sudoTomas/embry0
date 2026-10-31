"""The Slop-O-Meter forgets everything it kept of one repository, and nothing of another (#165), on Postgres.

What it keeps of a repository holds personal data: its commit authors' names
and emails, co-author trailers, and the forge logins of everyone who
commented or reviewed. When the repository leaves the meter's scope it all
goes, and a login goes once no event of another repository refers to it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from embry0.services.slop_meter.history import Commit
from embry0.services.slop_meter.score import Meter, Week
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.slop_meter import SlopMeterRepository

pytestmark = [pytest.mark.requires_postgres]

AT = datetime(2026, 1, 5, 9, tzinfo=UTC)
TABLES = (
    "slop_meter_repos",
    "slop_meter_commits",
    "slop_meter_events",
    "slop_meter_judgments",
    "slop_meter_weeks",
    "slop_meter_scores",
)


async def _keep(store: SlopMeterRepository, repo: str, logins: tuple[str, ...]) -> None:
    """Everything the meter keeps of ``repo``, its events by ``logins``."""
    commit = Commit(
        "a1", AT, "Ana Example", "ana@example.com", "Work", (("src/a.py", 10, 2),), (("Bo", "bo@example.com"),)
    )
    await store.set_state(repo, mirrored_at=AT)
    await store.add_commits(repo, 0, [commit.to_row()])
    await store.add_events(repo, [(f"ic:{n}", "comment", login, AT) for n, login in enumerate(logins)])
    for login in logins:
        await store.set_login(login, f"{login.lower()}@example.com")
    await store.judge(repo, "a1", slop=False, reason="moved", model="custom/m")
    week = Week(date(2026, 1, 5), 100.0, 60.0, 40.0, 8.0, 0.4)
    await store.store(repo, {"": Meter(0.4, 3, "Lasagna", False, [week], 1)}, AT)


async def _rows(db: DatabasePool, repo: str) -> dict[str, int]:
    return {table: int(await db.fetchval(f"SELECT count(*) FROM {table} WHERE repo = $1", repo)) for table in TABLES}


async def test_forgetting_a_repository_removes_its_rows_and_its_contributors_and_no_one_else_s(
    db_with_migrations: DatabasePool,
) -> None:
    store = SlopMeterRepository(db_with_migrations)
    forget = getattr(store, "forget", None)
    held = getattr(store, "held", None)
    assert forget is not None and held is not None, (
        "the meter's store lists the repositories it keeps anything for, and forgets one of them whole"
    )
    await _keep(store, "acme/widgets", ("Ana", "Carol"))
    await _keep(store, "other/kit", ("ANA",))
    assert await held() == ["acme/widgets", "other/kit"]

    counts = await forget("acme/widgets")

    assert await _rows(db_with_migrations, "acme/widgets") == dict.fromkeys(TABLES, 0), (
        "every row the meter kept of acme/widgets is gone: its history with its authors' names and emails, its "
        "events, judgments, series, scores and state"
    )
    assert all(n > 0 for n in (await _rows(db_with_migrations, "other/kit")).values()), (
        "nothing of other/kit is touched"
    )
    assert await store.logins() == {"ana": "ana@example.com"}, (
        "carol's login was referred to only by acme/widgets's events, so it goes; ana's stays, since an event of "
        "other/kit still names her (compared without regard to case)"
    )
    assert counts["commits"] == 1 and counts["events"] == 2 and counts["logins"] == 1, counts
    assert await held() == ["other/kit"]
    assert await forget("acme/widgets") == dict.fromkeys(
        ("commits", "events", "judgments", "weeks", "scores", "state", "logins"), 0
    ), "forgetting what is already gone removes nothing"


async def test_rows_kept_under_a_case_variant_move_onto_the_registry_s_spelling_in_one_piece(
    db_with_migrations: DatabasePool,
) -> None:
    store = SlopMeterRepository(db_with_migrations)
    respell = getattr(store, "respell", None)
    assert respell is not None, "the meter's store moves what it keeps under a case variant onto one spelling"
    await _keep(store, "Acme/Widgets", ("Ana",))
    assert await respell("Acme/Widgets", "acme/widgets") == "moved"
    assert await _rows(db_with_migrations, "Acme/Widgets") == dict.fromkeys(TABLES, 0), (
        "nothing is left under the old spelling"
    )
    assert all(n > 0 for n in (await _rows(db_with_migrations, "acme/widgets")).values()), (
        "every table's rows are under the registry's spelling, so the history is not read again"
    )
    assert [r["repo"] for r in await store.scores("acme/widgets")] == ["acme/widgets"], (
        "the repository's score reads under its registry spelling"
    )
    assert await store.scores("Acme/Widgets") == []
    assert await store.held() == ["acme/widgets"]
    assert await respell("Acme/Widgets", "acme/widgets") == "", "a spelling that holds nothing moves nothing"


async def test_a_case_variant_beside_a_reading_under_the_registry_s_spelling_is_dropped_not_kept_twice(
    db_with_migrations: DatabasePool,
) -> None:
    store = SlopMeterRepository(db_with_migrations)
    respell = getattr(store, "respell", None)
    assert respell is not None, "the meter's store moves what it keeps under a case variant onto one spelling"
    await _keep(store, "acme/widgets", ("Ana",))
    await _keep(store, "ACME/WIDGETS", ("Carol",))
    assert await respell("ACME/WIDGETS", "acme/widgets") == "merged"
    assert await _rows(db_with_migrations, "ACME/WIDGETS") == dict.fromkeys(TABLES, 0), (
        "the variant's copy of the contributors goes"
    )
    assert all(n > 0 for n in (await _rows(db_with_migrations, "acme/widgets")).values()), (
        "the reading under the registry's spelling is kept"
    )
    assert await store.logins() == {"ana": "ana@example.com"}, "with the login only the variant's events named"
    assert [r["repo"] for r in await store.scores()] == ["acme/widgets"], "one score, not two"
    with pytest.raises(ValueError):
        await respell("acme/widgets", "other/kit")
