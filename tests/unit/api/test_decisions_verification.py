"""A quest to review shows its job's verification, read from the stored row, on the real app over a real database (#312).

The verify node stores each job's result through ``JobVerificationsRepository.upsert``; the row carries the gate's
verdict as ``gate_verdict``, in the gate's own words. A card whose merge approval has no ``checks_state`` falls back to
that verdict for its ``Checks`` fact. It read a ``verdict`` key the row does not have, so no card ever showed one.

The app, its keys and its two repositories are :mod:`tests.fixtures.repository_writes`'; nothing reaches the network.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.job_verifications import JobVerificationsRepository
from embry0.workflows.verification.gate import GateReport, GateVerdict
from embry0.workflows.verification.result import compose_result
from embry0.workflows.verification.tiers import classify_diff, plan_browser_qa
from tests.fixtures.repository_writes import COVERED, NOW, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: Each gate verdict's job, and the Checks fact and verdict its card shows with no merge approval.
EXPECTED: dict[str, tuple[dict[str, str] | None, dict[str, str]]] = {
    "passed": ({"label": "Checks", "value": "verified", "tone": "good"}, {"label": "Needs a look", "tone": "warn"}),
    "new_failures": (
        {"label": "Checks", "value": "verification failed", "tone": "bad"},
        {"label": "Checks failing", "tone": "bad"},
    ),
    "unstable": (
        {"label": "Checks", "value": "no new failures, some unstable", "tone": "warn"},
        {"label": "Needs a look", "tone": "warn"},
    ),
    "error": (
        {"label": "Checks", "value": "could not verify", "tone": "warn"},
        {"label": "Needs a look", "tone": "warn"},
    ),
    "not_configured": (None, {"label": "Needs a look", "tone": "warn"}),
}


def _job(verdict: str) -> str:
    return f"job-verified-{verdict.replace('_', '-')}"


def _result(verdict: str) -> dict[str, Any]:
    """The result the verify node stores for a backend change whose gate decided ``verdict``."""
    tier = classify_diff(["src/app.py"])
    plan = plan_browser_qa(tier=tier.tier, needs_qa=False, managed_apps=[], deployed_apps=[])
    gate = GateReport.without_runs(cast(GateVerdict, verdict), note="tests")
    return compose_result(mode="on", tier=tier, gate=gate, plan=plan)


async def _verified_reviews(db: DatabasePool, verdicts: list[str]) -> None:
    """A finished job with a pull request and no merge approval for each verdict, its verification stored as the
    verify node stores it."""
    repo = JobVerificationsRepository(db)
    for i, verdict in enumerate(verdicts):
        finished = NOW - timedelta(minutes=10 + i)
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
            "pr_url, delivery_state, dispatched_by, total_cost_usd) "
            "VALUES ($1, $2, $3, 'completed', 'full-magnum-opus', $4, $4, $5, $6, 'pr_open', 'ada@example.com', 1)",
            _job(verdict),
            COVERED,
            f"Fix case {verdict}",
            finished - timedelta(minutes=30),
            finished,
            f"https://github.com/{COVERED}/pull/{500 + i}",
        )
        await repo.upsert(job_id=_job(verdict), repo=COVERED, result=_result(verdict))


def _checks(card: dict[str, Any]) -> dict[str, str] | None:
    return next((f for f in card["facts"] if f["label"] == "Checks"), None)


async def test_a_review_without_a_merge_approval_shows_its_stored_verification(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /decisions`` (the batched reads) and ``GET /decisions/{id}`` each show the Checks fact the stored
    ``gate_verdict`` names, and the card's verdict follows it."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _verified_reviews(db, list(EXPECTED))
        stored = await JobVerificationsRepository(db).for_jobs([_job(v) for v in EXPECTED])
        listed = await call(app, "GET", f"/api/v1/decisions?stage=done&repo={COVERED}&limit=500", keys.open)
        one = {v: await call(app, "GET", f"/api/v1/decisions/done:{_job(v)}", keys.open) for v in EXPECTED}
    assert {job: row["gate_verdict"] for job, row in stored.items()} == {_job(v): v for v in EXPECTED}, (
        f"each job's verification is stored with its gate verdict: {stored}"
    )
    assert listed.status_code == 200, listed.text
    cards = {d["id"]: d for d in listed.json()["decisions"]}
    got = {v: (_checks(cards[f"done:{_job(v)}"]), cards[f"done:{_job(v)}"]["verdict"]) for v in EXPECTED}
    assert got == EXPECTED, f"each card in the list shows the Checks fact its stored gate verdict names: {got}"
    assert {v: r.status_code for v, r in one.items()} == dict.fromkeys(EXPECTED, 200), {
        v: r.text for v, r in one.items()
    }
    alone = {v: (_checks(r.json()), r.json()["verdict"]) for v, r in one.items()}
    assert alone == EXPECTED, f"each card read alone shows the Checks fact its stored gate verdict names: {alone}"


async def test_a_passed_verification_with_a_merge_recommendation_is_ready_to_merge(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge approval that recommends merging but has no checks state of its own: the passed verification is the
    card's checks, so the card reads ``Ready to merge``; a failed one reads ``Checks failing``."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _verified_reviews(db, ["passed", "new_failures"])
        for i, verdict in enumerate(["passed", "new_failures"]):
            await db.execute(
                "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, job_id, status, ask) "
                "VALUES ($1, $2, $3, $4, 'abc', $5, 'pending', $6::jsonb)",
                f"ma-verified-{i}",
                COVERED,
                500 + i,
                f"https://github.com/{COVERED}/pull/{500 + i}",
                _job(verdict),
                {"recommendation": {"verdict": "merge"}},
            )
        resp = await call(app, "GET", f"/api/v1/decisions?stage=done&repo={COVERED}&limit=500", keys.open)
    assert resp.status_code == 200, resp.text
    cards = {d["id"]: d for d in resp.json()["decisions"]}
    got = {
        v: (_checks(cards[f"done:{_job(v)}"]), cards[f"done:{_job(v)}"]["verdict"]) for v in ("passed", "new_failures")
    }
    assert got == {
        "passed": (EXPECTED["passed"][0], {"label": "Ready to merge", "tone": "good"}),
        "new_failures": EXPECTED["new_failures"],
    }, f"a passed verification is the checks a merge recommendation needs, a failed one fails them: {got}"
