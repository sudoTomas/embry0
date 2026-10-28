# Settings documents: operator policy in the database, one section per feature

Status: decided (2026-10-28; section 14). Pull request A implements sections 2 to 9, C the dashboard tab and B the pilot (section 11). Refs #159, #160. Line numbers are at `e1e0567d`.

**Why.** Most policy added since #112 is an environment variable, read once at startup into a frozen settings object (`ask_settings`, `embry0/services/asks.py:46-63`; `scout_settings`, `embry0/services/scout.py:68-82`; `leads_settings`, `embry0/services/leads/loop.py:52-66`), so every change means editing `.env` and restarting the orchestrator, which interrupts running jobs. The policy that does live in the database is spread over four mechanisms, each with its own precedence and its own validation (section 1). Every later issue of the epic (#164 to #182) needs one place to put a feature's policy.

**What changes.** One settings document per deployment, stored in the database as YAML text with comments. Each feature owns a section of it, described by a pydantic model that is the only home of its defaults and bounds. A write is validated as a whole, and a 422 names every bad key. Every write is a revision with an actor and a source, and the newest one can be undone. Loops read their section at the top of every tick, so a change applies on the next tick with no restart. A policy environment variable is imported into the document once, then ignored with a warning, then removed a release later. The settings reference in `docs/configuration.md` is generated from the sections.

## 1. What exists today

| Mechanism | Where | Precedence | Validation | History | Write tier |
|---|---|---|---|---|---|
| Environment only | `Embry0Config`, e.g. `embry0/config.py:748-846` | env, else code default; read once at startup | pydantic bounds on some fields; parsers fall back silently on a bad value (`asks.py:47-52` for `ASK_HOURS`, `:66-77` drops unknown weekday names) | none | edit `.env`, restart |
| "A saved row wins forever" | `budget_config`, `provider_config`, `integration_config` (migrations 1 and 2, `embry0/storage/migrations/runner.py:172-180`, `:218-238`) | env-derived defaults on every read until a row is saved, then the row wins (`embry0/storage/repositories/budget_config.py:1-7`, `:65-87`; `provider_config.py:1-6`, `:48-55`, `:64-80`; `integration_config.py:1-7`, `:69-80`, `:174-184`) | ad hoc per route; the first problem found is the 422 (`embry0/api/v1/config.py:358-430`) | an audit row only | budget: any non-read key (`config.py:160-178` has no admin gate); provider, integration: admin (`config.py:252`, `:358`) |
| Organization settings | `organizations.settings` JSONB (`embry0/services/organizations.py:46-67`) | the organization's value, else the deployment's env default (`organizations.py:144-163`) | `validate_settings` stops at the first bad key (`organizations.py:86-119`); `POST /orgs` stores the body unvalidated (`embry0/api/v1/organizations.py:182-190`), so every reader re-checks defensively (`organizations.py:122-171`) | an audit row only | admin |
| Watcher stack overrides | `watcher_stacks.settings` (`embry0/services/watcher_stacks.py:26-53`) | overrides only: an absent key inherits the `WATCHER_*` default (`watcher_stacks.py:180-205`) | strict typed check of every key, all problems joined (`:69-137`); unknown keys dropped and echoed as `ignored_settings` (`embry0/api/v1/watcher_stacks.py:88-114`); a stored value that no longer checks is left out at tick time with a warning (`watcher_stacks.py:140-155`) | an audit row only | admin |
| Test setup (qa.yaml v2) | `repo_qa_configs.yaml_text` (migration 52, `runner.py:1245-1251`; ADR 0003) | the stored document is the only source | pydantic, `extra="forbid"` (`embry0/workflows/qa/qa_yaml_v2.py:49`, `:523-527`); 422 as plain sentences (`embry0/api/v1/qa_config.py:55-59`, `embry0/workflows/qa/qa_yaml_errors.py:28-60`) | `repo_config_revisions` in the write's transaction, only when the text changed, newest 100 kept (`embry0/storage/repositories/repo_qa_configs.py:160-172`, `repo_config_revisions.py:22`, `:52-75`; ADR 0019) | any non-read key |
| Standards | `standards` rows, one active version per owner and kind (migration 102, `runner.py:2915-2929`; ADR 0014) | the active version | every problem listed (`embry0/services/standards.py:158-240`); 422 `{"message", "problems"}` (`embry0/api/v1/standards.py:187-189`) | superseded versions; a `draft` status exists in the CHECK but no write path creates one (`standards.py:178-205` always activates) | admin |

Undo exists only for qa.yaml and preferences: newest change of a surface only, and only while the stored document still equals what that change wrote; the undo is itself a revision with source `restore` (`embry0/api/v1/repo_history.py:144-206`).

### What this design keeps and what it drops

