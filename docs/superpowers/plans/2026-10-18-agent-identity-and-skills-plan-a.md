# Agent identity and skills, plan A (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent rows carry identity; skills become real file sets that the orchestrator syncs into a shared store, mounts read-only into every sandbox and hands to the Agent SDK as plugins with the effective skill list as the allowlist; the HTTP API and MCP gain the catalog, source, inline, per-agent skill and tool routes.

**Architecture:** One migration (101) adds the identity columns and two tables. A `SkillStore` owns the `/skills` volume layout; a `SkillSourceSync` fills it (git clone with hooks and non-https transports off, directory copy, or rendering of inline rows) and writes each source's skill catalog into its row. `effective_skills` is one pure function, pinned to a JSON fixture the frontend will share. `SandboxManager` mounts every ready source read-only at create; `build_sdk_options` passes `plugins` and `skills`. Routers under `embry0/api/v1/skills.py` and `embry0/api/v1/tools.py`, MCP tools in `embry0/mcp_server/server.py`.

**Tech Stack:** Python 3.12, FastAPI, asyncpg (`DatabasePool`, JSONB codec on both directions), pydantic v2, structlog, `claude-agent-sdk` 0.2.164 (`ClaudeAgentOptions.plugins`, `.skills`), pytest with `asyncio_mode=auto`, ruff (`E F I UP`, line length 120), mypy `strict`.

**Spec:** `docs/superpowers/specs/2026-10-18-agent-identity-and-skills-design.md` (sections 3.1 to 3.5, 3.7, 4, 5; issues A to E). Plan B covers F and G (templates and frontend).

## Global Constraints

- Work in the worktree `/Users/tomas/repos/embry0-worktrees/agent-identity-skills` on branch `feat/agent-identity-skills`; never check out a branch in `/Users/tomas/repos/embry0`.
- Vendor-neutral naming: no customer, host, person or job id in code, tests, fixtures, docs or commit subjects (`CONTRIBUTING.md`, vendor-neutral naming).
- Migration number is **101**, exactly once, contiguous (`tests/unit/storage/test_migration_100.py` hands the "last entry" assertion to `test_migration_101.py`).
- Every new pipeline exception that can reach the executor needs a row in the exception table in `embry0/safety/error_codes.py` (the taxonomy guard test fails otherwise). This plan adds none that reach the executor; sync errors stay inside the sync service.
- Events are built only through constructors in `embry0/execution/events.py`; `docs/contracts/events.json` and the `docs/api.md` events table are regenerated with `python -m embry0.execution.events --write-contract docs/contracts/events.json` and `--render-markdown`.
- All writes to skills, sources, inline skills and agent identity are **admin** tier (`require_admin`).
- Skill names are `<plugin_name>:<skill>`; `plugin_name` comes from the source's `.claude-plugin/plugin.json`.
- Store layout: `/skills/<source_id>/<resolved_sha>/` plus `/skills/<source_id>/current`; sandbox mount `/opt/embry0/skills/<source_id>` read-only.
- A new test is shown failing on the pre-change tree with its own assertion message before the implementation lands. Read the pytest summary line, never only `grep FAILED`.
- Dev extras: `uv sync --extra dev --frozen` in a fresh worktree, then `.venv/bin/python -m pytest`, `.venv/bin/ruff check`, `.venv/bin/ruff format --check`, `.venv/bin/mypy embry0`.
- Database tests: throwaway Postgres per `CLAUDE.md` ("The local test baseline is not green"); `db_with_migrations` drops the schema per test, never a deployment's database.
- Commit messages: Conventional Commits, one concept per commit, no AI co-author footers, trailer `Claude-Session: https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.
- No time estimates anywhere.

## Review Focus

1. **A git source whose remote runs code on clone.** A repository with a `post-checkout` hook or a `.gitattributes` filter must not execute anything on the orchestrator. Task 4 pins hooks off with a fixture repository that would write a sentinel file.
2. **An operator pastes `git@github.com:...` or `http://` or a URL with a token in it.** The source must be refused with a 422 naming the rule, never stored and never attempted. Task 3 (repository validation) and Task 7 (route) pin this.
3. **A sandbox created while `current` points at a tree that was just replaced.** The old tree must stay readable until the rename completes and the mount must name an existing directory. Task 4 pins the rename order; Task 6 pins the missing-path skip.
4. **An agent whose effective list is empty.** The SDK must receive `skills=None`, not `[]`, or every repository skill disappears for agents that never configured skills. Task 6 pins both branches.
5. **A legacy template node with `skills: ["a", "b"]` as a bare list.** It must keep meaning "replace", and triage's old `agent_skills` shape must keep working until the prompt change lands in the same release. Task 5's fixture carries both forms.

---

## Wave plan

| Wave | Tasks | Why together |
|---|---|---|
| 1 | Task 1 | Migration and constants everything else imports |
| 2 | Task 2, Task 3 | Identity surface and skills repositories touch disjoint files |
| 3 | Task 4, Task 5 | Store and sync (filesystem) versus catalog and resolver (pure logic) |
| 4 | Task 6, Task 7 | Sandbox and SDK wiring versus HTTP routes |
| 5 | Task 8 | MCP parity and docs, needs the routes |

Each task gets its own worktree off the integration branch; the controller rebases and fast-forwards per wave, greps for conflict markers and duplicate `def`s, and runs the gate (ruff, mypy, full unit suite on a throwaway Postgres) before the next wave.

---

### Task 1: Migration 101, identity constants, identity contract

**Files:**
- Create: `embry0/agents/identity.py`
- Create: `docs/contracts/identity.json`
- Modify: `embry0/storage/migrations/runner.py` (append to `MIGRATIONS` after version 100, about line 2831)
- Create: `tests/unit/storage/test_migration_101.py`
- Modify: `tests/unit/storage/test_migration_100.py` (remove `test_migration_100_is_the_last_entry`)
- Create: `tests/unit/agents/test_identity.py`

**Interfaces:**
- Produces: `embry0.agents.identity.AVATARS: tuple[str, ...]`, `PALETTE: tuple[str, ...]`, `DEFAULT_AVATAR = "golem"`, `DEFAULT_COLOR = "slate"`, `BUILTIN_IDENTITY: dict[str, dict[str, str]]` (keys `display_name`, `avatar`, `color`, `tagline` per builtin type), `contract() -> dict[str, Any]`, `validate_avatar(value) -> str`, `validate_color(value) -> str` (raise `ValueError` naming the allowed ids).
- Produces: tables `skill_sources`, `inline_skills`; columns `agent_definitions.display_name/avatar/color/tagline`.

- [ ] **Step 1: Write the failing migration test**

`tests/unit/storage/test_migration_101.py`:

```python
"""Migration 101 — agent identity columns, skill_sources and inline_skills (agent identity and skills spec 3.1, 3.2)."""

from __future__ import annotations

import pytest

from embry0.storage.migrations.runner import MIGRATIONS, run_migrations


def test_migration_101_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(101) == 1, "migration 101 (agent identity, skill_sources, inline_skills) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_101_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_100.py)."""
    assert MIGRATIONS[-1][0] == 101, "migration 101 must add agent identity, skill_sources and inline_skills"


@pytest.mark.requires_postgres
async def test_migration_101_adds_identity_columns_and_tables(db_with_migrations) -> None:
    cols = {
        r["column_name"]: r
        for r in await db_with_migrations.fetch(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'agent_definitions'"
        )
    }
    for name in ("display_name", "avatar", "color", "tagline"):
        assert name in cols, f"agent_definitions.{name} missing after migration 101"
        assert cols[name]["is_nullable"] == "NO"
    assert "'golem'" in cols["avatar"]["column_default"]
    assert "'slate'" in cols["color"]["column_default"]
    tables = {
        r["table_name"]
        for r in await db_with_migrations.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    }
    assert {"skill_sources", "inline_skills"} <= tables


@pytest.mark.requires_postgres
async def test_migration_101_writes_builtin_identities(db_with_migrations) -> None:
    row = await db_with_migrations.fetchrow(
        "SELECT display_name, avatar, color FROM agent_definitions WHERE type = 'developer'"
    )
    assert row is not None
    assert (row["display_name"], row["avatar"], row["color"]) == ("Developer", "smith", "amber")


@pytest.mark.requires_postgres
async def test_migration_101_is_idempotent(db_with_migrations) -> None:
    await run_migrations(db_with_migrations)  # second run: every statement is IF NOT EXISTS / guarded
    n = await db_with_migrations.fetchval("SELECT count(*) FROM embry0_migrations WHERE version = 101")
    assert n == 1
```

Delete `test_migration_100_is_the_last_entry` from `tests/unit/storage/test_migration_100.py` (keep the gapless test).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_migration_101.py -q`
Expected: `test_migration_101_present_and_gapless` fails with "migration 101 ... must exist exactly once"; the Postgres tests fail (with `TEST_DATABASE_URL` set) because the columns are missing.

- [ ] **Step 3: Write the identity constants**

`embry0/agents/identity.py`:

```python
"""Agent identity vocabulary: the avatar roster and the color palette.

One source for the backend validators and, through docs/contracts/identity.json,
for the frontend (frontend/src/lib/identity.ts). The frontend keys its sprites
and tints by these ids, so an id is never renamed once released; a new one is
appended.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Final

AVATARS: Final[tuple[str, ...]] = (
    "seer",
    "smith",
    "warden",
    "alchemist",
    "herald",
    "arbiter",
    "scholar",
    "oracle",
    "engineer",
    "rogue",
    "bard",
    "golem",
)
PALETTE: Final[tuple[str, ...]] = ("gold", "cyan", "violet", "rose", "emerald", "amber", "sky", "slate")
DEFAULT_AVATAR: Final = "golem"
DEFAULT_COLOR: Final = "slate"
DISPLAY_NAME_MAX: Final = 60
TAGLINE_MAX: Final = 140

#: Identity of every builtin agent row; seeds and migration 101 write these.
BUILTIN_IDENTITY: Final[dict[str, dict[str, str]]] = {
    "triage": {"display_name": "Triage", "avatar": "seer", "color": "violet", "tagline": "Reads the issue, sets the course."},
    "developer": {"display_name": "Developer", "avatar": "smith", "color": "amber", "tagline": "Forges the change."},
    "review": {"display_name": "Reviewer", "avatar": "warden", "color": "rose", "tagline": "Guards the gate."},
    "qa": {"display_name": "QA", "avatar": "alchemist", "color": "emerald", "tagline": "Proves it in the vessel."},
    "onboarding": {"display_name": "Onboarding", "avatar": "herald", "color": "sky", "tagline": "Announces a new repository."},
    "pr-reviewer": {"display_name": "PR Reviewer", "avatar": "arbiter", "color": "cyan", "tagline": "Weighs a pull request."},
    "research": {"display_name": "Research", "avatar": "scholar", "color": "gold", "tagline": "Reads everything first."},
    "analysis": {"display_name": "Analysis", "avatar": "oracle", "color": "violet", "tagline": "Finds the pattern."},
    "ops": {"display_name": "Ops", "avatar": "engineer", "color": "amber", "tagline": "Keeps it running."},
}


def validate_avatar(value: str) -> str:
    if value not in AVATARS:
        raise ValueError(f"avatar must be one of {', '.join(AVATARS)}; got {value!r}")
    return value


def validate_color(value: str) -> str:
    if value not in PALETTE:
        raise ValueError(f"color must be one of {', '.join(PALETTE)}; got {value!r}")
    return value


def contract() -> dict[str, Any]:
    """The identity vocabulary as docs/contracts/identity.json carries it."""
    return {
        "avatars": list(AVATARS),
        "palette": list(PALETTE),
        "default_avatar": DEFAULT_AVATAR,
        "default_color": DEFAULT_COLOR,
        "display_name_max": DISPLAY_NAME_MAX,
        "tagline_max": TAGLINE_MAX,
        "builtin": BUILTIN_IDENTITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="embry0 agent identity contract")
    parser.add_argument("--write-contract", metavar="PATH", help="write docs/contracts/identity.json")
    args = parser.parse_args(argv)
    if args.write_contract:
        with open(args.write_contract, "w", encoding="utf-8") as fh:
            json.dump(contract(), fh, indent=2, sort_keys=True)
            fh.write("\n")
    else:
        sys.stdout.write(json.dumps(contract(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Write the identity contract test and generate the file**

`tests/unit/agents/test_identity.py`:

```python
"""The identity vocabulary on disk equals the one in code; every builtin has a valid identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embry0.agents import identity

REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "docs" / "contracts" / "identity.json"


def test_contract_file_matches_the_code() -> None:
    assert CONTRACT_PATH.exists(), (
        f"{CONTRACT_PATH} is missing; run: python -m embry0.agents.identity --write-contract docs/contracts/identity.json"
    )
    assert json.loads(CONTRACT_PATH.read_text(encoding="utf-8")) == identity.contract(), (
        "docs/contracts/identity.json differs from embry0/agents/identity.py; regenerate it with "
        "python -m embry0.agents.identity --write-contract docs/contracts/identity.json and commit the diff"
    )


def test_every_builtin_identity_is_valid() -> None:
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    assert set(identity.BUILTIN_IDENTITY) == set(BUILTIN_SEED), "every builtin agent needs an identity and nothing else does"
    for agent_type, ident in identity.BUILTIN_IDENTITY.items():
        identity.validate_avatar(ident["avatar"])
        identity.validate_color(ident["color"])
        assert 0 < len(ident["display_name"]) <= identity.DISPLAY_NAME_MAX, agent_type
        assert len(ident["tagline"]) <= identity.TAGLINE_MAX, agent_type


def test_defaults_are_in_the_vocabulary() -> None:
    assert identity.DEFAULT_AVATAR in identity.AVATARS
    assert identity.DEFAULT_COLOR in identity.PALETTE
    assert len(identity.AVATARS) == 12


@pytest.mark.parametrize("bad", ["", "wizard", "Smith"])
def test_validate_avatar_names_the_roster(bad: str) -> None:
    with pytest.raises(ValueError, match="avatar must be one of seer, smith"):
        identity.validate_avatar(bad)
