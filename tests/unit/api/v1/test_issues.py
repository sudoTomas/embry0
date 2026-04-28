"""Unit tests for the issues API — focused on the answer-input endpoint."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)

    mock_issues = MagicMock()
    mock_issues.get = AsyncMock(return_value={"id": "iss-001", "status": "awaiting_input"})
    app.state.issues_repo = mock_issues

    _input_row = {
        "id": "inp-001",
        "issue_id": "iss-001",
        "job_id": "job-001",
        "asking_node": "developer",
        "question": "Should I proceed?",
        "importance": "blocking",
        "auto_answer": None,
        "answer": None,
        "answered_by": None,
        "status": "pending",
        "created_at": datetime(2024, 1, 1),
        "answered_at": None,
    }
    _answered_row = {**_input_row, "answer": "yes", "answered_by": "user", "status": "answered"}

    mock_inputs = MagicMock()
    mock_inputs.get = AsyncMock(return_value=_input_row)
    mock_inputs.answer = AsyncMock()
    mock_inputs.count_pending_blocking = AsyncMock(return_value=1)
    mock_inputs.get = AsyncMock(side_effect=[_input_row, _answered_row])
    app.state.inputs_repo = mock_inputs

    mock_executor = MagicMock()
    mock_executor._track_task = MagicMock()
    mock_executor.resume = MagicMock()
    mock_executor._jobs = MagicMock()
    mock_executor._jobs.list = AsyncMock(return_value=([], 0))
    app.state.issue_executor = mock_executor

    return app


_CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client_with_pending_input(app):
    """AsyncClient pre-wired with a known issue and pending input."""
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, "iss-001", "inp-001"


@pytest.mark.asyncio
async def test_answer_input_rejects_extra_fields(client_with_pending_input):
    """AnswerInputRequest extra='forbid' must reject unexpected fields."""
    client, issue_id, input_id = client_with_pending_input
    resp = await client.post(
        f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer",
        json={"answer": "yes", "extra_field": "boom"},
        headers=_CSRF_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_answer_input_rejects_empty_answer(client_with_pending_input):
    """min_length=1 must reject an empty string answer."""
    client, issue_id, input_id = client_with_pending_input
    resp = await client.post(
        f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer",
        json={"answer": ""},
        headers=_CSRF_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_answer_input_accepts_valid_answer(client_with_pending_input):
    """Happy path — valid answer returns 200 with the updated input."""
    client, issue_id, input_id = client_with_pending_input
    resp = await client.post(
        f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer",
        json={"answer": "yes"},
        headers=_CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "yes"
