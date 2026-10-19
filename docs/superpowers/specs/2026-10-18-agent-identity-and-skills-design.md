# Agent identity and skills

**Status:** draft for review, 2026-10-18
**Workstream:** 2 of 3 (trace fidelity → agent identity and skills → game UI)
**Tracker:** GitHub milestone "Agent identity and skills"

## 1. Problem

An agent definition is a row with a slug, a model, a tool list, a skill list and a prompt. Two things the dashboard and the runtime both need are missing from it, and each has been worked around in a way that is now wrong.

**Agents have no identity.** `agent_definitions` carries no display name, avatar, color or tagline (`embry0/storage/migrations/runner.py:202`, extended at 430 and 616). The frontend derives visuals from the slug through four disagreeing maps: `frontend/src/lib/agentVisuals.ts` (three types, unicode glyphs), `frontend/src/lib/agentIcons.ts` (lists `validator` and `reviewer`, which no longer exist as rows), `frontend/src/lib/graph-utils.ts:6-36` (`AGENT_COLORS`), and `frontend/src/components/divine/operations.ts:115`. Triage is cyan, purple or emerald depending on the file. Any agent created through `POST /agents` renders as a grey bullet labelled "Custom". Workstream 3 cannot draw a roster of characters from data that does not exist.

**Skills are strings that do nothing.** A skill is a name such as `superpowers:writing-plans` stored in `agent_definitions.skills`, in a template node's `config.skills`, or in `pipeline_config.agent_skills`. The resolver threads the list into `AgentInvocation.skills` (`embry0/agents/resolver.py:291-311`), `run_agent_node` serialises it into the sandbox (`embry0/orchestration/nodes/agent.py:511`), the runner reads it back (`embry0/sandbox/runner.py:72`), and `build_sdk_options` then never looks at it (`embry0/agents/config_builder.py:24-99`). No sandbox image installs superpowers or any other skill set; `setting_sources=["project"]` only loads whatever the *target repository* ships under `.claude/skills/`. The only consumers of the list are the developer node's ask-user cap, which checks for one name (`_developer.py:1051`), and triage's prompt, which tells the model to pick one of two names. There is no catalog, no validation (any string is stored), no description, and no add-or-remove endpoint: the whole list is replaced through `PUT /agents/{type}`, the MCP `agent_update`, or the form's free-text chips.

Three adjacent defects make the fix wider than the two columns and one SDK option it sounds like:

