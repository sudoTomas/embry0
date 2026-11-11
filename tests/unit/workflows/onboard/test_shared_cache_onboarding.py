"""Onboarding leaves the shared build cache to the deployment.

The job stores the setup drafted from the repository (``write_config_node``):
the agent's draft, or a ``.onboard/qa.yaml`` the repository commits, which the
job reads as the draft. Whoever started the job, a draft that turns
``cache.turbo_remote`` on is not stored unless the stored setup already has it
on; an operator turns it on explicitly. Started by a key with a repository
allowlist, the job also replaces no setup that has it on. The check runs on
the stored setup with its row held. Who started the job rides from
``start_onboard_job`` into its launch record, so a relaunch keeps it, and into
the pipeline's state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository
from embry0.workflows.onboard.nodes import validate_node, write_config_node

REPO = "acme/widgets"
_SETUP = (Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2" / "single-app.yaml").read_text()


def _shares(on: bool, note: str = "") -> str:
    doc = yaml.safe_load(_SETUP)
    doc.setdefault("cache", {})["turbo_remote"] = {"enabled": on}
    if note:
        doc["defaults"]["acceptance_criteria_template"] = [note]
    return yaml.safe_dump(doc, sort_keys=False)


async def _write(configs: RepoQAConfigsRepository, draft: str, *, by_allowlisted_key: bool) -> dict[str, Any]:
    state = {
        "repo": REPO,
        "job_id": "job-000000000001",
        "onboard": {"validated": True, "qa_yaml_text": draft, "by_allowlisted_key": by_allowlisted_key},
    }
    out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
    return dict(out["onboard"])


@pytest.mark.requires_postgres
async def test_a_job_an_allowlisted_key_started_does_not_turn_on_the_shared_cache(
    db_with_migrations: DatabasePool,
) -> None:
    configs = RepoQAConfigsRepository(db_with_migrations)
    on = await _write(configs, _shares(True), by_allowlisted_key=True)
    stored_after_on = await configs.get(REPO)
    off = await _write(configs, _shares(False), by_allowlisted_key=True)
    stored_after_off = await configs.get(REPO)
    assert on["final_status"] == "failed" and "turns on the shared build cache" in on["failure_summary"], on
    assert stored_after_on is None, "the refused draft was stored"
    assert off["final_status"] == "completed", f"a draft that leaves it off is stored: {off}"
    assert stored_after_off is not None and stored_after_off["yaml_text"] == _shares(False)


@pytest.mark.requires_postgres
async def test_a_job_an_allowlisted_key_started_does_not_replace_an_admin_s_shared_setup(
    db_with_migrations: DatabasePool,
) -> None:
    configs = RepoQAConfigsRepository(db_with_migrations)
    await configs.upsert(REPO, _shares(True), updated_by="admin@example.com")
    answers = {
        "a draft that keeps it on": await _write(configs, _shares(True, "page loads quickly"), by_allowlisted_key=True),
        "a draft that turns it off": await _write(configs, _shares(False), by_allowlisted_key=True),
    }
    left = await configs.get(REPO)
    for name, answer in answers.items():
        assert answer["final_status"] == "failed", f"{name}: {answer}"
    assert "stored setup shares the build cache" in answers["a draft that turns it off"]["failure_summary"]
    assert left is not None and (left["yaml_text"], left["updated_by"]) == (_shares(True), "admin@example.com")


@pytest.mark.requires_postgres
async def test_a_first_setup_an_admin_saves_meanwhile_is_not_replaced(db_with_migrations: DatabasePool) -> None:
    """No setup is stored, so there is no row to hold: an admin's first save, sharing the cache and not committed,
    makes the job's write wait, and once it commits the job's draft does not replace it unjudged."""
    db = db_with_migrations
    configs = RepoQAConfigsRepository(db)
    # The repository is registered: only the setup's first row is being made.
    await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github') ON CONFLICT DO NOTHING", REPO)
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO repo_qa_configs (repo, yaml_text, updated_by) VALUES ($1, $2, 'admin@example.com')",
            REPO,
            _shares(True),
        )
        task = asyncio.create_task(_write(configs, _shares(False), by_allowlisted_key=True))
        await asyncio.sleep(0.5)
        waited = not task.done()
    answer = await task
    left = await configs.get(REPO)
    assert waited, f"the job's write did not wait for the admin's first save: {task.result()}"
    assert answer["final_status"] == "failed", answer
    assert left is not None and left["yaml_text"] == _shares(True), "the admin's first setup stays"


class _Clone:
    """The analysis sandbox's clone: ``cat`` of a path answers what the repository committed there."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def build_exec_cmd(self, _sandbox: str, cmd: list[str]) -> list[str]:
        return cmd

    async def run_cmd(self, cmd: list[str], timeout: float | None = None) -> str:
        if cmd[-1] in self.files:
            return self.files[cmd[-1]]
        raise RuntimeError(f"cat: {cmd[-1]}: No such file")


