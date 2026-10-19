"""Smoke tests for /repos/{owner}/{repo}/preferences endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)

    # Preferences repo — empty-by-default
    mock_prefs = MagicMock()
    mock_prefs.get = AsyncMock(return_value=None)

    async def _upsert(
        repo,
        sandbox_profile=None,
        language_hint=None,
        notes="",
        execution_mode=None,
        auth_mode=None,
        git_author_name=None,
        git_author_email=None,
        dev_checks=None,
        base_branch=None,
        verification=None,
        pr_review=None,
    ):
        return {
            "repo": repo,
            "sandbox_profile": sandbox_profile,
            "language_hint": language_hint,
            "notes": notes,
            "execution_mode": execution_mode,
            "auth_mode": auth_mode,
            "git_author_name": git_author_name,
            "git_author_email": git_author_email,
            "dev_checks": dev_checks or [],
            "base_branch": base_branch,
            "verification": verification,
            "pr_review": pr_review,
            "updated_at": datetime.now(UTC),
        }

    mock_prefs.upsert = AsyncMock(side_effect=_upsert)
    mock_prefs.delete = AsyncMock()

    # The routes write through the recorded variants (ADR 0019). They hand
    # the fields to upsert, so the assertions on upsert below still hold.
    async def _upsert_recorded(repo, *, actor="", source="person", reason="", **fields):
        return await mock_prefs.upsert(repo, **fields), 1

    mock_prefs.upsert_recorded = AsyncMock(side_effect=_upsert_recorded)
    mock_prefs.delete_recorded = AsyncMock(return_value=1)
    app.state.repo_preferences_repo = mock_prefs

    # Sandbox profiles — only "python-3.12" exists.
    mock_profiles = MagicMock()

    async def _profile_get(name: str):
        if name == "python-3.12":
            return {"name": name, "base_image": "img"}
        return None

    mock_profiles.get = AsyncMock(side_effect=_profile_get)
    app.state.profiles_repo = mock_profiles

    return app


@pytest.mark.asyncio
async def test_get_preferences_returns_null_when_absent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/acme/widgets/preferences")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_put_preferences_accepts_known_sandbox_profile(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={
                "sandbox_profile": "python-3.12",
                "language_hint": "python",
                "notes": "Primary service",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "acme/widgets"
    assert body["sandbox_profile"] == "python-3.12"
    assert body["language_hint"] == "python"
    assert body["notes"] == "Primary service"


@pytest.mark.asyncio
async def test_put_preferences_rejects_unknown_sandbox_profile(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"sandbox_profile": "no-such-profile"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 400
    assert "no-such-profile" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_preferences_allows_null_sandbox_profile(app):
    """sandbox_profile is optional — omitting or setting None must not 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"language_hint": "rust"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sandbox_profile"] is None
    assert body["language_hint"] == "rust"