1. **Overrides replace instead of merge.** A template node's `config.skills` and triage's `pipeline_config.agent_skills` each *replace* the definition's list (`resolver.py:85, 103`). Triage setting `agent_skills.developer = ["superpowers:brainstorming"]` silently discards the developer's six seeded skills for that job, and nothing shows the operator what the job actually carried.
2. **Node overrides never arrive (#78).** The pipeline editor writes `model`, `tools`, `skills` and the rest as top-level node fields (`frontend/src/lib/graph-utils.ts:118-133`); every backend reader takes them from `node["config"]` (`route_plan.py:81,152,165`; `api/v1/pipeline_templates.py:24`). Node-level skill deltas have to live in `config`, so this shape mismatch is on the path.
3. **Only nine slugs can run.** `_EXECUTABLE_AGENT_TYPES` (`embry0/workflows/_validation.py:21`) fixes the template vocabulary; `_step_target` and `step_to_runtime` in `route_plan.py` are hard-coded dicts; `generic_agent.py:122` falls back to `BUILTIN_SEED[agent_type]`, which raises for anything else; the editor palette whitelists seven slugs (`AgentBar.tsx:49`). A character recruited on `/agents/new` cannot join a party.

## 2. Goals and non-goals

**Goals**

- Every agent row carries a display name, an avatar id from a fixed roster, a color from a fixed palette, and a tagline. Builtins ship with theirs; the dashboard reads identity from the row and from nowhere else.
- A skill is a real file set that the Agent SDK loads. Skills come from **skill sources**: a pinned public git repository, a directory on the orchestrator, or SKILL.md documents written in the dashboard. The orchestrator fetches sources into a store that every sandbox mounts read-only, and passes them to the SDK as local plugins with the agent's effective skill list as the allowlist.
- A catalog endpoint lists the skills that exist, with descriptions; agent and template writes validate against it.
- Node-level and triage-level skill overrides are additive deltas; the effective set is computable from the stored data by one rule, mirrored on both sides.
- Per-skill add and remove endpoints, a tool catalog endpoint, and MCP tools with the same reach as the HTTP API.
- Any agent row can be placed in a pipeline template and runs on the generic agent node.

**Non-goals**

- Sprites, the pixel font, the party map. Workstream 3 draws the roster; this workstream gives it the data and ships a plain picker.
- Private skill sources. The schema leaves a `credential_id` slot empty; cloning is anonymous over https.
- Skills on the direct xAI executor (`DirectXaiExecutor`) or in a future Grok Build executor. The plugin and allowlist options are SDK-path only; the invocation carries what a later executor needs.
- A marketplace, versions of a skill, or skill ratings.
- Reworking the ask-user cap or triage's skill choice beyond making both additive.

## 3. Design

### 3.1 Identity fields

**Migration 101** adds to `agent_definitions`:

| column | type | rule |
|---|---|---|
| `display_name` | `TEXT NOT NULL DEFAULT ''` | 1 to 60 characters when set; empty means "show the slug" |
| `avatar` | `TEXT NOT NULL DEFAULT 'golem'` | one of the roster ids below |
| `color` | `TEXT NOT NULL DEFAULT 'slate'` | one of the palette ids below |
| `tagline` | `TEXT NOT NULL DEFAULT ''` | up to 140 characters |

The migration also writes the builtin identities for rows still at the default, so an upgraded deployment shows the roster without a reseed. The seeds (`BUILTIN_SEED`, `agent_seed.py` for qa, onboarding and pr-reviewer) carry the same values, and `reset()` restores them with the rest of the row.

The roster and palette are constants in a new module `embry0/agents/identity.py`, imported by the schema validators and exported to the frontend as `frontend/src/lib/identity.ts`, pinned equal by a contract test the same way `docs/contracts/events.json` pins the event vocabulary: the Python module writes `docs/contracts/identity.json` (test fails when stale) and the TypeScript test reads it.

| avatar id | builtin | palette id | note |
|---|---|---|---|
| `seer` | triage | `violet` | |
| `smith` | developer | `amber` | |
| `warden` | review | `rose` | |
| `alchemist` | qa | `emerald` | |
| `herald` | onboarding | `sky` | |
| `arbiter` | pr-reviewer | `cyan` | |
| `scholar` | research | `gold` | |
| `oracle` | analysis | `violet` | |
| `engineer` | ops | `amber` | |
| `rogue` | — | — | free for custom agents |
| `bard` | — | — | free for custom agents |
| `golem` | — | — | default for a new custom agent |

Palette: `gold`, `cyan`, `violet`, `rose`, `emerald`, `amber`, `sky`, `slate`. The frontend maps each id to the existing theme hues (`--color-primary` for gold, the Tailwind 400 shade for the others), so nothing off-theme can be stored.

**API.** `AgentCreateRequest` and `AgentUpdateRequest` gain the four optional fields with the validators above (422 names the allowed ids). `_ALLOWED_UPDATE_FIELDS` gains them. `GET /agents` and `GET /agents/{type}` return them. MCP `agent_update` gains them; MCP gains `agent_create` and `agent_delete` (both `confirm=true`), which exist over HTTP today and not over MCP.

**Frontend.** One module, `frontend/src/lib/agentIdentity.ts`, answers "how does this agent look" from the row: `identityOf(agent) -> { displayName, avatar, color, tagline, classes }`. It replaces `agentVisuals.ts`, `agentIcons.ts`, `roleIcons.ts` and the `AGENT_COLORS` in `graph-utils.ts`; their call sites (AgentCard, Console RunningCard stage badge, AgentsPage, AgentBar chips, pipeline nodes, StructuredView role colors) take identity from the row. Until workstream 3, the avatar renders as a tinted square with the display name's initial; the form gets a display name field, a tagline field, an avatar select showing the roster ids, and a palette swatch row. The `OperationGlyph` header on the form and list stays (it is the divine layer, not identity).

### 3.2 Skill sources

**Table `skill_sources`** (migration 101):

| column | type | rule |
|---|---|---|
| `id` | `TEXT PRIMARY KEY` | slug `^[a-z][a-z0-9-]{0,39}$`; also the mount directory name |
| `kind` | `TEXT NOT NULL` | `git`, `path` or `inline` |
| `url` | `TEXT NOT NULL DEFAULT ''` | git: `https://` only, no userinfo, host not an IP literal or a private or loopback name; path: an absolute directory inside the orchestrator container |
| `ref` | `TEXT NOT NULL DEFAULT ''` | git: the requested ref (tag, branch or sha), `_REF_RE` grammar from `workspace_init/git.py` |
| `resolved_sha` | `TEXT NOT NULL DEFAULT ''` | git: the commit the store holds; path and inline: a content hash of the synced tree |
| `plugin_name` | `TEXT NOT NULL DEFAULT ''` | the `name` from the plugin's `.claude-plugin/plugin.json`; the prefix of every skill name this source provides |
| `skills` | `JSONB NOT NULL DEFAULT '[]'` | catalog written at sync: `[{"name","description","path"}]` |
| `status` | `TEXT NOT NULL DEFAULT 'pending'` | `pending`, `ready`, `error` |
| `error` | `TEXT NOT NULL DEFAULT ''` | last sync failure, redacted |
| `enabled` | `BOOLEAN NOT NULL DEFAULT TRUE` | disabled sources are not mounted and their skills show `available: false` |
| `is_builtin` | `BOOLEAN NOT NULL DEFAULT FALSE` | |
| `credential_id` | `TEXT` | reserved, always `NULL` in this workstream; the API refuses a value |
| `synced_at`, `created_at`, `updated_at` | `TIMESTAMPTZ` | |

**Table `inline_skills`** (migration 101): `name TEXT PRIMARY KEY` (`^[a-z][a-z0-9-]{0,63}$`), `description TEXT NOT NULL`, `body TEXT NOT NULL` (the SKILL.md content below the frontmatter, at most 64 KiB), `created_at`, `updated_at`. These rows are the `inline` source.

**Builtin sources**, seeded by `embry0/storage/seeds/skill_sources_builtin.py` (insert-if-missing, like agents):

- `superpowers`: kind `git`, url `https://github.com/obra/superpowers.git`, `ref` pinned to the commit sha of the release current when the plan is written, recorded in the seed with the release tag in a comment. Bumping it is a deliberate one-line change, never `main`.
- `local`: kind `inline`, plugin name `local`. Rendered from `inline_skills`; a deployment with no inline skills has an empty `local` plugin that is still valid.

**The store.** A compose volume `skills-store`, mounted at `/skills` in the `orchestrator` service (read-write) and in `dind` (read-only). `infra/Dockerfile.orchestrator` creates `/skills` owned by the `orchestrator` user the way it creates `/data`, so the named volume inherits the ownership on first use. Layout:

```
/skills/<source_id>/<resolved_sha>/        the plugin root: .claude-plugin/plugin.json, skills/<name>/SKILL.md, ...
/skills/<source_id>/current                one line: the resolved_sha the sandboxes should mount
```

A sync writes into a temporary sibling directory and renames it into place, then rewrites `current`, so a sandbox created mid-sync sees either the old tree or the new one. Old trees are removed after the rename. The plugin root for `superpowers` is the repository root, which already carries `.claude-plugin/plugin.json`; for a `path` or `inline` source without one, the sync generates `{"name": "<source_id>", "version": "0.0.0"}` so the CLI accepts the directory as a plugin.

**Sync** (`embry0/skills/sync.py`, a service constructed at startup with the store path and the repository):

- git: `git clone --depth 1 --no-recurse-submodules --branch <ref> <url> <tmp>` when `ref` is a tag or branch, or `git init` + `fetch --depth 1 origin <sha>` + `checkout FETCH_HEAD` when it is a sha. Environment: `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `HOME` set to the temp dir, `-c core.hooksPath=/dev/null -c protocol.allow=never -c protocol.https.allow=always -c uploadpack.allowAnySHA1InWant=true`. Timeout 120 seconds through `asyncio.wait_for` with the process killed on expiry. The checked-out tree must be under 64 MiB and contain at least one `skills/*/SKILL.md`; the `.git` directory is deleted before the rename. This is the first time the orchestrator runs git itself; it runs it on a public tree it chose, with hooks and non-https transports disabled.
- path: copy the directory into the store (`shutil.copytree`, symlinks not followed, same size and SKILL.md checks), content-hash the result for `resolved_sha`.
- inline: render `skills/<name>/SKILL.md` with a frontmatter of `name` and `description` and the stored body, plus the generated plugin.json; content-hash for `resolved_sha`.
- After the tree is in place: parse every `skills/*/SKILL.md` frontmatter (`name`, `description`; the directory name when `name` is absent) into `skills`, read `plugin_name` from plugin.json, set `status=ready`, `synced_at`. On any failure: `status=error`, `error` set to the message passed through `redact_log_text`, the previous tree left untouched.
- Runs on source create, on `POST /skills/sources/{id}/sync`, when an inline skill is written or deleted, and at startup as a background task for every enabled source whose `current` file is missing from the store (a fresh volume). Startup never waits on it: a deployment boots without network and the catalog says `pending`.
- One sync per source at a time (per-id `asyncio.Lock`); a second request while one runs returns 409.

**Security.** The plugin tree is mounted read-only and executes nothing on the orchestrator. Inside the sandbox it is in the same trust domain as the target repository's own `.claude/` directory: a plugin's hooks and scripts run as the agent user, with the same Ring 2 and Ring 3 enforcement as any repository code, and cannot loosen `/workspace/.claude/settings.json`. The admin's pin (`ref`) is the trust decision, which is why `ref` cannot be a moving branch for a builtin and why the response shows the resolved sha. `embry0/safety/policy.py` needs no new deny rule: nothing under `/opt/embry0/skills` is in a sensitive path, and the CLI reads plugin files directly rather than through the file tools.

### 3.3 Catalog, effective skills, and the SDK

**Skill names** are `<plugin_name>:<skill>` everywhere, exactly as today's seeds spell them. The catalog is the union of every source's `skills` with the source's `plugin_name` prefixed. A name is *known* when some source lists it; *available* when that source is `ready` and `enabled`.

**Validation.** `POST /agents`, `PUT /agents/{type}`, the per-skill endpoints, template saves and the MCP tools reject an unknown name with 422 and the three closest known names. A known-but-unavailable name is accepted (a source may be syncing); the response carries `warnings`. Existing rows with names no source knows keep working: validation runs on writes, and the catalog marks such names `orphan` in `GET /agents/{type}`.

**Effective skills.** One pure function, `embry0/agents/skills.py::effective_skills(base, *deltas) -> list[str]`, and its mirror `frontend/src/lib/skills.ts::effectiveSkills`. A delta is `{"add": [...], "remove": [...]}`; `remove` applies before `add`; the result is deduplicated in base order then add order. A bare list where a delta is expected is the legacy *replace* form and is honoured as `{"add": list, "remove": "*"}`. Both implementations are pinned to the same cases in `tests/fixtures/skills/effective-skills.json`. The resolver applies: definition skills → template node delta (`config.skills`) → triage delta (`pipeline_config.agent_skills[type]`). Triage's prompt (`embry0/orchestration/nodes/triage.py:69-90`) is rewritten to emit a delta; the developer node's brainstorming check reads the effective list.

**Node config (#78).** `serializeGraph` writes the overrides under `config` (`{model, max_budget_usd, max_turns, effort, tools, skills, prompt_prepend, prompt_append, custom_prompt, custom_tools, sandbox}`) and `deserializeGraph` reads `config` first and the legacy top-level fields second, so stored templates keep loading. The backend readers (`route_plan.py`, `pipeline_templates.py`) gain one helper `node_config(node)` that does the same, and `tests/unit/workflows/test_template_models_reach_agents.py` gains a case whose input is a template produced by the frontend serializer fixture.

**The invocation.** `AgentInvocation` gains `skill_mounts: list[SkillMount]` where `SkillMount = (source_id, plugin_name, container_path)`, filled by `run_agent_node` from the store's current manifest for every ready and enabled source. `build_sdk_options` sets:

- `plugins=[{"type": "local", "path": m.container_path} for m in invocation.skill_mounts]`
- `skills=list(invocation.skills)` when the effective list is non-empty; `None` when it is empty, which keeps today's behaviour of the repository's own `.claude/skills` loading unfiltered. When the list is non-empty it is the allowlist, and a repository skill the agent should keep must be listed too; the form says so next to the picker.

`setting_sources` stays `["project"]`. The runner already passes `Skill` through the Ring 3 name check (`_INFRA_ALLOWED_TOOLS`).

**Mounts.** `SandboxManager` is constructed with the skill store and appends `-v /skills/<id>/<sha>:/opt/embry0/skills/<id>:ro` for every ready and enabled source to every sandbox it creates, reading `current` at create time. Bind mounts with `:ro` already work through `build_run_cmd` (the `/certs/client` mount uses the same form); the source path is resolved in DinD's namespace, which is why `dind` mounts the volume at the same `/skills` path. QA sub-task sandboxes and bootstrap sandboxes get the mounts too; they cost nothing and keep one code path. A source that becomes ready after a job's sandbox was created is not visible to that job; the job's events say which sources were mounted (`agent_started` gains `skill_sources: [...]`, an optional field added to the contract).

### 3.4 API

All writes are admin tier, like agents today.

| route | body / notes |
|---|---|
| `GET /skills` | `[{name, source_id, description, available, status}]`, sorted by name |
| `GET /skills/sources` | every source with status, resolved sha, skill count, `synced_at`, `error` |
| `POST /skills/sources` | `{id, kind, url, ref, enabled}`; `credential_id` refused with 422; triggers a sync; 201 with the row in `pending` |
| `GET /skills/sources/{id}` | the row with its `skills` catalog |
| `PATCH /skills/sources/{id}` | `{ref?, enabled?}`; a `ref` change triggers a sync; builtins accept `enabled` only |
| `POST /skills/sources/{id}/sync` | 202, or 409 while a sync runs |
| `DELETE /skills/sources/{id}` | 403 for a builtin; 409 listing the agents and templates that reference its skills; otherwise removes the row and the store directory |
| `GET /skills/inline` | the inline skills with name, description, `updated_at` |
| `PUT /skills/inline/{name}` | `{description, body}`; upsert; re-renders the `local` source |
| `DELETE /skills/inline/{name}` | 409 when an agent references `local:<name>` |
| `POST /agents/{type}/skills/{name}` | idempotent add; 422 unknown name; returns the agent |
| `DELETE /agents/{type}/skills/{name}` | idempotent remove; returns the agent |
| `GET /tools` | `[{name, source, description}]`: the Claude Code built-in tool names embry0 knows (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`, `Task`), each registered browser backend's tool names from `embry0/workflows/qa/browser_backends.py` under `source: "mcp:<backend>"`, and the log tool under `source: "mcp:context"` |