async def _onboard_committed(configs: RepoQAConfigsRepository, committed: str) -> dict[str, Any]:
    """An admin's onboarding job of a repository that commits ``committed`` as ``.onboard/qa.yaml``: the job reads it
    back as its draft (``validate_node``) and stores it (``write_config_node``)."""
    state: dict[str, Any] = {
        "repo": REPO,
        "job_id": "job-000000000002",
        "onboard": {"sandbox_id": "sb-000000000001", "round": 1, "by_allowlisted_key": False},
    }
    validated = await validate_node(
        state, {"configurable": {"docker": _Clone({"/workspace/.onboard/qa.yaml": committed})}}
    )
    assert validated["onboard"]["validated"] is True, validated
    out = await write_config_node(
        {**state, "onboard": validated["onboard"]}, {"configurable": {"qa_configs_repo": configs}}
    )
    return dict(out["onboard"])


@pytest.mark.requires_postgres
async def test_an_admin_s_onboarding_does_not_turn_on_the_shared_cache_a_repository_commits(
    db_with_migrations: DatabasePool,
) -> None:
    """The repository commits a ``.onboard/qa.yaml`` turning the shared cache on: the admin's job does not store it
    over a setup that has it off, nor over none."""
    configs = RepoQAConfigsRepository(db_with_migrations)
    over_none = await _onboard_committed(configs, _shares(True))
    nothing = await configs.get(REPO)
    await configs.upsert(REPO, _shares(False), updated_by="admin@example.com")
    over_off = await _onboard_committed(configs, _shares(True, "page loads quickly"))
    left = await configs.get(REPO)
    for name, answer in (("over no setup", over_none), ("over a setup with it off", over_off)):
        assert answer["final_status"] == "failed", f"{name}: {answer}"
        assert "never turns it on from a repository's content" in answer["failure_summary"], answer
    assert nothing is None, "the committed draft was stored"
    assert left is not None and left["yaml_text"] == _shares(False), "the admin's setup stays"


@pytest.mark.requires_postgres
async def test_an_admin_s_onboarding_keeps_it_on_where_an_admin_turned_it_on_and_stores_the_rest(
    db_with_migrations: DatabasePool,
) -> None:
    """So the checks above can fail: an admin's job stores a draft that leaves the cache off, keeps it on over a
    setup that has it on, and may turn it off."""
    configs = RepoQAConfigsRepository(db_with_migrations)
    off = await _onboard_committed(configs, _shares(False))
    await configs.upsert(REPO, _shares(True), updated_by="admin@example.com")
    kept = await _onboard_committed(configs, _shares(True, "page loads quickly"))
    kept_text = (await configs.get(REPO) or {}).get("yaml_text")
    turned_off = await _write(configs, _shares(False), by_allowlisted_key=False)
    left = await configs.get(REPO)
    assert (off["final_status"], kept["final_status"], turned_off["final_status"]) == ("completed",) * 3
    assert kept_text == _shares(True, "page loads quickly")
    assert left is not None and left["yaml_text"] == _shares(False)


def _starter() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.create = AsyncMock(return_value="job-000000000001")
    ex._db = None
    ex._audit_log_path = None
    ex._track_task = MagicMock()  # type: ignore[method-assign]
    ex._run_onboard_workflow = MagicMock()  # type: ignore[method-assign]
    return ex


@pytest.mark.parametrize("by_allowlisted_key", [True, False])
async def test_who_started_it_rides_on_the_launch_and_the_runner(by_allowlisted_key: bool) -> None:
    ex = _starter()
    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await ex.start_onboard_job(REPO, "main", by_allowlisted_key=by_allowlisted_key)
    assert ex._run_onboard_workflow.call_args.kwargs["by_allowlisted_key"] is by_allowlisted_key
    assert ex._track_task.call_args.kwargs["launch"]["by_allowlisted_key"] is by_allowlisted_key


async def test_a_relaunch_keeps_who_started_it() -> None:
    ex = _starter()
    ex._run_onboard_workflow = AsyncMock()  # type: ignore[method-assign]
    ex._announce_recovery = AsyncMock()  # type: ignore[method-assign]
    await ex._relaunch(
        "job-000000000001",
        {"runner": "onboard", "repo": REPO, "branch": "main", "skip_smoke": False, "by_allowlisted_key": True},
    )
    assert ex._run_onboard_workflow.await_args.kwargs["by_allowlisted_key"] is True


async def test_the_runner_puts_who_started_it_in_the_pipeline_s_state() -> None:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.get = AsyncMock(return_value={})
    ex._jobs.update = AsyncMock()
    ex._db = object()
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ex._load_merged_env_vars = AsyncMock(return_value=None)  # type: ignore[method-assign]
    ex._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    ex._announce_finished = AsyncMock()  # type: ignore[method-assign]
    seen: list[dict[str, Any]] = []

    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        seen.append(initial_state)
        return {**initial_state, "onboard": {"final_status": "failed", "failure_summary": "stop here"}}, False, None

    ex._execute_workflow_stream = stream  # type: ignore[method-assign]
    await ex._run_onboard_workflow(
        "job-000000000001", repo=REPO, branch="main", skip_smoke=True, by_allowlisted_key=True
    )
    assert seen and seen[0]["onboard"]["by_allowlisted_key"] is True, seen