- **Kept from qa.yaml:** the YAML text is stored as written, comments included, and is what `GET` returns. Unknown keys are refused, never dropped.
- **Kept from standards:** the parsed values are stored next to the text, so no reader parses YAML; every problem is reported at once.
- **Kept from ADR 0019:** a revision in the write's own transaction, before and after, actor, source and reason; newest 100 kept; undo of the newest change only, recorded as `restore`.
- **Kept from watcher stacks:** the document is sparse. It holds only what an operator set; everything else is the section model's default, so a default changed in code reaches every key nobody set. A stored value that the current code refuses is read as its default, with a warning, rather than stopping a tick, as `usable_settings` does for a stack (`watcher_stacks.py:140-155`).
- **Dropped:** "env-derived defaults until a row is saved, then the row forever". After the one-time import, the environment is not read for a policy setting at all.
- **Dropped:** draft and active versions. Nothing writes a standards draft today, and a dry run (section 5) answers "what would change" without a second stored state.
- **Dropped:** first-error validation (`organizations.py:86-119`) and silently dropping unknown keys (`watcher_stacks.py:120-137`).

## 2. The document

```yaml
# Operator policy for this deployment. Every key is optional; an absent key
# takes the default in docs/configuration.md#settings-reference.
version: 1
asks:
  enabled: true
  hours: "08-17"
  timezone: Europe/Berlin
```