Every mutation writes an audit row (`skill_source.created`, `skill_source.synced`, `skill_source.deleted`, `inline_skill.written`, `inline_skill.deleted`, `agent.skill_added`, `agent.skill_removed`) through `emit_audit`, as the other admin routes do.

**MCP parity** (`embry0/mcp_server/server.py`, each write with `confirm=true`): `skill_list`, `skill_source_list`, `skill_source_add`, `skill_source_sync`, `skill_source_remove`, `inline_skill_put`, `inline_skill_remove`, `agent_skill_add`, `agent_skill_remove`, `tool_list`, plus `agent_create` and `agent_delete` from 3.1.

**`docs/api.md`** gains a "Skills" section and the agent identity fields; `docs/credentials.md` is not touched (no credentials in this workstream); `docs/architecture.md` gains the store and sync in the sandbox section; `docs/ops/deploy.md` gains the `skills-store` volume under the compose changes and says the orchestrator image rebuild carries the `/skills` ownership. The glossary's `SP####` reservation is removed: skill names are `plugin:skill`, there is no second id.

### 3.5 Any agent in a template

- `validate_graph_definition` accepts an `agent_type` when it is one of the fixed node-backed types (`triage`, `developer`, `reviewer`, `review`, `qa`, `output`) **or** names an existing `agent_definitions` row. The validator takes the set of known rows as an argument; the API passes the current rows, `plan_route` passes them at plan time and skips the template with the existing log when a row has since been deleted.
- `route_plan._step_target` returns `"agent"` for any type not in its fixed map; `step_to_runtime` returns the type itself for those. `generic_agent_node` loads the row and falls back to `BUILTIN_SEED` only when the type is a builtin; a missing custom row fails the node with `generic_agent: no agent definition for <type>` rather than a `KeyError`.
- `DEFAULT_AGENT_MAX_TURNS` keeps its per-type entries and its default for the rest; `_AGENT_TOOLS` keeps its developer-baseline fallback because the invocation's tools override it in the runner.
- `AgentBar` builds the palette from `GET /agents` minus `triage` (the root, placed by the editor), plus the `output` sentinel; the dashed "Custom Agent" chip becomes a link to `/agents/new`. A node's identity (3.1) is shown on the chip.

