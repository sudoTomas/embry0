"""Integration tests for RepoPreferencesRepository against real postgres."""

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def prefs_repo(db_with_migrations: DatabasePool) -> RepoPreferencesRepository:
    # Delete any existing rows to isolate this test run.
    await db_with_migrations.execute("DELETE FROM repo_preferences")
    return RepoPreferencesRepository(db_with_migrations)


@pytest.mark.asyncio
async def test_upsert_inserts_new_row(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(
        repo="acme/widgets",
        sandbox_profile="java-17",
        language_hint="java",
        notes="Legacy monolith",
    )
    assert row["repo"] == "acme/widgets"
    assert row["sandbox_profile"] == "java-17"
    assert row["language_hint"] == "java"
    assert row["notes"] == "Legacy monolith"
    assert row["updated_at"] is not None


@pytest.mark.asyncio
async def test_upsert_updates_existing_row(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="acme/app", sandbox_profile="python-3.12")
    updated = await prefs_repo.upsert(
        repo="acme/app", sandbox_profile="python-3.13", language_hint="python", notes="Upgraded"
    )
    assert updated["sandbox_profile"] == "python-3.13"
    assert updated["language_hint"] == "python"
    assert updated["notes"] == "Upgraded"

    # Only one row should exist (PK uniqueness).
    rows = await prefs_repo.list_all()
    assert sum(1 for r in rows if r["repo"] == "acme/app") == 1


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(prefs_repo: RepoPreferencesRepository):
    assert await prefs_repo.get("ghost/town") is None


@pytest.mark.asyncio
async def test_delete_removes_row(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="temp/gone", sandbox_profile="python-3.12")
    await prefs_repo.delete("temp/gone")
    assert await prefs_repo.get("temp/gone") is None


@pytest.mark.asyncio
async def test_list_all_sorted_by_repo(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="b/two", sandbox_profile="python-3.12")
    await prefs_repo.upsert(repo="a/one", sandbox_profile="java-17")
    rows = await prefs_repo.list_all()
    repos = [r["repo"] for r in rows]
    assert repos == ["a/one", "b/two"]


@pytest.mark.asyncio
async def test_upsert_with_nullable_fields(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(repo="bare/repo")
    assert row["sandbox_profile"] is None
    assert row["language_hint"] is None
    assert row["notes"] == ""
    assert row["git_author_name"] is None
    assert row["git_author_email"] is None


@pytest.mark.asyncio
async def test_git_identity_round_trips(prefs_repo: RepoPreferencesRepository):
    """git_author_name/email (EMB-51, migration 41) persist and read back."""
    row = await prefs_repo.upsert(
        repo="acme/crm",
        git_author_name="Acme Bot",
        git_author_email="bot@example.com",
    )
    assert row["git_author_name"] == "Acme Bot"
    assert row["git_author_email"] == "bot@example.com"


@pytest.mark.asyncio
async def test_dev_checks_round_trip(prefs_repo: RepoPreferencesRepository):
    """dev_checks (RAV-1254, migration 53) persist as JSONB and read back."""
    checks = [{"name": "python", "paths": ["ml"], "command": "cd ml && poetry run pytest -q"}]
    row = await prefs_repo.upsert(repo="acme/crm", dev_checks=checks)
    assert row["dev_checks"] == checks
    # Omitting dev_checks on a later upsert keeps them (#350); None clears.
    row2 = await prefs_repo.upsert(repo="acme/crm", sandbox_profile="dev-python")
    assert row2["dev_checks"] == checks
    row3 = await prefs_repo.upsert(repo="acme/crm", dev_checks=None)
    assert row3["dev_checks"] == []


@pytest.mark.asyncio
async def test_upsert_preserves_other_fields_when_setting_base_branch(
    prefs_repo: RepoPreferencesRepository,
):
    """PUT replaces the row, so base_branch must ride alongside the rest."""
    row = await prefs_repo.upsert(
        repo="acme/widgets",
        sandbox_profile="python-3.12",
        base_branch="release/2026.09",
    )
    assert row["sandbox_profile"] == "python-3.12"
    assert row["base_branch"] == "release/2026.09"


@pytest.mark.asyncio
async def test_base_branch_defaults_to_null(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(repo="acme/no-pref", sandbox_profile="python-3.12")
    assert row["base_branch"] is None


# ── verification (#298/#299) ─────────────────────────────────────────────

_VERIFICATION = {"test_targets": [{"name": "jvm", "paths": ["platform/**"], "command": "cd platform && ./mvnw test"}]}


@pytest.mark.asyncio
async def test_verification_round_trips(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(repo="acme/app", verification=_VERIFICATION)
    assert row["verification"] == _VERIFICATION


@pytest.mark.asyncio
async def test_an_upsert_that_does_not_mention_verification_keeps_it(prefs_repo: RepoPreferencesRepository):
    """Every pre-existing client PUTs the whole object without this field."""
    await prefs_repo.upsert(repo="acme/app", verification=_VERIFICATION)
    row = await prefs_repo.upsert(repo="acme/app", sandbox_profile="python-3.13")
    assert row["sandbox_profile"] == "python-3.13"
    assert row["verification"] == _VERIFICATION


@pytest.mark.asyncio
async def test_an_empty_object_clears_verification(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="acme/app", verification=_VERIFICATION)
    assert (await prefs_repo.upsert(repo="acme/app", verification={}))["verification"] is None
    # …and a fresh row without one stores SQL NULL, not JSON null.
    assert (await prefs_repo.upsert(repo="acme/new"))["verification"] is None


# ── partial update (#350) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_upsert_naming_one_field_leaves_the_others_untouched(prefs_repo: RepoPreferencesRepository):
    checks = [{"name": "py", "paths": [], "command": "pytest -q"}]
    full = {
        "sandbox_profile": "python-3.12",
        "language_hint": "python",
        "notes": "keep me",
        "execution_mode": "cli",
        "auth_mode": "oauth",
        "git_author_name": "Bot",
        "git_author_email": "bot@example.com",
        "dev_checks": checks,
        "base_branch": "dev",
        "verification": _VERIFICATION,
        "pr_review": _PR_REVIEW,
    }
    await prefs_repo.upsert(repo="acme/partial", **full)
    row = await prefs_repo.upsert(repo="acme/partial", language_hint="rust")
    kept = {k: row[k] for k in full}
    assert kept == {**full, "language_hint": "rust"}, f"fields wiped by a one-field upsert: {kept}"


@pytest.mark.asyncio
async def test_an_explicit_none_clears_a_field(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="acme/clear", base_branch="dev", notes="n", verification=_VERIFICATION)
    row = await prefs_repo.upsert(repo="acme/clear", base_branch=None, notes=None, verification=None)
    assert (row["base_branch"], row["notes"], row["verification"]) == (None, "", None)


@pytest.mark.asyncio
async def test_an_unknown_field_is_refused(prefs_repo: RepoPreferencesRepository):
    with pytest.raises(TypeError, match="repo; DROP"):
        await prefs_repo.upsert(repo="acme/x", **{"repo; DROP": "x"})


# ── pr_review (#30) ──────────────────────────────────────────────────────

_PR_REVIEW = {"enabled": True, "depth": "targeted", "model": None}


@pytest.mark.asyncio
async def test_pr_review_round_trips(prefs_repo: RepoPreferencesRepository):
    row = await prefs_repo.upsert(repo="acme/widgets", pr_review=_PR_REVIEW)
    assert row["pr_review"] == _PR_REVIEW


@pytest.mark.asyncio
async def test_an_upsert_that_does_not_mention_pr_review_keeps_it(prefs_repo: RepoPreferencesRepository):
    await prefs_repo.upsert(repo="acme/widgets", pr_review=_PR_REVIEW)
    row = await prefs_repo.upsert(repo="acme/widgets", sandbox_profile="python-3.13")
    assert row["pr_review"] == _PR_REVIEW, "a save of another field wiped the review settings"


@pytest.mark.asyncio
async def test_a_fresh_row_has_no_pr_review(prefs_repo: RepoPreferencesRepository):
    assert (await prefs_repo.upsert(repo="acme/new"))["pr_review"] is None


@pytest.mark.asyncio
async def test_owners_are_stored_and_cleared(prefs_repo: RepoPreferencesRepository):
    """Owner proposals (spec 2026-10-24): the logins that answer for a repository; none falls back to CODEOWNERS."""
    row = await prefs_repo.upsert(repo="acme/app", owners=["octo", "pat"])
    assert row["owners"] == ["octo", "pat"]
    cleared = await prefs_repo.upsert(repo="acme/app", owners=[])
    assert cleared["owners"] is None
