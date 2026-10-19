"""Closing an organization need's case is admin work (ADR 0011).

Closing that case is the same answer as dismissing the need, and the needs
route that dismisses one is admin-only, so ``POST /cases/{id}/close`` on a case
whose origin is ``org`` needs the admin tier too. Every other case stays
operator work. Driven through the real app with real keys; the ledger is a stub.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

OPERATE_TOKEN = "emb_" + "o" * 43
ADMIN_TOKEN = "emb_" + "a" * 43


def _key(key_id: str, name: str, scope: str) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=None,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


KEYS = {
    hash_token(OPERATE_TOKEN): _key("key-operate00001", "ops-bot", "operate"),
    hash_token(ADMIN_TOKEN): _key("key-admin0000001", "admin-bot", "admin"),
}


def _case(case_id: str, origin: str) -> dict[str, Any]:
    return {"id": case_id, "repo": "acme/widgets", "origin": origin, "stage": "tracked", "resolution_path": None}


CASES = {"hc-org": _case("hc-org", "org"), "hc-watcher": _case("hc-watcher", "watcher")}


@pytest.fixture
def app() -> Any:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    app = create_app(config)
    app.state.db = None
    app.state.users_repo = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    app.state.healing_cases_repo = MagicMock(get=AsyncMock(side_effect=lambda case_id: CASES.get(case_id)))
    app.state.healing_cases = MagicMock(
        close=AsyncMock(return_value={"stage": "closed", "resolution_path": "out_of_band"})
    )
    yield app
    bust_api_key_cache()


def _close(app: Any, case_id: str, token: str) -> Any:
    return TestClient(app, raise_server_exceptions=False).post(
        f"/api/v1/cases/{case_id}/close",
        json={"resolution_path": "out_of_band"},
        headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
    )


def test_an_operate_key_cannot_close_an_organization_need_case(app: Any) -> None:
    response = _close(app, "hc-org", OPERATE_TOKEN)
    assert response.status_code == 403, (
        f"an operate key dismissed an organization need by closing its case: {response.status_code} {response.text}"
    )
    assert not app.state.healing_cases.close.await_count, "the case was closed anyway"


@pytest.mark.parametrize("token", [ADMIN_TOKEN, "master-key"], ids=["admin-key", "master-key"])
def test_the_admin_tier_closes_an_organization_need_case(app: Any, token: str) -> None:
    response = _close(app, "hc-org", token)
    assert response.status_code == 200, response.text
    assert app.state.healing_cases.close.await_count == 1


def test_an_operate_key_still_closes_any_other_case(app: Any) -> None:
    response = _close(app, "hc-watcher", OPERATE_TOKEN)
    assert response.status_code == 200, f"closing a watcher case became admin work: {response.text}"