### 3.6 Frontend

- **Agent form**: identity fields (3.1); tools as a checklist from `GET /tools` grouped by source, with the agent's current MCP-sourced names kept even when not in the catalog; skills as a searchable picker over `GET /skills` showing description and availability, with an inline "write a skill" link to the Skills page; unknown names cannot be typed. The MCP servers JSON textarea stays.
- **Agents list**: display name, tagline, avatar tile; skills column shows names on hover.
- **Skills page** (`/skills`, Infra group, admin): sources table (id, kind, url and ref, resolved sha, status with error, skill count, synced_at, enabled toggle, Sync, Remove), Add source form, Inline skills editor (name, description, Markdown body, save, delete) with the rendered catalog underneath.
- **Pipeline editor**: `AgentDetailPopup`'s skills section becomes two lists, "Added" and "Removed", over the definition's skills, with the computed effective set shown read-only beneath; the per-skill `mode` select and the `SkillConfig` object shape are removed (nothing reads them; a stored object form is read as its `name`). Node config saves under `config` (3.3).
- Tests follow the repo's conventions: render tests with `vi.mock` at the api boundary; `effectiveSkills` and identity pinned to the shared fixtures.

### 3.7 Error handling

- Sync errors never fail a request that triggered them: the row shows `error` and the dashboard shows it inline. A git timeout, a tree over the cap, a tree without skills, and a plugin.json that is not JSON are each their own message and each has a test.
- A sandbox whose skill mount path is missing (store wiped, `current` stale) fails at `docker run` with the Docker error; `SandboxManager` checks the paths it is about to mount and logs `skill_source_mount_missing` and skips that source instead, so a wiped volume degrades to "no skills" rather than "no jobs". Startup re-syncs it.
- An agent whose effective skills name a source that is disabled or in error still runs; the SDK simply finds no such skill, and `agent_started.skill_sources` says what was mounted.