```

Run: `.venv/bin/python -m embry0.agents.identity --write-contract docs/contracts/identity.json`

- [ ] **Step 5: Append migration 101**

In `embry0/storage/migrations/runner.py`, after the version 100 tuple (the last element of `MIGRATIONS`), append:

```python
    (
        101,
        "agent identity columns, skill_sources and inline_skills (agent identity and skills spec 3.1, 3.2)",
        # Identity: display_name ('' = show the slug), avatar and color from the
        # fixed vocabularies in embry0/agents/identity.py, tagline. The UPDATE
        # writes the builtin identities for rows still at the defaults so an
        # upgraded deployment shows the roster without a reseed; the seeders
        # carry the same values for a fresh database.
        #
        # skill_sources: where skills come from (spec 3.2). credential_id is
        # reserved and always NULL in this release. inline_skills: SKILL.md
        # documents written in the dashboard, rendered into the `local` source.
        """
        ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
        ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS avatar TEXT NOT NULL DEFAULT 'golem';
        ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS color TEXT NOT NULL DEFAULT 'slate';
        ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS tagline TEXT NOT NULL DEFAULT '';

        UPDATE agent_definitions SET display_name = v.display_name, avatar = v.avatar, color = v.color, tagline = v.tagline
        FROM (VALUES
            ('triage',      'Triage',      'seer',      'violet',  'Reads the issue, sets the course.'),
            ('developer',   'Developer',   'smith',     'amber',   'Forges the change.'),
            ('review',      'Reviewer',    'warden',    'rose',    'Guards the gate.'),
            ('qa',          'QA',          'alchemist', 'emerald', 'Proves it in the vessel.'),
            ('onboarding',  'Onboarding',  'herald',    'sky',     'Announces a new repository.'),
            ('pr-reviewer', 'PR Reviewer', 'arbiter',   'cyan',    'Weighs a pull request.'),
            ('research',    'Research',    'scholar',   'gold',    'Reads everything first.'),
            ('analysis',    'Analysis',    'oracle',    'violet',  'Finds the pattern.'),
            ('ops',         'Ops',         'engineer',  'amber',   'Keeps it running.')
        ) AS v(type, display_name, avatar, color, tagline)
        WHERE agent_definitions.type = v.type AND agent_definitions.display_name = '';

        CREATE TABLE IF NOT EXISTS skill_sources (
            id             TEXT PRIMARY KEY,
            kind           TEXT NOT NULL CHECK (kind IN ('git', 'path', 'inline')),
            url            TEXT NOT NULL DEFAULT '',
            ref            TEXT NOT NULL DEFAULT '',
            resolved_sha   TEXT NOT NULL DEFAULT '',
            plugin_name    TEXT NOT NULL DEFAULT '',
            skills         JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(skills) = 'array'),
            status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'error')),
            error          TEXT NOT NULL DEFAULT '',
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            is_builtin     BOOLEAN NOT NULL DEFAULT FALSE,
            credential_id  TEXT,
            synced_at      TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE skill_sources IS
            'Where agent skills come from: a pinned public git repository, a directory in the orchestrator container, or the inline SKILL.md rows. Synced into the skills store and mounted read-only into sandboxes.';

        CREATE TABLE IF NOT EXISTS inline_skills (
            name         TEXT PRIMARY KEY,
            description  TEXT NOT NULL,
            body         TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE inline_skills IS
            'SKILL.md documents written in the dashboard; rendered into the `local` skill source.';
        """,
    ),
```

The `MIGRATIONS` list type is `list[tuple[int, str, str]]`; the tuple order is (version, description, sql), matching every other entry.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_migration_101.py tests/unit/storage/test_migration_100.py tests/unit/agents/test_identity.py -q` with `TEST_DATABASE_URL` pointed at the throwaway Postgres.
Expected: all pass; summary line shows 0 failed. Also run `.venv/bin/python -m pytest tests/unit/storage -q -k migration` to confirm no other migration test pinned "last entry" to 100.

- [ ] **Step 7: Commit**

```bash
git add embry0/agents/identity.py docs/contracts/identity.json embry0/storage/migrations/runner.py tests/unit/storage/test_migration_101.py tests/unit/storage/test_migration_100.py tests/unit/agents/test_identity.py
git commit -m "feat(agents): identity vocabulary, migration 101 with identity columns and skills tables"
```

---

### Task 2: Identity on the repository, API, seeds and MCP

**Files:**
- Modify: `embry0/storage/repositories/agent_definitions.py` (`BUILTIN_SEED` entries, `_ALLOWED_UPDATE_FIELDS`, `create`, `reset`)
- Modify: `embry0/workflows/qa/agent_seed.py`, `embry0/workflows/onboard/agent_seed.py`, `embry0/workflows/pr_review/agent_seed.py` (seed dicts gain identity; seeders pass it)
- Modify: `embry0/storage/seeds/agents_builtin.py`
- Modify: `embry0/api/schemas/__init__.py` (`AgentCreateRequest`, `AgentUpdateRequest`)
- Modify: `embry0/api/v1/agents.py` (`create_agent` passes the new fields)
- Modify: `embry0/mcp_server/server.py`, `embry0/mcp_server/client.py` (identity on `agent_update`; new `agent_create`, `agent_delete`)
- Test: `tests/unit/storage/test_agent_definitions.py`, `tests/unit/api/v1/test_agents.py`, `tests/unit/mcp_server/test_server_agents.py` (create if the directory has no agent tool tests; otherwise extend the existing agent tool test file)

**Interfaces:**
- Consumes: `embry0.agents.identity` from Task 1.
- Produces: `AgentDefinitionsRepository.create(..., display_name: str = "", avatar: str = DEFAULT_AVATAR, color: str = DEFAULT_COLOR, tagline: str = "")`; rows returned by `list_all`, `get`, `create`, `update`, `reset` carry the four fields; `Embry0Client.agent_create(...)`, `agent_delete(agent_type)`.

- [ ] **Step 1: Write the failing repository and API tests**

