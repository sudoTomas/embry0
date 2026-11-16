"""Migration 151 — a binding is pinned only where the API pins one: a model account's, at repo or owner scope.

The API and ``CredentialsRepository.replace_bindings`` accept a pin nowhere else (``pin_allowed``), and the
resolver relies on that: a registry error at a step that holds no pin goes on to the environment. The table did
not say so. The migration unpins any other binding stored pinned, logging each and the count, and adds a CHECK
holding the table to the same rule. The number is ``VERSION`` alone: renumbering the migration changes that line
and this file's name.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import asyncpg
import pytest
from structlog.testing import capture_logs

from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.credentials import CredentialsRepository, pin_allowed

#: This migration's number.
VERSION = 151

ROLES = ("forge", "llm", "deploy", "tailnet")
SCOPES = {"repo": "acme/app", "owner": "github:acme", "host": "github.com", "global": ""}


def test_the_pin_rule_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_150.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must hold the table to the pin rule"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


class _Plain:
    async def encrypt(self, plaintext: str) -> str:
        return plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext


async def _credential(db: DatabasePool, label: str, *, llm: bool = False, status: str = "ok") -> str:
    repo = CredentialsRepository(db)
    row = await repo.create(
        kind="llm_api_key" if llm else "forge_token",
        provider="anthropic" if llm else "github",
        host="api.anthropic.com" if llm else "github.com",
        label=label,
        secret_ciphertext="secret-" + label,
        masked_preview="****",
        created_by="test",
    )
    if status != "ok":
        await db.execute("UPDATE credentials SET status = $2 WHERE id = $1", row["id"], status)
    return str(row["id"])


async def _bind(db: DatabasePool, cid: str, scope_kind: str, role: str, provider: str, *, pinned: bool) -> int:
    """Straight into the table, as a release that did not check the pin stored it."""
    return int(
        await db.fetchval(
            "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, pinned,"
            " created_by) VALUES ($1, $2, $3, $4, $5, $6, 'before') RETURNING id",
            scope_kind,
            SCOPES[scope_kind],
            role,
            provider,
            cid,
            pinned,
        )
    )


@pytest.mark.requires_postgres
async def test_a_pin_stored_where_none_is_written_is_cleared_logged_and_refused_after(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await db.execute("ALTER TABLE credential_bindings DROP CONSTRAINT IF EXISTS credential_bindings_pinned_check")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    ids = {
        # Forge accounts pinned at each step, the repository's disabled: pinned, it failed the job.
        "repo-bot": await _bind(
            db, await _credential(db, "repo-bot", status="disabled"), "repo", "forge", "github", pinned=True
        ),
        "owner-bot": await _bind(db, await _credential(db, "owner-bot"), "owner", "forge", "github", pinned=True),
        "host-bot": await _bind(db, await _credential(db, "host-bot"), "host", "forge", "github", pinned=True),
        "pool-llm": await _bind(
            db, await _credential(db, "pool-llm", llm=True), "global", "llm", "anthropic", pinned=True
        ),
        # Pins the API writes: kept.
        "repo-llm": await _bind(
            db, await _credential(db, "repo-llm", llm=True), "repo", "llm", "anthropic", pinned=True
        ),
        "owner-llm": await _bind(
            db, await _credential(db, "owner-llm", llm=True), "owner", "llm", "anthropic", pinned=True
        ),
        # Not pinned: untouched.
        "global-bot": await _bind(db, await _credential(db, "global-bot"), "global", "forge", "github", pinned=False),
    }

    with capture_logs() as logs:
        await run_migrations(db)
        await asyncio.sleep(0)

    label_of = {binding: label for label, binding in ids.items()}
    pinned = {
        label_of[int(r["id"])]: bool(r["pinned"]) for r in await db.fetch("SELECT id, pinned FROM credential_bindings")
    }
    assert pinned == {
        "repo-bot": False,
        "owner-bot": False,
        "host-bot": False,
        "pool-llm": False,
        "repo-llm": True,
        "owner-llm": True,
        "global-bot": False,
    }, pinned

    messages = [
        (e.get("log_level"), str(e["message"]))
        for e in logs
        if e.get("event") == "migration_message" and e.get("version") == VERSION
    ]
    named = sorted(int(m.split(" ", 2)[1]) for level, m in messages if level == "warning" and m.startswith("binding "))
    assert named == sorted(ids[label] for label in ("repo-bot", "owner-bot", "host-bot", "pool-llm")), messages
    assert ("warning", "4 pinned binding(s) unpinned") in messages, f"the count is logged: {messages}"
    assert any(
        m.startswith(f"binding {ids['repo-bot']} (repo acme/app, forge github) unpinned: ") for _level, m in messages
    ), f"each names its scope, role and provider: {messages}"

    # The repository's forge account, no longer pinned, is skipped as any disabled one: the owner's serves.
    resolver = CredentialResolver(
        CredentialsRepository(db),
        _Plain(),
        SimpleNamespace(github_token="", gitlab_token="", gitlab_url=""),
        environ={},
    )
    assert (await resolver.resolve_forge("acme/app", "github")).label == "owner-bot"

    with pytest.raises(asyncpg.CheckViolationError):
        await _bind(db, await _credential(db, "late-bot"), "host", "forge", "github", pinned=True)


@pytest.mark.requires_postgres
async def test_the_table_refuses_exactly_the_pins_pin_allowed_refuses(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    stored: dict[tuple[str, str], bool] = {}
    for role in ROLES:
        for scope_kind in SCOPES:
            cid = await _credential(db, f"{role}-{scope_kind}", llm=role == "llm")
            try:
                await _bind(db, cid, scope_kind, role, "anthropic" if role == "llm" else "github", pinned=True)
            except asyncpg.CheckViolationError:
                stored[(role, scope_kind)] = False
            else:
                stored[(role, scope_kind)] = True
    assert stored == {(role, scope): pin_allowed(role, scope) for role in ROLES for scope in SCOPES}, stored
