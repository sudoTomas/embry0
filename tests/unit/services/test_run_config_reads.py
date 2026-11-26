"""A run or review that cannot read its own config does not start (#551).

``_run_workflow`` and ``_run_pr_review_workflow`` read their job row inside a
catch-all meant for binding the trace id, and went on with no row: a person's
budget cap, the job's models and tools were the defaults. The run's read of the
repository's preferences was fail-open too. A failed read now fails the job
before anything starts, with the read's error, classified as what the read raised
(#588): ``ERR_STORAGE`` for a Postgres error or a value too deep to read, and the
code the rest of a run gives anything else.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from embry0.safety.error_codes import classify_failure
from embry0.services.account_waits import WaitPolicy
from embry0.settings.sections.account_wait import ACCOUNT_WAIT
from embry0.storage.database import StoredValueTooDeep
from tests.unit.services.test_failed_job_pr import PR, _executor, _patches


def _never_started() -> tuple[Any, list[str]]:
    started: list[str] = []

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        started.append(job_id)
        raise RuntimeError("the workflow ran")

    return _stream, started


def _failed(ex: Any) -> dict[str, Any]:
    updates = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status")]
    assert updates, "the job was never given a status"
    assert updates[-1]["status"] == "failed", f"the job ran without its config: {updates[-1]}"
    return updates[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"), "ERR_STORAGE"),
        (StoredValueTooDeep("jobs"), "ERR_STORAGE"),
        # Classified as themselves (#588), as the precheck's reads are: not a storage outage.
        (ConnectionResetError("pool is down"), "ERR_UNKNOWN"),
        (AttributeError("'NoneType' object has no attribute 'get'"), "ERR_UNKNOWN"),
    ],
    ids=["postgres", "too-deep", "connection", "bug"],
)
async def test_a_run_that_cannot_read_its_job_row_fails_before_it_starts(error: Exception, code: str) -> None:
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(side_effect=error)
    stream, started = _never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "acme/app"})
    assert started == [], "the workflow started without the job's row"
    failed = _failed(ex)
    assert str(error) in str(failed.get("error_message")), failed
    assert failed.get("error_code") == code, f"the read's failure is not classified as what it is: {failed}"
    assert failed.get("error_code") == classify_failure(exc=error).value, "the row read and the rest of a run disagree"


@pytest.mark.asyncio
async def test_a_review_that_cannot_read_its_job_row_fails_before_it_starts() -> None:
    from embry0.services._executor_run import _run_pr_review_workflow

    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(side_effect=ConnectionResetError("pool is down"))
    stream, started = _never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await _run_pr_review_workflow(ex, "job-1")
    assert started == [], "the review started without its frozen config"
    ex._refuse_if_over_budget.assert_not_awaited()
    assert "pool is down" in str(_failed(ex).get("error_message"))


@pytest.mark.asyncio
async def test_a_run_that_cannot_read_its_repositorys_preferences_fails_before_it_starts() -> None:
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(return_value={"trace_id": None, "repo": "acme/app", "task": "t"})
    ex._repo_prefs = MagicMock(get=AsyncMock(side_effect=ConnectionResetError("pool is down")))
    stream, started = _never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "acme/app"})
    assert started == [], "the workflow started on the default sandbox profile and modes"
    assert "pool is down" in str(_failed(ex).get("error_message"))


@pytest.mark.asyncio
async def test_an_agent_that_cannot_read_its_repositorys_preferences_raises() -> None:
    """The agent node's read of the preferences (execution and auth modes) fails rather than defaulting."""
    from embry0.orchestration.nodes.agent import _load_repo_preferences

    prefs = MagicMock(get=AsyncMock(side_effect=ConnectionResetError("pool is down")))
    with pytest.raises(ConnectionResetError, match="pool is down"):
        await _load_repo_preferences({"repo": "acme/app"}, {"configurable": {"repo_preferences_repo": prefs}})


@pytest.mark.asyncio
async def test_a_review_that_cannot_read_its_repositorys_preferences_raises() -> None:
    """The review's read of the preferences (its sandbox profile) fails rather than taking the default profile."""
    from embry0.workflows.pr_review.nodes import _repo_preferences

    prefs = MagicMock(get=AsyncMock(side_effect=ConnectionResetError("pool is down")))
    with pytest.raises(ConnectionResetError, match="pool is down"):
        await _repo_preferences({"repo_preferences_repo": prefs}, "acme/app")


# ---- the precheck decides which providers' keys the sandbox gets: its reads fail closed too ----


@pytest.mark.asyncio
async def test_the_precheck_does_not_take_the_seeds_for_the_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.services._executor_run import _agent_definitions_for_precheck
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    monkeypatch.setattr(AgentDefinitionsRepository, "list_all", AsyncMock(side_effect=ConnectionResetError("down")))
    ex = _executor()
    ex._db = object()
    with pytest.raises(ConnectionResetError, match="down"):
        await _agent_definitions_for_precheck(ex)


@pytest.mark.asyncio
async def test_the_precheck_does_not_take_no_policy_for_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment default on Grok: without the policy, the sandbox would get no xAI key for the run's agents."""
    from embry0.services._executor_run import _model_policy_for_precheck
    from embry0.storage.repositories.provider_config import ProviderConfigRepository

    monkeypatch.setattr(ProviderConfigRepository, "get", AsyncMock(side_effect=ConnectionResetError("down")))
    ex = _executor()
    ex._db = object()
    with pytest.raises(ConnectionResetError, match="down"):
        await _model_policy_for_precheck(ex)


@pytest.mark.asyncio
async def test_the_precheck_does_not_take_no_overrides_for_the_jobs() -> None:
    from embry0.services._executor_run import _precheck_llm_accounts

    ex = _executor()
    ex._credential_resolver = MagicMock()
    ex._jobs.get = AsyncMock(side_effect=ConnectionResetError("down"))
    with pytest.raises(ConnectionResetError, match="down"):
        # a resume Command: the overrides come from the row
        await _precheck_llm_accounts(ex, "job-1", object(), wait_policy=WaitPolicy(ACCOUNT_WAIT.defaults()))
