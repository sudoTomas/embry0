"""The `propose` key scope (owner proposals, spec 2026-10-24): read, answer proposals, nothing else.

A chat assistant holds this key to ask a repository's owners. A leaked copy
can start only the work an owner could accept, under the key's repo
allowlist, and can cancel, create or configure nothing.
"""

from __future__ import annotations

import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from embry0.api.deps import PROPOSE_SCOPE_MUTATIONS, bust_api_key_cache, require_auth
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.fixtures.routes import app_routes

TOKEN = "emb_" + "p" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _key(scope: str) -> ApiKeyRow:
    return ApiKeyRow(
        id="key-propose00001",
        user_id=None,
        name="assistant",
        prefix="emb_pppppppp",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


def _client(scope: str) -> TestClient:
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: _key(scope) if h == hash_token(TOKEN) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    app.state.users_repo = MagicMock()
    router = APIRouter()

    @router.get("/proposals")
    def feed():
        return {"ok": True}

    for answer in ("accept", "decline", "snooze"):
        router.add_api_route(f"/proposals/{{proposal_id}}/{answer}", lambda proposal_id: {"ok": True}, methods=["POST"])

    @router.post("/cases/{case_id}/accept")
    def accept_case(case_id: str):
        return {"ok": True}

    @router.post("/jobs")
    def create_job():
        return {"ok": True}

    @router.post("/issues/{issue_id}/triage")
    def triage(issue_id: str):
        return {"ok": True}

    app.include_router(router, prefix="/api/v1", dependencies=[require_auth])
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache():
    bust_api_key_cache()
    yield
    bust_api_key_cache()


def test_a_propose_key_reads_and_answers() -> None:
    client = _client("propose")
    assert client.get("/api/v1/proposals", headers=AUTH).status_code == 200
    for answer in ("accept", "decline", "snooze"):
        assert client.post(f"/api/v1/proposals/issue:i-1/{answer}", headers=AUTH).status_code == 200, answer


@pytest.mark.parametrize(
    "path",
    ["/api/v1/jobs", "/api/v1/cases/hc-1/accept", "/api/v1/issues/i-1/triage", "/api/v1/issues/proposals/triage"],
)
def test_a_propose_key_makes_no_other_mutation(path: str) -> None:
    response = _client("propose").post(path, headers=AUTH)
    assert response.status_code == 403 and "propose-scope" in response.json()["detail"], path


def test_every_allowed_mutation_is_a_real_route() -> None:
    from embry0.api.app import create_app

    routes = {(m, r.path) for r in app_routes(create_app()) if r.is_api_route for m in r.methods or ()}
    missing = sorted(m for m in PROPOSE_SCOPE_MUTATIONS if m not in routes)
    assert not missing, f"PROPOSE_SCOPE_MUTATIONS names routes the app does not have: {missing}"
    assert all(
        re.fullmatch(r"/api/v1/proposals/\{proposal_id\}/(accept|decline|snooze)", t)
        for _, t in PROPOSE_SCOPE_MUTATIONS
    )