## 4. Testing

- Migration 101 test in the `test_migration_NN.py` style: columns exist, builtin identities written, `skill_sources` and `inline_skills` created, idempotent.
- `identity.json` and `effective-skills.json` contract tests on both sides, each shown failing when the other side drifts.
- `build_sdk_options`: `plugins` and `skills` set from the invocation; `None` for an empty list; the existing options unchanged.
- Sync: a local bare git repository as the fixture upstream (no network in tests); tag, branch and sha refs; hooks disabled (a fixture repo with a `post-checkout` hook that would write a sentinel file, asserted absent); size cap; missing SKILL.md; atomic replace (old tree readable until rename); concurrent sync 409.
- Resolver: template delta, triage delta, legacy replace form, orphan names.
- `SandboxManager.create` passes the `:ro` bind for every ready source and skips a missing path.
- Routes: every table row above, including 409 bodies naming referencing agents.
- Any-agent templates: a custom row validates and runs on the generic node; a deleted row skips the template at plan time with the log.
- #78: a serializer-produced template reaches `step_template_config`.
- Full unit suite against a throwaway Postgres per `CLAUDE.md`, failure set compared to the baseline.

## 5. Migration and deploy notes

- Migration 101 (`max+1` at merge time).
- Compose: the `skills-store` volume on `orchestrator` (`/skills`) and `dind` (`/skills:ro`); the orchestrator image rebuild (`infra/deploy.sh` does it) carries the `/skills` ownership. A deploy does not rebuild the sandbox images, and `embry0/agents/config_builder.py` is baked into them, so **the sandbox images must be rebuilt** for `plugins` and `skills` to take effect (recipe in `docs/ops/deploy.md`). Until they are, the mounts are present and unused.
- First boot after deploy syncs `superpowers` in the background; the Skills page shows `pending` until it lands.

