"""The asks table and the decisions' codes, on a real database (#273).

The ask loop queues every new ask of a tick in one call. However many asks
and decisions that is, it takes the same few statements, and each decision
gets the code it would get made one by one: four letters from its id,
longer when another decision holds them, never another decision's.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from embry0.services.asks import code_candidates, code_for
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from tests.fixtures.asks import CountingDb

pytestmark = pytest.mark.requires_postgres

DUE = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
#: Two decisions whose ids give the same four letters, GYWH.
FIRST, SECOND = "offer:issue:551", "offer:issue:677"


@pytest.fixture
async def repo(db_with_migrations: DatabasePool) -> AsksRepository:
    return AsksRepository(db_with_migrations)


async def test_queueing_600_asks_takes_as_many_statements_as_queueing_one(
    repo: AsksRepository, db_with_migrations: DatabasePool
) -> None:
    counting = CountingDb(db_with_migrations)
    counted = AsksRepository(counting)  # type: ignore[arg-type]
    one = await counted.queue([("offer:issue:one", "ada@example.com", DUE)])
    for_one = len(counting.statements)
    counting.statements.clear()
    many = await counted.queue((f"offer:issue:many-{i}", "ada@example.com", DUE) for i in range(600))
    assert len(one) == 1 and len(many) == 600, f"every ask was queued: {len(one)}, {len(many)}"
    assert len(counting.statements) == for_one, (
        f"600 asks about 600 new decisions took {len(counting.statements)} statements, one ask {for_one}: "
        "the same, so no round trip per decision"
    )
    assert not any(s.startswith("SELECT code FROM decision_codes") and "WHERE" not in s for s in counting.statements), (
        "no statement reads every code stored"
    )
    codes = await repo.codes(f"offer:issue:many-{i}" for i in range(600))
    assert len(codes) == 600 and len(set(codes.values())) == 600, "each decision has a code of its own"
    assert all(code in code_candidates(d) for d, code in codes.items()), "each code is letters of its decision's id"


async def test_a_decision_gets_the_code_making_them_one_by_one_would_give(
    repo: AsksRepository, db_with_migrations: DatabasePool
) -> None:
    assert code_candidates(FIRST)[0] == code_candidates(SECOND)[0] == "GYWH", "the fixture's collision"
    counting = CountingDb(db_with_migrations)
    counted = AsksRepository(counting)  # type: ignore[arg-type]
    await counted.queue(
        [("offer:issue:apart-1", "ada@example.com", DUE), ("offer:issue:apart-2", "bo@example.com", DUE)]
    )
    apart = len(counting.statements)
    counting.statements.clear()
    await counted.queue([(SECOND, "ada@example.com", DUE), (FIRST, "bo@example.com", DUE)])
    assert len(counting.statements) == apart + 1, (
        f"two decisions whose four letters collide take one statement more than two that do not ({apart}): "
        f"five letters are asked for the second, once; it may not take the four letters the first just took and "
        f"be stored, refused and made again: {len(counting.statements)}"
    )
    codes = await repo.codes([FIRST, SECOND])
    assert codes == {SECOND: "GYWH", FIRST: "GYWHW"}, (
        f"both ids give GYWH: the decision queued first takes it and the other gets one more letter: {codes}"
    )
    assert await repo.code(FIRST) == "GYWHW" and await repo.code(SECOND) == "GYWH", "a code, once made, is kept"
    assert await repo.decision_of("gywhw") == FIRST, "and names its decision"


async def test_a_code_another_decision_holds_is_never_given_again(
    repo: AsksRepository, db_with_migrations: DatabasePool
) -> None:
    await db_with_migrations.execute(
        "INSERT INTO decision_codes (decision_id, code) VALUES ($1, $2)", "done:job-other", "GYWH"
    )
    await repo.queue([(FIRST, "ada@example.com", DUE), (SECOND, "ada@example.com", DUE)])
    codes = await repo.codes([FIRST, SECOND, "done:job-other"])
    assert codes == {"done:job-other": "GYWH", FIRST: "GYWHW", SECOND: "GYWHN"}, (
        f"GYWH is another decision's, so each new decision takes its five letters: {codes}"
    )


async def test_an_ask_its_person_already_holds_live_is_not_queued_twice(repo: AsksRepository) -> None:
    [first] = await repo.queue([("offer:issue:1", "Ada@Example.com", DUE)])
    again = await repo.queue([("offer:issue:1", "ada@example.com", DUE), ("offer:issue:1", "bo@example.com", DUE)])
    live = {(a["decision_id"], a["recipient"]): a["id"] for a in await repo.live()}
    assert len(again) == 1 and live[("offer:issue:1", "bo@example.com")] == again[0], (
        f"ada holds a live ask about it, so only bo's is queued: {again}"
    )
    assert live[("offer:issue:1", "ada@example.com")] == first, "the recipient is stored lower-cased, as before"
    assert await repo.queue([]) == [], "nothing to queue sends nothing"


async def test_each_statement_names_at_most_one_candidate_per_decision(db_with_migrations: DatabasePool) -> None:
    """A decision could take any of some forty prefixes of its letters; asking all of them at once for 20,000 new
    decisions sent some 790,000 strings in one statement. Each length is asked in turn instead."""
    counting = CountingDb(db_with_migrations)
    ids = [f"offer:issue:batch-{i}" for i in range(1_000)]
    await AsksRepository(counting).make_codes(ids)  # type: ignore[arg-type]
    asked = [
        args[0]
        for statement, args in zip(counting.statements, counting.arguments, strict=True)
        if statement.startswith("SELECT code FROM decision_codes WHERE code = ANY")
    ]
    assert asked, "the codes taken were asked"
    assert max(len(candidates) for candidates in asked) <= len(ids), (
        f"a statement named {max(len(c) for c in asked)} candidates for {len(ids)} decisions"
    )


async def test_codes_made_in_one_batch_are_those_made_one_by_one(db_with_migrations: DatabasePool) -> None:
    """Against the rule ``code`` always followed: each decision, in order, takes the shortest prefix of its
    letters that no stored code and no decision before it holds. Stored codes force five and six letters."""
    rng = random.Random(2026)
    ids = [f"offer:issue:{rng.getrandbits(40):x}" for _ in range(1_500)] + [SECOND, FIRST]
    rng.shuffle(ids)
    stored: dict[str, str] = {}
    for i, decision_id in enumerate(rng.sample(ids, 200)):
        for size in (0, 1) if i % 2 else (0,):
            code = code_candidates(decision_id)[size]
            if code not in stored.values():
                stored[f"done:held-{i}-{size}"] = code
    for decision_id, code in stored.items():
        await db_with_migrations.execute(
            "INSERT INTO decision_codes (decision_id, code) VALUES ($1, $2)", decision_id, code
        )
    taken = set(stored.values())
    expected: dict[str, str] = {}
    for decision_id in ids:
        expected[decision_id] = code_for(decision_id, taken)
        taken.add(expected[decision_id])

    made = await AsksRepository(db_with_migrations).make_codes(ids)
    assert {len(c) for c in made.values()} >= {4, 5, 6}, "the stored codes forced longer codes"
    differ = {d: (expected[d], made[d]) for d in ids if made[d] != expected[d]}
    assert not differ, f"codes that differ from making them one by one: {list(differ.items())[:5]}"


async def test_decisions_still_waiting_keep_the_order_given_from_one_length_to_the_next(
    db_with_migrations: DatabasePool,
) -> None:
    """Two decisions whose four letters are both taken wait for five; both ids give the same five letters too.
    Given in an order that is not their ids' order, the first given must take the five letters, as one by one."""
    a, b = "offer:issue:969", "offer:issue:1900"
    assert code_candidates(a)[:2] == code_candidates(b)[:2] and a > b, "the fixture: same four and five letters"
    await db_with_migrations.execute(
        "INSERT INTO decision_codes (decision_id, code) VALUES ('done:other', $1)", code_candidates(a)[0]
    )
    taken = {code_candidates(a)[0]}
    one_by_one = {}
    for decision_id in (a, b):
        one_by_one[decision_id] = code_for(decision_id, taken)
        taken.add(one_by_one[decision_id])
    await AsksRepository(db_with_migrations).queue([(a, "ada@example.com", DUE), (b, "bo@example.com", DUE)])
    made = await AsksRepository(db_with_migrations).codes([a, b])
    assert made == one_by_one, f"made one by one: {one_by_one}; in one batch: {made}"
