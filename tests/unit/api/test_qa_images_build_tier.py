"""POST /api/v1/qa/images/build — the tier gate on the bake branch.

A prebaked image becomes the repository's QA base, so baking from any branch
but the forge's default branch is an admin-tier action; a default-branch bake
stays at operator tier. The default branch is the one the forge reports
(``GitHubClient.get_repo``), never a ``repo_preferences.base_branch``. Auth is
enabled here (``auth_dev_mode=False``) because the dev-mode client bypasses
every tier check.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.deps import require_auth
from embry0.api.v1 import qa_images
from embry0.config import Embry0Config
from embry0.execution.github_client import GitHubClient
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

OPERATOR_TOKEN = "emb_" + "o" * 43
ADMIN_TOKEN = "emb_" + "a" * 43


def _key(scope: str, token: str) -> ApiKeyRow:
    return ApiKeyRow(
        id="key-qaimg00000001",
        user_id=None,
        name="k",
        prefix="emb_" + token[4:12],
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


def _client(*, preferred_base: str | None = None) -> TestClient:
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]

    keys = MagicMock()

    async def get_by_hash(h: str):
        if h == hash_token(OPERATOR_TOKEN):
            return _key("operate", OPERATOR_TOKEN)
        if h == hash_token(ADMIN_TOKEN):
            return _key("admin", ADMIN_TOKEN)
        return None

    keys.get_by_hash = AsyncMock(side_effect=get_by_hash)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    app.state.users_repo = MagicMock()

    # A preference is a diff-base setting; the gate must not read it.
    prefs = MagicMock()
    prefs.get = AsyncMock(return_value=({"base_branch": preferred_base} if preferred_base else None))
    app.state.repo_preferences_repo = prefs

    app.state.repos_repo = None  # resolve_forge → github default
    app.state.credential_resolver = None
    app.state.qa_configs_repo = None
    app.state.sandbox_manager = MagicMock()
    app.state.runtime = MagicMock()
    app.state.profiles_repo = MagicMock()
    app.state.proxy_manager = MagicMock()
    app.state.qa_image_tags_repo = MagicMock()
    app.state.db = None

    app.include_router(qa_images.router, prefix="/api/v1", dependencies=[require_auth])
    return TestClient(app)


def _post(client: TestClient, token: str, branch: str, *, forge_default: str | None):
    """Bake ``branch`` with the forge reporting ``forge_default`` (None: the forge does not answer)."""
    record = {"default_branch": forge_default} if forge_default else None
    get_repo = AsyncMock(return_value=record)
    with (
        patch.object(GitHubClient, "get_repo", get_repo),
        patch("embry0.cache.image_builder_cli.run_build_qa_image", new=AsyncMock(return_value="built")),
    ):
        response = client.post(
            "/api/v1/qa/images/build",
            json={"repo": "acme/widgets", "branch": branch},
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
        )
    return response, get_repo


def test_operator_may_build_the_forge_default_branch():
    response, get_repo = _post(_client(), OPERATOR_TOKEN, "develop", forge_default="develop")
    assert get_repo.await_count >= 1, "the gate must ask the forge for the repository's default branch"
    assert response.status_code == 200, f"an operator may bake the forge's default branch; got {response.text}"


def test_operator_may_not_build_another_branch():
    response, _ = _post(_client(), OPERATOR_TOKEN, "feature-x", forge_default="main")
    assert response.status_code == 403, (
        f"an operator baking a branch other than the forge's default must be refused; got {response.status_code}"
    )


def test_a_preference_naming_the_branch_does_not_make_it_the_default():
    response, _ = _post(_client(preferred_base="feature-x"), OPERATOR_TOKEN, "feature-x", forge_default="main")
    assert response.status_code == 403, (
        f"repo_preferences.base_branch must not stand in for the forge's default branch; got {response.status_code}"
    )


def test_main_is_not_assumed_when_the_forge_does_not_answer():
    response, _ = _post(_client(), OPERATOR_TOKEN, "main", forge_default=None)
    assert response.status_code == 403, (
        f"with no answer from the forge, a bake must need admin tier, not assume main; got {response.status_code}"
    )


def test_admin_may_build_any_branch():
    response, get_repo = _post(_client(), ADMIN_TOKEN, "feature-x", forge_default="main")
    assert get_repo.await_count >= 1, "the gate must ask the forge for the repository's default branch"
    assert response.status_code == 200, f"an admin may bake any branch; got {response.text}"
