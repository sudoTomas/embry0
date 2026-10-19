"""skill_sources and inline_skills repositories (agent identity and skills spec 3.2)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.storage.repositories.skills import (
    InlineSkillsRepository,
    SkillSourceRefused,
    SkillSourcesRepository,
    validate_source_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:someone/skills.git",
        "http://example.com/skills.git",
        "https://user:token@example.com/skills.git",
        "https://127.0.0.1/skills.git",
        "https://10.0.0.5/skills.git",
        "https://[::1]/skills.git",
        "https://localhost/skills.git",
        "https://printer.local/skills.git",
        "https://intranet/skills.git",
        "ssh://example.com/skills.git",
    ],
)
def test_validate_source_url_refuses_non_public_https(url: str) -> None:
    with pytest.raises(SkillSourceRefused):
        validate_source_url("git", url)


def test_validate_source_url_accepts_public_https() -> None:
    url = "https://example.com/someone/skills.git"
    assert validate_source_url("git", url) == url


def test_validate_source_url_path_kind_wants_an_absolute_path() -> None:
    assert validate_source_url("path", "/opt/skills/mine") == "/opt/skills/mine"
    with pytest.raises(SkillSourceRefused):
        validate_source_url("path", "relative/dir")
    with pytest.raises(SkillSourceRefused):
        validate_source_url("path", "/opt/skills/../../etc")


def test_validate_source_url_inline_kind_has_no_url() -> None:
    assert validate_source_url("inline", "") == ""
    with pytest.raises(SkillSourceRefused):
        validate_source_url("inline", "https://example.com/a.git")


@pytest.mark.requires_postgres
async def test_create_rejects_bad_id_kind_and_ref(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    with pytest.raises(SkillSourceRefused, match="id"):
        await repo.create(source_id="Bad Id", kind="git", url="https://example.com/a.git", ref="main")
    with pytest.raises(SkillSourceRefused, match="ref"):
        await repo.create(source_id="ok", kind="git", url="https://example.com/a.git", ref="main; rm -rf /")
    with pytest.raises(SkillSourceRefused, match="kind"):
        await repo.create(source_id="ok", kind="svn", url="https://example.com/a.git", ref="main")
    assert await repo.list_all() == []


@pytest.mark.requires_postgres
async def test_create_and_mark_synced(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    row = await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1.0.0")
    assert row["status"] == "pending" and row["enabled"] is True and row["credential_id"] is None
    skills = [{"name": "plan", "description": "Plans.", "path": "skills/plan/SKILL.md"}]
    row = await repo.mark_synced("mine", resolved_sha="a" * 40, plugin_name="mine", skills=skills)
    assert row["status"] == "ready" and row["resolved_sha"] == "a" * 40 and row["synced_at"] is not None
    assert row["skills"] == skills


@pytest.mark.requires_postgres
async def test_mark_error_records_status_and_truncates(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1")
    row = await repo.mark_error("mine", "x" * 5000)
    assert row["status"] == "error" and len(row["error"]) == 2000
    with pytest.raises(ValueError, match="not found"):
        await repo.mark_error("nope", "boom")


@pytest.mark.requires_postgres
async def test_update_allowlist_and_ref_check(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1")
    row = await repo.update("mine", ref="v2", enabled=False)
    assert row["ref"] == "v2" and row["enabled"] is False
    with pytest.raises(ValueError, match="Unknown fields"):
        await repo.update("mine", url="https://evil.example.com/a.git")
    with pytest.raises(SkillSourceRefused, match="ref"):
        await repo.update("mine", ref="$(id)")
    with pytest.raises(ValueError, match="not found"):
        await repo.update("nope", enabled=True)


@pytest.mark.requires_postgres
async def test_catalog_prefixes_plugin_name_and_marks_availability(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1")
    await repo.mark_synced(
        "mine", resolved_sha="b" * 40, plugin_name="mine", skills=[{"name": "plan", "description": "P", "path": "x"}]
    )
    await repo.create(source_id="off", kind="git", url="https://example.com/b.git", ref="v1")
    await repo.mark_synced(
        "off", resolved_sha="c" * 40, plugin_name="off", skills=[{"name": "z", "description": "Z", "path": "y"}]
    )
    await repo.update("off", enabled=False)
    cat = await repo.catalog()
    assert [c["name"] for c in cat] == ["mine:plan", "off:z"]
    assert cat[0]["available"] is True and cat[1]["available"] is False
    assert cat[0]["source_id"] == "mine" and cat[0]["description"] == "P" and cat[0]["status"] == "ready"
    assert cat[1]["status"] == "disabled"


@pytest.mark.requires_postgres
async def test_delete_refuses_builtin_and_unknown(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="superpowers", kind="git", url="https://example.com/s.git", ref="v1", is_builtin=True)
    with pytest.raises(ValueError, match="built-in"):
        await repo.delete("superpowers")
    with pytest.raises(ValueError, match="not found"):
        await repo.delete("nope")
    await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1")
    await repo.delete("mine")
    assert await repo.get("mine") is None


@pytest.mark.requires_postgres
async def test_inline_upsert_validates_name_and_size(db_with_migrations: Any) -> None:
    repo = InlineSkillsRepository(db_with_migrations)
    row = await repo.upsert(name="house-style", description="How we write.", body="# House style\n")
    assert row["name"] == "house-style"
    row = await repo.upsert(name="house-style", description="Updated.", body="# v2\n")
    assert row["description"] == "Updated." and [r["name"] for r in await repo.list_all()] == ["house-style"]
    with pytest.raises(SkillSourceRefused, match="name"):
        await repo.upsert(name="House Style", description="d", body="b")
    with pytest.raises(SkillSourceRefused, match="64 KiB"):
        await repo.upsert(name="big", description="d", body="x" * (64 * 1024 + 1))
    await repo.delete("house-style")
    assert await repo.get("house-style") is None
    with pytest.raises(ValueError, match="not found"):
        await repo.delete("house-style")
