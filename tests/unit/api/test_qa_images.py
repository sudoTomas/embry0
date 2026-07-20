"""POST /api/v1/qa/images/build (EMB-42) — endpoint wiring tests.

The build core (`run_build_qa_image`) is unit-tested in
tests/unit/cli/test_build_qa_image_cli.py; here we verify the HTTP wiring:
dependency presence check, payload validation, outcome passthrough, and
ImageBuildError mapping.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _wire_fake_deps(app) -> None:
    app.state.sandbox_manager = MagicMock()
    app.state.docker = MagicMock()
    app.state.profiles_repo = MagicMock()
    app.state.proxy_manager = MagicMock()
    app.state.qa_image_tags_repo = MagicMock()


async def test_build_returns_outcome(api_client):
    _wire_fake_deps(api_client.app)
    with patch(
        "embry0.cache.image_builder_cli.run_build_qa_image",
        new=AsyncMock(return_value="built"),
    ) as run:
        r = await api_client.post(
            "/api/v1/qa/images/build",
            json={"repo": "org/repo", "branch": "main", "force": False},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "built"
    kwargs = run.await_args.kwargs
    assert kwargs["repo"] == "org/repo"
    assert kwargs["deps"]["image_repo"] is api_client.app.state.qa_image_tags_repo


async def test_build_503_when_deps_missing(api_client):
    api_client.app.state.sandbox_manager = None
    api_client.app.state.docker = None
    r = await api_client.post(
        "/api/v1/qa/images/build",
        json={"repo": "org/repo"},
    )
    assert r.status_code == 503
    assert "not initialized" in r.json()["detail"]


async def test_build_502_on_image_build_error(api_client):
    from embry0.cache.image_builder import ImageBuildError

    _wire_fake_deps(api_client.app)
    with patch(
        "embry0.cache.image_builder_cli.run_build_qa_image",
        new=AsyncMock(side_effect=ImageBuildError("npm ci exploded")),
    ):
        r = await api_client.post(
            "/api/v1/qa/images/build",
            json={"repo": "org/repo", "branch": "dev", "force": True},
        )
    assert r.status_code == 502
    assert "npm ci exploded" in r.json()["detail"]


async def test_build_rejects_malformed_repo(api_client):
    r = await api_client.post(
        "/api/v1/qa/images/build",
        json={"repo": "not-a-repo"},
    )
    assert r.status_code == 422