@pytest.mark.asyncio
async def test_delete_preferences_returns_204(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/repos/acme/widgets/preferences",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_upsert_passes_execution_and_auth_modes(app) -> None:
    """execution_mode and auth_mode round-trip through the API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widget/preferences",
            json={"execution_mode": "sdk", "auth_mode": "api_key"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    mock_prefs = app.state.repo_preferences_repo
    mock_prefs.upsert.assert_called_once()
    kwargs = mock_prefs.upsert.call_args.kwargs
    assert kwargs["execution_mode"] == "sdk"
    assert kwargs["auth_mode"] == "api_key"


@pytest.mark.asyncio
async def test_git_identity_round_trips(app) -> None:
    """git_author_name/email (EMB-51) round-trip through the API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widget/preferences",
            json={"git_author_name": "Acme Bot", "git_author_email": "bot@example.com"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["git_author_name"] == "Acme Bot"
    assert body["git_author_email"] == "bot@example.com"


@pytest.mark.asyncio
async def test_git_author_email_rejects_implausible_address(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widget/preferences",
            json={"git_author_email": "not-an-email"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_preferences_round_trips_dev_checks(app):
    """RAV-1254: dev_checks validate and round-trip through the PUT."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"dev_checks": [{"name": "python", "paths": ["ml/", "/shared"], "command": "cd ml && pytest -q"}]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200, resp.text
    checks = resp.json()["dev_checks"]
    # paths are normalized to bare prefixes (slashes stripped).
    assert checks == [{"name": "python", "paths": ["ml", "shared"], "command": "cd ml && pytest -q"}]


@pytest.mark.asyncio
async def test_put_preferences_rejects_malformed_dev_checks(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_command = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"dev_checks": [{"name": "x", "paths": []}]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        empty_path = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"dev_checks": [{"name": "x", "paths": ["  "], "command": "make"}]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert no_command.status_code == 422
    assert empty_path.status_code == 422


@pytest.mark.asyncio
async def test_put_preferences_accepts_a_base_branch(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/group/backend/preferences",
            json={"base_branch": "dev"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["base_branch"] == "dev"
    assert app.state.repo_preferences_repo.upsert.await_args.kwargs["base_branch"] == "dev"


@pytest.mark.asyncio
async def test_put_preferences_accepts_a_slashed_base_branch(app):
    """release/2026.09 is a legal branch name and must not be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"base_branch": "release/2026.09"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["base_branch"] == "release/2026.09"


@pytest.mark.asyncio
async def test_blank_base_branch_is_stored_as_null_not_empty_string(app):
    """Clearing the field in the dashboard must mean "ask the forge", not
    "the branch named empty-string" — which would build `git fetch origin :`."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"base_branch": "  "},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["base_branch"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["feat branch", "a;rm -rf /", "-x" * 200, "br@nch"])
async def test_put_preferences_rejects_shell_unsafe_base_branch(app, bad):
    """base_branch is interpolated into the clone shell line — the API is the
    boundary that keeps the vocabulary strict."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"base_branch": bad},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_preferences_returns_the_stored_base_branch(app):
    """The response model has no extra="forbid", so an undeclared key would be
    silently dropped — this fails if base_branch is missing from the model."""
    app.state.repo_preferences_repo.get = AsyncMock(
        return_value={
            "repo": "acme/widgets",
            "sandbox_profile": None,
            "language_hint": None,
            "notes": "",
            "execution_mode": None,
            "auth_mode": None,
            "git_author_name": None,
            "git_author_email": None,
            "dev_checks": [],
            "base_branch": "release/1.2",
            "updated_at": datetime.now(UTC),
        }
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/acme/widgets/preferences")
    assert resp.status_code == 200
    assert resp.json()["base_branch"] == "release/1.2"


@pytest.mark.asyncio
async def test_put_without_base_branch_leaves_it_alone(app):
    """#350: PUT is a partial update — an omitted base_branch is not sent to
    the repository at all, so the stored value survives; null clears it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"language_hint": "rust"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert app.state.repo_preferences_repo.upsert.await_args.kwargs == {"language_hint": "rust"}


# ── verification (#298/#299) ─────────────────────────────────────────────


async def _put(app, body: dict):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.put(
            "/api/v1/repos/acme/widgets/preferences", json=body, headers={"X-Requested-With": "XMLHttpRequest"}
        )


@pytest.mark.asyncio
async def test_put_accepts_a_verification_config(app):
    target = {"name": "jvm", "paths": ["platform/**"], "command": "cd platform && ./mvnw test"}
    resp = await _put(app, {"verification": {"test_targets": [target], "tiers": {"backend_paths": ["infra/**"]}}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification"] == {"test_targets": [target], "tiers": {"backend_paths": ["infra/**"]}}


@pytest.mark.asyncio
async def test_put_without_verification_leaves_it_alone_and_empty_object_clears(app):
    await _put(app, {"notes": "x"})
    assert "verification" not in app.state.repo_preferences_repo.upsert.await_args.kwargs
    await _put(app, {"verification": {}})
    assert app.state.repo_preferences_repo.upsert.await_args.kwargs["verification"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"test_targets": [{"name": "jvm", "command": "x" * 2001}]},  # never truncated: refused
        {"test_targets": [{"name": "../etc", "command": "make test"}]},
        {"test_targets": [{"name": "a", "command": "t"}, {"name": "a", "command": "t"}]},
        {"test_targets": [{"name": "a", "command": "t", "paths": ["/abs/**"]}]},
        {"tiers": {"frontend_paths": ["a**b"]}},
        {"unknown_key": 1},
    ],
)
async def test_put_refuses_a_malformed_verification_config(app, bad):
    assert (await _put(app, {"verification": bad})).status_code == 422


@pytest.mark.asyncio
async def test_get_tolerates_an_odd_stored_verification_object(app):
    row = await app.state.repo_preferences_repo.upsert("acme/widgets", verification={"test_targets": "not-a-list"})
    app.state.repo_preferences_repo.get = AsyncMock(return_value=row)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/acme/widgets/preferences")
    assert resp.status_code == 200
    assert resp.json()["verification"] == {"test_targets": "not-a-list"}


@pytest.mark.asyncio
async def test_put_pr_review_stores_the_whole_settings_object(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": {"enabled": True}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200, resp.text
    kwargs = app.state.repo_preferences_repo.upsert.call_args.kwargs
    assert kwargs == {"pr_review": {"enabled": True, "depth": "full", "model": None, "ready_authors": "team"}}, (
        "a partial pr_review must be stored whole, and nothing else may be written"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"depth": "deep"},
        {"enabled": "yes"},
        {"extra": 1},
        {"ready_authors": "everyone"},
        {"ready_authors": "Team"},
        {"ready_authors": None},
        {"ready_authors": 1},
    ],
)
async def test_put_pr_review_rejects_a_malformed_object(app, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": body},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422, f"{body} was accepted: {resp.text}"
    loc = resp.json()["detail"][0]["loc"]
    # Refused inside the settings object, not as an unknown pr_review field.
    assert loc[:2] == ["body", "pr_review"] and len(loc) == 3, f"refused at {loc}, not inside pr_review"


@pytest.mark.asyncio
async def test_put_pr_review_refuses_an_unpriced_model(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": {"enabled": True, "model": "grok-0-unpriced"}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422, resp.text
    # The model id itself contains "price", so match the route's refusal text,
    # not pydantic's echo of the body.
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "no price entry" in detail, f"not refused as unpriced: {resp.text}"


@pytest.mark.asyncio
async def test_a_blank_pr_review_model_means_the_heavy_tier(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": {"enabled": True, "model": "   "}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200, resp.text
    assert app.state.repo_preferences_repo.upsert.call_args.kwargs["pr_review"]["model"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ready_authors", ["team", "anyone"])
async def test_put_pr_review_stores_who_triggers_automatic_reviews(app, ready_authors):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": {"enabled": True, "ready_authors": ready_authors}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200, resp.text
    stored = app.state.repo_preferences_repo.upsert.call_args.kwargs["pr_review"]
    assert stored["ready_authors"] == ready_authors, f"stored {stored}"
    assert resp.json()["pr_review"]["ready_authors"] == ready_authors


@pytest.mark.asyncio
async def test_a_put_without_ready_authors_saves_the_team_default(app):
    # What a PUT without ready_authors stores is what a GET then reads back.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/v1/repos/acme/widgets/preferences",
            json={"pr_review": {"enabled": True}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        saved = await app.state.repo_preferences_repo.upsert.side_effect(
            "acme/widgets", **app.state.repo_preferences_repo.upsert.call_args.kwargs
        )
        app.state.repo_preferences_repo.get = AsyncMock(return_value=saved)
        resp = await client.get("/api/v1/repos/acme/widgets/preferences")
    assert resp.status_code == 200, resp.text
    assert resp.json()["pr_review"].get("ready_authors") == "team", f"GET returned {resp.json()['pr_review']}"


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_get_fills_in_team_for_a_stored_pr_review_without_ready_authors(app, db_with_migrations):
    # A row saved before ready_authors existed, written straight through the repository.
    from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

    prefs_repo = RepoPreferencesRepository(db_with_migrations)
    await prefs_repo.upsert("acme/widgets", pr_review={"enabled": True, "depth": "targeted", "model": None})
    await prefs_repo.upsert("acme/gadgets", pr_review={"enabled": True, "depth": "full", "ready_authors": "anyone"})
    await prefs_repo.upsert("acme/tools", notes="no review settings")
    app.state.repo_preferences_repo = prefs_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        legacy = (await client.get("/api/v1/repos/acme/widgets/preferences")).json()["pr_review"]
        anyone = (await client.get("/api/v1/repos/acme/gadgets/preferences")).json()["pr_review"]
        unset = (await client.get("/api/v1/repos/acme/tools/preferences")).json()["pr_review"]
    assert legacy == {"enabled": True, "depth": "targeted", "model": None, "ready_authors": "team"}, (
        f"GET returned {legacy}: a stored pr_review without ready_authors must read as team, other fields as stored"
    )
    assert anyone["ready_authors"] == "anyone", f"a stored anyone was rewritten: {anyone}"
    assert unset is None, f"a repo with no pr_review gained one: {unset}"