Add to `tests/unit/storage/test_agent_definitions.py` (Postgres-backed, same fixture style as the file's existing tests):

```python
async def test_create_stores_identity_and_defaults(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    row = await repo.create(agent_type="cartographer", description="maps", model="claude-sonnet-5")
    assert (row["display_name"], row["avatar"], row["color"], row["tagline"]) == ("", "golem", "slate", "")
    row = await repo.create(
        agent_type="lantern", description="lights", model="claude-sonnet-5",
        display_name="Lantern", avatar="bard", color="sky", tagline="Sings the changelog.",
    )
    assert (row["display_name"], row["avatar"], row["color"]) == ("Lantern", "bard", "sky")


async def test_update_accepts_identity_fields(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await repo.create(agent_type="lantern", description="lights", model="claude-sonnet-5")
    row = await repo.update("lantern", display_name="Lantern", avatar="rogue", color="rose", tagline="t")
    assert (row["display_name"], row["avatar"], row["color"], row["tagline"]) == ("Lantern", "rogue", "rose", "t")


async def test_reset_restores_builtin_identity(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await repo.update("developer", display_name="Renamed", avatar="golem", color="slate", tagline="x")
    row = await repo.reset("developer")
    assert (row["display_name"], row["avatar"], row["color"]) == ("Developer", "smith", "amber"), (
        "reset must restore the seeded identity with the rest of the row"
    )
```

Add to `tests/unit/api/v1/test_agents.py`:

```python
@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_avatar(app):
    app.state.agent_defs_repo.create = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "lantern", "description": "d", "model": "claude-sonnet-5", "avatar": "wizard"},
        )
    assert resp.status_code == 422
    assert "avatar must be one of" in resp.text
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_passes_identity_to_repository(app):
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "lantern"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "lantern", "description": "d", "model": "claude-sonnet-5",
                "display_name": "Lantern", "avatar": "bard", "color": "sky", "tagline": "Sings.",
            },
        )
    assert resp.status_code == 201
    kwargs = app.state.agent_defs_repo.create.await_args.kwargs
    assert (kwargs["display_name"], kwargs["avatar"], kwargs["color"], kwargs["tagline"]) == ("Lantern", "bard", "sky", "Sings.")


@pytest.mark.asyncio
async def test_update_agent_rejects_long_tagline(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/api/v1/agents/developer", json={"tagline": "x" * 141})
    assert resp.status_code == 422
```

The `app` fixture in that file uses `auth_dev_mode=True`, which satisfies `require_admin`; `_reject_if_disabled` fails open when `provider_repo` is absent but `ensure_models_priced` runs, so the tests use `claude-sonnet-5`, a priced catalog model.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_agent_definitions.py tests/unit/api/v1/test_agents.py -q`
Expected: the new tests fail (`create() got an unexpected keyword argument 'display_name'`, 201 instead of 422, missing keys).

- [ ] **Step 3: Repository and seeds**

`embry0/storage/repositories/agent_definitions.py`:

```python
from embry0.agents.identity import BUILTIN_IDENTITY, DEFAULT_AVATAR, DEFAULT_COLOR
```

Each `BUILTIN_SEED` entry gains the identity fields by merging at the bottom of the module, so the dicts keep their hand-written form:

```python
for _agent_type, _ident in BUILTIN_IDENTITY.items():
    BUILTIN_SEED[_agent_type].update(_ident)
```

(`qa` and `pr-reviewer` are deep copies, so the update touches the copy only; `QA_AGENT_SEED` and `PR_REVIEWER_AGENT_SEED` themselves also gain the identity keys in their own modules so their seeders can pass them; see below.)

```python
_ALLOWED_UPDATE_FIELDS = {
    "description", "model", "tools", "skills", "system_prompt", "execution_mode", "auth_mode", "mcp_servers",
    "display_name", "avatar", "color", "tagline",
}
```

`create`:

```python
    async def create(
        self,
        agent_type: str,
        description: str,
        model: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        system_prompt: str = "",
        execution_mode: str | None = None,
        auth_mode: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        display_name: str = "",
        avatar: str = DEFAULT_AVATAR,
        color: str = DEFAULT_COLOR,
        tagline: str = "",
    ) -> dict[str, Any]:
        """Insert a new custom agent definition."""
        row = await self._db.fetchrow(
            """
            INSERT INTO agent_definitions
                (type, description, model, tools, skills, system_prompt, is_builtin, execution_mode, auth_mode,
                 mcp_servers, display_name, avatar, color, tagline)
            VALUES ($1, $2, $3, $4, $5, $6, false, $7, $8, $9, $10, $11, $12, $13)
            RETURNING *
            """,
            agent_type, description, model, tools or [], skills or [], system_prompt,
            execution_mode, auth_mode, mcp_servers or {}, display_name, avatar, color, tagline,
        )
```

`reset` adds `display_name = $10, avatar = $11, color = $12, tagline = $13` to the SET clause (the `WHERE type = $14`) and passes `seed["display_name"], seed["avatar"], seed["color"], seed["tagline"]`.

In `embry0/workflows/qa/agent_seed.py`, `embry0/workflows/onboard/agent_seed.py`, `embry0/workflows/pr_review/agent_seed.py`: the seed dict gains `**BUILTIN_IDENTITY["qa"]` (respectively `"onboarding"`, `"pr-reviewer"`) as its last entries, imported from `embry0.agents.identity`; each seeder's `repo.create(...)` and `repo.update(...)` calls pass `display_name=SEED["display_name"], avatar=SEED["avatar"], color=SEED["color"], tagline=SEED["tagline"]`. `embry0/storage/seeds/agents_builtin.py::seed_missing_builtin_agents` passes the same four keys from `seed_fields`.

- [ ] **Step 4: Schemas and route**

`embry0/api/schemas/__init__.py`:

```python
from embry0.agents.identity import DISPLAY_NAME_MAX, TAGLINE_MAX, validate_avatar, validate_color


class AgentCreateRequest(BaseModel):
    ...existing fields...
    display_name: str = Field(default="", max_length=DISPLAY_NAME_MAX)
    avatar: str = "golem"
    color: str = "slate"
    tagline: str = Field(default="", max_length=TAGLINE_MAX)

    @field_validator("avatar")
    @classmethod
    def _avatar(cls, v: str) -> str:
        return validate_avatar(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return validate_color(v)


class AgentUpdateRequest(BaseModel):
    ...existing fields...
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)
    avatar: str | None = None
    color: str | None = None
    tagline: str | None = Field(default=None, max_length=TAGLINE_MAX)

    @field_validator("avatar")
    @classmethod
    def _avatar(cls, v: str | None) -> str | None:
        return None if v is None else validate_avatar(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return None if v is None else validate_color(v)
```

`embry0/api/v1/agents.py::create_agent` passes `display_name=req.display_name, avatar=req.avatar, color=req.color, tagline=req.tagline` to `repo.create`. `update_agent` needs no change: `model_dump(exclude_unset=True)` already forwards the new keys and the repository allowlist accepts them. An explicit `null` for `avatar` or `color` would try to write NULL into a NOT NULL column; add to `update_agent` before the repository call:

```python
    for key in ("display_name", "avatar", "color", "tagline"):
        if key in updates and updates[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
```

- [ ] **Step 5: MCP identity fields, agent_create, agent_delete**

`embry0/mcp_server/client.py`:

```python
    async def agent_create(
        self,
        agent_type: str,
        *,
        description: str,
        model: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        system_prompt: str = "",
        execution_mode: str | None = None,
        auth_mode: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        display_name: str = "",
        avatar: str = "golem",
        color: str = "slate",
        tagline: str = "",
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "type": agent_type, "description": description, "model": model, "tools": tools, "skills": skills,
                "system_prompt": system_prompt, "execution_mode": execution_mode, "auth_mode": auth_mode,
                "mcp_servers": mcp_servers, "display_name": display_name, "avatar": avatar, "color": color,
                "tagline": tagline,
            }
        )
        return _as_dict(await self._request("POST", "/api/v1/agents", body=body))

    async def agent_delete(self, agent_type: str) -> dict[str, Any]:
        return _as_dict(await self._request("DELETE", f"/api/v1/agents/{agent_type}"))
```

`agent_update` gains keyword parameters `display_name`, `avatar`, `color`, `tagline` (all `str | None = None`) added to its body dict.

`embry0/mcp_server/server.py`: `agent_update` gains the same four optional parameters and forwards them; add:

```python
@_tool
async def agent_create(
    agent_type: str,
    description: str,
    model: str,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    system_prompt: str = "",
    execution_mode: str | None = None,
    auth_mode: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    display_name: str = "",
    avatar: str = "golem",
    color: str = "slate",
    tagline: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a custom agent definition. avatar is one of the roster ids and color one of the palette ids
    (see GET /agents for the builtins' values). Requires confirm=true (adds a runnable agent)."""
    if not confirm:
        return _confirmation_refusal("agent_create", f"create agent definition {agent_type!r}")
    return await _get_client().agent_create(
        agent_type, description=description, model=model, tools=tools, skills=skills, system_prompt=system_prompt,
        execution_mode=execution_mode, auth_mode=auth_mode, mcp_servers=mcp_servers, display_name=display_name,
        avatar=avatar, color=color, tagline=tagline,
    )


@_tool
async def agent_delete(agent_type: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a custom agent definition (builtins answer 403). Requires confirm=true (destructive)."""
    if not confirm:
        return _confirmation_refusal("agent_delete", f"delete agent definition {agent_type!r}")
    return await _get_client().agent_delete(agent_type)
```

Add `agent_create` and `agent_delete` to the module docstring's list of confirm-gated tools. Tests for the MCP tools follow the existing pattern in `tests/unit/mcp_server/` (the client is patched with `AsyncMock`; the refusal without `confirm` is asserted; the forwarded kwargs are asserted).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_agent_definitions.py tests/unit/api/v1/test_agents.py tests/unit/api/v1/test_agent_definitions_api.py tests/unit/mcp_server -q` and `.venv/bin/python -m pytest tests/unit/workflows -q -k "seed or agent_seed"`.
Expected: all pass. Then `.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0`.

- [ ] **Step 7: Commit**

```bash
git add embry0/storage/repositories/agent_definitions.py embry0/workflows/qa/agent_seed.py embry0/workflows/onboard/agent_seed.py embry0/workflows/pr_review/agent_seed.py embry0/storage/seeds/agents_builtin.py embry0/api/schemas/__init__.py embry0/api/v1/agents.py embry0/mcp_server/server.py embry0/mcp_server/client.py tests/
git commit -m "feat(agents): identity fields on the repository, API, seeds and MCP; agent_create and agent_delete tools"
```

---

### Task 3: Skill source and inline skill repositories, builtin source seed

**Files:**
- Create: `embry0/storage/repositories/skills.py`
- Create: `embry0/storage/seeds/skill_sources_builtin.py`
- Create: `tests/unit/storage/test_skills_repositories.py`
- Modify: `embry0/api/app.py` (`app.state.skill_sources_repo`, `app.state.inline_skills_repo` next to line 701; seeder call next to `seed_missing_builtin_agents`)

**Interfaces:**
- Produces:

```python
class SkillSourceRefused(ValueError): ...   # a source the table must not store; the route maps it to 422

class SkillSourcesRepository:
    def __init__(self, db: DatabasePool) -> None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def get(self, source_id: str) -> dict[str, Any] | None: ...
    async def create(self, *, source_id: str, kind: str, url: str = "", ref: str = "", enabled: bool = True,
                     is_builtin: bool = False) -> dict[str, Any]: ...   # validates id, kind, url, ref; raises SkillSourceRefused
    async def update(self, source_id: str, **fields: Any) -> dict[str, Any]: ...  # allowlist: ref, enabled, status, error, resolved_sha, plugin_name, skills, synced_at
    async def delete(self, source_id: str) -> None: ...   # ValueError for a builtin or an unknown id
    async def mark_synced(self, source_id: str, *, resolved_sha: str, plugin_name: str, skills: list[dict[str, str]]) -> dict[str, Any]: ...
    async def mark_error(self, source_id: str, error: str) -> dict[str, Any]: ...
    async def catalog(self) -> list[dict[str, Any]]: ...   # [{name, source_id, description, available, status}], sorted by name

class InlineSkillsRepository:
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def get(self, name: str) -> dict[str, Any] | None: ...
    async def upsert(self, *, name: str, description: str, body: str) -> dict[str, Any]: ...  # validates name and body size
    async def delete(self, name: str) -> None: ...

SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
INLINE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
INLINE_BODY_MAX = 64 * 1024
def validate_source_url(kind: str, url: str) -> str: ...   # https only, no userinfo, no IP literal, no private/loopback hostname; path kind: absolute path
```

- Produces: `seed_builtin_skill_sources(repo)` inserting `superpowers` (git, pinned sha) and `local` (inline) if missing.

- [ ] **Step 1: Write the failing tests**

`tests/unit/storage/test_skills_repositories.py`:

```python
"""skill_sources and inline_skills repositories (agent identity and skills spec 3.2)."""

from __future__ import annotations

import pytest

from embry0.storage.repositories.skills import (
    InlineSkillsRepository,
    SkillSourceRefused,
    SkillSourcesRepository,
    validate_source_url,
)

pytestmark = pytest.mark.requires_postgres


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:someone/skills.git",
        "http://example.com/skills.git",
        "https://user:token@example.com/skills.git",
        "https://127.0.0.1/skills.git",
        "https://10.0.0.5/skills.git",
        "https://localhost/skills.git",
        "ssh://example.com/skills.git",
    ],
)
def test_validate_source_url_refuses_non_public_https(url: str) -> None:
    with pytest.raises(SkillSourceRefused):
        validate_source_url("git", url)


def test_validate_source_url_accepts_public_https() -> None:
    assert validate_source_url("git", "https://example.com/someone/skills.git") == "https://example.com/someone/skills.git"


def test_validate_source_url_path_kind_wants_an_absolute_path() -> None:
    assert validate_source_url("path", "/opt/skills/mine") == "/opt/skills/mine"
    with pytest.raises(SkillSourceRefused):
        validate_source_url("path", "relative/dir")


async def test_create_rejects_bad_id_and_ref(db_with_migrations) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    with pytest.raises(SkillSourceRefused, match="id"):
        await repo.create(source_id="Bad Id", kind="git", url="https://example.com/a.git", ref="main")
    with pytest.raises(SkillSourceRefused, match="ref"):
        await repo.create(source_id="ok", kind="git", url="https://example.com/a.git", ref="main; rm -rf /")


async def test_create_and_mark_synced(db_with_migrations) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    row = await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1.0.0")
    assert row["status"] == "pending" and row["enabled"] is True and row["credential_id"] is None
    row = await repo.mark_synced(
        "mine", resolved_sha="a" * 40, plugin_name="mine",
        skills=[{"name": "plan", "description": "Plans.", "path": "skills/plan/SKILL.md"}],
    )
    assert row["status"] == "ready" and row["resolved_sha"] == "a" * 40 and row["synced_at"] is not None


async def test_catalog_prefixes_plugin_name_and_marks_availability(db_with_migrations) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="mine", kind="git", url="https://example.com/a.git", ref="v1")
    await repo.mark_synced("mine", resolved_sha="b" * 40, plugin_name="mine", skills=[{"name": "plan", "description": "P", "path": "x"}])
    await repo.create(source_id="off", kind="git", url="https://example.com/b.git", ref="v1")
    await repo.mark_synced("off", resolved_sha="c" * 40, plugin_name="off", skills=[{"name": "z", "description": "Z", "path": "y"}])
    await repo.update("off", enabled=False)
    cat = await repo.catalog()
    assert [c["name"] for c in cat] == ["mine:plan", "off:z"]
    assert cat[0]["available"] is True and cat[1]["available"] is False


async def test_delete_refuses_builtin(db_with_migrations) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await repo.create(source_id="superpowers", kind="git", url="https://example.com/s.git", ref="v1", is_builtin=True)
    with pytest.raises(ValueError, match="built-in"):
        await repo.delete("superpowers")


async def test_inline_upsert_validates_name_and_size(db_with_migrations) -> None:
    repo = InlineSkillsRepository(db_with_migrations)
    row = await repo.upsert(name="house-style", description="How we write.", body="# House style\n")
    assert row["name"] == "house-style"
    with pytest.raises(SkillSourceRefused, match="name"):
        await repo.upsert(name="House Style", description="d", body="b")
    with pytest.raises(SkillSourceRefused, match="64 KiB"):
        await repo.upsert(name="big", description="d", body="x" * (64 * 1024 + 1))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_skills_repositories.py -q`
Expected: `ModuleNotFoundError: embry0.storage.repositories.skills`.

- [ ] **Step 3: Write the repositories**

`embry0/storage/repositories/skills.py`:

```python
"""skill_sources and inline_skills: where agent skills come from (spec 3.2).

A row here is a promise the sync service keeps: `status` says whether the
store holds the tree, `skills` is the catalog read from it at sync time, and
`plugin_name` is the prefix every skill of the source carries.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
INLINE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Same grammar as embry0/workspace_init/git.py: the ref is passed to git on a command line.
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
INLINE_BODY_MAX = 64 * 1024
SOURCE_KINDS = ("git", "path", "inline")
_UPDATE_FIELDS = {"ref", "enabled", "status", "error", "resolved_sha", "plugin_name", "skills", "synced_at"}
_LOCAL_HOSTS = {"localhost", "localhost.localdomain"}


class SkillSourceRefused(ValueError):
    """The table must not store this source or skill; the API answers 422 with the message."""


def validate_source_url(kind: str, url: str) -> str:
    if kind == "inline":
        if url:
            raise SkillSourceRefused("an inline source has no url")
        return ""
    if kind == "path":
        if not url.startswith("/") or "/../" in f"{url}/":
            raise SkillSourceRefused("a path source names an absolute directory inside the orchestrator container")
        return url.rstrip("/") or "/"
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SkillSourceRefused("a git source is cloned anonymously over https; only https:// urls are accepted")
    if parts.username or parts.password:
        raise SkillSourceRefused("a git source url must not carry credentials")
    host = (parts.hostname or "").lower()
    if not host or host in _LOCAL_HOSTS or host.endswith(".local") or "." not in host:
        raise SkillSourceRefused("a git source url must name a public host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SkillSourceRefused("a git source url must name a host, not an IP address")
    return url


def _check_id(source_id: str) -> str:
    if not SOURCE_ID_RE.match(source_id):
        raise SkillSourceRefused("id must match ^[a-z][a-z0-9-]{0,39}$")
    return source_id


def _check_ref(kind: str, ref: str) -> str:
    if kind != "git":
        if ref:
            raise SkillSourceRefused(f"a {kind} source has no ref")
        return ""
    if not REF_RE.match(ref):
        raise SkillSourceRefused("ref must be a tag, branch or sha matching ^[A-Za-z0-9._/-]{1,255}$")
    return ref


class SkillSourcesRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM skill_sources ORDER BY is_builtin DESC, id")
        return [dict(r) for r in rows]

    async def get(self, source_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM skill_sources WHERE id = $1", source_id)
        return None if row is None else dict(row)

    async def create(
        self, *, source_id: str, kind: str, url: str = "", ref: str = "", enabled: bool = True, is_builtin: bool = False
    ) -> dict[str, Any]:
        if kind not in SOURCE_KINDS:
            raise SkillSourceRefused(f"kind must be one of {', '.join(SOURCE_KINDS)}")
        _check_id(source_id)
        url = validate_source_url(kind, url)
        ref = _check_ref(kind, ref)
        row = await self._db.fetchrow(
            """
            INSERT INTO skill_sources (id, kind, url, ref, enabled, is_builtin)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            source_id, kind, url, ref, enabled, is_builtin,
        )
        assert row is not None
        logger.info("skill_source_created", source_id=source_id, kind=kind)
        return dict(row)

    async def update(self, source_id: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - _UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown fields for update: {sorted(unknown)}")
        if "ref" in fields:
            fields["ref"] = _check_ref("git", str(fields["ref"]))
        if not fields:
            row = await self.get(source_id)
            if row is None:
                raise ValueError(f"skill source {source_id!r} not found")
            return row
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=1))
        params: list[Any] = [*fields.values(), source_id]
        row = await self._db.fetchrow(
            f"UPDATE skill_sources SET {sets}, updated_at = NOW() WHERE id = ${len(params)} RETURNING *", *params
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def mark_synced(
        self, source_id: str, *, resolved_sha: str, plugin_name: str, skills: list[dict[str, str]]
    ) -> dict[str, Any]:
        row = await self._db.fetchrow(
            """
            UPDATE skill_sources
            SET status = 'ready', error = '', resolved_sha = $1, plugin_name = $2, skills = $3,
                synced_at = NOW(), updated_at = NOW()
            WHERE id = $4 RETURNING *
            """,
            resolved_sha, plugin_name, skills, source_id,
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def mark_error(self, source_id: str, error: str) -> dict[str, Any]:
        row = await self._db.fetchrow(
            "UPDATE skill_sources SET status = 'error', error = $1, updated_at = NOW() WHERE id = $2 RETURNING *",
            error[:2000], source_id,
        )
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        return dict(row)

    async def delete(self, source_id: str) -> None:
        row = await self.get(source_id)
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        if row["is_builtin"]:
            raise ValueError(f"cannot delete built-in skill source {source_id!r}")
        await self._db.execute("DELETE FROM skill_sources WHERE id = $1", source_id)
        logger.info("skill_source_deleted", source_id=source_id)

    async def catalog(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for src in await self.list_all():
            prefix = src["plugin_name"] or src["id"]
            available = src["status"] == "ready" and bool(src["enabled"])
            for skill in src["skills"] or []:
                out.append(
                    {
                        "name": f"{prefix}:{skill['name']}",
                        "source_id": src["id"],
                        "description": skill.get("description", ""),
                        "available": available,
                        "status": src["status"] if src["enabled"] else "disabled",
                    }
                )
        return sorted(out, key=lambda c: c["name"])


class InlineSkillsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM inline_skills ORDER BY name")
        return [dict(r) for r in rows]

    async def get(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM inline_skills WHERE name = $1", name)
        return None if row is None else dict(row)

    async def upsert(self, *, name: str, description: str, body: str) -> dict[str, Any]:
        if not INLINE_NAME_RE.match(name):
            raise SkillSourceRefused("name must match ^[a-z][a-z0-9-]{0,63}$")
        if not description.strip():
            raise SkillSourceRefused("description is required")
        if len(body.encode("utf-8")) > INLINE_BODY_MAX:
            raise SkillSourceRefused("body must be at most 64 KiB")
        row = await self._db.fetchrow(
            """
            INSERT INTO inline_skills (name, description, body) VALUES ($1, $2, $3)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, body = EXCLUDED.body, updated_at = NOW()
            RETURNING *
            """,
            name, description, body,
        )
        assert row is not None
        return dict(row)

    async def delete(self, name: str) -> None:
        status = await self._db.execute("DELETE FROM inline_skills WHERE name = $1", name)
        if status.endswith(" 0"):
            raise ValueError(f"inline skill {name!r} not found")
```

- [ ] **Step 4: Builtin source seed and app wiring**

`embry0/storage/seeds/skill_sources_builtin.py`:

```python
"""Builtin skill sources (spec 3.2): insert-if-missing, never overwriting."""

from __future__ import annotations

import structlog

from embry0.storage.repositories.skills import SkillSourcesRepository

logger = structlog.get_logger(__name__)

SUPERPOWERS_URL = "https://github.com/obra/superpowers.git"
#: The commit of release tag v6.4.2 (the 6.4 line is where the seeded skill
#: names, e.g. superpowers:writing-plans, are known to exist; v7.0.0 exists
#: and is a separate evaluation). Bumping it is a deliberate one-line change
#: reviewed like any dependency bump; never a branch name.
SUPERPOWERS_REF = "8ca22dba9a94f28898bbce59f2537ff4d87c747d"

BUILTIN_SOURCES: tuple[dict[str, str], ...] = (
    {"source_id": "superpowers", "kind": "git", "url": SUPERPOWERS_URL, "ref": SUPERPOWERS_REF},
    {"source_id": "local", "kind": "inline", "url": "", "ref": ""},
)


async def seed_builtin_skill_sources(repo: SkillSourcesRepository) -> None:
    for spec in BUILTIN_SOURCES:
        if await repo.get(spec["source_id"]) is not None:
            continue
        await repo.create(**spec, is_builtin=True)
        logger.info("builtin_skill_source_seeded", source_id=spec["source_id"])
```

The sha was resolved on 2026-10-18 with `git ls-remote --tags https://github.com/obra/superpowers.git` (`refs/tags/v6.4.2^{}`). A test asserts `re.fullmatch(r"[0-9a-f]{40}", SUPERPOWERS_REF)` so a branch name cannot ship, and a second test asserts every `superpowers:*` name in `BUILTIN_SEED` appears in a checked-in list `tests/fixtures/skills/superpowers-v6.4.2-skills.txt` (the `skills/*/` directory names of that tag, written by the implementer from `git ls-tree`), so a future bump that drops a seeded skill fails here rather than as an orphan at runtime.

`embry0/api/app.py`: next to line 701 add `app.state.skill_sources_repo = SkillSourcesRepository(db)` and `app.state.inline_skills_repo = InlineSkillsRepository(db)`; after the `seed_missing_builtin_agents` call add:

```python
    from embry0.storage.seeds.skill_sources_builtin import seed_builtin_skill_sources

    await seed_builtin_skill_sources(SkillSourcesRepository(db))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/test_skills_repositories.py -q` (Postgres set). Add `tests/unit/storage/test_skill_sources_seed.py` with the sha-shape test and a test that the seeder inserts both rows once (second call inserts nothing). Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add embry0/storage/repositories/skills.py embry0/storage/seeds/skill_sources_builtin.py embry0/api/app.py tests/unit/storage/test_skills_repositories.py tests/unit/storage/test_skill_sources_seed.py
git commit -m "feat(skills): skill_sources and inline_skills repositories, builtin superpowers and local sources"
```

---

### Task 4: Skill store and sync service, compose volume, startup sync

**Files:**
- Create: `embry0/skills/__init__.py`, `embry0/skills/store.py`, `embry0/skills/sync.py`
- Modify: `embry0/config.py` (add `skills_store_path: Path = Path("/skills")` near `image_registry`)
- Modify: `embry0/api/app.py` (construct `SkillStore` and `SkillSourceSync`; `app.state.skill_store`, `app.state.skill_sync`; startup background sync task registered in `app.state.background_tasks`)
- Modify: `infra/docker-compose.yml` (`skills-store` volume on `orchestrator` at `/skills` and on `dind` at `/skills:ro`; declare under `volumes:`)
- Modify: `infra/Dockerfile.orchestrator` (`mkdir -p /data /skills && chown orchestrator:orchestrator /data /skills`)
- Create: `tests/unit/skills/__init__.py`, `tests/unit/skills/test_store.py`, `tests/unit/skills/test_sync.py`
- Create: `tests/unit/infra/test_skills_store_compose.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class SkillMount:
    source_id: str
    plugin_name: str
    host_path: str        # /skills/<id>/<sha>, the path DinD bind-mounts
    container_path: str   # /opt/embry0/skills/<id>

class SkillStore:
    SANDBOX_ROOT = "/opt/embry0/skills"
    def __init__(self, root: Path) -> None: ...
    def source_dir(self, source_id: str) -> Path: ...
    def current_sha(self, source_id: str) -> str | None: ...        # reads <root>/<id>/current
    def tree_path(self, source_id: str, sha: str) -> Path: ...
    def publish(self, source_id: str, sha: str, staged: Path) -> Path: ...  # rename staged -> tree_path, write current, remove older trees
    def mounts(self, sources: Iterable[Mapping[str, Any]]) -> list[SkillMount]: ...  # ready+enabled rows whose current tree exists
    def remove_source(self, source_id: str) -> None: ...

class SkillSyncError(Exception): ...

class SkillSourceSync:
    def __init__(self, store: SkillStore, repo: SkillSourcesRepository, inline_repo: InlineSkillsRepository,
                 *, git_timeout: float = 120.0, max_tree_bytes: int = 64 * 1024 * 1024) -> None: ...
    async def sync(self, source_id: str) -> dict[str, Any]: ...     # the updated row; raises SkillSyncBusy when one runs
    async def sync_missing(self) -> None: ...                       # startup: every enabled source with no current tree
    def is_syncing(self, source_id: str) -> bool: ...

class SkillSyncBusy(Exception): ...

def parse_skill_frontmatter(text: str) -> tuple[str | None, str]: ...   # (name, description) from a SKILL.md
```

- [ ] **Step 1: Write the failing store tests**

`tests/unit/skills/test_store.py`:

```python
"""SkillStore: the /skills layout and its atomic publish (spec 3.2)."""

from __future__ import annotations

from pathlib import Path

from embry0.skills.store import SkillStore


def _stage(tmp_path: Path, name: str) -> Path:
    staged = tmp_path / "staging" / name
    (staged / "skills" / "plan").mkdir(parents=True)
    (staged / "skills" / "plan" / "SKILL.md").write_text("---\nname: plan\n---\n", encoding="utf-8")
    return staged


def test_publish_moves_the_tree_writes_current_and_drops_older_trees(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    first = store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    assert first == store.tree_path("mine", "a" * 40) and first.is_dir()
    assert store.current_sha("mine") == "a" * 40
    second = store.publish("mine", "b" * 40, _stage(tmp_path, "two"))
    assert second.is_dir() and not first.exists(), "an older tree is removed after the new one is current"
    assert store.current_sha("mine") == "b" * 40
    assert not (tmp_path / "staging" / "two").exists(), "publish renames, it does not copy"


def test_current_sha_is_none_for_an_unknown_source(tmp_path: Path) -> None:
    assert SkillStore(tmp_path).current_sha("nothing") is None


def test_mounts_only_ready_enabled_sources_with_a_tree(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.publish("mine", "a" * 40, _stage(tmp_path, "one"))
    rows = [
        {"id": "mine", "plugin_name": "mine", "status": "ready", "enabled": True, "resolved_sha": "a" * 40},
        {"id": "off", "plugin_name": "off", "status": "ready", "enabled": False, "resolved_sha": "a" * 40},
        {"id": "broken", "plugin_name": "broken", "status": "error", "enabled": True, "resolved_sha": ""},
        {"id": "wiped", "plugin_name": "wiped", "status": "ready", "enabled": True, "resolved_sha": "c" * 40},
    ]
    mounts = store.mounts(rows)
    assert [m.source_id for m in mounts] == ["mine"]
    assert mounts[0].host_path == str(tmp_path / "skills" / "mine" / ("a" * 40))
    assert mounts[0].container_path == "/opt/embry0/skills/mine"
```

- [ ] **Step 2: Write the failing sync tests**

`tests/unit/skills/test_sync.py` uses a local bare repository as the upstream (no network). The repository fixture (`tests/unit/skills/conftest.py`) runs `git init`, commits a `skills/plan/SKILL.md` and a `.claude-plugin/plugin.json` with `{"name": "fixture-plugin"}`, tags `v1.0.0`, and installs a `post-checkout` hook into the upstream's `.git/hooks` that writes `/tmp/<random>` (hooks of a clone's origin never run on the clone side, so the test additionally creates a `.gitattributes` with a `filter=evil` line plus a `.git/config` filter in the upstream to make sure config is not inherited). The sentinel path is passed through an environment variable the hook reads; the test asserts the sentinel does not exist after sync.

```python
"""SkillSourceSync: git, path and inline sources into the store (spec 3.2)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.skills.store import SkillStore
from embry0.skills.sync import SkillSourceSync, SkillSyncBusy, parse_skill_frontmatter


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "upstream"
    (repo / "skills" / "plan").mkdir(parents=True)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "fixture-plugin"}), encoding="utf-8")
    (repo / "skills" / "plan" / "SKILL.md").write_text(
        "---\nname: plan\ndescription: Plans the work.\n---\n# Plan\n", encoding="utf-8"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "skills")
    _git(repo, "tag", "v1.0.0")
    sha = _git(repo, "rev-parse", "HEAD")
    return repo, sha


def _repo_mock(row: dict) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    repo.mark_synced = AsyncMock(side_effect=lambda sid, **kw: {**row, **kw, "status": "ready"})
    repo.mark_error = AsyncMock(side_effect=lambda sid, error: {**row, "status": "error", "error": error})
    return repo


async def test_git_sync_by_tag_publishes_tree_and_catalog(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    row = {"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True}
    repo = _repo_mock(row)
    sync = SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True)
    out = await sync.sync("fx")
    assert out["status"] == "ready"
    kwargs = repo.mark_synced.await_args.kwargs
    assert kwargs["resolved_sha"] == sha
    assert kwargs["plugin_name"] == "fixture-plugin"
    assert kwargs["skills"] == [{"name": "plan", "description": "Plans the work.", "path": "skills/plan/SKILL.md"}]
    tree = store.tree_path("fx", sha)
    assert (tree / "skills" / "plan" / "SKILL.md").is_file()
    assert not (tree / ".git").exists(), "the .git directory is not published"


async def test_git_sync_by_sha(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": sha, "enabled": True})
    await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert store.current_sha("fx") == sha


async def test_git_sync_never_runs_upstream_hooks_or_filters(tmp_path: Path, upstream, monkeypatch) -> None:
    repo_path, _ = upstream
    sentinel = tmp_path / "sentinel"
    hook = repo_path / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    hook.chmod(0o755)
    # A global git config that would run a filter on checkout; GIT_CONFIG_GLOBAL must point away from it.
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[filter \"evil\"]\n\tsmudge = touch {sentinel}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    (repo_path / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".gitattributes")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "attrs")
    _git(repo_path, "tag", "-f", "v1.0.0")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert not sentinel.exists(), "a hook or filter from the source ran on the orchestrator"


async def test_git_sync_refuses_a_tree_without_skills(tmp_path: Path) -> None:
    repo_path = tmp_path / "empty"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("no skills\n", encoding="utf-8")
    _git(repo_path, "init", "-q", "-b", "main")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "x")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "main", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert out["status"] == "error"
    assert "no skills/*/SKILL.md" in repo.mark_error.await_args.args[1]
    assert store.current_sha("fx") is None


async def test_git_sync_refuses_an_oversized_tree(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), max_tree_bytes=10, allow_file_urls_for_tests=True).sync("fx")
    assert out["status"] == "error" and "64 MiB" not in out["error"] and "larger than" in out["error"]


async def test_git_sync_timeout_is_an_error_not_a_hang(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    sync = SkillSourceSync(store, repo, MagicMock(), git_timeout=0.0001, allow_file_urls_for_tests=True)
    out = await sync.sync("fx")
    assert out["status"] == "error" and "timed out" in out["error"]


async def test_failed_sync_keeps_the_previous_tree(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    row = {"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True}
    repo = _repo_mock(row)
    sync = SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True)
    await sync.sync("fx")
    row["ref"] = "does-not-exist"
    out = await sync.sync("fx")
    assert out["status"] == "error"
    assert store.current_sha("fx") == sha and store.tree_path("fx", sha).is_dir()


async def test_concurrent_sync_is_busy(tmp_path: Path, upstream) -> None:
    import asyncio

    repo_path, _ = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    sync = SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True)
    first = asyncio.create_task(sync.sync("fx"))
    await asyncio.sleep(0)
    with pytest.raises(SkillSyncBusy):
        await sync.sync("fx")
    await first


async def test_path_sync_copies_and_generates_plugin_json(tmp_path: Path) -> None:
    src = tmp_path / "mine"
    (src / "skills" / "house").mkdir(parents=True)
    (src / "skills" / "house" / "SKILL.md").write_text("---\nname: house\ndescription: House style.\n---\n", encoding="utf-8")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "mine", "kind": "path", "url": str(src), "ref": "", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock()).sync("mine")
    assert out["status"] == "ready"
    sha = repo.mark_synced.await_args.kwargs["resolved_sha"]
    plugin = json.loads((store.tree_path("mine", sha) / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "mine"
    assert repo.mark_synced.await_args.kwargs["plugin_name"] == "mine"


async def test_inline_sync_renders_rows(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True})
    inline = MagicMock()
    inline.list_all = AsyncMock(return_value=[{"name": "house-style", "description": "How we write.", "body": "# Rules\n"}])
    out = await SkillSourceSync(store, repo, inline).sync("local")
    assert out["status"] == "ready"
    sha = repo.mark_synced.await_args.kwargs["resolved_sha"]
    text = (store.tree_path("local", sha) / "skills" / "house-style" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: house-style\ndescription: How we write.\n---\n# Rules\n")
    assert repo.mark_synced.await_args.kwargs["plugin_name"] == "local"


async def test_inline_sync_with_no_rows_is_an_empty_but_ready_plugin(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True})
    inline = MagicMock()
    inline.list_all = AsyncMock(return_value=[])
    out = await SkillSourceSync(store, repo, inline).sync("local")
    assert out["status"] == "ready" and repo.mark_synced.await_args.kwargs["skills"] == []


def test_parse_skill_frontmatter() -> None:
    assert parse_skill_frontmatter("---\nname: a\ndescription: B c.\n---\nbody") == ("a", "B c.")
    assert parse_skill_frontmatter("# no frontmatter") == (None, "")
    assert parse_skill_frontmatter("---\ndescription: 'quoted'\n---\n") == (None, "quoted")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/skills -q`
Expected: `ModuleNotFoundError: embry0.skills`.

- [ ] **Step 4: Write the store**

`embry0/skills/__init__.py` is empty. `embry0/skills/store.py`:

```python
"""The skills store: /skills/<source_id>/<resolved_sha>/ trees and a `current` pointer (spec 3.2).

The orchestrator writes here; DinD sees the same volume at the same path and
bind-mounts a tree read-only into each sandbox at /opt/embry0/skills/<id>.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CURRENT = "current"


@dataclass(frozen=True)
class SkillMount:
    source_id: str
    plugin_name: str
    host_path: str
    container_path: str


class SkillStore:
    SANDBOX_ROOT = "/opt/embry0/skills"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def source_dir(self, source_id: str) -> Path:
        return self._root / source_id

    def tree_path(self, source_id: str, sha: str) -> Path:
        return self.source_dir(source_id) / sha

    def staging_dir(self, source_id: str) -> Path:
        """A sibling of the final tree, so the publish rename stays on one filesystem."""
        path = self.source_dir(source_id) / ".staging"
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    def current_sha(self, source_id: str) -> str | None:
        pointer = self.source_dir(source_id) / CURRENT
        try:
            sha = pointer.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return sha or None

    def publish(self, source_id: str, sha: str, staged: Path) -> Path:
        """Rename ``staged`` into place, point ``current`` at it, drop older trees."""
        target = self.tree_path(source_id, sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        Path(staged).rename(target)
        pointer = self.source_dir(source_id) / CURRENT
        tmp = pointer.with_suffix(".tmp")
        tmp.write_text(sha + "\n", encoding="utf-8")
        tmp.replace(pointer)
        for entry in self.source_dir(source_id).iterdir():
            if entry.is_dir() and entry.name not in {sha, ".staging"}:
                shutil.rmtree(entry, ignore_errors=True)
        logger.info("skill_source_published", source_id=source_id, sha=sha)
        return target

    def mounts(self, sources: Iterable[Mapping[str, Any]]) -> list[SkillMount]:
        out: list[SkillMount] = []
        for src in sources:
            if src.get("status") != "ready" or not src.get("enabled"):
                continue
            sha = self.current_sha(str(src["id"]))
            if sha is None or not self.tree_path(str(src["id"]), sha).is_dir():
                logger.warning("skill_source_mount_missing", source_id=src["id"], sha=sha)
                continue
            out.append(
                SkillMount(
                    source_id=str(src["id"]),
                    plugin_name=str(src.get("plugin_name") or src["id"]),
                    host_path=str(self.tree_path(str(src["id"]), sha)),
                    container_path=f"{self.SANDBOX_ROOT}/{src['id']}",
                )
            )
        return out

    def remove_source(self, source_id: str) -> None:
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)
```

- [ ] **Step 5: Write the sync service**

`embry0/skills/sync.py`:

```python
"""Fill the skills store from a source (spec 3.2): git clone, directory copy, or inline rows.

The orchestrator runs git here, on a tree the admin chose and pinned, with
hooks, non-https transports and every inherited config disabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog

from embry0.safety.redaction import redact_log_text
from embry0.skills.store import SkillStore
from embry0.storage.repositories.skills import InlineSkillsRepository, SkillSourcesRepository

logger = structlog.get_logger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_PLUGIN_JSON = Path(".claude-plugin") / "plugin.json"


class SkillSyncError(Exception):
    """Why a sync failed; stored (redacted) on the row."""


class SkillSyncBusy(Exception):
    """A sync for this source is already running."""


def parse_skill_frontmatter(text: str) -> tuple[str | None, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, ""
    name: str | None = None
    description = ""
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip().strip("'\"")
        if key.strip() == "name":
            name = value or None
        elif key.strip() == "description":
            description = value
    return name, description


def _git_env(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ASKPASS": "/bin/false",
        "LC_ALL": "C",
    }


_GIT_SAFETY = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
    "-c", "core.fsmonitor=false",
    "-c", "submodule.recurse=false",
]


class SkillSourceSync:
    def __init__(
        self,
        store: SkillStore,
        repo: SkillSourcesRepository,
        inline_repo: InlineSkillsRepository,
        *,
        git_timeout: float = 120.0,
        max_tree_bytes: int = 64 * 1024 * 1024,
        allow_file_urls_for_tests: bool = False,
    ) -> None:
        self._store = store
        self._repo = repo
        self._inline = inline_repo
        self._git_timeout = git_timeout
        self._max_tree_bytes = max_tree_bytes
        self._allow_file = allow_file_urls_for_tests
        self._locks: dict[str, asyncio.Lock] = {}

    def is_syncing(self, source_id: str) -> bool:
        lock = self._locks.get(source_id)
        return lock is not None and lock.locked()

    async def sync(self, source_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(source_id, asyncio.Lock())
        if lock.locked():
            raise SkillSyncBusy(source_id)
        async with lock:
            row = await self._repo.get(source_id)
            if row is None:
                raise ValueError(f"skill source {source_id!r} not found")
            staged = self._store.staging_dir(source_id)
            try:
                sha, plugin_name, skills = await self._fill(row, staged)
                self._store.publish(source_id, sha, staged)
                updated = await self._repo.mark_synced(source_id, resolved_sha=sha, plugin_name=plugin_name, skills=skills)
                logger.info("skill_source_synced", source_id=source_id, sha=sha, skills=len(skills))
                return updated
            except SkillSyncError as exc:
                shutil.rmtree(staged, ignore_errors=True)
                message = redact_log_text(str(exc))
                logger.warning("skill_source_sync_failed", source_id=source_id, error=message)
                return await self._repo.mark_error(source_id, message)

    async def sync_missing(self) -> None:
        """Startup: every enabled source with no tree in the store. Never raises."""
        for row in await self._repo.list_all():
            if not row["enabled"] or self._store.current_sha(row["id"]) is not None:
                continue
            try:
                await self.sync(row["id"])
            except (SkillSyncBusy, ValueError) as exc:
                logger.warning("skill_source_startup_sync_skipped", source_id=row["id"], error=str(exc))

    async def _fill(self, row: dict[str, Any], staged: Path) -> tuple[str, str, list[dict[str, str]]]:
        kind = row["kind"]
        if kind == "git":
            sha = await self._clone(row["url"], row["ref"], staged)
        elif kind == "path":
            self._copy_dir(Path(row["url"]), staged)
            sha = _content_hash(staged)
        elif kind == "inline":
            await self._render_inline(staged)
            sha = _content_hash(staged)
        else:
            raise SkillSyncError(f"unknown source kind {kind!r}")
        self._check_size(staged)
        plugin_name = self._ensure_plugin_json(staged, default_name=row["id"])
        skills = _catalog(staged)
        if kind != "inline" and not skills:
            raise SkillSyncError("the tree has no skills/*/SKILL.md; a skill source must carry at least one skill")
        return sha, plugin_name, skills

    async def _clone(self, url: str, ref: str, staged: Path) -> str:
        if url.startswith("file://") and not self._allow_file:
            raise SkillSyncError("file:// urls are not accepted")
        home = Path(tempfile.mkdtemp(prefix="embry0-skills-home-"))
        try:
            if _SHA_RE.match(ref):
                await self._git(home, staged, "init", "-q")
                await self._git(home, staged, "remote", "add", "origin", url)
                await self._git(home, staged, "fetch", "-q", "--depth", "1", "--no-recurse-submodules", "origin", ref)
                await self._git(home, staged, "checkout", "-q", "--no-recurse-submodules", "FETCH_HEAD")
            else:
                parent = staged.parent
                shutil.rmtree(staged, ignore_errors=True)
                await self._git(
                    home, parent, "clone", "-q", "--depth", "1", "--no-recurse-submodules", "--branch", ref, url, staged.name
                )
            sha = (await self._git(home, staged, "rev-parse", "HEAD")).strip()
        finally:
            shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(staged / ".git", ignore_errors=True)
        if not _SHA_RE.match(sha):
            raise SkillSyncError(f"git did not report a commit sha for {ref!r}")
        return sha

    async def _git(self, home: Path, cwd: Path, *args: str) -> str:
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", *_GIT_SAFETY, *args,
            cwd=str(cwd), env=_git_env(home),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._git_timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SkillSyncError(f"git {args[0]} timed out after {self._git_timeout:g}s") from None
        if proc.returncode != 0:
            raise SkillSyncError(f"git {args[0]} failed: {err.decode('utf-8', 'replace').strip()[:500]}")
        return out.decode("utf-8", "replace")

    def _copy_dir(self, source: Path, staged: Path) -> None:
        if not source.is_dir():
            raise SkillSyncError(f"{source} is not a directory in the orchestrator container")
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(source, staged, symlinks=False, ignore=shutil.ignore_patterns(".git"))

    async def _render_inline(self, staged: Path) -> None:
        for row in await self._inline.list_all():
            skill_dir = staged / "skills" / row["name"]
            skill_dir.mkdir(parents=True, exist_ok=True)
            description = str(row["description"]).replace("\n", " ").strip()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {row['name']}\ndescription: {description}\n---\n{row['body']}", encoding="utf-8"
            )
        (staged / "skills").mkdir(parents=True, exist_ok=True)

    def _check_size(self, staged: Path) -> None:
        total = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file())
        if total > self._max_tree_bytes:
            raise SkillSyncError(f"the tree is {total} bytes, larger than the {self._max_tree_bytes} byte cap")

    @staticmethod
    def _ensure_plugin_json(staged: Path, *, default_name: str) -> str:
        path = staged / _PLUGIN_JSON
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SkillSyncError(f".claude-plugin/plugin.json is not JSON: {exc}") from None
            name = str(data.get("name") or "").strip()
            if not name:
                raise SkillSyncError(".claude-plugin/plugin.json has no name")
            return name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": default_name, "version": "0.0.0"}, indent=2) + "\n", encoding="utf-8")
        return default_name


def _catalog(staged: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    skills_dir = staged / "skills"
    if not skills_dir.is_dir():
        return out
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name, description = parse_skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        out.append(
            {
                "name": name or skill_md.parent.name,
                "description": description,
                "path": str(skill_md.relative_to(staged)),
            }
        )
    return out


def _content_hash(root: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
```

Notes for the implementer: the `file://` allowance exists only for tests (the repository layer already refuses non-https urls, so production never reaches it); `protocol.allow=never` with `protocol.https.allow=always` still refuses `file://` unless the test also passes `-c protocol.file.allow=always`, so when `allow_file_urls_for_tests` is set append `"-c", "protocol.file.allow=always"` to the safety list for that instance.

- [ ] **Step 6: Config, app wiring, compose, Dockerfile**

`embry0/config.py`, next to `image_registry`:

```python
    # Where skill sources are synced to (spec 3.2). The compose file mounts the
    # `skills-store` volume here and at the same path in dind, because the
    # sandbox bind mounts are resolved in dind's namespace.
    skills_store_path: Path = Path("/skills")
```

`embry0/api/app.py`, after the SandboxManager construction block (around line 1206):

```python
    from embry0.skills.store import SkillStore
    from embry0.skills.sync import SkillSourceSync

    skill_store = SkillStore(config.skills_store_path)
    app.state.skill_store = skill_store
    app.state.skill_sync = SkillSourceSync(skill_store, app.state.skill_sources_repo, app.state.inline_skills_repo)
    sync_task = asyncio.create_task(app.state.skill_sync.sync_missing())
    app.state.background_tasks.add(sync_task)
    sync_task.add_done_callback(app.state.background_tasks.discard)
```

(`app.state.skill_sources_repo` and `inline_skills_repo` are created in `_init_app_state` by Task 3, which runs before this block; confirm the order when wiring and, if `_init_app_state` runs later, construct the repositories here from `db` the way `docker_access_registry` does.) `SandboxManager` receives `skill_store=skill_store` in Task 6; this task only constructs it.

`infra/docker-compose.yml`: under `orchestrator.volumes` add `- skills-store:/skills`; under `dind.volumes` add `- skills-store:/skills:ro`; under the top-level `volumes:` add `skills-store:`. Comment both mounts: "spec 3.2: the orchestrator syncs skill sources here; dind bind-mounts the trees read-only into sandboxes, so the path must be the same in both".

`infra/Dockerfile.orchestrator`: change the non-root user block to

```dockerfile
RUN useradd --create-home orchestrator \
    && mkdir -p /data /skills \
    && chown orchestrator:orchestrator /data /skills
```

`tests/unit/infra/test_skills_store_compose.py` parses the compose file with `yaml.safe_load` and asserts the three entries plus the Dockerfile `chown` line (string containment), in the style of the existing `tests/unit/infra/` tests.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/skills tests/unit/infra -q`; then ruff, ruff format, mypy.
Expected: all pass. `test_git_sync_never_runs_upstream_hooks_or_filters` must be shown failing once by temporarily removing `core.hooksPath=/dev/null` and `GIT_CONFIG_GLOBAL` from the environment in a scratch run (confirm the sentinel appears), then restored.

- [ ] **Step 8: Commit**

```bash
git add embry0/skills embry0/config.py embry0/api/app.py infra/docker-compose.yml infra/Dockerfile.orchestrator tests/unit/skills tests/unit/infra/test_skills_store_compose.py
git commit -m "feat(skills): skill store and sync service; skills-store volume shared with dind"
```

---

### Task 5: Catalog validation, effective skills, resolver deltas, node config, triage prompt

**Files:**
- Create: `embry0/agents/skills.py`
- Create: `tests/fixtures/skills/effective-skills.json`
- Create: `tests/unit/agents/test_skills.py`
- Modify: `embry0/agents/resolver.py` (`resolve_agent_config` skills merge)
- Modify: `embry0/workflows/issue_to_pr/route_plan.py` (`node_config` helper; `_config_overrides` passes the delta through untouched)
- Modify: `embry0/api/v1/pipeline_templates.py:24` (use `node_config`)
- Modify: `embry0/orchestration/nodes/triage.py:69-90` (prompt emits a delta)
- Modify: `embry0/workflows/issue_to_pr/nodes/_developer.py:1051-1052` (cap from the effective list)
- Modify: `embry0/api/v1/agents.py` and `embry0/api/v1/pipeline_templates.py` (validate skill names against the catalog on write)
- Test: `tests/unit/agents/test_resolver.py` (extend), `tests/unit/workflows/test_route_plan.py`, `tests/unit/workflows/test_template_models_reach_agents.py` (serializer-shaped case), `tests/unit/api/v1/test_agents.py`, `tests/unit/api/v1/test_pipeline_templates*.py`

**Interfaces:**
- Produces:

```python
# embry0/agents/skills.py
Delta = Mapping[str, Any]   # {"add": [...], "remove": [...] | "*"} or a bare list (legacy replace)
def normalize_delta(value: Any) -> dict[str, Any]: ...        # list -> {"add": list, "remove": "*"}; dict -> validated dict; None -> {"add": [], "remove": []}
def effective_skills(base: Iterable[str], *deltas: Any) -> list[str]: ...
def closest_names(name: str, known: Iterable[str], n: int = 3) -> list[str]: ...
class UnknownSkillError(ValueError): ...   # .unknown: list[str], .suggestions: dict[str, list[str]]
def check_skill_names(names: Iterable[str], catalog_names: Collection[str]) -> None: ...   # raises UnknownSkillError

# embry0/workflows/issue_to_pr/route_plan.py
def node_config(node: Mapping[str, Any]) -> dict[str, Any]: ...  # node["config"] when a dict, else the legacy top-level override keys
```

- Consumes: `SkillSourcesRepository.catalog()` from Task 3 (the routes call `check_skill_names` with `{c["name"] for c in await repo.catalog()}`).

- [ ] **Step 1: Write the fixture and the failing tests**

`tests/fixtures/skills/effective-skills.json` (consumed by this task's test and, in plan B, by `frontend/src/lib/__tests__/skills.test.ts`):

```json
{
  "cases": [
    {"name": "no deltas", "base": ["a:x", "a:y"], "deltas": [], "expected": ["a:x", "a:y"]},
    {"name": "add appends in order and dedupes", "base": ["a:x"], "deltas": [{"add": ["a:y", "a:x", "a:z"]}], "expected": ["a:x", "a:y", "a:z"]},
    {"name": "remove before add", "base": ["a:x", "a:y"], "deltas": [{"remove": ["a:x"], "add": ["a:x"]}], "expected": ["a:y", "a:x"]},
    {"name": "remove unknown is a no-op", "base": ["a:x"], "deltas": [{"remove": ["a:q"]}], "expected": ["a:x"]},
    {"name": "legacy list replaces", "base": ["a:x", "a:y"], "deltas": [["b:z"]], "expected": ["b:z"]},
    {"name": "remove star then add", "base": ["a:x"], "deltas": [{"remove": "*", "add": ["b:z"]}], "expected": ["b:z"]},
    {"name": "two deltas compose", "base": ["a:x"], "deltas": [{"add": ["a:y"]}, {"remove": ["a:x"]}], "expected": ["a:y"]},
    {"name": "empty legacy list clears", "base": ["a:x"], "deltas": [[]], "expected": []},
    {"name": "null delta is identity", "base": ["a:x"], "deltas": [null], "expected": ["a:x"]}
  ]
}
```

`tests/unit/agents/test_skills.py`:

```python
"""effective_skills and the catalog check (spec 3.3), pinned to the shared fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embry0.agents.skills import UnknownSkillError, check_skill_names, closest_names, effective_skills, normalize_delta

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "skills" / "effective-skills.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_effective_skills_matches_fixture(case: dict) -> None:
    assert effective_skills(case["base"], *case["deltas"]) == case["expected"], case["name"]


def test_normalize_delta_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="skills delta"):
        normalize_delta({"add": "a:x"})
    with pytest.raises(ValueError, match="skills delta"):
        normalize_delta(42)


def test_check_skill_names_lists_unknown_with_suggestions() -> None:
    known = {"superpowers:writing-plans", "superpowers:brainstorming", "local:house-style"}
    with pytest.raises(UnknownSkillError) as exc:
        check_skill_names(["superpowers:writing-plan", "local:house-style"], known)
    assert exc.value.unknown == ["superpowers:writing-plan"]
    assert exc.value.suggestions["superpowers:writing-plan"][0] == "superpowers:writing-plans"
    assert "superpowers:writing-plans" in str(exc.value)


def test_closest_names_is_bounded_and_ranked() -> None:
    assert len(closest_names("x", ["a", "b", "c", "d"], n=2)) == 2
    assert closest_names("writing-plan", ["writing-plans", "brainstorming", "debugging"], n=1) == ["writing-plans"]
```

Add to `tests/unit/agents/test_resolver.py`:

```python
def test_template_and_triage_skill_deltas_are_additive() -> None:
    definition = {"model": "m", "tools": [], "skills": ["s:a", "s:b"], "system_prompt": ""}
    resolved = resolve_agent_config(
        "developer", definition,
        template_config={"agent_skills": {"developer": {"add": ["s:c"], "remove": ["s:a"]}}},
        pipeline_config={"agent_skills": {"developer": {"add": ["s:d"]}}},
    )
    assert resolved.skills == ["s:b", "s:c", "s:d"]


def test_legacy_list_skill_override_still_replaces() -> None:
    definition = {"model": "m", "tools": [], "skills": ["s:a", "s:b"], "system_prompt": ""}
    resolved = resolve_agent_config("developer", definition, pipeline_config={"agent_skills": {"developer": ["s:z"]}})
    assert resolved.skills == ["s:z"]
```

Add to `tests/unit/workflows/test_route_plan.py`:

```python
def test_node_config_reads_config_then_legacy_top_level() -> None:
    from embry0.workflows.issue_to_pr.route_plan import node_config

    assert node_config({"config": {"model": "m", "skills": {"add": ["s:x"]}}})["model"] == "m"
    legacy = {"node_id": "n", "agent_type": "developer", "model": "m2", "skills": ["s:y"], "tools": None, "max_turns": 3}
    assert node_config(legacy) == {"model": "m2", "skills": ["s:y"], "max_turns": 3}
    assert node_config({"config": {}, "model": "ignored"}) == {}, "an empty config dict wins over legacy keys"
```

Add to `tests/unit/workflows/test_template_models_reach_agents.py` a case whose nodes carry the frontend's legacy top-level shape (`{"node_id": t, "agent_type": t, "model": f"model-for-{t}"}`) and assert the same models arrive (#78 backend side).

Add to `tests/unit/api/v1/test_agents.py`:

```python
@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_skill_name(app):
    catalog = MagicMock()
    catalog.catalog = AsyncMock(return_value=[{"name": "superpowers:writing-plans", "available": True}])
    app.state.skill_sources_repo = catalog
    app.state.agent_defs_repo.create = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "lantern", "description": "d", "model": "claude-sonnet-5", "skills": ["superpowers:writing-plan"]},
        )
    assert resp.status_code == 422
    assert "superpowers:writing-plans" in resp.text, "the 422 names the closest known skill"
    app.state.agent_defs_repo.create.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_skills.py tests/unit/agents/test_resolver.py tests/unit/workflows/test_route_plan.py tests/unit/workflows/test_template_models_reach_agents.py tests/unit/api/v1/test_agents.py -q`
Expected: import errors for `embry0.agents.skills` and `node_config`; the resolver test fails with `['s:c', 's:d']` or a `TypeError` from treating a dict as a list; the legacy-shape template case fails with `definition-model`; the API test gets 201.

- [ ] **Step 3: Write `embry0/agents/skills.py`**

```python
"""Skill names, deltas and the one rule that turns them into an effective list (spec 3.3).

Mirrored by frontend/src/lib/skills.ts; both are pinned to
tests/fixtures/skills/effective-skills.json.
"""

from __future__ import annotations

import difflib
from collections.abc import Collection, Iterable
from typing import Any


def normalize_delta(value: Any) -> dict[str, Any]:
    """A delta dict with ``add`` (list) and ``remove`` (list or "*").

    A bare list is the legacy *replace* form: ``{"add": list, "remove": "*"}``.
    ``None`` is the identity delta.
    """
    if value is None:
        return {"add": [], "remove": []}
    if isinstance(value, list):
        return {"add": [str(v) for v in value], "remove": "*"}
    if isinstance(value, dict):
        add = value.get("add", [])
        remove = value.get("remove", [])
        if not isinstance(add, list) or not (remove == "*" or isinstance(remove, list)):
            raise ValueError('skills delta must be {"add": [...], "remove": [...] | "*"}')
        return {"add": [str(v) for v in add], "remove": "*" if remove == "*" else [str(v) for v in remove]}
    raise ValueError(f"skills delta must be a list or a dict, not {type(value).__name__}")


def effective_skills(base: Iterable[str], *deltas: Any) -> list[str]:
    """Apply each delta in order: remove, then add; deduplicated, base order then add order."""
    current = list(dict.fromkeys(str(s) for s in base))
    for raw in deltas:
        delta = normalize_delta(raw)
        if delta["remove"] == "*":
            current = []
        else:
            drop = set(delta["remove"])
            current = [s for s in current if s not in drop]
        for name in delta["add"]:
            if name not in current:
                current.append(name)
    return current


def closest_names(name: str, known: Iterable[str], n: int = 3) -> list[str]:
    candidates = sorted(set(known))
    return difflib.get_close_matches(name, candidates, n=n, cutoff=0.0)[:n]


class UnknownSkillError(ValueError):
    def __init__(self, unknown: list[str], suggestions: dict[str, list[str]]) -> None:
        self.unknown = unknown
        self.suggestions = suggestions
        parts = [f"{u} (did you mean: {', '.join(suggestions[u]) or 'nothing close'})" for u in unknown]
        super().__init__("unknown skill name(s): " + "; ".join(parts))


def check_skill_names(names: Iterable[str], catalog_names: Collection[str]) -> None:
    unknown = [n for n in dict.fromkeys(names) if n not in catalog_names]
    if unknown:
        raise UnknownSkillError(unknown, {u: closest_names(u, catalog_names) for u in unknown})
```

- [ ] **Step 4: Resolver, route plan, validation, prompt, cap**

`embry0/agents/resolver.py::resolve_agent_config`: replace the two skills assignments with

```python
    from embry0.agents.skills import effective_skills

    skills = effective_skills(
        skills,
        (template_config or {}).get("agent_skills", {}).get(agent_type),
        (pipeline_config or {}).get("agent_skills", {}).get(agent_type),
    )
```

placed after the tools merge so model and tools handling stays as it is; update the docstring line "For tools and skills: later levels replace earlier" to "For tools: later levels replace earlier. For skills: each level is a delta (add/remove) over the previous, a bare list meaning replace (`embry0.agents.skills.effective_skills`)."

`embry0/workflows/issue_to_pr/route_plan.py`:

```python
_LEGACY_NODE_OVERRIDE_KEYS = (
    "model", "max_budget_usd", "max_turns", "effort", "tools", "skills",
    "prompt_prepend", "prompt_append", "custom_prompt", "custom_tools", "sandbox",
)


def node_config(node: Mapping[str, Any]) -> dict[str, Any]:
    """A node's overrides: ``config`` when it is a dict, else the legacy top-level keys (#78).

    The editor wrote overrides at the node's top level for a while; stored
    templates of that shape keep working. An explicit ``config`` dict wins.
    """
    cfg = node.get("config")
    if isinstance(cfg, dict):
        return dict(cfg)
    return {k: node[k] for k in _LEGACY_NODE_OVERRIDE_KEYS if node.get(k) is not None}
```

Use it at the three sites (`build_route_plan` line 81, `step_template_config` line 152, `triage_template_config` line 165) and in `embry0/api/v1/pipeline_templates.py:24` (`step_models = [str(node_config(n).get("model") or "") for n in nodes]`). `_config_overrides` keeps `out["agent_skills"] = {run_agent_type: cfg["skills"]}` but no longer wraps it in `list(...)`: the value is now a delta or a legacy list and the resolver normalizes it.

`embry0/orchestration/nodes/triage.py`: replace the "Set the chosen skill" paragraph with

```
Set the chosen skill as an ADDITION in `pipeline_config.agent_skills` (the
developer keeps its configured skills; you add one):
  {"developer": {"add": ["superpowers:brainstorming"]}}     # or
  {"developer": {"add": ["superpowers:writing-plans"]}}
```

`embry0/workflows/issue_to_pr/nodes/_developer.py:1051`: compute the effective list rather than reading the raw override:

```python
        from embry0.agents.skills import effective_skills

        raw = (state.get("pipeline_config", {}) or {}).get("agent_skills", {}).get("developer")
        skills_for_dev = effective_skills(definition.get("skills") or [], raw)
```

where `definition` is the developer's loaded agent definition already in scope in that function (it is loaded at `_developer.py:895`; thread it to this block if it is not in scope, as a parameter of the helper that contains these lines). The template delta is intentionally not applied here: the cap reads what triage attached, as before, now on top of the row.

Catalog validation on writes, in `embry0/api/v1/agents.py`:

```python
from embry0.agents.skills import UnknownSkillError, check_skill_names


async def _check_skills(request: Request, names: list[str] | None) -> None:
    if not names:
        return
    repo = getattr(request.app.state, "skill_sources_repo", None)
    if repo is None:  # minimal test apps
        return
    known = {c["name"] for c in await repo.catalog()}
    try:
        check_skill_names(names, known)
    except UnknownSkillError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

called in `create_agent` (`req.skills`) and `update_agent` (`updates.get("skills")`). In `embry0/api/v1/pipeline_templates.py`, the save paths collect every node's `node_config(n).get("skills")`, normalize each with `normalize_delta` (422 on `ValueError`), and check the union of `add` lists the same way. `GET /agents/{type}` annotates: `agent["orphan_skills"] = [s for s in agent["skills"] if s not in known]` when the repository is wired (empty list otherwise).

- [ ] **Step 5: Run the tests to verify they pass**

Run the Step 2 command plus `.venv/bin/python -m pytest tests/unit/workflows/issue_to_pr tests/unit/orchestration -q -k "triage or developer or skill"` and the three lint commands.
Expected: all pass. The triage prompt test(s) that quote the old `{"developer": ["superpowers:brainstorming"]}` text must be updated to the delta form in the same commit.

- [ ] **Step 6: Commit**

```bash
git add embry0/agents/skills.py tests/fixtures/skills/effective-skills.json tests/unit/agents/test_skills.py embry0/agents/resolver.py embry0/workflows/issue_to_pr/route_plan.py embry0/api/v1/pipeline_templates.py embry0/api/v1/agents.py embry0/orchestration/nodes/triage.py embry0/workflows/issue_to_pr/nodes/_developer.py tests/
git commit -m "feat(skills): effective-skills rule with shared fixture, additive deltas, node_config helper, catalog check on writes"
```

---

### Task 6: Skill mounts on sandboxes, plugins and skills in the SDK options

**Files:**
- Modify: `embry0/agents/invocation.py` (`skill_mounts: tuple[SkillMountSpec, ...] = ()`)
- Modify: `embry0/agents/config_builder.py` (`plugins`, `skills`)
- Modify: `embry0/execution/sandbox_manager.py` (`skill_store` and `skill_sources_repo` constructor arguments; mounts at create; `skill_mounts_for(container_name)`)
- Modify: `embry0/execution/agent_runner.py` (`skill_mounts(container_id) -> list[dict]`)
- Modify: `embry0/orchestration/nodes/agent.py` (serialize `skill_mounts`; `agent_started` gets `skill_sources`)
- Modify: `embry0/sandbox/runner.py` (`_invocation_from_config` reads `skill_mounts`)
- Modify: `embry0/execution/events.py` (`AgentStartedEvent.skill_sources: NotRequired[list[str]]`; constructor parameter), regenerate `docs/contracts/events.json` and the `docs/api.md` events table
- Modify: `embry0/agents/executor.py` (pass `skill_sources` where `agent_started` is emitted)
- Modify: `embry0/api/app.py` (pass `skill_store=` and `skill_sources_repo=` to `SandboxManager`)
- Test: `tests/unit/agents/test_config_builder.py`, `tests/unit/execution/test_sandbox_manager.py`, `tests/unit/orchestration/test_agent_node*.py` (the serialization test that exists for `run_id`/`node`), `tests/unit/sandbox/test_runner*.py`, `tests/unit/execution/test_event_contract.py` (regenerated contract)

**Interfaces:**
- Produces:

```python
# embry0/agents/invocation.py
class SkillMountSpec(TypedDict):
    source_id: str
    plugin_name: str
    container_path: str

AgentInvocation.skill_mounts: tuple[SkillMountSpec, ...] = ()

# embry0/execution/sandbox_manager.py
SandboxManager.__init__(..., skill_store: SkillStore | None = None, skill_sources_repo: Any | None = None)
async def skill_mounts_for(self, container_name: str) -> list[SkillMount]   # what create mounted; falls back to the store's current manifest

# embry0/execution/agent_runner.py
async def skill_mounts(self, container_id: str) -> list[SkillMountSpec]
```

- Consumes: `SkillStore.mounts`, `SkillMount` (Task 4); `SkillSourcesRepository.list_all` (Task 3).

- [ ] **Step 1: Write the failing tests**

`tests/unit/agents/test_config_builder.py`:

```python
def test_build_sdk_options_passes_plugins_and_skills_allowlist() -> None:
    mounts = (
        {"source_id": "superpowers", "plugin_name": "superpowers", "container_path": "/opt/embry0/skills/superpowers"},
        {"source_id": "local", "plugin_name": "local", "container_path": "/opt/embry0/skills/local"},
    )
    opts = build_sdk_options(_inv(skills=["superpowers:writing-plans"], skill_mounts=mounts))
    assert opts.plugins == [
        {"type": "local", "path": "/opt/embry0/skills/superpowers"},
        {"type": "local", "path": "/opt/embry0/skills/local"},
    ]
    assert opts.skills == ["superpowers:writing-plans"]


def test_build_sdk_options_empty_skills_means_no_filter() -> None:
    """[] would hide every skill, including the repository's own under .claude/skills; None leaves them."""
    opts = build_sdk_options(_inv(skills=[]))
    assert opts.skills is None
    assert opts.plugins == []
```

`tests/unit/execution/test_sandbox_manager.py`:

```python
@pytest.mark.asyncio
async def test_create_mounts_every_ready_skill_source_read_only(tmp_path):
    from embry0.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    staged = tmp_path / "staged"
    (staged / "skills" / "x").mkdir(parents=True)
    (staged / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    store.publish("superpowers", "a" * 40, staged)
    sources = MagicMock()
    sources.list_all = AsyncMock(
        return_value=[
            {"id": "superpowers", "plugin_name": "superpowers", "status": "ready", "enabled": True},
            {"id": "gone", "plugin_name": "gone", "status": "ready", "enabled": True},
        ]
    )
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    mgr = SandboxManager(docker=docker, proxy_manager=_make_proxy_manager(), skill_store=store, skill_sources_repo=sources)
    await mgr.create(job_id="job-skills")
    volumes = docker.build_run_cmd.call_args.kwargs["volumes"]
    expected = f"{tmp_path / 'skills' / 'superpowers' / ('a' * 40)}:/opt/embry0/skills/superpowers:ro"
    assert expected in volumes
    assert not any(v.startswith(str(tmp_path / "skills" / "gone")) for v in volumes), "a source with no tree is skipped, not mounted"
    mounts = await mgr.skill_mounts_for("sandbox-job-skills")
    assert [m.source_id for m in mounts] == ["superpowers"]


@pytest.mark.asyncio
async def test_create_without_a_skill_store_mounts_nothing_extra(manager: SandboxManager):
    await manager.create(job_id="job-plain")
    volumes = manager._docker.build_run_cmd.call_args.kwargs["volumes"]
    assert not volumes or not any("/opt/embry0/skills/" in v for v in volumes)
```

Serialization test (extend the existing `run_agent_node` serialization test that asserts `run_id`/`node`): `agent_runner.skill_mounts` is an `AsyncMock` returning one spec; assert `serialized["skill_mounts"]` equals it. Runner test: `_invocation_from_config({... "skill_mounts": [spec]})` yields `invocation.skill_mounts == (spec,)` and an absent key yields `()`.

Contract: `AgentStartedEvent` gains `skill_sources: NotRequired[list[str]]`; the executor passes `skill_sources=[m["source_id"] for m in invocation.skill_mounts]` when non-empty. `test_event_contract.py` fails until `docs/contracts/events.json` and the `docs/api.md` table are regenerated; the frontend `events.contract.test.ts` compares types and fields, so plan B's frontend work must add the optional field to `EVENT_FIELDS` (note it in the plan B handoff; the backend commit alone makes the frontend contract test fail, which is why Task 6 also edits `frontend/src/lib/types/events.ts` to add the optional `skill_sources` field to `agent_started` and nothing else, and runs `npm test -- --run src/lib/types/__tests__/events.contract.test.ts` from `frontend/`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_config_builder.py tests/unit/execution/test_sandbox_manager.py -q`
Expected: `TypeError: AgentInvocation.__init__() got an unexpected keyword argument 'skill_mounts'`; `SandboxManager.__init__() got an unexpected keyword argument 'skill_store'`.

- [ ] **Step 3: Invocation and SDK options**

`embry0/agents/invocation.py`:

```python
from typing import Any, Literal, TypedDict


class SkillMountSpec(TypedDict):
    """A skill source mounted into the sandbox: the SDK loads it as a local plugin."""

    source_id: str
    plugin_name: str
    container_path: str
```

and on `AgentInvocation`, after `node`:

```python
    # Spec 3.3: every ready, enabled skill source SandboxManager mounted into
    # this job's sandbox. build_sdk_options turns each into a local plugin;
    # `skills` is the allowlist over them (and over the repository's own).
    skill_mounts: tuple[SkillMountSpec, ...] = ()
```

`embry0/agents/config_builder.py`, in the `ClaudeAgentOptions(...)` call:

```python
        # Spec 3.3: the mounted skill sources are local plugins; the effective
        # skill list is the allowlist. None, not [], when the list is empty:
        # [] hides every skill, including the repository's own under
        # /workspace/.claude/skills, which setting_sources=["project"] loads.
        plugins=[{"type": "local", "path": m["container_path"]} for m in invocation.skill_mounts],
        skills=list(invocation.skills) if invocation.skills else None,
```

Update the docstring bullets accordingly.

- [ ] **Step 4: SandboxManager, AgentRunner, node, runner, event**

`embry0/execution/sandbox_manager.py`: constructor gains `skill_store: SkillStore | None = None, skill_sources_repo: Any | None = None` (import `SkillStore`, `SkillMount` from `embry0.skills.store`), stored on `self._skill_store`, `self._skill_sources_repo`, plus `self._skill_mounts_by_container: dict[str, list[SkillMount]] = {}`. In `create`, after the `volumes` loop that appends `f"{vol_name}:{mount_point}"`:

```python
        # Spec 3.3: every ready, enabled skill source, read-only, resolved in
        # dind's namespace (the store volume is mounted at the same path there).
        skill_mounts = await self._resolve_skill_mounts()
        for mount in skill_mounts:
            extra_volumes.append(f"{mount.host_path}:{mount.container_path}:ro")
```

and after the container is created, `self._skill_mounts_by_container[name] = skill_mounts`. Helper:

```python
    async def _resolve_skill_mounts(self) -> list[SkillMount]:
        if self._skill_store is None or self._skill_sources_repo is None:
            return []
        try:
            rows = await self._skill_sources_repo.list_all()
        except Exception as exc:  # the store is never a reason a sandbox cannot start
            logger.warning("skill_sources_unavailable_for_mount", error=str(exc))
            return []
        return self._skill_store.mounts(rows)

    async def skill_mounts_for(self, container_name: str) -> list[SkillMount]:
        """What ``create`` mounted into this sandbox; after a restart, the store's current manifest."""
        known = self._skill_mounts_by_container.get(container_name)
        return list(known) if known is not None else await self._resolve_skill_mounts()
```

`destroy` drops the container's entry. `embry0/execution/agent_runner.py`:

```python
    async def skill_mounts(self, container_id: str) -> list[SkillMountSpec]:
        name = container_id if container_id.startswith("sandbox-") else container_id
        return [
            {"source_id": m.source_id, "plugin_name": m.plugin_name, "container_path": m.container_path}
            for m in await self._sandbox_mgr.skill_mounts_for(name)
        ]
```

(use whatever the runner already holds as the sandbox manager attribute and the name convention `create` used: `sandbox-<job_id>` sanitized; the manager keys its dict by that name, and the node has `state["sandbox_container_id"]`, so the runner must map id to name the way the manager's other per-container methods do; if the manager only knows ids, key the dict by the container id returned by `run_cmd` instead and keep the test aligned.)

`embry0/orchestration/nodes/agent.py`: before `serialized = {...}`:

```python
    skill_mounts = await agent_runner.skill_mounts(state.get("sandbox_container_id", "")) if hasattr(agent_runner, "skill_mounts") else []
    invocation = replace(invocation, run_id=run_id, node=node or agent_type, skill_mounts=tuple(skill_mounts))
```

and `"skill_mounts": list(invocation.skill_mounts),` in the dict. `embry0/sandbox/runner.py::_invocation_from_config`: `skill_mounts=tuple(cfg.get("skill_mounts") or ())`.

`embry0/execution/events.py`: `AgentStartedEvent` gains `skill_sources: NotRequired[list[str]]`; `agent_started(..., skill_sources: list[str] | None = None)` adds `{"skill_sources": skill_sources}` to the fields when not None. The emit site in `embry0/agents/executor.py` passes `skill_sources=[m["source_id"] for m in invocation.skill_mounts] or None`. Regenerate: `python -m embry0.execution.events --write-contract docs/contracts/events.json` and paste `--render-markdown` between the markers in `docs/api.md`. In `frontend/src/lib/types/events.ts`, add `skill_sources` to the optional fields of `agent_started` (and the matching type), run the frontend contract test.

`embry0/api/app.py`: `SandboxManager(..., skill_store=skill_store, skill_sources_repo=app.state.skill_sources_repo)`; this means the `SkillStore` construction from Task 4 moves above the `SandboxManager(...)` call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents tests/unit/execution tests/unit/orchestration tests/unit/sandbox -q` and `cd frontend && npm test -- --run src/lib/types/__tests__/events.contract.test.ts`; then the three lint commands.
Expected: all pass, including `test_event_contract.py` and `test_job_event_fixtures.py`.

- [ ] **Step 6: Commit**

```bash
git add embry0/agents/invocation.py embry0/agents/config_builder.py embry0/execution/sandbox_manager.py embry0/execution/agent_runner.py embry0/orchestration/nodes/agent.py embry0/sandbox/runner.py embry0/execution/events.py embry0/agents/executor.py embry0/api/app.py docs/contracts/events.json docs/api.md frontend/src/lib/types/events.ts tests/
git commit -m "feat(skills): mount ready skill sources read-only into sandboxes; plugins and skills allowlist in the SDK options"
```

---

### Task 7: HTTP routes for skills, sources, inline skills, per-agent skills and tools

**Files:**
- Create: `embry0/api/v1/skills.py`, `embry0/api/v1/tools.py`
- Modify: `embry0/api/v1/agents.py` (per-agent skill add and remove)
- Modify: `embry0/api/app.py` (include the two routers with `auth_deps`)
- Modify: `embry0/api/schemas/__init__.py` (`SkillSourceCreateRequest`, `SkillSourcePatchRequest`, `InlineSkillRequest`)
- Create: `tests/unit/api/v1/test_skills_api.py`, `tests/unit/api/v1/test_tools_api.py`
- Modify: `tests/unit/api/v1/test_agents.py`

**Interfaces:**
- Consumes: Task 3 repositories, Task 4 `SkillStore`/`SkillSourceSync`, Task 5 `check_skill_names`.
- Produces: the routes of spec 3.4; audit actions `skill_source.created`, `skill_source.updated`, `skill_source.synced`, `skill_source.deleted`, `inline_skill.written`, `inline_skill.deleted`, `agent.skill_added`, `agent.skill_removed`.

- [ ] **Step 1: Write the failing route tests**

`tests/unit/api/v1/test_skills_api.py` builds the app the way `test_agents.py` does and sets `app.state.skill_sources_repo`, `app.state.inline_skills_repo`, `app.state.skill_sync`, `app.state.skill_store`, `app.state.agent_defs_repo`, `app.state.templates_repo` to `MagicMock`s with `AsyncMock` methods. Cases, each one test:

- `GET /skills` returns `repo.catalog()` verbatim.
- `GET /skills/sources` returns `repo.list_all()` with `syncing: bool` added from `skill_sync.is_syncing`.
- `POST /skills/sources` with a valid body: `repo.create` awaited with the fields, `skill_sync.sync` scheduled (assert `asyncio.create_task` was used by awaiting the returned task via a captured mock, or assert `skill_sync.sync` awaited within the request when `EMBRY0_SYNC_INLINE_FOR_TESTS`, simpler: the route awaits `request.app.state.skill_sync.sync(...)` inside `asyncio.create_task` and stores the task in `app.state.background_tasks`; the test asserts the task set gained one task and the mock was awaited after `await asyncio.sleep(0)`), 201, the row in the body.
- `POST /skills/sources` with `credential_id`: 422 "credential_id is reserved".
- `POST /skills/sources` when `repo.create` raises `SkillSourceRefused("a git source ... https")`: 422 with the message.
- `PATCH /skills/sources/superpowers` on a builtin with `ref`: 403 "built-in sources accept enabled only"; with `enabled: false`: 200 and `repo.update` awaited.
- `POST /skills/sources/mine/sync`: 202; when `skill_sync.sync` raises `SkillSyncBusy`: 409.
- `DELETE /skills/sources/mine` when an agent references `mine:x`: 409 whose body lists `agents: ["lantern"]`; when `repo.delete` raises `ValueError("cannot delete built-in")`: 403; happy path: 200 and `skill_store.remove_source` called.
- `GET /skills/inline`, `PUT /skills/inline/house-style` (upsert awaited, `skill_sync.sync("local")` scheduled, 200), `PUT` with a bad name: 422, `DELETE /skills/inline/house-style` when an agent references `local:house-style`: 409.
- `POST /agents/lantern/skills/superpowers:writing-plans`: `agent_defs_repo.update` awaited with the row's skills plus the new name, idempotent when present (no update, 200), 422 when unknown, 404 when the agent does not exist.
- `DELETE /agents/lantern/skills/superpowers:writing-plans`: update awaited without it; 200 when absent.
- Every mutation: `emit_audit` patched and asserted with the action name.

`tests/unit/api/v1/test_tools_api.py` (its own `app` fixture: `create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))`, nothing else on `app.state` is needed):

```python
async def test_tools_catalog_lists_builtin_browser_and_context_tools(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tools")
    assert resp.status_code == 200
    rows = resp.json()
    names = {r["name"]: r["source"] for r in rows}
    assert names["Read"] == "builtin" and names["Bash"] == "builtin"
    assert names["mcp__context__logs_query"] == "mcp:context"
    assert any(src.startswith("mcp:playwright") for src in names.values())
    assert all({"name", "source", "description"} <= set(r) for r in rows)
    assert rows == sorted(rows, key=lambda r: (r["source"] != "builtin", r["source"], r["name"]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/v1/test_skills_api.py tests/unit/api/v1/test_tools_api.py -q`
Expected: 404 or 405 on every route.

- [ ] **Step 3: Schemas**

`embry0/api/schemas/__init__.py`:

```python
class SkillSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    kind: str = Field(pattern="^(git|path|inline)$")
    url: str = Field(default="", max_length=2000)
    ref: str = Field(default="", max_length=255)
    enabled: bool = True
    credential_id: str | None = Field(default=None, description="reserved; any value is refused in this release")

    @field_validator("credential_id")
    @classmethod
    def _reserved(cls, v: str | None) -> str | None:
        if v is not None:
            raise ValueError("credential_id is reserved: skill sources are public in this release")
        return v


class SkillSourcePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class InlineSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=64 * 1024)
```

- [ ] **Step 4: Routes**

`embry0/api/v1/skills.py`:

```python
"""Skills: catalog, sources, inline skills (agent identity and skills spec 3.4). Writes are admin."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from embry0.api.deps import request_actor, require_admin
from embry0.api.schemas import InlineSkillRequest, SkillSourceCreateRequest, SkillSourcePatchRequest
from embry0.audit.helpers import emit_audit
from embry0.skills.sync import SkillSyncBusy
from embry0.storage.repositories.skills import InlineSkillsRepository, SkillSourceRefused, SkillSourcesRepository

router = APIRouter()


def _sources(request: Request) -> SkillSourcesRepository:
    repo = getattr(request.app.state, "skill_sources_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="skill storage not initialized")
    return cast("SkillSourcesRepository", repo)


def _inline(request: Request) -> InlineSkillsRepository:
    repo = getattr(request.app.state, "inline_skills_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="skill storage not initialized")
    return cast("InlineSkillsRepository", repo)


def _schedule_sync(request: Request, source_id: str) -> None:
    sync = getattr(request.app.state, "skill_sync", None)
    if sync is None:
        return
    task = asyncio.create_task(sync.sync(source_id))
    tasks = getattr(request.app.state, "background_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)


async def _audit(request: Request, action: str, **details: Any) -> None:
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


def _serialize(row: dict[str, Any], request: Request) -> dict[str, Any]:
    sync = getattr(request.app.state, "skill_sync", None)
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
    out["syncing"] = bool(sync is not None and sync.is_syncing(row["id"]))
    return out


async def _referencing_agents(request: Request, predicate: Any) -> list[str]:
    agents_repo = getattr(request.app.state, "agent_defs_repo", None)
    if agents_repo is None:
        return []
    return sorted(a["type"] for a in await agents_repo.list_all() if any(predicate(s) for s in a.get("skills") or []))


@router.get("/skills")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    return await _sources(request).catalog()


@router.get("/skills/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    return [_serialize(r, request) for r in await _sources(request).list_all()]


@router.get("/skills/sources/{source_id}")
async def get_source(source_id: str, request: Request) -> dict[str, Any]:
    row = await _sources(request).get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    return _serialize(row, request)


@router.post("/skills/sources", status_code=201, dependencies=[require_admin])
async def create_source(body: SkillSourceCreateRequest, request: Request) -> dict[str, Any]:
    try:
        row = await _sources(request).create(
            source_id=body.id, kind=body.kind, url=body.url, ref=body.ref, enabled=body.enabled
        )
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"skill source {body.id!r} already exists") from exc
        raise
    await _audit(request, "skill_source.created", source_id=body.id, kind=body.kind, url=body.url, ref=body.ref)
    _schedule_sync(request, body.id)
    return _serialize(row, request)


@router.patch("/skills/sources/{source_id}", dependencies=[require_admin])
async def patch_source(source_id: str, body: SkillSourcePatchRequest, request: Request) -> dict[str, Any]:
    repo = _sources(request)
    row = await repo.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    fields = body.model_dump(exclude_unset=True)
    if row["is_builtin"] and "ref" in fields:
        raise HTTPException(status_code=403, detail="built-in sources accept enabled only; their ref is pinned in code")
    if not fields:
        return _serialize(row, request)
    try:
        updated = await repo.update(source_id, **fields)
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _audit(request, "skill_source.updated", source_id=source_id, fields=sorted(fields))
    if "ref" in fields:
        _schedule_sync(request, source_id)
    return _serialize(updated, request)


@router.post("/skills/sources/{source_id}/sync", status_code=202, dependencies=[require_admin])
async def sync_source(source_id: str, request: Request) -> dict[str, Any]:
    sync = getattr(request.app.state, "skill_sync", None)
    if sync is None:
        raise HTTPException(status_code=503, detail="skill sync not initialized")
    if await _sources(request).get(source_id) is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    if sync.is_syncing(source_id):
        raise HTTPException(status_code=409, detail=f"a sync of {source_id!r} is already running")
    await _audit(request, "skill_source.synced", source_id=source_id)
    _schedule_sync(request, source_id)
    return {"id": source_id, "status": "syncing"}


@router.delete("/skills/sources/{source_id}", dependencies=[require_admin])
async def delete_source(source_id: str, request: Request) -> dict[str, Any]:
    repo = _sources(request)
    row = await repo.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    prefix = (row["plugin_name"] or row["id"]) + ":"
    agents = await _referencing_agents(request, lambda s: s.startswith(prefix))
    if agents:
        raise HTTPException(
            status_code=409,
            detail={"message": f"skill source {source_id!r} is referenced by agents; remove its skills first", "agents": agents},
        )
    try:
        await repo.delete(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=403 if "built-in" in str(exc) else 404, detail=str(exc)) from None
    store = getattr(request.app.state, "skill_store", None)
    if store is not None:
        store.remove_source(source_id)
    await _audit(request, "skill_source.deleted", source_id=source_id)
    return {"id": source_id, "status": "deleted"}


@router.get("/skills/inline")
async def list_inline(request: Request) -> list[dict[str, Any]]:
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in await _inline(request).list_all()]


@router.put("/skills/inline/{name}", dependencies=[require_admin])
async def put_inline(name: str, body: InlineSkillRequest, request: Request) -> dict[str, Any]:
    try:
        row = await _inline(request).upsert(name=name, description=body.description, body=body.body)
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _audit(request, "inline_skill.written", name=name)
    _schedule_sync(request, "local")
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


@router.delete("/skills/inline/{name}", dependencies=[require_admin])
async def delete_inline(name: str, request: Request) -> dict[str, Any]:
    agents = await _referencing_agents(request, lambda s: s == f"local:{name}")
    if agents:
        raise HTTPException(
            status_code=409, detail={"message": f"inline skill {name!r} is referenced by agents", "agents": agents}
        )
    try:
        await _inline(request).delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await _audit(request, "inline_skill.deleted", name=name)
    _schedule_sync(request, "local")
    return {"name": name, "status": "deleted"}
```

Template references for the 409 on delete: also scan `templates_repo.list_all()` node configs (`node_config(n).get("skills")` normalized, its `add` list) when `templates_repo` is wired, returning `templates: [...]` beside `agents`.

`embry0/api/v1/agents.py` additions:

```python
@router.post("/agents/{agent_type}/skills/{skill_name}", dependencies=[require_admin])
async def add_agent_skill(agent_type: str, skill_name: str, request: Request, repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo)) -> dict[str, Any]:
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    await _check_skills(request, [skill_name])
    skills = list(agent.get("skills") or [])
    if skill_name in skills:
        return agent
    updated = await repo.update(agent_type, skills=[*skills, skill_name])
    await _audit(request, "agent.skill_added", agent_type=agent_type, skill=skill_name)
    return updated


@router.delete("/agents/{agent_type}/skills/{skill_name}", dependencies=[require_admin])
async def remove_agent_skill(agent_type: str, skill_name: str, request: Request, repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo)) -> dict[str, Any]:
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    skills = list(agent.get("skills") or [])
    if skill_name not in skills:
        return agent
    updated = await repo.update(agent_type, skills=[s for s in skills if s != skill_name])
    await _audit(request, "agent.skill_removed", agent_type=agent_type, skill=skill_name)
    return updated
```

(`_audit` is the same helper as in `skills.py`; move it to `embry0/api/v1/_audit.py` and import it in both, rather than duplicating.) `skill_name` in a path contains a colon; FastAPI passes it through unchanged.

`embry0/api/v1/tools.py`:

```python
"""GET /tools: the tool names an agent definition can list (spec 3.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from embry0.workflows.issue_to_pr.log_tool import CONTEXT_LOGS_TOOL
from embry0.workflows.qa.browser_backends import backend_for, backend_names

router = APIRouter()

BUILTIN_TOOLS: tuple[tuple[str, str], ...] = (
    ("Read", "Read a file"),
    ("Write", "Create or overwrite a file"),
    ("Edit", "Replace text in a file"),
    ("Bash", "Run a shell command in the sandbox"),
    ("Glob", "Find files by pattern"),
    ("Grep", "Search file contents"),
    ("WebFetch", "Fetch a URL (through the sandbox egress policy)"),
    ("WebSearch", "Search the web"),
    ("NotebookEdit", "Edit a Jupyter notebook cell"),
    ("Task", "Delegate to a subagent"),
)


def tool_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"name": n, "source": "builtin", "description": d} for n, d in BUILTIN_TOOLS]
    for backend_name in backend_names():
        backend = backend_for(backend_name)
        rows.extend(
            {"name": tool, "source": f"mcp:{backend_name}", "description": f"{backend_name} browser tool"}
            for tool in backend.browser_tools()
        )
    rows.append({"name": CONTEXT_LOGS_TOOL, "source": "mcp:context", "description": "Query the deployment's logs"})
    return sorted(rows, key=lambda r: (r["source"] != "builtin", r["source"], r["name"]))


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    return tool_catalog()
```

`embry0/api/app.py`: `app.include_router(skills.router, prefix="/api/v1", tags=["skills"], dependencies=auth_deps)` and the same for `tools.router` with `tags=["tools"]`, next to the agents router.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api -q` and the three lint commands. Expected: all pass. Also run `.venv/bin/python -m pytest tests/unit/api/test_route_inventory*.py -q` or whatever test pins the route list against `docs/api.md`; if one exists, it will fail until Task 8 documents the routes, which is acceptable inside the wave only if Task 8 lands before the gate; otherwise document the routes in this task.

- [ ] **Step 6: Commit**

```bash
git add embry0/api/v1/skills.py embry0/api/v1/tools.py embry0/api/v1/_audit.py embry0/api/v1/agents.py embry0/api/app.py embry0/api/schemas/__init__.py tests/unit/api
git commit -m "feat(api): skills catalog, sources, inline skills, per-agent skill add/remove and tool catalog routes"
```

---

### Task 8: MCP parity and documentation

**Files:**
- Modify: `embry0/mcp_server/server.py`, `embry0/mcp_server/client.py`
- Modify: `docs/api.md` (new "Skills" and "Tools" sections; agents section gains the identity fields and per-agent skill routes)
- Modify: `docs/architecture.md` ("Sandbox Container Architecture": the skills store and mounts; "Storage": the two tables)
- Modify: `docs/ops/deploy.md` ("Deploy a change": the `skills-store` volume and the sandbox image rebuild for this release)
- Modify: `docs/glossary.md` (remove the `SP` row and the `SP0014` examples; skill names are `plugin:skill`)
- Test: `tests/unit/mcp_server/test_server_skills.py`

**Interfaces:**
- Produces MCP tools: `skill_list`, `skill_source_list`, `skill_source_add`, `skill_source_sync`, `skill_source_remove`, `inline_skill_put`, `inline_skill_remove`, `agent_skill_add`, `agent_skill_remove`, `tool_list`.

- [ ] **Step 1: Write the failing MCP tests**

`tests/unit/mcp_server/test_server_skills.py`, following the existing MCP test pattern (patch `_get_client` with an `AsyncMock`-bearing stub):

```python
async def test_skill_source_add_requires_confirm(client_stub) -> None:
    from embry0.mcp_server import server

    out = await server.skill_source_add("mine", "git", url="https://example.com/a.git", ref="v1")
    assert out["error"] == "confirmation_required"
    client_stub.skill_source_add.assert_not_awaited()


async def test_skill_source_add_forwards(client_stub) -> None:
    from embry0.mcp_server import server

    await server.skill_source_add("mine", "git", url="https://example.com/a.git", ref="v1", confirm=True)
    client_stub.skill_source_add.assert_awaited_once_with("mine", kind="git", url="https://example.com/a.git", ref="v1", enabled=True)


async def test_agent_skill_add_forwards(client_stub) -> None:
    from embry0.mcp_server import server

    await server.agent_skill_add("developer", "superpowers:writing-plans", confirm=True)
    client_stub.agent_skill_add.assert_awaited_once_with("developer", "superpowers:writing-plans")


async def test_read_tools_need_no_confirm(client_stub) -> None:
    from embry0.mcp_server import server

    await server.skill_list()
    await server.skill_source_list()
    await server.tool_list()
    client_stub.skill_list.assert_awaited_once()
    client_stub.skill_source_list.assert_awaited_once()
    client_stub.tool_list.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/mcp_server/test_server_skills.py -q`
Expected: `AttributeError: module 'embry0.mcp_server.server' has no attribute 'skill_source_add'`.

- [ ] **Step 3: Client and server tools**

`embry0/mcp_server/client.py`, a new section:

```python
    # ------------------------------------------------------------------- skills

    async def skill_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/skills"))

    async def skill_source_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/skills/sources"))

    async def skill_source_add(self, source_id: str, *, kind: str, url: str = "", ref: str = "", enabled: bool = True) -> dict[str, Any]:
        body = {"id": source_id, "kind": kind, "url": url, "ref": ref, "enabled": enabled}
        return _as_dict(await self._request("POST", "/api/v1/skills/sources", body=body))

    async def skill_source_sync(self, source_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("POST", f"/api/v1/skills/sources/{source_id}/sync"))

    async def skill_source_remove(self, source_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("DELETE", f"/api/v1/skills/sources/{source_id}"))

    async def inline_skill_put(self, name: str, *, description: str, body: str) -> dict[str, Any]:
        return _as_dict(await self._request("PUT", f"/api/v1/skills/inline/{name}", body={"description": description, "body": body}))

    async def inline_skill_remove(self, name: str) -> dict[str, Any]:
        return _as_dict(await self._request("DELETE", f"/api/v1/skills/inline/{name}"))

    async def agent_skill_add(self, agent_type: str, skill_name: str) -> dict[str, Any]:
        return _as_dict(await self._request("POST", f"/api/v1/agents/{agent_type}/skills/{skill_name}"))

    async def agent_skill_remove(self, agent_type: str, skill_name: str) -> dict[str, Any]:
        return _as_dict(await self._request("DELETE", f"/api/v1/agents/{agent_type}/skills/{skill_name}"))

    async def tool_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/tools"))
```

`embry0/mcp_server/server.py`: ten `@_tool` functions mirroring the client, with docstrings that say what each does and that the writes need `confirm=true`; reads (`skill_list`, `skill_source_list`, `tool_list`) take no `confirm`. Add the write tools to the module docstring list and extend the `instructions=` string with "skills (catalog, sources, inline skills, per-agent add/remove) and the tool catalog".

- [ ] **Step 4: Docs**

`docs/api.md`: a "## Skills" section after "## Runtime configuration" with the table from spec 3.4 (route, tier, body, answer); a "## Tools" subsection; in the agents part of the document (or a new "## Agents" subsection if there is none) the four identity fields, their vocabularies with a pointer to `docs/contracts/identity.json`, `orphan_skills` on `GET /agents/{type}`, and the two per-agent skill routes. `docs/architecture.md` "Sandbox Container Architecture" gains a paragraph: the `/skills` store, the read-only bind mounts at `/opt/embry0/skills/<id>`, the plugin and allowlist options, and that the sandbox images must be rebuilt for the SDK options to take effect; "Storage" lists `skill_sources` and `inline_skills` with one line each and the migration number 101 in the migrations reference. `docs/ops/deploy.md` "Deploy a change" gains a note under the compose section: the `skills-store` volume appears on first `docker compose up -d` after this release; the orchestrator image rebuild carries `/skills` ownership; the sandbox images need the rebuild recipe for `config_builder.py`. `docs/glossary.md`: delete the `SP` row from the prefix table and the two `SP0014` mentions; add one sentence under Energy: "A skill is referenced by its `plugin:skill` name; it has no second id."

- [ ] **Step 5: Run everything**

Run: `.venv/bin/python -m pytest tests/unit -q` against the throwaway Postgres (compare the failure set with the baseline run on `origin/main`), `.venv/bin/ruff check embry0 tests`, `.venv/bin/ruff format --check embry0 tests`, `.venv/bin/mypy embry0`, `python scripts/check_denylist.py` if the secret is available locally (CI runs it anyway), and `cd frontend && npm test -- --run`.
Expected: 0 failed; the failure set equals the baseline's.

- [ ] **Step 6: Commit**

```bash
git add embry0/mcp_server docs/api.md docs/architecture.md docs/ops/deploy.md docs/glossary.md tests/unit/mcp_server/test_server_skills.py
git commit -m "feat(mcp): skills, sources, inline, per-agent skill and tool catalog tools; document the skills API and store"
```

---

## Integration and PR

1. After wave 5: `git log origin/main..HEAD --oneline`; grep the tree for `<<<<<<<`, `>>>>>>>`; grep touched files for duplicate `def` names; run the full gate once more.
2. One whole-branch review (Opus or better) with the spec and this plan; one fix wave; one scoped re-review.
3. PR title `feat(agents): identity fields and real skills from synced sources (plan A)`, body per the repository's PR template with the spec and plan links, "Fixes #78" only if plan B is in the same release (otherwise "Refs #78"), and the deploy notes below. Last line `https://claude.ai/code/session_011MYiGJuebTGdHFAS8GWusQ`.

## Deploy notes (for the PR body)

- Migration 101 runs at startup.
- Compose change: new `skills-store` volume on `orchestrator` and `dind`; `docker compose up -d` creates it. The orchestrator image rebuild (`infra/deploy.sh`) carries the `/skills` ownership.
- **Sandbox images must be rebuilt** (`embry0/agents/config_builder.py`, `embry0/agents/invocation.py`, `embry0/sandbox/runner.py` are baked in): recipe in `docs/ops/deploy.md`. Until then, sources are mounted and ignored.
- First boot syncs `superpowers` and `local` in the background; `GET /skills/sources` shows `pending` until done; the orchestrator needs outbound https to github.com for that one clone.
- Verify on a deployment: `GET /skills/sources` both `ready`; `GET /skills` lists `superpowers:*`; a QA or standalone job's `agent_started` event carries `skill_sources: ["superpowers", "local"]`; `docker exec <sandbox> ls /opt/embry0/skills/superpowers/skills` inside DinD shows the skill directories.
