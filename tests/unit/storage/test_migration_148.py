"""Migration 148 — each owner binding stored with '-' and '.' read as '_' names one owner, on its prefix's forge.

Until this release ``github:my-org`` was stored as ``github:my_org``, and a model account bound to an
owner was read on either forge. The migration gives a GitHub binding GitHub's spelling (each '_' a '-',
unless exactly one known GitHub owner reads as it) and removes none of them; it gives a GitLab binding
the one known GitLab owner whose name reads as it, and removes, logs and audits one that several or no
known owners read as. The number is ``VERSION`` alone: renumbering the migration changes that line and
this file's name.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.credentials import CredentialsRepository
from embry0.storage.repositories.repos import ReposRepository

#: This migration's number.
VERSION = 148


def test_the_owner_spelling_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_147.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must give owner bindings one owner each"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


class _Plain:
    async def encrypt(self, plaintext: str) -> str:
        return plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext


#: (stored scope value, role, label, what the migration leaves: a scope value, or None for removed)
BEFORE_AND_AFTER = [
    # GitHub: a login holds no '_' or '.', so each '_' was a '-' ...
    ("github:my_org", "forge", "my-org-bot", "github:my-org"),
    ("github:lone_org", "forge", "lone-org-bot", "github:lone-org"),  # nobody known: still never removed
    ("github:acme", "forge", "acme-bot", "github:acme"),
    # ... unless one known GitHub owner reads as it: a managed user's login holds '_'.
    ("github:octo_emu", "forge", "octo-emu-bot", "github:octo_emu"),
    # GitLab: the one known GitLab owner whose name reads as it (registered as Uni.Grp/app: compared lowercased).
    ("gitlab:uni_grp", "forge", "uni-bot", "gitlab:uni.grp"),
    ("gitlab:other_grp", "forge", "grp-bot", None),  # other-grp (an organization) and other_grp (a repository)
    ("gitlab:lone_grp", "forge", "lone-bot", None),  # nobody known
    ("gitlab:cross_fg", "forge", "cross-bot", None),  # cross-fg is a GitHub owner, not a GitLab one
    ("gitlab:plain", "forge", "plain-bot", "gitlab:plain"),  # no '_': never rewritten
    ("gitlab:new-grp_x", "forge", "exact-bot", "gitlab:new-grp_x"),  # holds '-': written exactly
    # Model accounts follow the same rule, by the forge their prefix names.
    ("github:solo_x", "llm", "solo-x-llm", "github:solo-x"),
    ("github:gl", "llm", "gl-llm", "github:gl"),  # gl is a GitLab group: it no longer serves it
    ("gitlab:dot_io", "llm", "dot-llm", None),  # dot.io is a GitHub owner, not a GitLab one
    ("gitlab:uni_grp", "llm", "uni-llm", "gitlab:uni.grp"),
    ("gitlab:both", "llm", "both-llm", "gitlab:both"),
    # One whose new scope already holds a binding of the same role and provider.
    ("github:dup_x", "llm", "dup-moved-llm", None),
    ("github:dup-x", "llm", "dup-kept-llm", "github:dup-x"),
]


async def _credential(db: DatabasePool, label: str, provider: str, kind: str) -> str:
    row = await CredentialsRepository(db).create(
        kind=kind,
        provider=provider,
        host={"gitlab": "gitlab.example.com", "github": "github.com"}.get(provider, "api.anthropic.com"),
        label=label,
        secret_ciphertext="secret-" + label,
        masked_preview="****",
        created_by="test",
    )
    return str(row["id"])


@pytest.mark.requires_postgres
async def test_each_stored_owner_binding_names_one_owner_or_is_removed_on_record(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO repos (repo, forge) VALUES ('My-Org/widgets', 'github'), ('other_grp/app', 'gitlab'),"
        " ('cross-fg/app', 'github'), ('dot.io/app', 'github'), ('octo_emu/app', 'github'), ('acme/web', 'github'),"
        " ('Uni.Grp/app', 'gitlab'), ('gl/app', 'gitlab'), ('both/app', 'gitlab')"
    )
    await db.execute("INSERT INTO organizations (owner, forge) VALUES ('other-grp', 'gitlab'), ('both', 'github')")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    ids: dict[str, str] = {}
    for value, role, label, _after in BEFORE_AND_AFTER:
        forge = value.split(":", 1)[0]
        provider = "anthropic" if role == "llm" else forge
        ids[label] = await _credential(db, label, provider, "llm_api_key" if role == "llm" else "forge_token")
        # As the release before stored it: straight into the table, as given.
        await db.execute(
            "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by)"
            " VALUES ('owner', $1, $2, $3, $4, 'before')",
            value,
            role,
            provider,
            ids[label],
        )
    stored = {
        str(r["credential_id"]): int(r["id"])
        for r in await db.fetch("SELECT id, credential_id FROM credential_bindings WHERE scope_kind = 'owner'")
    }

    with capture_logs() as logs:
        await run_migrations(db)
        await asyncio.sleep(0)

    label_of = {cid: label for label, cid in ids.items()}
    after = {
        label_of[str(r["credential_id"])]: str(r["scope_value"])
        for r in await db.fetch("SELECT scope_value, credential_id FROM credential_bindings WHERE scope_kind = 'owner'")
    }
    expected = {label: value for _v, _r, label, value in BEFORE_AND_AFTER if value is not None}
    assert after == expected, f"each binding names one owner on its forge, or is gone: {after}"

    removed = [label for _v, _r, label, value in BEFORE_AND_AFTER if value is None]
    warned = sorted(
        e["message"].split(" ", 3)[2]
        for e in logs
        if e.get("event") == "migration_message" and e.get("log_level") == "warning" and e.get("version") == VERSION
    )
    assert warned == sorted(str(stored[ids[label]]) for label in removed), (
        f"each removal is logged as a warning naming the binding: {[e for e in logs if e.get('version') == VERSION]}"
    )
    rows = await db.fetch(
        "SELECT actor, details FROM audit_log WHERE action = 'credential.bound' AND actor = 'embry0:migration'"
    )
    audited = {}
    for r in rows:
        details = r["details"] if isinstance(r["details"], dict) else json.loads(r["details"])
        audited[label_of[details["credential_id"]]] = (
            details["removed_binding_id"],
            details["scope_value"],
            details["reason"],
        )
    old = {label: value for value, _r, label, _a in BEFORE_AND_AFTER}
    assert {label: audited[label][:2] for label in audited} == {
        label: (stored[ids[label]], old[label]) for label in removed
    }, f"each removal leaves an audit row naming the binding and its old value: {audited}"
    assert audited["grp-bot"][2] == "several known owners read as it", audited["grp-bot"]
    assert audited["lone-bot"][2] == "no known owner reads as it", audited["lone-bot"]
    assert audited["dup-moved-llm"][2] == "its new scope already holds a binding of this role and provider"

    resolver = CredentialResolver(
        CredentialsRepository(db),
        _Plain(),
        SimpleNamespace(github_token="", gitlab_token="", gitlab_url="https://gitlab.example.com"),
        environ={},
        repo_forge=ReposRepository(db, default_forge="").forge_or_none,
    )
    forge_tokens = {
        repo: (await resolver.resolve_forge(repo, forge)).label
        for repo, forge in (("My-Org/widgets", "github"), ("octo_emu/app", "github"), ("uni.grp/app", "gitlab"))
    }
    assert forge_tokens == {"My-Org/widgets": "my-org-bot", "octo_emu/app": "octo-emu-bot", "uni.grp/app": "uni-bot"}, (
        f"each known owner resolves the forge token bound for it before: {forge_tokens}"
    )
    assert (await resolver.resolve_llm("uni.grp/app", "anthropic")).label == "uni-llm"
    with pytest.raises(LookupError):
        # github:gl is a GitHub owner's; gl/app is on GitLab.
        await resolver.resolve_llm("gl/app", "anthropic")
    for repo, forge in (("other_grp/app", "gitlab"), ("cross-fg/app", "gitlab")):
        with pytest.raises(LookupError):
            await resolver.resolve_forge(repo, forge)
