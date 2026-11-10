# Areas in the database: a repository's areas stored in embry0, not in the repository

Status: design, with open decisions (section 15) for the maintainer. Refs #159, #175, #179. Line numbers are at `b2668f26`.

**Why.** A repository's areas (spec 2026-10-25) are read from `.embry0/areas.yml` on its default branch (`embry0/services/areas.py:36`), and the area mapper changes them by opening a pull request on the repository (`embry0/workflows/issue_to_pr/areas_delivery.py`). The project decided long ago that a target repository carries no embry0 configuration file (ADR 0003, migration 52's comment at `embry0/storage/migrations/runner.py:1231-1239`), and epic #159 restates it: every operator decision is a document in the database with a schema, a 422 naming each problem, revisions with undo, and a write path through the API, the CLI, MCP and the dashboard. Areas are the last feature that reads its configuration from the target repository. Under that design an organization adopting embry0 commits an embry0 file to every monorepo, and area owners, who decide who embry0 asks and who may answer, are edited through pull requests on someone else's code.

**What changes.** A repository's areas become a document stored in embry0 (`repo_areas`), recorded in `repo_config_revisions` like the test setup and preferences, so the repository page's History lists and undoes them. They are read and written through `GET` and `PUT /repos/{repo}/-/areas`, MCP tools, a CLI and an editor on the repository page. The area mapper no longer opens a pull request: its result is a proposal that a person applies or rejects, as the onboarding agent's test setup is (ADR 0019), and applying it is a revision that undo reverses. Each repository's `.embry0/areas.yml` is imported once, then never read; a later release deletes the import, and with it the last code path that reads embry0 configuration from a target repository.

## 1. What exists today

### The file and its readers

`AreaReader` (`embry0/services/areas.py:181-205`) reads `.embry0/areas.yml` through GitHub's contents API (`read_contents`, `:146-153`) and keeps each repository's areas an hour (`CACHE_SECONDS`, `:38`). `parse_areas` (`:56-73`) is lenient: a file that is not YAML names no areas, an entry without a name is skipped, and an owner that is not a GitHub login is dropped without a word. Every reader goes through the forge, each with its own credential path:

| Reader | Where | Credential | Uses |
|---|---|---|---|
| Areas API | `embry0/api/v1/areas.py:45-83` | the organization's (`org_forge_client`); a repository whose organization embry0 does not hold has no areas | `GET /repos/{repo}/-/areas` |
| Owner proposals | `embry0/api/v1/proposals.py:83-145`, `:170-180` | the organization's | `area` and `owners` of every proposal (`embry0/services/proposals.py:322-357`) |
| Decisions | `embry0/api/v1/decisions.py:189-208` | the organization's (through `proposals._areas`) | a finished quest's owners |
| World | `embry0/api/v1/world.py:143-163` | the organization's | towns in a region |
| Pull requests embry0 opens | `embry0/services/_executor_outcomes.py:618-641`, `:764-776` | the executor's GitHub client and credential resolver | `[Name]` title, `area:<slug>` label, `jobs.area` |
| Leads | `embry0/services/leads/loop.py:53-63` (`lead_area`), `:95` (the default of `area_of`, which production never overrides, `embry0/api/app.py:2473-2481`) | the lead loop's client | `leads.area` |
| Scout | `embry0/api/app.py:2791-2804`, `:2832` | the Slop-O-Meter's client | one scouting unit per area (`embry0/services/scout.py:126-131`) |
| Slop-O-Meter | `embry0/services/slop_meter/loop.py:44`, `:245`, `:665` | none: the bare mirror's copy of the file | one meter per area |

### The writer

`POST /repos/{repo}/-/areas/map` (`embry0/api/v1/areas.py:86-130`) starts a `map-areas` job whose task carries the file on the default branch (`embry0/services/area_map.py:21-37`). The `area-mapper` (`embry0/workflows/issue_to_pr/specialist_agents.py:792-866`) reports the whole file under `## Areas file`; the output step (`embry0/workflows/issue_to_pr/plan_route.py:744-752`, chosen by the template's name) commits it on the job's branch and pushes, and the executor opens the pull request. Nothing changes until that merges. The built-in rows say so: the template's `delivers` is "a pull request that maps the repository's areas" (migration 143, `runner.py:4085-4090`), and the agent's description and prompt name the file (`embry0/storage/repositories/agent_definitions.py:333-347`). The progression fact `areas_mapped` counts the first merged map pull request per repository (`embry0/storage/repositories/progression.py:406-408`), shown as the Cartographer achievement (`embry0/services/progression_pack_default.yaml:436-443`).

### Owners

Three sources, all GitHub logins, resolved in two copies of one rule (`embry0/api/v1/proposals.py:170-180` and `embry0/api/v1/decisions.py:189-208`): the area's `owners`, else the repository's `repo_preferences.owners` (`embry0/api/schemas/repo_preferences.py:128-144`, operate tier), else the users `CODEOWNERS` names for `*`. Owners are not only asked: a chat assistant may answer a decision, `merge` included, for a person whose GitHub login is among its owners (`embry0/api/v1/asks.py:292-305`).

### Other "parts of a repository"

- **qa.yaml v2 apps** (`embry0/workflows/qa/qa_yaml_v2.py:279-388`): a thing that boots or is deployed, with a URL. Only a deployed app may carry `when.changed_paths`, which narrows its QA runs to diffs that touch it; managed apps follow the workspace provider's affected set.
- **Knowledge maps** (`embry0/services/knowledge_maps.py:219-229`, `docs/maps.md:20`, `:30`): a scanner's `areas` are `{id, label, summary, x, y, r}`, rings drawn on the map's plane; a part names its ring by id. They carry no paths, and a map may describe a whole organization.
- **Three glob grammars:** areas' own (`embry0/services/areas.py:77-95`: a trailing `/` means everything below, a leading `/` is dropped, `**` may touch other characters), qa.yaml's (`embry0/workflows/qa/_glob_match.py`: anchored, `**` a whole segment, `[...]` classes, no leading or trailing `/`), and the Slop-O-Meter's (`embry0/services/slop_meter/globs.py`: lowercase, a glob without `/` matches a file's name at any depth).

