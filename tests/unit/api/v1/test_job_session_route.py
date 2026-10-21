"""GET /jobs/{id}/agents/{type}/session serves the stored transcript, redacted (spec 3.6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.safety.redaction import register_known_secrets, unregister_known_secrets

pytestmark = pytest.mark.asyncio

PLANTED = "sk-ant-planted-secret-value-0123456789abcdef"

ROW = {
    "id": "sess-1",
    "job_id": "job-s1",
    "agent_type": "developer",
    "mode": "anthropic_api",
    "session_id": "conv-1",
    "messages": [
        {"role": "user", "content": "Fix the login bug"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Using token {PLANTED} to call the API"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"export KEY={PLANTED}"}},
            ],
        },
    ],
    "session_blob": None,
    "last_turn_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    "created_at": datetime(2026, 10, 20, 11, 0, tzinfo=UTC),
}


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = MagicMock()
    app.state.agent_sessions_repo = MagicMock()
    app.state.agent_sessions_repo.get = AsyncMock(return_value=dict(ROW))
    return app


@pytest.fixture
def planted_secret():
    register_known_secrets([PLANTED])
    yield PLANTED
    unregister_known_secrets([PLANTED])


async def _get(app, job_id: str, agent: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/jobs/{job_id}/agents/{agent}/session")


async def test_messages_are_served_with_the_planted_secret_redacted(app, planted_secret):
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_type"] == "developer" and body["mode"] == "anthropic_api"
    assert len(body["messages"]) == 2
    assert planted_secret not in resp.text, "the planted secret reached the client"
    assert "***REDACTED***" in body["messages"][1]["content"][0]["text"]
    assert "***REDACTED***" in body["messages"][1]["content"][1]["input"]["command"]
    app.state.agent_sessions_repo.get.assert_awaited_once_with("job-s1", "developer")


async def test_messages_stored_as_text_are_decoded(app):
    app.state.agent_sessions_repo.get = AsyncMock(
        return_value={**ROW, "messages": '[{"role": "user", "content": "hi"}]'}
    )
    body = (await _get(app, "job-s1", "developer")).json()
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_a_blob_only_row_is_204_with_no_body(app):
    app.state.agent_sessions_repo.get = AsyncMock(return_value={**ROW, "messages": None, "session_blob": b"cli-state"})
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 204, resp.text
    assert resp.content == b""


async def test_no_row_is_404(app):
    app.state.agent_sessions_repo.get = AsyncMock(return_value=None)
    resp = await _get(app, "job-s1", "reviewer")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No stored session for this agent"
