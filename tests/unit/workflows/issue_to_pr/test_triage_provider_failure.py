"""#466: a triage run that died on the provider account is reported as that,
on the job — not as ERR_TRIAGE_MALFORMED with "error result: success".

Drives the real triage node, then feeds its state update to the real job
outcome handler, the way the stream merges node updates into ``result``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command

from embry0.services.credentials.health import CredentialHealth
from embry0.services.issue_executor import IssueExecutor

QUOTA = "ERR_PROVIDER_QUOTA_EXHAUSTED"
MALFORMED = "ERR_TRIAGE_MALFORMED"
# Verbatim from production event logs (2026-10-02 05:15 UTC).
OUT_OF_USAGE = "You're out of extra usage · resets Oct 5, 12am (UTC)"
RESET = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


def _account_failure() -> dict[str, Any]:
    """What run_agent_node returns for a triage run the executor read as a spent window."""
    return {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": True,
                "error_message": OUT_OF_USAGE,
                "error_code": QUOTA,
                "provider_resets_at": RESET.isoformat(),
                "provider": "anthropic",
                "output": OUT_OF_USAGE,
                "cost_usd": 0.0,
            }
        ],
        "total_cost_usd": 0.0,
        "current_stage": "triage_failed",
        "errors": [f"triage: {OUT_OF_USAGE}"],
    }


def _state() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Fix the thing",
        "issue_id": "iss-1",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 0.0,
    }


def _config() -> dict[str, Any]:
    return {"configurable": {"agent_runner": MagicMock(), "credentials": {}, "repo_preferences_repo": None}}


async def _triage(*run_results: dict[str, Any], answers: Any = None) -> Command:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=list(run_results))),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes.interrupt", return_value=answers),
    ):
        command = await triage_node(_state(), _config())
    assert isinstance(command, Command), f"triage must end the job with a Command, got {type(command)}"
    assert command.goto == END
    return command


class _CredentialsRepo:
    def __init__(self) -> None:
        self.rows = {"cred-anthropic": {"id": "cred-anthropic", "label": "claude-a", "status": "ok"}}
        self.status_calls: list[tuple[Any, ...]] = []

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        row = self.rows.get(credential_id)
        return dict(row) if row else None

    async def set_status(self, credential_id, status, *, reason=None, quarantined_until=None):
        self.status_calls.append((credential_id, status, reason, quarantined_until))
        self.rows[credential_id].update(status=status, quarantined_until=quarantined_until)
        return dict(self.rows[credential_id])

    async def uses_for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [{"role": "llm", "provider": "anthropic", "credential_id": "cred-anthropic"}]

    async def finish_uses(self, job_id: str, error_code: str) -> int:
        return 1


async def _job_outcome(update: dict[str, Any]) -> tuple[dict[str, Any], _CredentialsRepo]:
    repo = _CredentialsRepo()
    cfg = SimpleNamespace(credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600)
    health = CredentialHealth(repo, cfg, clock=lambda: datetime(2026, 10, 2, 5, 15, 19, tzinfo=UTC))
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock(update=AsyncMock(), get=AsyncMock(return_value=None))
    ex._issues = MagicMock(update=AsyncMock(), update_parent_status=AsyncMock())
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._credentials_repo = repo
    ex._credential_health = health
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await ex._handle_workflow_result("iss-1", "job-1", dict(update))
    return ex._jobs.update.await_args.kwargs, repo


@pytest.mark.asyncio
async def test_triage_on_a_spent_account_fails_the_job_with_the_provider_code() -> None:
    command = await _triage(_account_failure())
    job, _ = await _job_outcome(command.update)
    assert job["status"] == "failed"
    assert job["error_code"] == QUOTA, f"the job must carry the agent's provider code, got {job['error_code']}"
    assert OUT_OF_USAGE in (job["error_message"] or ""), (
        f"the job's error_message must carry the provider's sentence, got {job['error_message']!r}"
    )


@pytest.mark.asyncio
async def test_triage_on_a_spent_account_quarantines_until_the_reset() -> None:
    command = await _triage(_account_failure())
    _, repo = await _job_outcome(command.update)
    assert [call[:3] for call in repo.status_calls] == [("cred-anthropic", "quarantined", "quota_exhausted")], (
        f"the account that served triage must be quarantined as quota_exhausted, got {repo.status_calls}"
    )
    assert repo.status_calls[0][3] == RESET, f"quarantine must last until the reset, got {repo.status_calls[0][3]}"


@pytest.mark.asyncio
async def test_triage_resume_on_a_spent_account_fails_the_job_with_the_provider_code() -> None:
    """The needs_info → answers → re-run path has its own failure guard."""
    asks = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": json.dumps(
                    {
                        "action": "needs_info",
                        "confidence": 0.3,
                        "questions": [{"question": "Which service?", "importance": "blocking"}],
                        "reasoning": "unclear",
                    }
                ),
            }
        ],
        "total_cost_usd": 0.0,
        "current_stage": "triage_complete",
    }
    command = await _triage(asks, _account_failure(), answers="the billing service")
    job, _ = await _job_outcome(command.update)
    assert job["error_code"] == QUOTA, f"resume path: expected {QUOTA}, got {job['error_code']}"
    assert OUT_OF_USAGE in (job["error_message"] or "")


@pytest.mark.asyncio
async def test_malformed_triage_json_is_still_triage_malformed() -> None:
    malformed = {
        "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": "NOT VALID JSON <<<"}],
        "total_cost_usd": 0.01,
        "current_stage": "triage_complete",
    }
    # #455: the malformed answer gets one repair turn, which is malformed too.
    command = await _triage(malformed, malformed)
    job, repo = await _job_outcome(command.update)
    assert job["error_code"] == MALFORMED, f"unparseable triage output must stay {MALFORMED}, got {job['error_code']}"
    assert repo.status_calls == [], "a malformed answer is not the account's fault"


@pytest.mark.asyncio
async def test_a_non_account_triage_error_keeps_the_node_code() -> None:
    crashed = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": True,
                "error_message": "Reached maximum number of turns (25)",
                "error_code": "ERR_AGENT_MAX_TURNS",
                "provider": "anthropic",
                "output": "",
            }
        ],
        "total_cost_usd": 0.0,
        "current_stage": "triage_failed",
    }
    command = await _triage(crashed)
    job, repo = await _job_outcome(command.update)
    assert job["error_code"] == MALFORMED, "only an account failure outranks the node's code"
    assert repo.status_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "ERR_SANDBOX_IMAGE_STALE",
            "The sandbox image 'acme-sandbox:old' was built before what this release runs agents with: it lacks the "
            "runner's execute-only interpreter (/usr/local/bin/embry0-runner-python). No agent ran.",
        ),
        ("ERR_SANDBOX_EXEC_STALLED", "sandbox exec stalled before the agent started: no read of the config"),
        ("ERR_SANDBOX_AGENT_START_FAILED", "The sandbox could not start the agent: docker exec exited 126"),
    ],
)
async def test_a_triage_run_that_never_started_fails_the_job_with_its_own_code(code: str, message: str) -> None:
    """#673: triage puts ERR_TRIAGE_MALFORMED on any agent error, but a run that never started (a stale image, a
    stalled exec, a failed start) is what an operator acts on: the job carries the run's code, and no account is
    quarantined."""
    never_started = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": True,
                "error_message": message,
                "error_code": code,
                "provider": "anthropic",
                "output": "",
            }
        ],
        "total_cost_usd": 0.0,
        "current_stage": "triage_failed",
        "errors": [f"triage: {message}"],
    }
    command = await _triage(never_started)
    assert command.update.get("error_code") == MALFORMED, "triage itself still names its node's code"
    job, repo = await _job_outcome(command.update)
    assert job["error_code"] == code, f"the job must carry the run's own code, got {job['error_code']}"
    assert repo.status_calls == [], "a run that never started is not the account's fault"
