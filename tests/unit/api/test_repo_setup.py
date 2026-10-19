"""Start, review, apply and turn down a setup proposal (ADR 0019).

Driven through the real app (``create_app``) on a real database, with API
keys so the actor and the tier are the ones production sees. The executor is
a fake that creates the job row the way the real one does and starts nothing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.bootstrap import render_starter_qa_yaml
from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.budget_config import BudgetConfigRepository
from embry0.storage.repositories.environment import EnvironmentRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.provider_config import ProviderConfigRepository
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository
from embry0.storage.repositories.repo_setup_proposals import RepoSetupProposalsRepository
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles

REPO = "acme/widgets"
SETUP = f"/api/v1/repos/{REPO}/setup"
QA = f"/api/v1/repos/{REPO}/qa-config"
STALE = "Someone changed the test setup after embry0 made this proposal. Run embry0 again for a fresh one."
MEDIUM = "claude-sonnet-5"

V1 = "version: 2\nqa_required: never\nworkspace_provider:\n  type: static-apps\n"
# A setup the free check passes: a browser machine, an app on the test computer.
GOOD = render_starter_qa_yaml(REPO, app="web")
# Passes too, but tests a real site, which a person must confirm.
LIVE = GOOD.replace('"http://localhost:3000"', '"https://www.example.com"')
# Every run would stop: slim has no browser.
NO_BROWSER = GOOD.replace("sandbox_profile: qa-external", "sandbox_profile: slim")

OPERATOR = "emb_" + "o" * 43
READER = "emb_" + "r" * 43
ELSEWHERE = "emb_" + "e" * 43


def _key(key_id: str, scope: str, user_id: str | None, allowed: tuple[str, ...] | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=user_id,
        name=f"{scope}-key",
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(OPERATOR): _key("key-operator0001", "operate", "usr-dev"),
    hash_token(READER): _key("key-reader000001", "read", None),
    hash_token(ELSEWHERE): _key("key-elsewhere001", "operate", None, ("acme/other",)),
}


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


class FakeExecutor:
    """Creates the onboard job row as IssueExecutor does, and runs nothing."""

    def __init__(self, jobs: JobsRepository) -> None:
        self._jobs = jobs
        self.calls: list[dict[str, Any]] = []
        self.refuse: str | None = None

    async def start_onboard_job(self, repo: str, branch: str, **kwargs: Any) -> str:
        self.calls.append({"repo": repo, "branch": branch, **kwargs})
        if self.refuse:
            raise ValueError(self.refuse)
        return await self._jobs.create(repo=repo, task="onboard", pipeline_template="onboard")


def _paid_from(label: str | None) -> Any:
    resolver = MagicMock()
    if label is None:
        resolver.resolve_llm = AsyncMock(side_effect=RuntimeError("no account"))
    else:
        resolver.resolve_llm = AsyncMock(return_value=MagicMock(label=label))
    return resolver


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> Any:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    app = create_app(config)
    db = db_with_migrations
    state = app.state
    state.db = db
    state.qa_configs_repo = RepoQAConfigsRepository(db)
    state.repo_preferences_repo = RepoPreferencesRepository(db)
    state.repo_config_revisions_repo = RepoConfigRevisionsRepository(db)
    state.repo_setup_proposals_repo = RepoSetupProposalsRepository(db)
    state.jobs_repo = JobsRepository(db)
    state.env_repo = EnvironmentRepository(db)
    state.repos_repo = ReposRepository(db, default_forge="github")
    state.org_repos_repo = OrgReposRepository(db)
    state.budget_repo = BudgetConfigRepository(db)
    state.provider_repo = ProviderConfigRepository(db)
    state.profiles_repo = SandboxProfilesRepository(db)
    await seed_builtin_sandbox_profiles(state.profiles_repo)
    await state.provider_repo.update(model_medium=MEDIUM)
    state.credential_resolver = _paid_from("team AI account")
    state.issue_executor = FakeExecutor(state.jobs_repo)
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="dev@example.com")
    users = MagicMock()
    users.get = AsyncMock(side_effect=lambda uid: user if uid == "usr-dev" else None)
    state.users_repo = users
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(OPERATOR)) as c:
        yield c


async def _proposal(
    app: Any,
    yaml_text: str | None = GOOD,
    *,
    repo: str = REPO,
    base_updated_at: datetime | None = None,
    status: str = "pending",
) -> str:
    """A proposal as a finished propose-mode run leaves it."""
    proposals = app.state.repo_setup_proposals_repo
    proposal_id = await proposals.create(repo, base_updated_at=base_updated_at, created_by="dev@example.com")
    job_id = await app.state.jobs_repo.create(repo=repo, task="onboard", pipeline_template="onboard")
    await proposals.attach_job(proposal_id, job_id)
    if status != "running":
        await proposals.finish(
            proposal_id,
            status=status,
            proposed_yaml=yaml_text,
            notes_md="Started web with npm run dev.",
            smoke={"results": [{"app": "web", "ok": True}]},
        )
    return proposal_id


async def _job_ended(db: DatabasePool, job_id: str, status: str, error: str | None = None) -> None:
    await db.execute(
        "UPDATE jobs SET status = $2, error_message = $3, finished_at = NOW() WHERE job_id = $1", job_id, status, error
    )


async def _audit(db: DatabasePool, action: str) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT actor, details FROM audit_log WHERE action = $1 ORDER BY id", action)
    return [dict(r) for r in rows]


# -- start ---------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_propose_needs_the_cost_confirmed_first(client, app) -> None:
    for body in ({}, {"confirm_spend": False}):
        resp = await client.post(f"{SETUP}/propose", json=body)
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Confirm the cost first: send confirm_spend true."
    assert app.state.issue_executor.calls == []
    assert await app.state.repo_setup_proposals_repo.latest(REPO) is None


@pytest.mark.requires_postgres
async def test_propose_starts_one_onboard_run_in_propose_mode(client, app) -> None:
    resp = await client.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"proposal_id", "job_id"}

    [call] = app.state.issue_executor.calls
    assert call["repo"] == REPO and call["branch"] == "main"
    assert call["proposal_id"] == body["proposal_id"]
    assert call["agent_models"] == {"onboarding": MEDIUM}
    assert call["dispatched_by"] == "dev@example.com"

    row = await app.state.repo_setup_proposals_repo.get(body["proposal_id"])
    assert row is not None
    assert (row["status"], row["job_id"], row["created_by"]) == ("running", body["job_id"], "dev@example.com")
    assert row["base_updated_at"] is None


@pytest.mark.requires_postgres
async def test_propose_keeps_the_time_the_setup_was_last_saved(client, app) -> None:
    await client.put(QA, content=V1)
    saved = await app.state.qa_configs_repo.get(REPO)
    proposal_id = (await client.post(f"{SETUP}/propose", json={"confirm_spend": True})).json()["proposal_id"]
    row = await app.state.repo_setup_proposals_repo.get(proposal_id)
    assert row["base_updated_at"] == saved["updated_at"]


@pytest.mark.requires_postgres
async def test_a_second_propose_while_one_runs_is_refused(client, app) -> None:
    assert (await client.post(f"{SETUP}/propose", json={"confirm_spend": True})).status_code == 200
    resp = await client.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "embry0 is already setting up testing for this repository."
    assert len(app.state.issue_executor.calls) == 1


@pytest.mark.requires_postgres
async def test_propose_retires_a_waiting_proposal(client, app) -> None:
    waiting = await _proposal(app)
    resp = await client.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 200, resp.text
    assert (await app.state.repo_setup_proposals_repo.get(waiting))["status"] == "stale"


@pytest.mark.requires_postgres
async def test_a_run_cut_off_by_a_restart_does_not_hold_the_repository(client, app, db_with_migrations) -> None:
    stuck = await _proposal(app, status="running")
    await _job_ended(db_with_migrations, (await app.state.repo_setup_proposals_repo.get(stuck))["job_id"], "failed")
    resp = await client.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 200, resp.text
    assert (await app.state.repo_setup_proposals_repo.get(stuck))["status"] == "failed"


@pytest.mark.requires_postgres
async def test_a_start_that_fails_leaves_no_proposal(client, app) -> None:
    app.state.issue_executor.refuse = "repo acme/widgets is not registered"
    resp = await client.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "repo acme/widgets is not registered"
    assert await app.state.repo_setup_proposals_repo.latest(REPO) is None


@pytest.mark.requires_postgres
async def test_a_key_that_may_not_start_work_here_changes_nothing(app) -> None:
    waiting = await _proposal(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(ELSEWHERE)) as c:
        resp = await c.post(f"{SETUP}/propose", json={"confirm_spend": True})
    assert resp.status_code == 403
    assert (await app.state.repo_setup_proposals_repo.get(waiting))["status"] == "pending"
    assert app.state.issue_executor.calls == []


@pytest.mark.requires_postgres
async def test_the_run_starts_from_the_branch_embry0_works_from(client, app) -> None:
    await app.state.repo_preferences_repo.upsert("acme/prefs", base_branch="develop")
    await app.state.org_repos_repo.upsert_fact(
        RepoFacts(owner="acme", repo="Acme/Facts", default_branch="trunk"), run_id="run-1"
    )
    for repo in ("acme/prefs", "acme/facts", "acme/bare"):
        resp = await client.post(f"/api/v1/repos/{repo}/setup/propose", json={"confirm_spend": True})
        assert resp.status_code == 200, resp.text
    assert [c["branch"] for c in app.state.issue_executor.calls] == ["develop", "trunk", "main"]


# -- cost ----------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_cost_with_no_recorded_run_says_so(client, app) -> None:
    await app.state.budget_repo.update(max_budget_per_job_usd=7.5)
    resp = await client.get(f"{SETUP}/cost")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "recorded_runs": 0,
        "typical_cost_usd": None,
        "per_job_budget_usd": 7.5,
        "model": MEDIUM,
        "paid_from": "team AI account",
    }
    app.state.credential_resolver.resolve_llm.assert_awaited_once_with(REPO, "anthropic")


@pytest.mark.requires_postgres
async def test_cost_is_the_median_of_the_newest_twenty_completed_setup_runs(client, app, db_with_migrations) -> None:
    jobs = app.state.jobs_repo
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # Two old, dear runs, then twenty recent ones costing 0.10 .. 2.00.
    costs = [50.0, 60.0] + [round(0.1 * n, 2) for n in range(1, 21)]
    for n, cost in enumerate(costs):
        job_id = await jobs.create(repo="acme/any", task="onboard", pipeline_template="onboard")
        await db_with_migrations.execute(
            "UPDATE jobs SET status = 'completed', total_cost_usd = $2, created_at = $3 WHERE job_id = $1",
            job_id,
            cost,
            start + timedelta(minutes=n),
        )
    failed = await jobs.create(repo="acme/any", task="onboard", pipeline_template="onboard")
    await _job_ended(db_with_migrations, failed, "failed")
    other = await jobs.create(repo="acme/any", task="fix it", pipeline_template="issue-to-pr")
    await db_with_migrations.execute(
        "UPDATE jobs SET status = 'completed', total_cost_usd = 99 WHERE job_id = $1", other
    )

    body = (await client.get(f"{SETUP}/cost")).json()
    assert body["recorded_runs"] == 22
    assert body["typical_cost_usd"] == pytest.approx(1.05)


@pytest.mark.requires_postgres
async def test_cost_names_no_account_when_none_resolves(client, app) -> None:
    app.state.credential_resolver = _paid_from(None)
    assert (await client.get(f"{SETUP}/cost")).json()["paid_from"] is None


# -- review ----------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_no_proposal_is_404(client) -> None:
    assert (await client.get(f"{SETUP}/proposal")).status_code == 404


@pytest.mark.requires_postgres
async def test_a_waiting_proposal_reads_with_its_check_and_the_current_setup(client, app) -> None:
    await client.put(QA, content=V1)
    saved = await app.state.qa_configs_repo.get(REPO)
    proposal_id = await _proposal(app, LIVE, base_updated_at=saved["updated_at"])

    resp = await client.get(f"{SETUP}/proposal")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "repo",
        "status",
        "job_id",
        "job_status",
        "created_by",
        "created_at",
        "decided_by",
        "decided_at",
        "notes_md",
        "failure",
        "smoke",
        "proposed_yaml",
        "current_yaml",
        "check",
    }
    assert (body["id"], body["repo"], body["status"]) == (proposal_id, REPO, "pending")
    assert body["job_status"] == "pending"
    assert (body["proposed_yaml"], body["current_yaml"]) == (LIVE, V1)
    assert body["notes_md"] == "Started web with npm run dev."
    assert body["smoke"] == {"results": [{"app": "web", "ok": True}]}
    assert isinstance(body["created_at"], str) and body["decided_at"] is None
    assert body["check"]["ok"] is True
    assert [w["code"] for w in body["check"]["warnings"]] == ["live_address"]


@pytest.mark.requires_postgres
async def test_a_failed_proposal_has_no_check(client, app) -> None:
    await _proposal(app, None, status="failed")
    body = (await client.get(f"{SETUP}/proposal")).json()
    assert (body["status"], body["check"], body["current_yaml"]) == ("failed", None, None)


@pytest.mark.requires_postgres
async def test_a_running_proposal_whose_job_stopped_is_reported_and_stored_as_failed(
    client, app, db_with_migrations
) -> None:
    proposal_id = await _proposal(app, status="running")
    job_id = (await app.state.repo_setup_proposals_repo.get(proposal_id))["job_id"]

    assert (await client.get(f"{SETUP}/proposal")).json()["status"] == "running"

    await _job_ended(db_with_migrations, job_id, "failed", "the orchestrator restarted")
    body = (await client.get(f"{SETUP}/proposal")).json()
    assert (body["status"], body["job_status"]) == ("failed", "failed")
    assert body["failure"] == "embry0 stopped before it finished: the orchestrator restarted"
    stored = await app.state.repo_setup_proposals_repo.get(proposal_id)
    assert (stored["status"], stored["failure"]) == ("failed", body["failure"])


@pytest.mark.requires_postgres
async def test_a_job_that_stopped_without_a_reason_still_ends_the_proposal(client, app, db_with_migrations) -> None:
    proposal_id = await _proposal(app, status="running")
    await _job_ended(
        db_with_migrations, (await app.state.repo_setup_proposals_repo.get(proposal_id))["job_id"], "cancelled"
    )
    body = (await client.get(f"{SETUP}/proposal")).json()
    assert (body["status"], body["failure"]) == ("failed", "embry0 stopped before it finished.")


@pytest.mark.requires_postgres
async def test_pending_proposals_are_listed_for_the_overview(client, app) -> None:
    waiting = await _proposal(app)
    await _proposal(app, repo="acme/other", status="failed")
    resp = await client.get("/api/v1/setup/proposals", params={"status": "pending"})
    assert resp.status_code == 200, resp.text
    [item] = resp.json()["proposals"]
    assert (item["id"], item["repo"]) == (waiting, REPO)
    assert isinstance(item["created_at"], str)


# -- apply ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_apply_writes_a_proposal_revision_that_undo_takes_back_to_the_base(
    client, app, db_with_migrations
) -> None:
    await client.put(QA, content=V1)
    saved = await app.state.qa_configs_repo.get(REPO)
    proposal_id = await _proposal(app, base_updated_at=saved["updated_at"])

    resp = await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={"acknowledged": []})
    assert resp.status_code == 200, resp.text
    revision_id = resp.json()["revision_id"]
    assert (await client.get(QA)).text == GOOD

    revision = await app.state.repo_config_revisions_repo.get(revision_id)
    assert (revision["source"], revision["actor"], revision["reason"]) == (
        "proposal",
        "dev@example.com",
        f"proposal {proposal_id}",
    )
    stored = await app.state.repo_setup_proposals_repo.get(proposal_id)
    assert (stored["status"], stored["decided_by"]) == ("applied", "dev@example.com")
    assert await _audit(db_with_migrations, "repo.setup_applied") == [
        {
            "actor": "dev@example.com",
            "details": {"repo": REPO, "proposal_id": proposal_id, "revision_id": revision_id},
        }
    ]

    undo = await client.post(f"/api/v1/repos/{REPO}/history/{revision_id}/undo")
    assert undo.status_code == 200, undo.text
    assert (await client.get(QA)).text == V1


@pytest.mark.requires_postgres
async def test_apply_after_someone_saved_the_setup_is_stale_and_writes_nothing(client, app) -> None:
    proposal_id = await _proposal(app, base_updated_at=None)
    await client.put(QA, content=V1)

    resp = await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={"acknowledged": []})
    assert resp.status_code == 409
    assert resp.json()["detail"] == STALE
    assert (await client.get(QA)).text == V1
    stored = await app.state.repo_setup_proposals_repo.get(proposal_id)
    assert (stored["status"], stored["decided_by"]) == ("stale", "dev@example.com")


@pytest.mark.requires_postgres
async def test_apply_needs_every_warning_ticked(client, app) -> None:
    proposal_id = await _proposal(app, LIVE)
    resp = await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={"acknowledged": ["testing_off"]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["missing"] == ["live_address"]
    assert resp.json()["detail"]["message"] == "Tick each warning before you apply this setup."
    assert (await client.get(QA)).status_code == 404
    assert (await app.state.repo_setup_proposals_repo.get(proposal_id))["status"] == "pending"

    ticked = await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={"acknowledged": ["live_address"]})
    assert ticked.status_code == 200, ticked.text
    assert (await client.get(QA)).text == LIVE


@pytest.mark.requires_postgres
async def test_apply_refuses_a_setup_every_run_would_stop_on(client, app) -> None:
    proposal_id = await _proposal(app, NO_BROWSER)
    resp = await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={"acknowledged": []})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["problems"][0]["plain"] == "The work machine 'slim' has no browser, so every test run would stop."
    assert (
        detail["message"] == "The work machine 'slim' has no browser, so every test run would stop. Use 'qa-external'."
    )
    assert (await client.get(QA)).status_code == 404
    assert (await app.state.repo_setup_proposals_repo.get(proposal_id))["status"] == "pending"


@pytest.mark.requires_postgres
async def test_apply_only_a_waiting_proposal_of_this_repository(client, app) -> None:
    applied = await _proposal(app)
    assert (await client.post(f"{SETUP}/proposal/{applied}/apply", json={})).status_code == 200
    again = await client.post(f"{SETUP}/proposal/{applied}/apply", json={})
    assert again.status_code == 409
    assert again.json()["detail"] == "This setup was already applied."

    elsewhere = await _proposal(app, repo="acme/other")
    assert (await client.post(f"{SETUP}/proposal/{elsewhere}/apply", json={})).status_code == 404
    assert (await client.post(f"{SETUP}/proposal/prop-000000000000/apply", json={})).status_code == 404


@pytest.mark.requires_postgres
async def test_a_read_key_can_look_but_not_apply(app) -> None:
    proposal_id = await _proposal(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(READER)) as c:
        assert (await c.get(f"{SETUP}/proposal")).status_code == 200
        assert (await c.get(f"{SETUP}/cost")).status_code == 200
        assert (await c.post(f"{SETUP}/proposal/{proposal_id}/apply", json={})).status_code == 403
        assert (await c.post(f"{SETUP}/propose", json={"confirm_spend": True})).status_code == 403
        assert (await c.post(f"{SETUP}/skip-testing")).status_code == 403
    assert (await app.state.repo_setup_proposals_repo.get(proposal_id))["status"] == "pending"


# -- turn down ---------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_reject_turns_the_proposal_down(client, app, db_with_migrations) -> None:
    proposal_id = await _proposal(app)
    resp = await client.post(f"{SETUP}/proposal/{proposal_id}/reject")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"proposal_id": proposal_id, "status": "rejected"}
    stored = await app.state.repo_setup_proposals_repo.get(proposal_id)
    assert (stored["status"], stored["decided_by"]) == ("rejected", "dev@example.com")
    assert await _audit(db_with_migrations, "repo.setup_rejected") == [
        {"actor": "dev@example.com", "details": {"repo": REPO, "proposal_id": proposal_id}}
    ]

    again = await client.post(f"{SETUP}/proposal/{proposal_id}/reject")
    assert again.status_code == 409
    assert again.json()["detail"] == "This setup was already turned down."
    assert (await client.post(f"{SETUP}/proposal/{proposal_id}/apply", json={})).status_code == 409
    assert (await client.get(QA)).status_code == 404


# -- skip testing ----------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_skip_testing_stores_a_starter_with_testing_off(client, app) -> None:
    resp = await client.post(f"{SETUP}/skip-testing")
    assert resp.status_code == 200, resp.text
    stored = (await client.get(QA)).text
    assert stored == render_starter_qa_yaml(REPO)
    assert "qa_required: never" in stored
    revision = await app.state.repo_config_revisions_repo.get(resp.json()["revision_id"])
    assert (revision["source"], revision["actor"], revision["reason"]) == ("person", "dev@example.com", "skip testing")


@pytest.mark.requires_postgres
async def test_skip_testing_leaves_an_existing_setup_alone(client) -> None:
    await client.put(QA, content=GOOD)
    resp = await client.post(f"{SETUP}/skip-testing")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "This repository already has a test setup."
    assert (await client.get(QA)).text == GOOD


# -- wiring ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_the_app_wires_the_proposal_store(api_client) -> None:
    """Production's _init_app_state must hand the routes their store."""
    resp = await api_client.get("/api/v1/setup/proposals")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"proposals": []}
    assert (await api_client.get("/api/v1/repos/acme/wired/setup/proposal")).status_code == 404
