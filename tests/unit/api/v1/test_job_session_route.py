"""GET /jobs/{id}/agents/{type}/session serves the stored transcript, redacted (spec 3.6)."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.safety.redaction import register_known_secrets, unregister_known_secrets
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

pytestmark = pytest.mark.asyncio

# Opaque on purpose: no pattern in redaction.py matches these, so only the
# known-values path can scrub them. One is registered (a credential-registry
# secret); the others arrive as the job's env vars through the mocked loader.
PLANTED = "Zq9vLx2mPw7rTn4KbHs8"
REPO_SECRET = "Yt3nQw8vLp2rMx6KdHs9Fb"
REPO_TOKEN_NAMED = "Hq5wRt7yUp3iLk9oZx2cVb"

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
    app.state.jobs_repo.get = AsyncMock(return_value={"job_id": "job-s1", "repo": "owner/repo", "status": "completed"})
    app.state.agent_sessions_repo = MagicMock()
    app.state.agent_sessions_repo.get = AsyncMock(return_value=dict(ROW))
    # The orchestrator's loader of a job's merged, decrypted env vars (the rows
    # the sandbox had in its environment). OPAQUE_VALUE is a secret-typed var
    # whose name no heuristic flags; APP_TOKEN is a config var a secret-looking
    # name flags, as the sandbox's own scrub would.
    app.state.issue_executor = MagicMock()
    app.state.issue_executor._load_merged_env_vars = AsyncMock(
        return_value=[
            {"key": "OPAQUE_VALUE", "value": REPO_SECRET, "scope": "app", "var_type": "secret"},
            {"key": "APP_TOKEN", "value": REPO_TOKEN_NAMED, "scope": "app", "var_type": "config"},
            {"key": "MODE", "value": "dev", "scope": "app", "var_type": "config"},
        ]
    )
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


async def test_the_jobs_secret_env_vars_are_redacted_like_the_sandbox_did(app):
    """The sandbox scrubbed the stream with the job's decrypted env vars in its
    environment; the stored messages never passed through that, so the route
    rebuilds the same environment from the orchestrator's loader."""
    app.state.agent_sessions_repo.get = AsyncMock(
        return_value={
            **ROW,
            "messages": [
                {"role": "assistant", "content": f"Connecting with {REPO_SECRET} and {REPO_TOKEN_NAMED} in mode dev"},
            ],
        }
    )
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 200, resp.text
    text = resp.json()["messages"][0]["content"]
    assert REPO_SECRET not in text, "the job's secret-typed env var reached the client"
    assert REPO_TOKEN_NAMED not in text, "an env var with a secret-looking name reached the client"
    assert text == "Connecting with ***REDACTED*** and ***REDACTED*** in mode dev"
    app.state.issue_executor._load_merged_env_vars.assert_awaited_once_with("owner/repo", "job-s1")


async def test_a_plain_string_message_is_redacted_too(app, planted_secret):
    app.state.agent_sessions_repo.get = AsyncMock(
        return_value={**ROW, "messages": [f"system: token {planted_secret} in use", {"role": "user", "content": "hi"}]}
    )
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 200, resp.text
    assert planted_secret not in resp.text, "a non-dict message reached the client unredacted"
    assert resp.json()["messages"][0] == "system: token ***REDACTED*** in use"


async def test_an_unresolved_job_still_redacts_with_the_known_secrets(app, planted_secret):
    app.state.jobs_repo.get = AsyncMock(return_value=None)
    resp = await _get(app, "job-s1", "developer")
    assert resp.status_code == 200, resp.text
    assert planted_secret not in resp.text
    app.state.issue_executor._load_merged_env_vars.assert_not_awaited()


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


# ----- Tier: the stored session is operator-tier, the verification row read-tier -----
#
# A transcript holds what the agent saw inside the sandbox, scrubbed after the
# fact; the verification row is a verdict. A read key may read the second and
# not the first.

KEYS = {"emb_read": "read", "emb_merge": "merge", "emb_operate": "operate", "emb_admin": "admin"}


def _key_row(token: str, scope: str) -> ApiKeyRow:
    return ApiKeyRow(
        id=f"key-{scope}-000000",
        user_id=None,
        name=f"{scope}-key",
        prefix=token[:12],
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


@pytest.fixture
def tiered_app(app):
    """The same app with real bearer auth: the master key and one DB key per scope."""
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")
    rows = {hash_token(token): _key_row(token, scope) for token, scope in KEYS.items()}
    app.state.api_keys_repo = MagicMock()
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: rows.get(h))
    app.state.api_keys_repo.touch_last_used = AsyncMock()
    app.state.job_verifications_repo = MagicMock()
    app.state.job_verifications_repo.get = AsyncMock(
        return_value={"job_id": "job-s1", "report": {}, "gate_verdict": "passed"}
    )
    bust_api_key_cache()
    yield app
    bust_api_key_cache()


async def _get_as(app, token: str, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize("token", ["emb_read", "emb_merge"])
async def test_a_key_below_operator_cannot_read_a_stored_session(tiered_app, token):
    resp = await _get_as(tiered_app, token, "/api/v1/jobs/job-s1/agents/developer/session")
    assert resp.status_code == 403, f"a {KEYS[token]} key read the stored session: {resp.status_code} {resp.text}"
    assert "operator" in resp.json()["detail"]


@pytest.mark.parametrize("token", ["emb_operate", "emb_admin", "master-key"])
async def test_an_operator_or_admin_key_reads_a_stored_session(tiered_app, token):
    resp = await _get_as(tiered_app, token, "/api/v1/jobs/job-s1/agents/developer/session")
    assert resp.status_code == 200, resp.text


async def test_no_credentials_is_401_not_403(tiered_app):
    async with AsyncClient(transport=ASGITransport(app=tiered_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/job-s1/agents/developer/session")
    assert resp.status_code == 401, resp.text


async def test_the_verification_row_stays_read_tier(tiered_app):
    resp = await _get_as(tiered_app, "emb_read", "/api/v1/jobs/job-s1/verification")
    assert resp.status_code == 200, f"a read key must still read the verification row: {resp.status_code} {resp.text}"