- **Top level:** an optional `version` (the schema version, `1`; absent means the version the running code writes), and one key per registered section. Each section is a mapping of that section's fields.
- **Sparse.** A key not written takes its default. An empty document (or one holding only comments) is every default; `PUT` of an empty document is how a deployment returns to the defaults, recorded and undoable. There is no `DELETE`.
- **No secrets.** A section field whose name looks secret (`SECRET_NAME_PATTERN`, `embry0/config_redaction.py:66`) is refused at registration, and a test checks every registered section. Credentials stay in the credential registry or the environment.
- **YAML or JSON in.** A JSON body is stored as the YAML it dumps to; a YAML body is stored byte for byte. Refused with a 422 each: aliases (one can expand a small text into a large `data` value) and merge keys; a key written twice, at any level (YAML would silently keep the second); a document nested too deeply to parse; the NUL character, which Postgres cannot store. The text is capped at 256 KiB (413 beyond, before the body is read when `Content-Length` says so).
- **What a newer release wrote** (a rollback past the release that added it) is kept: a section the running code does not know, and a key a known section does not have. A `PUT` passes while it sends each back exactly as stored, type included (`1` is not `true`): as the stored *text* has it (what an operator holds), or as the stored values do (what a JSON client sends back from GET's `data`, which a newer release may have normalised), and the answer lists them as `unknown_sections` and `unknown_keys`; the stored values are kept, so the newer release finds them on the way forward. For a section, nothing written, `null` and `{}` are alike. A `PUT` that changes one is a 422. A reader ignores them.
- **Schema changes are additive.** A new field takes a default and needs no version change. A field that is renamed, retyped or removed bumps `version` and ships an upgrade step that the boot runs on the stored document, recorded as a revision with source `upgrade`. A `PUT` naming a version newer than the code is a 422.

## 3. Sections: the registry

A feature declares its section in a leaf module that imports only pydantic, the standard library and the registry:

```python
# embry0/settings/sections/asks.py
class AsksSettings(SectionModel):        # SectionModel: extra="forbid", strict=True, frozen=True
    enabled: bool = Field(False, description="Keep one ask per person a decision waits on ...")
    hours: str = Field("09-18", description="Working hours, HH-HH, in `timezone` ...")
    ...

ASKS = Section(
    name="asks",
    model=AsksSettings,
    summary="When and how often embry0 asks a person about a decision that waits on them.",
    org_keys={"hours", "weekdays", "timezone", "remind_hours", "expire_days", "max_open_per_person"},
    repo_keys=set(),
    env={"enabled": "asks_enabled", "hours": "ask_hours", ...},   # field -> Embry0Config attribute (section 7)
)
```

- `Section` is generic over its model, so `snapshot.get(ASKS)` is typed `AsksSettings`.
- **The model is the only home of defaults and bounds** (`Field(ge=..., le=...)`, validators). No feature repeats a default in code, as the scout defaults are repeated today (`embry0/config.py:787`, `embry0/services/scout.py:62`, `:79`).
- **`org_keys` and `repo_keys`** name the fields an organization or a repository may override. They are declared and checked now, and served in the schema and the effective view, but nothing resolves them until #161, which then needs no change to the registry or the document.
- **A `Section` refuses to be built**, at import time, with a `SectionError` naming every problem:
  - a malformed name; a reserved one (`version`, and `body`, `query`, `path`, `header`, `cookie`, whose first `loc` segment the dashboard's error parser drops); a word YAML 1.1 reads as a boolean or null (`on`, `off`, `yes`, `no`, `true`, `false`, `null`), for a section or a field; a name that looks secret, for the section or any field, nested models included;
  - an empty summary; a model, or a model nested in a field, that is not a `SectionModel` or loosens its `extra="forbid"` or `strict`; a field that turns strict off (`Field(strict=False)`);
  - a field without a default or a `description`; defaults the section's own checks refuse;
  - an `org_keys`, `repo_keys` or `env` entry that names no field.

  A `Catalog` refuses a section named twice.
- **Validation never raises anything but a refusal.** `Section.validate(raw)` (`validate_values`) validates through JSON, strictly, and turns whatever a check raises into `SectionRefused`, pinned to the key whose check raised: a validator that raises `KeyError` (as `zoneinfo` does for an unknown zone) is a 422 on a write, the default on a read, and a skipped value in the env import, never a 500, a failed tick or a boot that does not start.
- **Where sections live.** `embry0/settings/sections/<name>.py`, one module per section, and `embry0/settings/sections/__init__.py` lists them in one tuple, `CATALOG`. A test fails when a module in that directory is not in `CATALOG`, so the set of sections never depends on which feature modules happen to be imported, and a feature cannot be missing from validation or from the generated docs.
- **No import cycles.** `embry0/settings/registry.py` imports only pydantic, the standard library and `embry0.config_redaction` (a leaf, for the secret-name pattern). Section modules import only the registry, and the package's `__init__` imports each of them for `CATALOG`. The store (section 6) imports the catalog. Features import their section module (for the type) and receive the store or a reader callable by injection. A test walks the imports of every module under `embry0/settings/sections/` with `ast` and fails on anything but the registry, `pydantic` and its submodules (not a package whose name only starts with it), the standard library, and a sibling section module.

## 4. Storage

Two migrations, 128 and 129. Both create tables only; the application layer writes the content, as for qa.yaml (`runner.py:1233-1235`), because SQL cannot validate it.

**Migration 128: revision history**, a new table (decision D1):

```sql
CREATE TABLE IF NOT EXISTS config_revisions (
    id          BIGSERIAL PRIMARY KEY,
    scope       TEXT NOT NULL,                              -- 'deployment'; #161 adds organization scopes
    surface     TEXT NOT NULL CHECK (surface IN ('settings')),
    before      JSONB,                                      -- {"yaml_text", "data", "schema_version"}
    after       JSONB,
    actor       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'person'
                CHECK (source IN ('person', 'file', 'bootstrap', 'restore', 'upgrade')),
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_config_revisions_scope ON config_revisions (scope, surface, id DESC);
```

`file` is listed now so #162's `apply` needs no migration. `ConfigRevisionsRepository` (`embry0/storage/repositories/config_revisions.py`) appends and trims with the same logic as `RepoConfigRevisionsRepository.append` (`repo_config_revisions.py:34-76`); `repo_config_revisions` and its repository are not changed.

**Migration 129: the document.**

```sql
CREATE TABLE IF NOT EXISTS settings_documents (
    scope                  TEXT PRIMARY KEY CHECK (scope = 'deployment'),
    yaml_text              TEXT NOT NULL DEFAULT '',
    data                   JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(data) = 'object'),
    schema_version         INTEGER NOT NULL DEFAULT 1,
    generation             BIGINT NOT NULL DEFAULT 1,
    bootstrapped_keys      TEXT[] NOT NULL DEFAULT '{}',
    updated_by             TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO settings_documents (scope) VALUES ('deployment') ON CONFLICT (scope) DO NOTHING;
```

- `data` is the sparse, validated values of `yaml_text`. Readers read `data` and never parse YAML.
- `generation` goes up by one in the transaction of every write, so a reader that sees a new generation also sees the values it belongs to. A Postgres sequence would not do: `nextval` is visible before the writer commits, and a reader could cache the old values under the new number until the next write.
- `bootstrapped_keys` is the ledger of the one-time env import (section 7), one `section.key` per key. It is per key because the row exists from the first boot of this release on, while later releases add sections, and keys to existing sections, whose env values still need importing once.
- The row is inserted by the migration, so the read path never meets a missing row.
- The `scope` CHECK allows only `deployment`. #161 decides whether organization documents become rows here or stay in `organizations.settings`; either way the reader's freshness check stays one indexed query returning one opaque token (today `generation` and `updated_at`).

**Writes** run in one transaction: `SELECT ... FOR UPDATE` on the row, the optional `If-Match` check against `generation` (strong comparison; `*` matches any, a weak tag never), validation, the update (`generation + 1`), and the revision, appended only when `yaml_text` changed (the qa.yaml rule, `repo_qa_configs.py:162`). The newest 100 revisions per scope and surface are kept.

## 5. API

All under `/api/v1`, in `embry0/api/v1/settings.py`, mounted with `auth_deps` like the config router (`embry0/api/app.py:2902`).

| Route | Tier | What it does |
|---|---|---|
| `GET /config/settings` | any key | `{yaml_text, data, schema_version, generation, updated_by, updated_at, sections, unknown_sections, unknown_keys}`, all from one read of the row, with `ETag: "<generation>"`; `?format=yaml` answers the text alone (`application/yaml`) |
| `PUT /config/settings` | admin, unrestricted key | Replace the document. Body: YAML (`application/yaml`, `text/yaml`, `text/plain`) or a JSON object. Optional `If-Match` (the `ETag`, a list of them, or `*`; strong comparison, so a weak tag never matches; 409 when none is stored), `?reason=`, `?source=person\|file`. Answers `{generation, revision_id, changes, unknown_sections, unknown_keys}`; `revision_id` is null when the text was unchanged. 413 past 256 KiB |
| `PUT /config/settings?dry_run=1` | admin | Validate and compare; store nothing. `If-Match` is checked as for a write. Answers `{would_change, text_diff, changes: [{key, before, after, set_before, set_after, effective_before, effective_after}], unknown_sections, unknown_keys}`, or the same 422 or 409 as a real `PUT` |
| `GET /config/settings/effective` | any key | Every field of every section: `{key, value, default, source, set_by, set_at, env, env_still_set, overridable, refused, description}` (below) |
| `GET /config/settings/schema` | any key | The JSON Schema of the whole document, as the generator reads it (editors, #162's offline `check`) |
| `GET /config/settings/revisions` | any key | Newest first: `{id, what, actor, source, reason, created_at, undoable, why_not}`; `what` names the keys that changed |
| `GET /config/settings/revisions/{id}` | any key | One revision with `before_text`, `after_text` and its `changes` |
| `POST /config/settings/revisions/{id}/undo` | admin, unrestricted key | ADR 0019's rule: the newest revision only, while the stored document still equals its `after`, else 409 with the same `why_not` text (`repo_history.py:32`). The old text is validated against today's schema first (422 "That earlier version is no longer valid", as `repo_history.py:140-141`). Recorded with source `restore`; answers like a `PUT` |

**The 422.** One response lists every problem, in FastAPI's own shape, so the dashboard's existing parser renders each as `asks.remind_hours: Input should be greater than 0` (`frontend/src/api/errorDetail.ts:9-42`):

```json
{"detail": [
  {"loc": ["asks", "remind_hours"], "type": "greater_than", "msg": "Input should be greater than 0"},
  {"loc": ["asks", "enabled"], "type": "bool_type", "msg": "Input should be a valid boolean"},
  {"loc": ["aks"], "type": "unknown_section", "msg": "'aks' is not a settings section. Did you mean 'asks'?"}
]}
```

- Each section is validated through JSON, strictly, by its model (`extra="forbid"`), and every error of every section is collected before anything is refused. Through JSON, so a value parsed from YAML, sent as JSON, or read back from the database meets the same rules. `loc` is the path from the document root; `input` is never returned. A check that raises anything else is a `check_failed` problem at its key.
- YAML reads a bare `on`, `off`, `yes` or `no` as a boolean; a field that wanted one of a few words and got a boolean says to put the word in quotes.
- An unknown key gets a near-miss hint the way `qa_yaml_errors._pydantic_error` does (`qa_yaml_errors.py:46-55`).
- A YAML syntax error is one entry with `loc: []`, `type: "yaml_syntax"` and the line and column.
- Strict means `"5"` is not a number and `"true"` is not a boolean, as for watcher stacks (`watcher_stacks.py:69-71`); an integer is still a valid float.

**Effective view.** `source` is `default` (no stored value), `document` (stored), or `bootstrap` (stored by the env import and not changed since). `set_by`, `set_at` and the `bootstrap` source come from the newest kept revision that changed that key; a key last changed before the kept history has them null. A stored value the current code refuses shows `source: default` with `refused: "<msg>"`. `env` is the variable the field was imported from and `env_still_set` whether it is still in the process environment; the value itself is not repeated. `overridable` lists `organization` and `repository` as the section declares them.

**Auth.** Reads take any authenticated key, as `GET /config/budget` and `GET /config/provider` do: the document holds no secrets. Writes take the admin tier (decision D4) and an unrestricted key: `enforce_repo_allowlist(request, None, ...)` refuses a key with a repository allowlist, since the document governs every repository (`embry0/api/repo_allowlist.py:44-55`, `:81-92`).

**Audit.** `settings.updated` (keys changed, revision id), `settings.change_undone`, `settings.bootstrapped` (section and keys, no values), added to `embry0/audit/actions.py` with labels; `tests/unit/audit/test_action_registry.py` already checks both directions.

**Ingress** (`docs/ops/deploy.md:92-107`): these are "every other `/api/` route": reachable through the dashboard tunnel and the LAN, 404 through the webhooks tunnel, authenticated on every path. No new row is needed in that table.

## 6. Reading

```python
class SettingsStore:                       # embry0/settings/store.py, app.state.settings
    async def snapshot(self) -> Snapshot   # one SELECT generation, updated_at; reload data when either moved
    async def save(...) -> SaveResult      # the PUT path; replaces the cached snapshot at once
    async def preview(...) -> Preview      # the dry-run path
    async def undo(revision_id, *, actor) -> SaveResult
    def edit(*, actor, source, reason) -> AsyncContextManager[Draft]   # the locked row, for the env import

class Snapshot:                            # immutable
    generation: int
    def get(self, section: Section[M]) -> M   # the section's model, defaults filled in

def section_reader(store, section) -> Callable[[], Awaitable[M]]   # what a one-section loop is built with
```

- **Once per tick.** A loop calls `await settings.snapshot()` at the top of its tick and uses that snapshot for the whole tick, so one tick never mixes two versions. The check is one primary-key query for `generation` and `updated_at`; the row is read again (in one statement) whenever either differs from the snapshot's, in either direction, so a database restored to an older copy is read again too. This process's own writes replace its snapshot at once. An API handler that needs policy calls it once per request.
- **Features stay testable without a database.** A loop receives a callable that returns its section (`Callable[[], Awaitable[AsksSettings]]`), wired in `app.py` with `section_reader(app.state.settings, ASKS)`. Unit tests pass a lambda.
- **A refused stored value** (stored under an older, looser schema) is dropped key by key: the section is validated, the keys named by its errors are removed and it is validated again, so every other key keeps its value. Each is logged once per generation as `settings_stored_value_invalid` (section, key, reason), and the effective view marks it.
- **Database errors.** If the check or the read fails, the last snapshot is used and `settings_refresh_failed` is logged; a tick never fails because of it. The first load happens at boot, right after migrations and the env import; a failure there is logged (`settings_first_read_failed`) and the first tick reads again.
- **More than one process.** The orchestrator is one uvicorn process (`infra/Dockerfile.orchestrator:98-103` passes no `--workers`). The MCP container has no database connection and reaches settings only through the API (`infra/docker-compose.yml:494-512`, `EMBRY0_API_URL`), so it needs nothing. A CLI subcommand that runs as its own process reads the current generation when it starts. A second orchestrator replica, which the migration runner already allows for (`runner.py:3-12`), sees a write by the other within one tick, because the counter is in the row, not in memory. No `LISTEN`/`NOTIFY` is used anywhere today, and none is needed.

## 7. From environment variables to the document

**Declaring the source.** A section's `env` maps a field to the `Embry0Config` attribute it was read from. An entry may carry a converter when the two forms differ (a comma list to a list, `EnvImport("leads_finders", split_commas)`). The variable's name is derived from the attribute as `config_lint.declared_env_names` derives it (`embry0/config_lint.py:78`). A test checks every `env` entry names an existing `Embry0Config` field while that field exists.

**The import, at every boot**, after migrations and before any loop starts (beside `run_migrations`, `embry0/api/app.py:1036`), in one transaction holding the row lock, so two replicas booting together import once:

1. For each key a section declares in `env` that is not in `bootstrapped_keys` (`section.key`), and not already written in the document:
2. take it when its attribute is in `config.model_fields_set` (the variable is set in the environment or `.env`; pydantic-settings marks a field set even when its value equals the default, checked) and its converted value differs from the key's default;
3. validate it as the section; a value the section refuses on its own, whatever its check or converter raises, is not imported and is logged as `settings_bootstrap_value_refused` (variable, key, reason), and the boot goes on. Then validate the accepted values together with the section's stored values this release still accepts (a stored value it refuses reads as its default and never blocks the import); a value refused only in that combination is *held*: logged as `settings_bootstrap_value_held`, not imported and not marked, so the next boot tries it again;
4. write the accepted values into the text under a comment naming the variables and the date: a section the text does not hold is appended as a block, keys for a section it holds go at the end of that section's block. Merge them into `data`, record a revision with source `bootstrap` and actor `embry0:bootstrap`, and write a `settings.bootstrapped` audit row through the store's own pool, all only when something was imported;
5. add every key it looked at, a held one aside, to `bootstrapped_keys` whether or not anything was imported, so it never runs again for that key. The ledger is per key so that a key a later release adds to a section imported before still gets its variable.

Only variables an operator set are imported, never code defaults: a section that changes a default on purpose (#165 wants background readers off on a new deployment) must not have the old default frozen into every existing document. The document's own line ending (`\n` or `\r\n`) is kept. A section written as nothing, `~`, `null` or `{}` becomes a block; keys after a block scalar go above the blank and comment lines that follow it. Where the values cannot be written in (a flow-style section with keys, a `|+` scalar last, a text that would not parse back to the same values), the whole document is written again from its values: the new text's first line says its comments were lost, and `settings_text_rewritten` is logged. Each imported value is checked as its section when it is read, and the rest of the document is left as stored: a value an older release stored and this one refuses never blocks the import.

**After the import.** At every boot, each variable whose key is in the ledger and that is still set is logged once as `settings_env_ignored` (variable, key, and whether it equals the document's effective value), and `env_still_set` is true in the effective view. `embry0 config` (`embry0/cli/__main__.py:310-353`), which runs without a database, adds a line for each such variable set in its environment: `ASK_HOURS is governed by the settings document (asks.hours); it is read once, at the first start of the release that added the section, and ignored after that`.

**One release later** the `Embry0Config` fields go. This step is not built yet; the release that first removes a moved variable builds it. The `env` entries stay, keyed by the variable's name once the attribute is gone (`tests/unit/settings/test_env.py`, which checks every attribute exists, then learns that form), for two reasons: the import still reads it from the process environment, so a deployment that skipped the deprecation release keeps its value; and `config_lint` reports the retired name with its new key (`ASK_HOURS is no longer read; set asks.hours in the settings document`). The near-miss detector alone would not catch it, since it only looks under prefixes that declared fields still use (`config_lint.py:1-21`).

## 8. Reference docs

`python -m embry0.settings.reference --write docs/configuration.md` rewrites one block of `docs/configuration.md` between `<!-- settings-reference:start -->` and `<!-- settings-reference:end -->`, under a new section "Settings document" that also explains the routes. The block has one table per section, from the catalog's JSON Schema: key, type, default, allowed values or bounds, overridable by, imported from (the variable), description. This follows the events contract, which is generated by a module command and pinned by a test (`tests/unit/execution/test_event_contract.py:24-32`).

Tests:

- the committed block equals the rendered one, and the failure message gives the command;
- every field has a description (also refused at registration), and no description holds a ticket key (the rule of `tests/unit/test_user_facing_text.py`);
- the existing drift guard, `tests/unit/docs/test_configuration_docs.py:58-71`, is unchanged. While a moved variable is still an `Embry0Config` field, the generated "imported from" column names it, which satisfies that guard; the hand-written row in the environment table is deleted in the same PR, so each variable is described once. Once the field is removed, that guard no longer asks for it.

## 9. CLI, MCP and dashboard

| | This issue (#160) | #162 |
|---|---|---|
| API | every route in section 5 | none new |
| CLI | `embry0 config` names the variables the document governs (section 7) | `embry0 settings check`, `diff`, `apply`, `export`, `import-env`; a generated `docs/examples/settings.yaml` |
| MCP | none | `settings_get`, `settings_set` (`confirm=true`) |
| Dashboard | the Policy tab below, in its own pull request (decision D3) | `managed_by: file` makes the editor read-only |

**A minimal dashboard editor** (decision D3): a "Policy" tab on the Settings page (`frontend/src/pages/SettingsPage.tsx:9-33`) holding the YAML text in a textarea with Preview (the dry run's changes), Save (with `If-Match`), the problems of a 422 next to the text, the effective values with their source, and the revisions with Undo. It reuses the patterns of `QaConfigEditor` (`frontend/src/components/qa/QaConfigEditor.tsx`, 276 lines) and `HistoryPanel` (`frontend/src/components/repos/HistoryPanel.tsx`). A form generated from the schema is left out: the text editor covers every section the day it is registered.

## 10. The pilot section

#160 needs one real section: a loop that sees a change on its next tick, and a non-default env value imported once.

| Candidate | Variables | Readers | For | Against |
|---|---|---|---|---|
| **Asks** (decided, D2) | `ASKS_ENABLED`, `ASK_HOURS`, `ASK_WEEKDAYS`, `ASK_TIMEZONE`, `ASK_REMIND_HOURS`, `ASK_EXPIRE_DAYS`, `ASK_MAX_OPEN_PER_PERSON`, `ASK_TICK_SECONDS` (`embry0/config.py:799-808`) | `AskLoop` (`embry0/services/ask_loop.py:33-76`), `GET /asks/due` (`embry0/api/v1/asks.py:80-85`) | Off by default, so the smallest blast radius. Two readers. Every variable moves; none waits on #164 or #165. The section fixes real gaps: a bad `ASK_HOURS` silently becomes 9 to 18 (`asks.py:47-52`), an unknown weekday is dropped (`:66-77`), and a bad `ASK_TIMEZONE` raises in every tick (`asks.py:82`). #179 already asks for an `asks` section overridable per organization | #179 may later move working hours and timezone to a person; they stay as organization-level defaults then |
| Scout, four keys | `SCOUT_TEMPLATES`, `SCOUT_MAX_IN_FLIGHT`, `SCOUT_REVISIT_HOURS`, `SCOUT_MAX_LEADS_PER_JOB` (`embry0/config.py:785-792`) | `ScoutLoop` (`embry0/services/scout.py:422-432`) | #168's acceptance "a change to the scout's templates applies on the next tick" is the same proof | Only half the scout moves: `SCOUT_MODEL` waits on #164 and the repository lists on #165, and the loop stays off without a model, so the section cannot hold `enabled`. Splits #168 |
| Leads core | `LEADS_ENABLED`, `LEADS_FINDERS`, the two intervals, the per-day cap, `LEADS_REVIEW_STALE_DAYS`, `LEADS_FLAKY_COMMITS`, `LEADS_TICKET_MIN_SCORE` (`embry0/config.py:765-775`) | `LeadsLoop` (`embry0/services/leads/loop.py:52-66`, `:242-249`), the backlog finder | Highest value: on by default, the most-used of the new loops | On by default, so the riskiest place to prove a new mechanism; the minimum score is repeated in three places (#168) and the readiness work belongs with it |

**The asks section:**

| Key | Default | Bounds | Organization override | Was |
|---|---|---|---|---|
| `enabled` | `false` | | no | `ASKS_ENABLED` |
| `hours` | `"09-18"` | `HH-HH`, 0 ≤ start < end ≤ 24 | yes | `ASK_HOURS` |
| `weekdays` | `"mon-fri"` | day names, ranges and commas; an unknown name is refused | yes | `ASK_WEEKDAYS` |
| `timezone` | `"UTC"` | a zone `zoneinfo` knows | yes | `ASK_TIMEZONE` |
| `remind_hours` | `24` | > 0 | yes | `ASK_REMIND_HOURS` |
| `expire_days` | `7` | > 0 | yes | `ASK_EXPIRE_DAYS` |
| `max_open_per_person` | `3` | ≥ 1 | yes | `ASK_MAX_OPEN_PER_PERSON` |
| `tick_seconds` | `120` | ≥ 30 | no | `ASK_TICK_SECONDS` |

As built (pull request B): the three text keys keep the form an operator already writes in `.env`, so the import converts nothing and `asks: {hours: "08-17", weekdays: sun-thu, timezone: Asia/Dubai}` reads as spoken. Each is checked by the one parser the feature uses (`parse_hours`, `parse_weekdays` in `embry0/settings/sections/asks.py`) and stored in one spelling (`9-17` is `09-17`, `Mon, Wed` is `mon,wed`), so an equivalent variable is not imported as a change; a time zone is checked by loading it, which is exactly what a tick does. The numbers also take upper bounds (`remind_hours` ≤ 8760, `expire_days` ≤ 365, `tick_seconds` ≤ 86400), so no stored value can overflow a tick's `timedelta` or its sleep. `GET /asks/due` answers `enabled` beside the list.

What changes in the feature: `AskLoop.start` no longer returns when asks are off (`ask_loop.py:68-70`). It runs always, reads the section at the top of each tick, does nothing while `enabled` is false and sleeps the section's `tick_seconds`, so turning asks on applies within one tick. `ask_settings(config)` becomes a conversion from the section model to the existing `AskSettings` dataclass, so `embry0/services/asks.py` stays pure. `GET /asks/due` reads the snapshot per request.

## 11. Delivery

Three pull requests (decision D3). A lands first; C and B branch from it:

- **A. `feat(config): store operator policy in a settings document`.** The registry and an empty catalog, migrations 128 and 129, the store and snapshot, every route of section 5, the env import (generic), the reference generator with an empty block, the `embry0 config` addition, audit actions, ADR 0026 ("operator policy lives in a settings document"), `docs/api.md` and the new section of `docs/configuration.md`. Proven with a section registered only in tests. No behaviour changes for any feature. Closes acceptance criteria 1 and 2.
- **C. `feat(frontend): edit the settings document on the Settings page`.** The Policy tab of section 9. It lands no later than B, so that the moment `.env` stops governing asks, an operator has a way to change them other than raw API calls.
- **B. `feat(asks): read the ask policy from the settings document`.** The asks section, the loop and route changes, the generated reference for it, removal of the hand-written `ASK*` rows, a line in the decisions spec, and upgrade notes ("`ASK*` values are imported once; after that edit the settings document; the variables are removed in the next minor release"). Closes acceptance criteria 3 and 4, #160, and #202 (`GET /asks/due` ignores `ASKS_ENABLED`; the silent `ASK_HOURS`, `ASK_WEEKDAYS` and `ASK_TIMEZONE` handling).

No PR rebuilds a sandbox or proxy image: nothing under `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/` or `embry0/execution/` changes. Each deploys with the usual deploy.

## 12. Test plan

Each new test is shown failing on a clean tree of `origin/main` before the change (`git archive` into a temporary directory, the new test file copied in), with its own assertion message. Database tests run with `TEST_DATABASE_URL` set, in a private database.

| Acceptance criterion (#160) | Tests | PR |
|---|---|---|
| An unknown key, a wrong type and an out-of-range value each get a 422 that names the key | one test per kind, each asserting `loc` is the key's path; one document holding all three gets three entries; an unknown section gets the near-miss hint; a YAML syntax error gets its line; `input` is never in the body | A |
| A PUT followed by an undo restores the previous document, and both are revisions recording actor and source | PUT as admin, undo, stored text equals the first text byte for byte; two revisions, `person` then `restore`, both with the caller as actor; a second undo of the older revision is 409; an unchanged PUT records nothing; a dry run stores nothing and returns the change; a stale `If-Match` is 409 | A |
| A loop that reads a section sees a change on its next tick, with no restart | store level: a write in one store is seen by a second store's next `snapshot()` (two processes); feature level: one `AskLoop` instance ticks with `expire_days: 7` and an ask two days old stays live, the document is changed to `expire_days: 1` through the API, the next tick of the same instance expires it; `enabled: false` to `true` takes effect on the next tick | A (store), B (asks) |
| First boot imports a non-default env value once; a second boot does not import it again | with `ASK_REMIND_HOURS=12` set, the import writes `asks.remind_hours: 12` with source `bootstrap`; a person changes it to 6; a second import with the variable still set leaves 6, records nothing and logs `settings_env_ignored`; a variable set to its default is not imported; a refused value is skipped with a warning and the boot continues; two concurrent imports write one revision | A (with the test section), B (asks) |
| New tests fail on the pre-change tree | fail-first output for each new test file, in the PR description | all |

Also, in A:

- **Registry:** each refusal of `Section` and `Catalog` in section 3; every module under `embry0/settings/sections/` is in `CATALOG`; those modules import only the registry, pydantic and the standard library.
- **Store:** a refused stored value reads as its default and keeps its sibling keys; a failing generation check keeps the last snapshot; the reload reads generation and data in one statement; a database restored to an older copy is read again; a stored value whose check raises `KeyError` does not stop a read.
- **Review fixes:** a key a later release adds to an imported section imports its variable; a key written twice, a document nested too deeply and the NUL character are 422s; a check raising `KeyError` is a 422 on a write and a skipped value in the import; a rollback leaves the stored document writable; an Enum field is described with its values; `If-Match` is honoured on a dry run, `*` matches any and a weak tag never; undo answers like a PUT.
- **Auth:** a read key, an operate key and a restricted admin key are each refused on PUT and undo; a read key can GET.
- **Unknown sections:** a stored unknown section survives an unchanged PUT and is reported; changing it is a 422.
- **Docs:** the generated block equals the committed one; every field is described.
- **Migrations:** 129's test takes over "is the last entry" from 127's (via 128), as `tests/unit/storage/test_migration_127.py:23-26` hands over.
- **`embry0 config`:** names a variable the document governs.

The full unit suite runs against Postgres before each PR is opened, and the failure sets are compared with `main`, not the counts.

## 13. Decided here

These are cheap to change later, or follow from the issue:

- One document per deployment, sections at the top level, an optional `version` key.
- The 422 uses FastAPI's list shape (`loc`, `type`, `msg`), which the dashboard already renders, instead of the standards shape or a joined string.
- Reads take any key; writes take an unrestricted key.
- `data` is stored sparse; defaults are never written into the document.
- Text that differs only in comments is a revision, as for qa.yaml.
- Section definitions live in `embry0/settings/sections/`, listed in one catalog.
- Only variables an operator set, with a value different from the default, are imported.
- The env import ledger is per key (an independent review found a per-section ledger would skip a key a later release adds).
- A stored unknown section, or a key a known section does not have, is kept and passed through while sent back as the stored text has it.
- Anything a section's check raises is a refusal, never an escape.
- No draft state; a dry run answers what a change would do.
- `config_revisions.source` lists `file` now, for #162.

## 14. Decisions

Taken by the maintainer on 2026-10-28, each the option this spec recommended.

- **D1. Where revisions are stored:** a new `config_revisions` table keyed by scope and surface; `repo_config_revisions` stays as it is. The table serves the organization layer of #161 too. Rejected: widening `repo_config_revisions` with an empty repository name for the deployment, and renaming it behind a compatibility view, whose rollback depends on the view accepting every write.
- **D2. The pilot section:** asks, all eight `ASK*` settings. Off by default, two readers, nothing waits on #164 or #165, and it fixes the silent fallbacks filed as #202. Rejected: scout's four keys (half the scout, and it splits #168) and leads core (on by default).
- **D3. Delivery:** three pull requests. A is the foundation, proven with a section that exists only in tests; C is the minimal Policy tab (YAML editor with preview, history and undo); B is the asks pilot. Rejected: leaving the dashboard to a later issue (asks editable only by raw API meanwhile), and one pull request of about 4,000 lines.
- **D4. Who may write:** admin for every section, as for provider, integration, standards and watcher stacks. When #171 moves the budget policy in, writing it tightens from any non-read key (`config.py:160-178`) to admin.
- **D5. Granularity:** one document per deployment, as #160 proposes: one file for #162 to check, diff and apply, and one revision list. The cost accepted: undo reverses only the newest change, so undoing an older change to one feature waits until a later change to another is undone first.

## Out of scope

- Organization and repository layers, and validating `POST /orgs` and the watcher stack PUT against section models (#161).
- The CLI file workflow and the MCP tools (#162).
- Moving any feature other than the pilot (#164 to #182).
- The roughly 170 settings that predate #112 (#159).
