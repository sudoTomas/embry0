"""Setup proposals: the test setups the onboarding agent drafts in propose mode (ADR 0019).

A proposal is running while its job runs, then pending (a draft for a person
to review) or failed. A person applies or rejects a pending one, and a
pending one goes stale when the test setup changed under it. A repository
has at most one running or pending proposal.
"""

import re
from datetime import UTC, datetime

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_setup_proposals import LiveProposalExists, RepoSetupProposalsRepository

pytestmark = pytest.mark.requires_postgres

REPO = "acme/widgets"
OTHER = "acme/gadgets"
PERSON = "dev@example.com"
SETUP = "version: 2\nqa_required: never\n"


@pytest.fixture
def proposals(db_with_migrations: DatabasePool) -> RepoSetupProposalsRepository:
    return RepoSetupProposalsRepository(db_with_migrations)


async def _pending(proposals: RepoSetupProposalsRepository, repo: str = REPO) -> str:
    proposal_id = await proposals.create(repo, base_updated_at=None, created_by=PERSON)
    assert await proposals.finish(proposal_id, status="pending", proposed_yaml=SETUP)
    return proposal_id


async def test_a_new_proposal_is_running_and_keeps_its_base_and_who_started_it(proposals) -> None:
    base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    proposal_id = await proposals.create(REPO, base_updated_at=base, created_by=PERSON)

    assert re.fullmatch(r"prop-[0-9a-f]{12}", proposal_id), proposal_id
    row = await proposals.get(proposal_id)
    assert row is not None
    assert (row["repo"], row["status"], row["base_updated_at"], row["created_by"]) == (REPO, "running", base, PERSON)
    assert (row["job_id"], row["proposed_yaml"], row["notes_md"], row["smoke"], row["failure"]) == (
        None,
        None,
        "",
        None,
        "",
    )


async def test_a_second_run_is_refused_while_one_is_running_or_waiting(proposals) -> None:
    first = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    with pytest.raises(LiveProposalExists):
        await proposals.create(REPO, base_updated_at=None, created_by=PERSON)

    await proposals.finish(first, status="pending", proposed_yaml=SETUP)
    with pytest.raises(LiveProposalExists):
        await proposals.create(REPO, base_updated_at=None, created_by=PERSON)

    assert await proposals.create(OTHER, base_updated_at=None, created_by=PERSON), "another repository is free"


async def test_a_failed_run_frees_the_repository(proposals) -> None:
    first = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    await proposals.finish(first, status="failed", failure="no valid config after 3 analysis rounds")

    second = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)

    assert second != first
    latest = await proposals.latest(REPO)
    assert latest is not None and latest["id"] == second


async def test_the_job_is_attached_after_the_start(proposals) -> None:
    proposal_id = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    await proposals.attach_job(proposal_id, "job-1")
    row = await proposals.get(proposal_id)
    assert row is not None and row["job_id"] == "job-1"


async def test_a_completed_run_leaves_a_pending_draft(proposals) -> None:
    proposal_id = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    smoke = {"results": {"web": "ready"}, "failures": []}

    stored = await proposals.finish(
        proposal_id, status="pending", proposed_yaml=SETUP, notes_md="Found one web app.", smoke=smoke
    )

    assert stored is True
    row = await proposals.get(proposal_id)
    assert row is not None
    assert (row["status"], row["proposed_yaml"], row["notes_md"], row["smoke"], row["failure"]) == (
        "pending",
        SETUP,
        "Found one web app.",
        smoke,
        "",
    )
    assert row["updated_at"] >= row["created_at"]


async def test_a_run_finishes_once(proposals) -> None:
    """Only a running proposal finishes, so a late writer cannot overwrite the outcome."""
    proposal_id = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    assert await proposals.finish(proposal_id, status="failed", failure="onboard workflow crashed") is True

    assert await proposals.finish(proposal_id, status="pending", proposed_yaml=SETUP) is False

    row = await proposals.get(proposal_id)
    assert row is not None
    assert (row["status"], row["proposed_yaml"], row["failure"]) == ("failed", None, "onboard workflow crashed")
    assert await proposals.finish("prop-000000000000", status="failed") is False


async def test_a_decision_is_taken_once_and_only_on_a_pending_proposal(proposals) -> None:
    proposal_id = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    assert await proposals.decide(proposal_id, status="applied", decided_by=PERSON) is False, "still running"

    await proposals.finish(proposal_id, status="pending", proposed_yaml=SETUP)
    assert await proposals.decide(proposal_id, status="applied", decided_by=PERSON) is True
    assert await proposals.decide(proposal_id, status="rejected", decided_by="ops@example.com") is False

    row = await proposals.get(proposal_id)
    assert row is not None
    assert (row["status"], row["decided_by"]) == ("applied", PERSON)
    assert row["decided_at"] is not None


async def test_stale_marks_only_this_repositorys_pending_proposal(proposals) -> None:
    waiting = await _pending(proposals)
    elsewhere = await _pending(proposals, OTHER)

    assert await proposals.mark_stale_pending(REPO) == 1

    running = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    assert await proposals.mark_stale_pending(REPO) == 0, "a running proposal is not stale"
    statuses = {pid: (await proposals.get(pid) or {}).get("status") for pid in (waiting, elsewhere, running)}
    assert statuses == {waiting: "stale", elsewhere: "pending", running: "running"}


async def test_list_pending_lists_only_pending_proposals_newest_first(proposals) -> None:
    older = await _pending(proposals)
    newer = await _pending(proposals, OTHER)
    await proposals.create("acme/tools", base_updated_at=None, created_by=PERSON)

    assert [row["id"] for row in await proposals.list_pending()] == [newer, older]


async def test_latest_is_the_newest_and_none_without_any(proposals) -> None:
    assert await proposals.latest(REPO) is None
    assert await proposals.get("prop-000000000000") is None

    first = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    await proposals.finish(first, status="failed", failure="smoke failed")
    second = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)

    latest = await proposals.latest(REPO)
    assert latest is not None and latest["id"] == second


async def test_delete_removes_a_proposal_whose_start_failed(proposals) -> None:
    proposal_id = await proposals.create(REPO, base_updated_at=None, created_by=PERSON)
    await proposals.delete(proposal_id)
    assert await proposals.get(proposal_id) is None
    assert await proposals.create(REPO, base_updated_at=None, created_by=PERSON), "the repository is free again"