## 6. Issue breakdown (milestone "Agent identity and skills")

| Issue | Scope |
|---|---|
| A | Migration 101 (identity columns and both tables), identity constants and `identity.json`, API and MCP identity fields, `agent_create` and `agent_delete` MCP tools, seeds and reset |
| B | Skill store and sync service, builtin sources, compose volume and Dockerfile ownership, startup background sync, audit rows |
| C | Catalog and effective-skills rule with shared fixture, validation on writes, resolver deltas, triage prompt, developer cap on the effective list, `node_config` helper (#78 backend side), `agent_started.skill_sources` |
| D | Invocation `skill_mounts`, `build_sdk_options` plugins and skills, `SandboxManager` mounts and missing-path skip |
| E | Skills, sources, inline, per-agent skill, and tools routes; MCP parity; `docs/api.md`, `docs/architecture.md`, `docs/ops/deploy.md`, glossary |
| F | Any-agent templates: validator, route plan, generic node, palette |
| G | Frontend: `agentIdentity.ts` replacing the four maps, form identity fields and pickers, agents list, Skills page, editor deltas and `config` serialization (#78 frontend side), `skills.ts` mirror |

A carries this spec. B, C and D depend on A; E depends on B and C; F depends on C; G depends on E and F. Two plans: A to E (backend), F and G (templates and frontend).
