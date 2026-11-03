"""The proposal-cap predicate over the ledger (#278, for #283).

``count_open_unaccepted_watcher_cases`` is THE rule the watcher's draft cap
must use once it moves onto the ledger. Every clause below is one way the
cap was, or could be, pinned by something that is not an open watcher draft.
"""

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.healing.lifecycle import OPEN_UNACCEPTED_STAGES
from embry0.services.healing.service import HealingCaseService, TrackerRef
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.healing_cases import CAP_STAGES, HealingCasesRepository
from embry0.storage.repositories.watcher_runs import WatcherRunsRepository

pytestmark = pytest.mark.requires_postgres

REPO = "acme/widgets"


def test_the_cap_stages_are_the_lifecycles_open_unaccepted_ones() -> None:
    assert CAP_STAGES == tuple(stage.value for stage in OPEN_UNACCEPTED_STAGES)


async def _draft(db: DatabasePool, service: HealingCaseService, key: str, **case: object) -> tuple[str, str]:
    now = datetime.now(UTC)
    run_id = await WatcherRunsRepository(db).create(
        window_start=now - timedelta(hours=1), window_end=now, action="proposed", tracker="linear", tracker_key=key
    )
    case.setdefault("origin", "watcher")
    opened = await service.open_case(
        repo=REPO,
        title=key,
        watcher_run_id=run_id,
        tracker=TrackerRef("linear", key),
        **case,  # type: ignore[arg-type]
    )
    return run_id, opened["id"]


async def test_only_live_unaccepted_watcher_drafts_hold_a_slot(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    repo = HealingCasesRepository(db)
    service = HealingCaseService(repo)

    await _draft(db, service, "ENG-1")  # counts
    await _draft(db, service, "ENG-2")  # counts

    # Accepted: the draft was dispatched.
    _, accepted = await _draft(db, service, "ENG-3")
    issue_id = "iss-1"
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, 't', $2)", issue_id, REPO)
    await service.attach_issue(accepted, issue_id)
    # Closed.
    _, closed = await _draft(db, service, "ENG-4")
    await service.close(closed, resolution_path="cancelled")
    # Released by reconcile: dead in Linear, whatever the ledger still says.
    released_run, _ = await _draft(db, service, "ENG-5")
    await WatcherRunsRepository(db).release_ticket("linear", "ENG-5")
    # Aged out of the window.
    await _draft(db, service, "ENG-6", at=datetime.now(UTC) - timedelta(days=30))
    # Not the watcher's: a human's ticket must never eat a watcher slot.
    await service.open_case(repo=REPO, origin="human", title="h", tracker=TrackerRef("linear", "ENG-7"))
    await service.open_case(repo=REPO, origin="webhook", title="w", tracker=TrackerRef("github", "12"))
    # No tracker: an alert/ci/watcher finding that never became a draft.
    await service.open_case(repo=REPO, origin="alert", title="a")
    await service.open_case(repo=REPO, origin="watcher", title="no draft filed")

    assert await repo.count_open_unaccepted_watcher_cases(days=7) == 2
    assert sorted(await repo.open_unaccepted_watcher_tracker_keys(days=7)) == ["ENG-1", "ENG-2"]
    # The window is the caller's (WATCHER_PROPOSAL_WINDOW_DAYS).
    assert await repo.count_open_unaccepted_watcher_cases(days=60) == 3
    assert released_run


async def test_the_cap_can_be_scoped_to_a_repo(db_with_migrations: DatabasePool) -> None:
    repo = HealingCasesRepository(db_with_migrations)
    service = HealingCaseService(repo)
    await _draft(db_with_migrations, service, "ENG-1")
    await service.open_case(repo="other/repo", origin="watcher", title="x", tracker=TrackerRef("linear", "ENG-9"))
    assert await repo.count_open_unaccepted_watcher_cases(days=7) == 2
    assert await repo.count_open_unaccepted_watcher_cases(days=7, repo=REPO) == 1


async def test_find_by_tracker_tells_two_repos_apart(db_with_migrations: DatabasePool) -> None:
    """GitHub issue numbers repeat across repos: #42 exists in every repo with
    42 issues, so a lookup without the repo could return anyone's case (#378)."""
    repo = HealingCasesRepository(db_with_migrations)
    service = HealingCaseService(repo)
    mine = await service.open_case(repo=REPO, origin="watcher", title="mine", tracker=TrackerRef("github", "42"))
    theirs = await service.open_case(
        repo="other/repo", origin="watcher", title="theirs", tracker=TrackerRef("github", "42")
    )

    assert (await repo.find_by_tracker("github", "42", REPO))["id"] == mine["id"]
    assert (await repo.find_by_tracker("github", "42", "other/repo"))["id"] == theirs["id"]
    # Linear identifiers are globally unique, so its callers may still omit it.
    assert (await repo.find_by_tracker("github", "42"))["id"] in {mine["id"], theirs["id"]}


async def test_accepting_a_draft_releases_its_cap_slot(db_with_migrations: DatabasePool) -> None:
    """The cap is drafts awaiting a human. Acceptance is the answer (#415)."""
    db = db_with_migrations
    service = HealingCaseService(HealingCasesRepository(db))
    run_id, case_id = await _draft(db, service, "ENG-30")
    issue_id = "iss-accept-slot"
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, 't', $2)", issue_id, REPO)

    await service.attach_issue(case_id, issue_id)

    released = await db.fetchval("SELECT released_at FROM watcher_runs WHERE id = $1", run_id)
    assert released is not None, (
        "accepting a draft must stamp watcher_runs.released_at; "
        "the slot is a draft awaiting a human, and acceptance is the answer (#415)"
    )


async def test_closing_a_case_does_not_release_its_cap_slot(db_with_migrations: DatabasePool) -> None:
    """Close is not acceptance. A dead draft is released by reconcile, not by close (#415)."""
    db = db_with_migrations
    service = HealingCaseService(HealingCasesRepository(db))
    run_id, case_id = await _draft(db, service, "ENG-31")

    await service.close(case_id, resolution_path="cancelled")

    released = await db.fetchval("SELECT released_at FROM watcher_runs WHERE id = $1", run_id)
    assert released is None, "closing a case must not stamp released_at; that slot ends on acceptance or reconcile"
