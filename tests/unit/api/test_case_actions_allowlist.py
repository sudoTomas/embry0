"""Acting on a case the key does not read answers as acting on a case that does not exist.

On the real app over a real database (:mod:`tests.fixtures.case_reads`). A
key with a repository allowlist accepting or closing another repository's
case, or a case filed on an owner or on no repository, gets the missing
case's answer byte for byte, changes nothing, starts nothing, and is logged
and audited once under the allowlist's actor, naming the case's repository,
its owner's ``<owner>/*`` or ``(no repository)``. The allowlist is checked
before anything else a case answers: its stage (a closed case's 409), its
origin (an organization need's case takes the admin tier), the body and the
run it asks for. A key without a list reaches each case, and the limited key
its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.case_reads import CASE, NO_REPOSITORY, OWNER_CASE, STARTED, case_reads_app, missing_like
from tests.fixtures.issue_reads import same_answer
from tests.fixtures.repository_writes import COVERED, OWNER, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

#: Each case the limited key does not read, and what its refusal's audit row names.
HIDDEN = {
    CASE[UNCOVERED]: UNCOVERED,
    OWNER_CASE[OWNER[COVERED]]: f"{OWNER[COVERED]}/*",
    OWNER_CASE[OWNER[UNCOVERED]]: f"{OWNER[UNCOVERED]}/*",
    NO_REPOSITORY: "(no repository)",
}
#: Each action on one case: its path and body.
ACTIONS = {
    "accept": ("/api/v1/cases/{}/accept", {"accepted_by": "ada"}),
    "close": ("/api/v1/cases/{}/close", {"resolution_path": "abandoned", "closed_by": "ada"}),
}


async def _state(db: DatabasePool) -> dict[str, Any]:
    """What an action could change: every case's stage and resolution, and how many jobs and issues there are."""
    cases = await db.fetch("SELECT id, stage, resolution_path, updated_at FROM healing_cases ORDER BY id")
    return {
        "cases": [tuple(r) for r in cases],
        "jobs": await db.fetchval("SELECT COUNT(*) FROM jobs"),
        "issues": await db.fetchval("SELECT COUNT(*) FROM issues"),
        "events": await db.fetchval("SELECT COUNT(*) FROM healing_case_events"),
    }


async def _refusals(db: DatabasePool) -> list[tuple[str, str, str]]:
    rows = await db.fetch(
        "SELECT actor, details->>'repo' AS repo, details->>'key_name' AS key FROM audit_log "
        "WHERE action = 'apikey.repo_refused' ORDER BY id"
    )
    return [(str(r["actor"]), str(r["repo"]), str(r["key"])) for r in rows]


