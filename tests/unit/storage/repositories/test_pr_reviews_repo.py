"""PrReviewsRepository against real Postgres (#30)."""

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.pr_reviews import PrReviewsRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

SHA1 = "1" * 40
SHA2 = "2" * 40
VERDICT = {"summary": "Looks careful.", "review_comments": []}


async def _claim(reviews: PrReviewsRepository, key: str, sha: str = SHA1, number: int = 7) -> str:
    review_id = await reviews.claim(repo="acme/widgets", number=number, head_sha=sha, trigger="ready", trigger_key=key)
    assert review_id is not None, f"claim of a fresh key {key!r} returned None"
    return review_id


async def test_a_key_is_claimed_once(db_with_migrations: DatabasePool) -> None:
    reviews = PrReviewsRepository(db_with_migrations)
    key = f"ready:acme/widgets#7@{SHA1}"

    first = await _claim(reviews, key)
    second = await reviews.claim(repo="acme/widgets", number=7, head_sha=SHA1, trigger="ready", trigger_key=key)

    assert first.startswith("prr-")
    assert second is None, "a redelivered trigger must not claim a second review"
    assert await reviews.exists(key) is True
    assert await reviews.exists(f"ready:acme/widgets#7@{SHA2}") is False


async def test_a_released_claim_can_be_taken_again(db_with_migrations: DatabasePool) -> None:
    reviews = PrReviewsRepository(db_with_migrations)
    key = "command:github:1001"
    review_id = await _claim(reviews, key)

    await reviews.release(review_id)

    assert await reviews.exists(key) is False, "release() left the claim in place"
    assert await _claim(reviews, key)


async def test_release_never_drops_a_review_that_has_a_job(db_with_migrations: DatabasePool) -> None:
    reviews = PrReviewsRepository(db_with_migrations)
    job_id = await JobsRepository(db_with_migrations).create(repo="acme/widgets", task="t", dispatched_by="octocat")
    review_id = await _claim(reviews, "command:github:1002")
    await reviews.attach_job(review_id, job_id)

    await reviews.release(review_id)

    row = await reviews.get(review_id)
    assert row is not None and row["job_id"] == job_id, "release() deleted a review a job is running"


async def test_outcome_keeps_the_verdict_and_stamps_posted_at_only_when_posted(
    db_with_migrations: DatabasePool,
) -> None:
    reviews = PrReviewsRepository(db_with_migrations)
    unposted = await _claim(reviews, "command:github:1")
    posted = await _claim(reviews, "command:github:2")

    await reviews.record_outcome(unposted, verdict=VERDICT, posted=False)
    await reviews.record_outcome(posted, verdict=VERDICT, posted=True)

    row_unposted, row_posted = await reviews.get(unposted), await reviews.get(posted)
    assert row_unposted is not None and row_posted is not None
    assert row_unposted["verdict"] == VERDICT and row_unposted["posted_at"] is None
    assert row_posted["verdict"] == VERDICT and row_posted["posted_at"] is not None


async def test_previous_reviews_are_the_posted_ones_newest_first_without_this_job(
    db_with_migrations: DatabasePool,
) -> None:
    jobs = JobsRepository(db_with_migrations)
    reviews = PrReviewsRepository(db_with_migrations)
    job_ids = [await jobs.create(repo="acme/widgets", task="t", dispatched_by="octocat") for _ in range(3)]
    for index, (job_id, sha) in enumerate(zip(job_ids, [SHA1, SHA2, SHA2], strict=True)):
        review_id = await _claim(reviews, f"command:github:{index}", sha=sha)
        await reviews.attach_job(review_id, job_id)
        if index < 2:
            await reviews.record_outcome(review_id, verdict=VERDICT, posted=True)
    await _claim(reviews, "command:github:other-pr", number=8)

    rows = await reviews.previous_reviews("acme/widgets", 7, exclude_job_id=job_ids[2])
    assert [r["job_id"] for r in rows] == [job_ids[1], job_ids[0]], "expected the posted reviews, newest first"
    assert [r["head_sha"] for r in rows] == [SHA2, SHA1]

    assert [r["job_id"] for r in await reviews.previous_reviews("acme/widgets", 7, exclude_job_id=job_ids[1])] == [
        job_ids[0]
    ], "a review must never be its own previous review"
    assert await reviews.previous_reviews("acme/widgets", 9, exclude_job_id="job-x") == []


async def test_a_purged_job_leaves_its_review_and_verdict(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    reviews = PrReviewsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t", dispatched_by="octocat")
    review_id = await _claim(reviews, "command:github:9")
    await reviews.attach_job(review_id, job_id)
    await reviews.record_outcome(review_id, verdict=VERDICT, posted=True)

    await db_with_migrations.execute("DELETE FROM jobs WHERE job_id = $1", job_id)

    rows = await reviews.previous_reviews("acme/widgets", 7, exclude_job_id="job-new")
    assert len(rows) == 1 and rows[0]["job_id"] is None and rows[0]["verdict"] == VERDICT