### Precedents this design follows

- **qa.yaml left the repository in two steps:** `da57a86e` (#134) stored it in the database and imported the in-repo file on first use with a deprecation warning; `ff3c3173` (#137) deleted the in-repo channel. Deployments that skipped the import release had to `PUT` their configs.
- **ADR 0019:** every write to a repository's test setup or preferences appends a `repo_config_revisions` row in its own transaction; undo reverses the newest change of a surface while the stored document still equals its result; an agent's draft is a proposal a person applies, compare-and-set against the document it started from.
- **ADR 0026 and the settings documents spec (2026-10-28):** YAML kept as written with its comments, validated values stored beside it so no reader parses YAML, `generation` and `If-Match`, `?dry_run=1`, the 422 in FastAPI's list shape, the refusals of `embry0/text_documents.py`.
- **ADR 0028:** facts about a repository with no wider default are not a settings layer key. A repository's areas have no deployment or organization default, so they are their own document, not a settings section.

## 2. The document (schema v1)

```yaml
# Areas of acme/widgets. The first area whose paths match a file has it.
version: 1
areas:
  - name: Billing
    paths:
      - "services/billing/**"
      - "web/apps/billing/"          # a trailing / is everything below: web/apps/billing/**
    logs: ["billing-api"]
    owners: ["octo"]
  - name: Search
    paths: ["services/search/**"]
```

The format is today's file with an optional `version`, so an existing file is a valid document unless the stricter checks below refuse something in it (section 7 says what the import does then).

| Key | Rule |
|---|---|
| `version` | Optional; `1`. A document naming a newer version is a 422 |
| `areas` | A list, at most 200 entries; empty or absent means the repository names no areas |
| `areas[].name` | Required text, 1 to 100 characters, no line break. Its slug (`[a-z0-9]+` runs joined by `-`, `Area.slug`) must not be empty and must differ from every other area's: two areas with one slug would share the label `area:<slug>` |
| `areas[].paths` | Optional list, at most 200 globs, in qa.yaml's grammar (`compile_glob`, `embry0/workflows/qa/_glob_match.py:67`), plus one shorthand kept from the file: a trailing `/` stands for `/**`. The stored values hold the expanded form |
| `areas[].logs` | Optional list, at most 50 strings, each 1 to 200 characters, no line break. Matched as today: case ignored, found anywhere in a watcher case's signature or title |
| `areas[].owners` | Optional list, at most 20 logins, checked as `repo_preferences.owners` is (`GITHUB_LOGIN_RE`, `embry0/api/schemas/repo_preferences.py:38`); a leading `@` is dropped, and a login written twice (case ignored) is kept once. Section 9 says why it stays the same check |

- **Strict, every problem at once.** Unknown keys are refused (`extra="forbid"`), types are strict, and one 422 lists every problem in FastAPI's shape, so the dashboard's parser renders `areas.0.paths.1: '**' must be its own path segment`. A glob, login or name problem never echoes the value.
- **The refusals every operator document shares** (`embry0/text_documents.py`): aliases, merge keys, keys written twice, keys that are not text, numbers YAML cannot read, NUL, nesting past the stored depth. The text is capped at 256 KiB (413 beyond).
- **Stored as written.** `yaml_text` is the text as sent, comments included; a JSON body is stored as the YAML it dumps to. `data` holds the validated, normalized values (`@` dropped, `/` expanded), and every reader reads `data`.
- **Additive within version 1.** A key a later release adds takes a default. A reader ignores keys it does not know, so a rollback past that release still reads the rest; a write by the older release is refused naming the key, as for any unknown key.
- **Empty is a value.** `PUT` of an empty text, or of `areas: []`, stores a document that names no areas: recorded and undoable. There is no `DELETE`.

## 3. Storage: migration 145

Migration 145 (reserved for this work) creates three tables, widens one CHECK and refreshes two built-in rows. It creates no document: the application imports them (section 7), because SQL cannot validate them.

```sql
CREATE TABLE IF NOT EXISTS repo_areas (
    repo            TEXT PRIMARY KEY REFERENCES repos(repo) ON DELETE CASCADE,
    yaml_text       TEXT NOT NULL,
    data            JSONB NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    schema_version  INTEGER NOT NULL DEFAULT 1,
    generation      BIGINT NOT NULL DEFAULT 1,
    updated_by      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS repo_areas_proposals (
    id               TEXT PRIMARY KEY,
    repo             TEXT NOT NULL,
    job_id           TEXT,
    status           TEXT NOT NULL CHECK (status IN
                     ('running', 'pending', 'applied', 'rejected', 'stale', 'failed', 'unchanged')),
    base_generation  BIGINT NOT NULL,            -- 0: no document when the map started
    proposed_text    TEXT,
    notes_md         TEXT NOT NULL DEFAULT '',
    failure          TEXT NOT NULL DEFAULT '',
    created_by       TEXT NOT NULL DEFAULT '',
    decided_by       TEXT,
    decided_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_repo_areas_proposals_repo ON repo_areas_proposals (repo, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_repo_areas_proposals_live
    ON repo_areas_proposals (repo) WHERE status IN ('running', 'pending');

CREATE TABLE IF NOT EXISTS repo_areas_imports (
    repo        TEXT PRIMARY KEY,
    outcome     TEXT NOT NULL CHECK (outcome IN ('pending', 'imported', 'none', 'failed')),
    detail      TEXT NOT NULL DEFAULT '',
    blob_sha    TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO repo_areas_imports (repo, outcome) SELECT repo, 'pending' FROM repos ON CONFLICT DO NOTHING;
```

- **`repo_areas`** is keyed as `repo_qa_configs` is (the registry's spelling, removed with the repository; `runner.py:1248-1253`); its history in `repo_config_revisions` outlives it. A registered repository without a row has the empty document at generation 0, so the read path never needs a row and a first write is `If-Match: "0"`. `generation` goes up by one in every write's transaction, as for the settings document (`docs/superpowers/specs/2026-10-28-settings-documents-design.md`, section 4).
- **`repo_areas_proposals`** is `repo_setup_proposals` (migration 105, `runner.py:3000-3033`) for this document: the same statuses plus `unchanged` (a map whose document equals the stored one, which today "opens nothing"), and the base as a generation instead of a timestamp. The partial unique index is again the lock against two maps of one repository. A table of its own, not a `surface` column on `repo_setup_proposals`: that table's status, readiness and progression readers (`embry0/services/repo_readiness.py`, the `setup_applied` fact at `progression.py:430-432`) would each need a filter, and one wrong reader would count a map as a test setup.
- **`repo_areas_imports`** is the import's ledger. The migration lists every repository registered when it runs, so the import reads exactly those (section 7).
- **`repo_config_revisions`** (migration 104, `runner.py:2974-2998`): the `surface` CHECK gains `areas`, and the `source` CHECK gains `import` (the one-time import) and `file` (the CLI's apply, as `config_revisions` has it). `before` and `after` are `{"yaml_text", "data"}`; `KEEP_PER_SURFACE` (100) applies per repository and surface as now.
- **Built-in rows, only where unchanged:** the `map-areas` template's `delivers` becomes "areas for a person to apply" where it still holds migration 143's text; the `area-mapper` agent's `description` and `system_prompt` become the new seed (section 6) where they still equal the seed at `b2668f26`. A row an operator edited keeps its text, as migration 106 did for identities (`runner.py:3034-3058`). Built-in template descriptions are rewritten by the boot seed already (`embry0/storage/repositories/pipeline_templates.py:166-225`); agents and `delivers` are not, hence the SQL.

## 4. Reading

```python
class AreasStore:                                      # embry0/services/areas_store.py, app.state.areas
    async def get(self, repo: str) -> StoredAreas      # text, data, generation, updated_by, updated_at; empty at 0
    async def areas_of(self, repos: Iterable[str]) -> dict[str, list[Area]]   # one query, keyed lower-case
    async def save(self, repo, text, *, if_match, actor, source, reason, dry_run=False) -> SaveResult
```

- **One query per call, no cache.** `areas_of` reads `data` for every repository it is asked about in one `SELECT ... WHERE lower(repo) = ANY($1)`. The rows are small, so the hour-long cache and `fresh=` go: a change applies at the next read. A loop reads once per pass, not per item (the leads loop once per repository, not once per lead as `lead_area` does now).
- **Every reader of section 1 is given the store**, by injection, as a required argument: `LeadsLoop.area_of` loses its default (`lead_area`, which production relies on today), and the executor takes the store in its constructor instead of reaching for the module's `AREAS` (`embry0/services/issue_executor.py:144-145`). A test pins the production wiring of each (the guardrail on optional arguments production cannot work without, #369):

  | Reader | New source |
  |---|---|
  | Areas API, owner proposals, decisions, world | `app.state.areas.areas_of(...)` |
  | Pull requests embry0 opens | the store; the compare call stays (GitHub only, as now) |
  | Leads | `area_of(repo, lead)` built on the store, read once per repository pass |
  | Scout | `areas_for` built on the store |
  | Slop-O-Meter | the store instead of the mirror's file; the version a score records adds the areas' generation, so an areas change rescores the repository on the next tick without judging, as a settings change does (`embry0/services/slop_meter/loop.py:740-775`) |

- **One owner resolver** (`embry0/services/owners.py`) replaces the two copies of the rule: `owners_for(repo, area) -> Owners(logins, source)` with `source` one of `area`, `repository`, `codeowners`. The proposals feed, decisions and asks call it. Section 9 says why #179 needs it.
- `Area` and the matching functions (`area_of_path`, `area_of_paths`, `area_of_logs`, `area_of_labels`, `titled`) stay in `embry0/services/areas.py` unchanged; `_glob` is replaced by qa.yaml's `compile_glob`, which matches every glob the new schema accepts.

## 5. API

All under `/api/v1`, in `embry0/api/v1/areas.py`, behind the `/-/` separator every repository sub-resource uses. The routes added here get no old path without the separator (`embry0/api/v1/repo_old_paths.py:38`); `GET .../areas` and `POST .../areas/map` keep theirs until it is removed.

| Route | Tier | What it does |
|---|---|---|
| `GET /repos/{repo}/-/areas` | read | `{repo, yaml_text, areas: [{name, slug, paths, logs, owners}], generation, updated_by, updated_at, imported, latest_job, proposal}` with `ETag: "<generation>"`; `?format=yaml` answers the text alone. `imported` is `{outcome, at, blob_sha}` from the ledger, or null. `proposal` is the newest proposal's `{id, status, job_id, created_at}`, or null. `path` (the file) is gone; `fresh` is ignored |
| `PUT /repos/{repo}/-/areas` | operate (decision OD3) | Replace the document. YAML (`application/yaml`, `text/yaml`, `text/plain`) or a JSON object. Optional `If-Match`, `?reason=`, `?source=person\|file`, `?dry_run=1` (validate and compare; store nothing). Answers `{generation, revision_id, changes}`; `revision_id` is null when the text did not change. `changes` lists areas added, removed and changed, each change by key (paths, logs, owners). 404 for an unregistered repository: the registry is not written by this route (a typo must not register a repository nobody uses) |
| `POST /repos/{repo}/-/areas/map` | operate | As today, but it first retires a pending proposal (`stale`), creates a `running` one at the current generation, and starts the job with that proposal's id (section 6). 409 while one runs |
| `GET /repos/{repo}/-/areas/proposal` | read | The newest proposal: `{id, status, job_id, base_generation, stale, proposed_text, changes, text_diff, notes_md, failure, created_by, decided_by, decided_at}`; `stale` is whether the stored generation moved since. 404 when there is none |
| `POST /repos/{repo}/-/areas/proposal/{id}/apply` | operate | Section 6. Answers `{revision_id}`; 409 stale, 409 not pending, 422 no longer valid |
| `POST /repos/{repo}/-/areas/proposal/{id}/reject` | operate | Marks it `rejected`; stores nothing |
| `GET /areas/proposals?status=pending` | read | Every pending proposal, newest first, for the dashboard's BriefBar, as `GET /setup/proposals` (`embry0/api/v1/repo_setup.py:195-206`) |
| `GET /repos/{repo}/-/history`, `POST .../history/{id}/undo` | as today | Gain the surface `areas` (`change_what` describes it by area; `_current` reads `repo_areas`; undo checks the old text against today's schema first, as it does for the test setup) |

**Audit** (`embry0/audit/actions.py`, with labels; `tests/unit/audit/test_action_registry.py` checks both ways): `repo.areas_set`, `repo.areas_imported`, `repo.areas_map_applied`, `repo.areas_map_rejected`. Undo keeps `repo.change_undone`. None carries a document's values.

**Ingress:** these are "every other `/api/` route" (`docs/ops/deploy.md`): the dashboard tunnel and the LAN, never the webhooks tunnel. No new row.

## 6. The mapper proposes; a person applies

1. **Start.** `POST .../areas/map` checks the key's allowlist (`CREATE_A_JOB`) before anything else, retires a pending proposal of the repository as `stale`, creates a `running` proposal with `base_generation` the stored generation, and starts the job. The task carries the stored document's text (`map_task(repo, existing=stored.yaml_text, ...)`), not the file. The job's pipeline config carries `areas_proposal_id` instead of `open_pr`, so the output step knows what to deliver from the job, not from the template's name (the name check at `plan_route.py:749` goes).
2. **Finish.** The output step reads the full report (not the job's summary, which is cut at 10,000 characters and can lose the block), takes the `yaml` block under `## Areas file` (`report_file`, kept) and the rest as notes (`_summary`, kept), and returns them in the state. It pushes nothing: the git step, the drop file and `GitStep.AREAS_DELIVERY` are deleted. At completion the executor stores the outcome on the proposal: `pending` with the text; `unchanged` when the text's values equal the stored ones; `failed` with the reason when there is no block or the schema refuses it (the problems, each with its key). A job that fails, is cancelled or is cut off by a restart marks its proposal `failed`, as `_fail_setup_proposal` does (`embry0/services/_executor_run.py:380-392`), and reading a `running` proposal whose job is gone settles it (`settle`, `embry0/services/repo_setup.py:129-141`).
3. **Review.** The proposal shows what applying would change, area by area: added, removed, renamed by slug, paths, logs and owners, with **owners called out first**, since a login added there may answer that area's decisions in chat (section 1), and the mapper's notes (`## Changes`, `## Not covered`, `## Assumptions`). Where it shows is decision OD4.
4. **Apply** is ADR 0019's compare-and-set: in one transaction, the proposal moves from `pending` to `applied` only while the stored generation still equals `base_generation`, and the document is written with source `proposal`, actor the person, reason `proposal <id> (job <job_id>)`. A document that moved since is `stale` (409). The text is validated again against today's schema (422 if no longer valid). **Reject** moves it to `rejected` and writes nothing. A person who wants to change the proposal first opens it in the editor; saving there moves the generation, so the proposal reads as stale and is retired by the next map.
5. **Undo** of the applied revision (`POST .../history/{id}/undo`) puts back the document the map replaced, byte for byte, or the empty document when there was none, while nothing changed since: the second acceptance criterion of #175. The undo is itself a revision (`restore`).

**The agent.** The new seed of the `area-mapper` prompt says embry0 keeps the areas and a person applies its document before it takes effect; that the task carries the current document to start from; that a `.embry0/areas.yml` in the tree is a leftover embry0 no longer reads; that owners are logins of the repository's forge. The report's shape (`Areas: N`, the table, `## Areas file`, `## Changes`, `## Not covered`) is unchanged, so an operator's own copy of the prompt keeps working. The template's description (`embry0/storage/seeds/pipeline_templates_builtin.py:340-346`) and `delivers` say the same.

**Progression.** `areas_mapped` counts, per repository, the first of: a map pull request merged before this release (today's fragment), or a proposal applied. A repository mapped before keeps its credit; one mapped after earns it on apply. The default pack's `what_counts` becomes "Repositories whose areas map was applied"; a customised pack keeps its words.

## 7. The one-time import, and the end of reading the repository

**What is read.** For each ledger row (`repo_areas_imports`) still `pending` or `failed`, after boot and in the background (no loop waits on it): repositories on a forge other than GitHub are `none` with no call, since today's reader only ever read GitHub; any other repository's `.embry0/areas.yml` is read from its default branch through the contents API, with the credential the resolver picks for the repository (the pull request path's, so a repository whose organization embry0 does not hold is imported too). At most four reads at once.

**What is stored.**

- No file: `none`. A file that is not YAML or names nothing usable: `none`, with the reason in `detail` and the log.
- A file the v1 schema accepts as written: stored byte for byte, source `import`, actor `embry0:areas-import`, reason `imported from .embry0/areas.yml (blob <sha>)`, outcome `imported`.
- A file the v1 schema refuses: what today's lenient reader reads (`parse_areas`) is what embry0 already acts on, so that is what is imported. Its values are normalised (a leading `/` dropped, a trailing `/` expanded) and checked again, and an entry or value that still fails is left out. The stored text is then written from the values, under a header comment that says it was imported, that the original's comments were lost, and which entries or values were left out and why, as the settings import does when it cannot keep a text (spec 2026-10-28, section 7).
- A read that fails (network, credential, a 5xx): `failed`, `attempts + 1`, retried at the next boot. A repository with a stored document already is `none`: the import never overwrites a person's write.

**Not read.** A repository registered after migration 145 ran has no ledger row and is never read: it starts with no areas, and a person maps or writes them. Nothing reads a repository's file after its row leaves `pending` or `failed`.

**Telling people.** The Areas section shows `imported` ("imported from the repository's `.embry0/areas.yml` on DATE; embry0 no longer reads that file"). embry0 never deletes the file: writing to a repository to remove configuration is the coupling this change removes. Operators are told not to delete it until the release that removes the import, so a rollback still reads it (below).

**Rollback.** A rollback to the release before leaves the three tables unread and the widened CHECKs harmless; that release reads the files again, which are still there. Areas written in embry0 meanwhile are not visible to it.

**The removal release** (decision OD5) deletes: the import module and its boot task; `parse_areas`, `AreaReader`, `AREAS`, `CACHE_SECONDS`, `AREAS_PATH` and `_glob`; `read_contents` moves to the one remaining caller, the `CODEOWNERS` reader (`embry0/api/v1/proposals.py:83-130`); `_read_file` in the areas API; `lead_area`'s and the scout's forge reads; `AREAS_PATH` from the Slop-O-Meter's mirror reads. The ledger table stays, unread, until a later migration drops it. A static test (section 13) then allows no `.embry0/areas.yml` anywhere under `embry0/`.

**What is not embry0 configuration.** The criterion "no code path reads configuration from a file in the target repository" is read as the epic states it: no *embry0* configuration file. Files a repository keeps for its own reasons and that embry0 reads as evidence stay: `CODEOWNERS` (the forge's review rules, the owners fallback), `.env.example` (`embry0/api/v1/environment.py:298-345`), `package.json` and the rest of discovery (`embry0/services/org_discovery.py:244-290`), and the Slop-O-Meter's ignore and linter files (`CONFIG_FILES`, `embry0/services/slop_meter/weights.py:113`). The mapper still reads the repository's tree in its sandbox; that is its job.

## 8. Areas, apps and map groups

They are three different things, and the design keeps them apart (decision OD1):

- An **area** is a part of a repository that people answer for: it names work, routes asks, labels pull requests, scores a meter. It is partitioning: the first area whose glob matches a file has it.
- A qa.yaml **app** is something that runs and is tested at a URL. Two apps can share an area (`billing-api` and `billing-web` in Billing), and one app can span several (a web shell that loads every area's pages). Its `when.changed_paths` is a relevance test for a deployed app, not a partition: overlapping apps are normal.
- A knowledge map's **areas** are a scanner's rings on a plane. They may group parts with no path (a table, a host, a whole repository on an organization map), so they cannot be a repository's partition.

What they share is the glob grammar: areas take qa.yaml's (section 2), so a glob copied between the two documents means the same thing. The recommendation keeps the word **area** for the first, **app** for the second, and calls the third **groups** where people read it (the Maps page's "colour by" and legend, `docs/maps.md`), while `embry0.map/1` keeps reading the key `areas`, and a future `embry0.map/2` may rename it. The glossary gains one entry, **Area**, saying this.

## 9. Owners, and what #179 needs

Today's precedence stays: the area's owners, else the repository's (`repo_preferences.owners`), else `CODEOWNERS`. Where the repository's own owners live is decision OD2.

**Settled here:**

- Area owners are logins checked as `repo_preferences.owners` are, because every consumer matches them against GitHub logins: `users.github_login` (`embry0/api/v1/handoffs.py:40`), `_may_decide` (`embry0/api/v1/asks.py:292-305`), and the `owner=` filter of the proposals feed. A different rule for one of the three sources would give an owner who can never be matched to a person. #179 widens all of them together when people get forge-neutral identities.
- One resolver (section 4) returns each owner with where it came from.

**What #179 builds on this, and needs no change here for:**

1. The areas document refers to people by login and holds nothing else about them: no email, chat handle, time zone or hours. #179's organization `people` document maps a login to the person, looked up in the repository's organization.
2. `owners_for(repo, area)` is the one place a login becomes a person. #179 resolves there and every consumer (feed, decisions, asks) gets people.
3. A later form of owner, such as a group, can be added inside version 1 as a prefixed string (`group:billing`). A reader skips an owner form it does not know, so an older release reads the logins and ignores the rest. Validating that a login names a known person is #179's: it can add a `warnings` list to the `PUT` answer without changing this schema.
4. The asks rights stay with owners (`_may_decide`). Who may *write* owners is decision OD3; #179 can tighten it once a person can be "the area's owner" in embry0's own terms.

## 10. The read boundary

A key whose repository allowlist does not cover a repository gets, from every route of section 5, exactly what it would get for a repository nobody registered (`embry0/api/repository_reads.py:1-35`):

- `GET .../areas`: the same 404 ("is not a registered repository") as today (`embry0/api/v1/areas.py:36-42`), with no read of `repo_areas`, the ledger or proposals. `GET .../areas/proposal`: the same 404 as no proposal. History: no entries, as now.
- Every write (`PUT`, `?dry_run=1` included, map, apply, reject, undo): `enforce_repo_allowlist` refuses with 403 `repo_not_allowed`, audited, **before** the registry or the document is read, so the answer is the same whether the repository is registered or not (as the map route is pinned today, `tests/unit/api/test_repository_reads.py:356-380`).
- Lists: `GET /areas/proposals` is narrowed in its query (`covered_slugs`) and by `covered`. An area's name, paths and owners reach other answers (proposals, decisions, world, jobs, meters) only through rows of repositories those routes already narrow.
- MCP tools and the CLI call the API with their key, so the same rule applies. The boot import runs as the orchestrator, with no key.

`tests/unit/api/test_repository_reads_inventory.py` lists every new `GET` route in `READ_ROUTES` (its walk fails until it does), and the write inventory (`tests/unit/api/test_allowlist_boundary_writes.py`) every new write.

## 11. MCP, CLI and dashboard

- **MCP** (`embry0/mcp_server/server.py`, each write with `confirm=true`): `areas_get(repo)` (the document, its generation and the newest proposal), `areas_set(repo, text, expected_generation, dry_run, reason)` (`dry_run` runs without confirm), `areas_map(repo, run_with)`, `areas_proposal_decide(repo, proposal_id, decision="apply"|"reject")`, `areas_undo(repo, revision_id)`. The tool descriptions say that target repositories carry no areas file.
- **CLI** (the epic's "apply a file"): `embry0 areas check FILE` (offline, the API's own checks), `diff REPO FILE`, `apply REPO FILE` (diff, confirm, `If-Match`, source `file`), `export REPO [-o FILE]`. They reuse `embry0/cli/settings.py`'s client, exit codes and confirmation.
- **Dashboard** (`frontend/src/components/repos/RepoAreasSection.tsx`, `frontend/src/api/areas.ts`, `frontend/src/hooks/useRepoAreas.ts`): the table stays as the view. **Edit** opens a YAML editor on the stored text, the pattern of the Policy tab and `QaConfigEditor`: Preview (the dry run's changes), Save with `If-Match`, the 422's problems next to the text. The imported note of section 7. The proposal card of section 6 (changes, owners first, notes, Apply, Reject, "open in the editor", stale state). **Map areas** says the result waits for a person, not that a pull request opens. `HistoryPanel` names the sources `import` and `file`. The BriefBar (`frontend/src/components/dashboard/BriefBar.tsx:52`) lists pending maps beside pending setups. No form generated from the schema: a text editor covers the document, as the settings spec decided (D3).

## 12. Delivery

Four pull requests. The first two deploy together, so the moment embry0 stops reading the file a person can edit and approve areas in the dashboard:

1. **`feat(areas): store a repository's areas in embry0`**: migration 145; the schema and `AreasStore`; the API of section 5; the import; every reader of section 1 on the store; the owner resolver; the mapper's proposal path and the deletion of its git push; the agent and template seeds; the progression fact; MCP tools; audit actions; `docs/areas.md` (rewritten), `docs/api.md`, `docs/specialist-agents.md`, `docs/architecture.md`, `docs/glossary.md`; ADR 0030 ("a repository's areas live in embry0; its agent proposes and a person applies"); this spec's "as built" notes. No behaviour changes but the source of areas and the map's delivery.
2. **`feat(frontend): edit and approve a repository's areas`**: section 11's dashboard.
3. **`feat(cli): check, diff and apply a repository's areas`**.
4. **`refactor(areas): stop importing .embry0/areas.yml`**, in the release that decision OD5 names: section 7's removal list, the static test tightened to none, the upgrade note.

**Deploy notes.** No sandbox, QA or proxy image rebuild: nothing under `embry0/sandbox/`, `embry0/safety/` or `embry0/agents/` changes, and `embry0/execution/sandbox_git.py` is not baked into an image (`infra/Dockerfile.sandbox:87-93`). The usual deploy runs migration 145, then the import in the background; until it finishes (seconds per repository), a pull request embry0 opens is not labelled with an area and a proposal falls back to the repository's owners.

## 13. Test plan

Each new test is shown failing on a clean `git archive origin/main` tree first, with its own assertion message. Database tests run with `TEST_DATABASE_URL` set, in a private database.

| Acceptance (#175) | Tests | PR |
|---|---|---|
| No code path reads configuration from a file in the target repository | A static test fails on any `.embry0/areas` path in `embry0/` outside the import module (PR 1) and anywhere (PR 4); it fails on `b2668f26`, where eleven modules name it. With stored areas and a fake forge client that fails the test on any request for a path under `contents/.embry0/`: the proposals feed, decisions, world, the pull request fallback, a lead pass, a scout tick and a meter score each run and name the stored area. The production wiring of the leads loop, the scout, the executor and the meter passes the store (the #369 guardrail) | 1, 4 |
| A mapped set of areas applies only after approval, and an undo restores the previous set | A map job's report becomes a `pending` proposal, `repo_areas` is unchanged, no git step runs and no pull request opens; apply writes it (source `proposal`); undo puts back the earlier text byte for byte, both recorded; apply after a `PUT` is 409 and the proposal `stale`; reject writes nothing; a report equal to the stored values is `unchanged`; a report the schema refuses is `failed` with each problem's key; a job that fails marks its proposal `failed` | 1 |

Also:

- **Schema:** one 422 per rule of section 2, each with its `loc`; a document with several problems lists all of them; no value is echoed; a duplicate slug, a bad glob and a non-login owner each refused; the trailing `/` expands; YAML and JSON bodies; 413 past the cap.
- **Store and API:** a registered repository with no row reads empty at generation 0; `If-Match` stale is 409 and `"0"` creates; an unchanged `PUT` records nothing; a dry run stores nothing and returns the changes; `?format=yaml` returns the text; `PUT` for an unregistered repository is 404 and registers nothing.
- **Import:** a valid file is stored byte for byte with source `import`; a lenient-only file is stored from its values with the header naming what was left out; no file and a GitLab repository are `none` with no forge call; a failed read is retried on the next run and not after it succeeds; a repository registered after the migration is never read; a stored document is never overwritten; the second run makes no forge call (the fake client counts).
- **Read boundary:** the inventory walk with the new routes; a limited key's `GET`s answer byte for byte as for an unregistered repository; every write is refused before any read, the same for a registered and an unregistered repository.
- **Owners:** the resolver's precedence and provenance; the feed and decisions give the same owners as before for the same areas (one test over both, built from the old cases).
- **Progression:** `areas_mapped` counts an applied proposal and keeps a pre-release merged map; a repository with both counts once.
- **Migration 145:** the tables and indexes; the CHECKs accept the new surface and sources and still refuse others; the ledger lists the repositories registered at the time; the built-in rows change only where they held the old seed (an edited row keeps its text); its test takes over "is the last entry" from 144's.
- **Frontend:** the editor's preview, save and 422 problems; the proposal card's changes with owners first, apply, reject and stale; the BriefBar line; History's labels.
- **CLI:** `check` offline with the API's messages; `apply` diffs, confirms and sends `If-Match`.

The full unit suite runs against Postgres before each pull request is opened, and its failure set is compared with `main`'s.

## 14. Decided here

These follow from the code and the project's decisions, or are cheap to change later:

- A document per repository in `repo_areas`, not a settings section or layer (ADR 0028: facts with no wider default), not a column of `repo_preferences` (a long text with comments and its own history).
- Revisions in `repo_config_revisions` under a new surface `areas`, so the repository's existing History and undo cover it (ADR 0019), not in `config_revisions`.
- Settings-document conventions for the text: stored as written, `data` beside it, `generation` and `If-Match`, `?dry_run=1`, the 422 list shape, the shared refusals, 256 KiB.
- A registered repository is required; no auto-registration on `PUT`.
- The map's result is an ADR 0019 proposal in a table of its own, applied as is or rejected; the mapper never writes to the repository.
- What a job delivers is in its pipeline config (`areas_proposal_id`), not keyed by the template's name.
- qa.yaml's glob grammar, plus the trailing `/` shorthand; one glob means the same in both documents.
- Owners checked as `repo_preferences.owners` are, until #179 widens every source.
- The import reads only repositories registered when migration 145 ran, never overwrites a stored document, and keeps what today's reader reads when the schema is stricter.
- embry0 never deletes a repository's `.embry0/areas.yml`.
- `CODEOWNERS` and the other files a repository keeps for itself are evidence, not embry0 configuration, and stay.
- The CLI is built (the epic asks for "apply a file"), in its own pull request.
- Undo restores the previous document, the empty one included.

## 15. Open decisions

Each needs the maintainer. The recommended option is first.

### OD1. One word, and how areas relate to qa.yaml apps and map groups

1. **(Recommended) Three concepts, three words, one glob grammar.** "Area" is the repository part this document names; qa.yaml apps stay apps, with their own `when.changed_paths`; a knowledge map's `areas` read as "groups" on the Maps page and in `docs/maps.md`, while `embry0.map/1` keeps the key. Areas and apps share qa.yaml's glob grammar. *Costs:* an app's `changed_paths` and an area's `paths` can name the same directories and drift apart; a wording change on the Maps page. *Risk:* low; nothing stored changes meaning.
2. **Apps refer to areas.** A deployed app's `when` may say `areas: [billing]` instead of globs, so areas are the one home of a repository's path globs. *Costs:* qa.yaml validation reads a second document, so a test setup can no longer be checked alone (the CLI's offline check included); renaming or removing an area must be refused, or must silently change which QA runs an app takes part in; a qa.yaml schema change. *Risk:* an areas edit by one person changes another's QA coverage.
3. **Map groups are areas.** A repository map's parts are grouped and coloured by the repository's areas, from their paths, and a scanner's own `areas` are dropped. *Costs:* organization maps and parts without paths lose their grouping; a published upload format changes meaning. *Risk:* scanners already writing `embry0.map/1` render differently.

### OD2. Where a repository's own owners live

1. **(Recommended) They stay in `repo_preferences.owners`.** The areas document holds per-area owners; the one resolver reads both, then `CODEOWNERS`. *Costs:* "who answers" is edited in two places (Preferences and Areas) until #179 decides where all owners live. *Risk:* none new.
2. **They move into the areas document** as a top-level `owners:` (the repository's default), migration 145 copying each `repo_preferences.owners` into it, and the preferences field deprecated for a release. *Costs:* changes the preferences API, its History descriptions and the import (two sources merged into one document); a repository with no areas gets an areas document only to hold owners. *Risk:* a client writing owners through preferences during the deprecation release writes a field nobody reads.
3. **Per-area owners leave the areas document;** #179's `people` document says which areas each person answers for. *Costs:* reverses #179's own text ("the areas document refers to people by login"); the mapper can no longer propose owners; this issue then ships areas without owners until #179. *Risk:* proposals lose their area owners for that time.

### OD3. Who may write areas and decide a map

1. **(Recommended) Operate tier, with the key's allowlist covering the repository.** The tier of `PUT /preferences`, which already sets repository owners with the same rights in asks, and of applying a setup proposal. *Costs:* any operate key or operator covering the repository can make a login an area owner, and that person can then answer the area's decisions in chat, `merge` included (`embry0/api/v1/asks.py:292-305`); today that took a pull request merged on the repository. *Risk:* the same as `repo_preferences.owners` today, for a narrower set of work.
2. **Admin, with the allowlist covering the repository** (the tier of a repository's settings layer, ADR 0028). *Costs:* an operator who sets repository owners today cannot set area owners; inconsistent unless `repo_preferences.owners` tightens too, which changes an existing API. *Risk:* low, but most operators can then only map, not apply.
3. **Operate for names, paths and logs; admin for any change to `owners`.** *Costs:* a field-level check in `PUT`, in apply (a map whose owners differ needs an admin) and in undo; the dashboard must explain why a save was refused. *Risk:* a split rule that the next feature does not copy.

### OD4. Where a waiting map shows up

1. **(Recommended) Where a waiting test setup shows (ADR 0019).** A card in the repository page's Areas section with the changes and Apply and Reject, a line in the dashboard's BriefBar, and a link from the map job's card in the review inbox. *Costs:* someone who works only from chat is not asked; they open the dashboard. *Risk:* a map waits unnoticed; the BriefBar line is the mitigation.
2. **The first, plus `apply` and `reject` on the map job's decision card**, so asks reach the repository's owners in chat and they answer there. *Costs:* two new decision actions through the decisions model, `AnswerBody.action` (`embry0/api/v1/asks.py:278-289`), `_may_decide` and every deployment's button handler; a chat card has a few lines to show a document diff. *Risk:* an owner applies areas, owners included, from a summary they did not read in full.
3. **A new owner-proposal kind (`areas`).** *Costs:* owner proposals are work to start; a finished map would read as a quest on offer whose "start" means apply, mixing the two stages the decisions spec keeps apart. *Risk:* confusing words on the quest board.

### OD5. When embry0 stops reading `.embry0/areas.yml`

1. **(Recommended) Import in this release; delete the import in the next minor release,** with an upgrade note, as qa.yaml did (`da57a86e`, then `ff3c3173`) and as the settings document does for a policy variable. *Costs:* #175's first acceptance criterion holds from the next minor release, so #175 stays open, or closes with a follow-up for pull request 4. *Risk:* a deployment that skips this release never imports; the note tells it to `PUT` or map.
2. **Delete it in pull request 4 of this issue, once this deployment's import has run.** *Costs:* another deployment that upgrades across both pull requests at once never imports, with only the release notes to warn it. *Risk:* silent loss of areas elsewhere.
3. **No automatic import:** an "Import from the repository" button and route per repository until the removal release. *Costs:* on upgrade every repository loses its areas until someone presses it; one more route to delete later. *Risk:* pull requests and proposals lose their areas in between.

## Out of scope

- Areas for GitLab merge requests (the title and label stay GitHub-only, a non-goal of spec 2026-10-25); the stored document itself is forge-neutral.
- People, their identities across forges, and ask policy (#179).
- Per-area settings: budgets, pipelines, models, QA (spec 2026-10-25's non-goals).
- Renaming the key `areas` in the knowledge map format.