@pytest.mark.parametrize("action", list(ACTIONS))
@pytest.mark.parametrize("hidden", list(HIDDEN))
async def test_acting_on_a_case_the_key_does_not_read_answers_as_on_a_missing_one(
    hidden: str, action: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    path, body = ACTIONS[action]
    missing_id = missing_like(hidden)
    async with case_reads_app(db, monkeypatch) as (app, keys, _values):
        before = await _state(db)
        refused = await call(app, "POST", path.format(hidden), keys.limited, body)
        missing = await call(app, "POST", path.format(missing_id), keys.limited, body)
        after = await _state(db)
        audited = await _refusals(db)
        reached = await call(app, "POST", path.format(hidden), keys.open, body)
    differ = same_answer(refused, {hidden: "<case>"}, missing, {missing_id: "<case>"})
    assert differ is None and refused.status_code == 404, (
        f"{action} {hidden}: the limited key's answer is a missing case's: {differ}"
    )
    assert after == before, f"{action} {hidden}: the refused call changed nothing: {before} -> {after}"
    assert audited == [(REPO_ALLOWLIST, HIDDEN[hidden], "limited")], (
        f"{action} {hidden}: the refusal is audited once, under the allowlist's actor, naming {HIDDEN[hidden]}: "
        f"{audited}"
    )
    assert same_answer(reached, {hidden: "<case>"}, missing, {missing_id: "<case>"}) is not None, (
        f"{action} {hidden}: a key without a list reaches the case, so the check measures something: "
        f"{reached.status_code} {reached.text}"
    )


@pytest.mark.parametrize("action", list(ACTIONS))
async def test_the_key_acts_on_its_own_case(
    action: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its own finding, sent back for a retry on its issue, is dispatched again; closing it closes it."""
    path, body = ACTIONS[action]
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        own = await call(app, "POST", path.format(CASE[COVERED]), keys.limited, body)
        audited = await _refusals(db_with_migrations)
    expected = {"accept": (202, "job_id", STARTED), "close": (200, "stage", "closed")}[action]
    assert (own.status_code, own.json().get(expected[1])) == (expected[0], expected[2]), own.text
    assert audited == [], audited


#: Close bodies that a case the key reads would refuse after the allowlist check: an unknown path (422), none on a case
#: with none of its own (422), and a valid one on a case already closed (409).
CLOSE_BODIES = {
    "valid": {"resolution_path": "abandoned", "closed_by": "ada"},
    "unknown path": {"resolution_path": "no-such-path", "closed_by": "ada"},
    "no path": {"closed_by": "ada"},
}


@pytest.mark.parametrize("body", list(CLOSE_BODIES))
@pytest.mark.parametrize("hidden", list(HIDDEN))
async def test_closing_a_closed_case_the_key_does_not_read_answers_as_a_missing_one(
    hidden: str, body: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist is checked before the case's stage and the body are: a closed case answers 404, not 409."""
    db = db_with_migrations
    missing_id = missing_like(hidden)
    async with case_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute(
            "UPDATE healing_cases SET stage = 'closed', closed_at = now(), resolution_path = 'cancelled' WHERE id = $1",
            hidden,
        )
        refused = await call(app, "POST", f"/api/v1/cases/{hidden}/close", keys.limited, CLOSE_BODIES[body])
        missing = await call(app, "POST", f"/api/v1/cases/{missing_id}/close", keys.limited, CLOSE_BODIES[body])
        reached = await call(app, "POST", f"/api/v1/cases/{hidden}/close", keys.open, CLOSE_BODIES[body])
    differ = same_answer(refused, {hidden: "<case>"}, missing, {missing_id: "<case>"})
    assert differ is None and refused.status_code == 404, f"closed {hidden}, {body}: a missing case's answer: {differ}"
    assert reached.status_code in (409, 422), f"a key without a list is told why, so this measures something: {reached}"


@pytest.mark.parametrize("owner", list(OWNER.values()))
async def test_a_key_below_admin_closing_an_owner_s_case_gets_a_missing_case_s_answer(
    owner: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing an organization need's case takes the admin tier; the allowlist is checked first, so an operate key with
    a list is answered 404 as on a missing case, not 403 for its tier."""
    db = db_with_migrations
    hidden = OWNER_CASE[owner]
    missing_id = missing_like(hidden)
    body = CLOSE_BODIES["valid"]
    async with case_reads_app(db, monkeypatch) as (app, _keys, _values):
        _row, bot = await ApiKeysRepository(db).create(
            name="operate-bot", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        _row, wide = await ApiKeysRepository(db).create(
            name="operate-wide", scope="operate", user_id=None, created_by="tests"
        )
        bust_api_key_cache()
        before = await _state(db)
        refused = await call(app, "POST", f"/api/v1/cases/{hidden}/close", bot, body)
        missing = await call(app, "POST", f"/api/v1/cases/{missing_id}/close", bot, body)
        after = await _state(db)
        tier = await call(app, "POST", f"/api/v1/cases/{hidden}/close", wide, body)
    differ = same_answer(refused, {hidden: "<case>"}, missing, {missing_id: "<case>"})
    assert differ is None and refused.status_code == 404, f"{hidden}: the operate key gets a missing case's: {differ}"
    assert after == before, f"{hidden}: the refused close changed nothing"
    assert tier.status_code == 403, (
        f"an operate key without a list is refused for its tier, so this measures it: {tier}"
    )


@pytest.mark.parametrize("hidden", list(HIDDEN))
async def test_accepting_a_case_the_key_does_not_read_is_checked_before_its_run_with(
    hidden: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with a key nobody stored is refused for a case the key reads (422); one it does not read answers 404 first."""
    body = {"accepted_by": "ada", "run_with": {"credential_id": "cred-000000000009"}}
    missing_id = missing_like(hidden)
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        refused = await call(app, "POST", f"/api/v1/cases/{hidden}/accept", keys.limited, body)
        missing = await call(app, "POST", f"/api/v1/cases/{missing_id}/accept", keys.limited, body)
        own = await call(app, "POST", f"/api/v1/cases/{CASE[COVERED]}/accept", keys.limited, body)
    differ = same_answer(refused, {hidden: "<case>"}, missing, {missing_id: "<case>"})
    assert differ is None and refused.status_code == 404, f"{hidden}: a missing case's answer: {differ}"
    assert 400 <= own.status_code < 500 and own.status_code != 404, (
        f"the key's own case refuses the run, so this measures something: {own.status_code} {own.text}"
    )
