# Settings layers: organization and repository overrides of the settings document

Status: decided (2026-10-29; section 15, each on its recommended option). Pull request 1 is the `POST /orgs` fix, pull request 2 the layers (sections 2 to 7 and 10, as built in section 16); 3 and 4 follow. Refs #159, #161; builds on #160 (`docs/superpowers/specs/2026-10-28-settings-documents-design.md`, ADR 0026). Line numbers are at `343b4030`.

**Why.** One deployment can serve several organizations, and their working hours, caps, label vocabularies and conventions differ. The settings document of #160 is one per deployment, so every key in it is global. The registry already lets a section name the keys an organization or a repository may override (`Section.org_keys`, `Section.repo_keys`, `embry0/settings/registry.py:114-131`), and the effective view already lists them (`embry0/settings/effective.py:63-67`), but nothing resolves them: "nothing resolves them until the organization and repository layers exist" (`registry.py:118-123`). Meanwhile the two per-owner and per-repository layers that do exist validate differently (section 1).

**What changes.** Each key resolves as code default, then the deployment document, then the organization's layer, then the repository's layer, where the section declares the key overridable at that level. A layer is a document of the same shape as the deployment's, holding only what that organization or repository sets, stored, validated, versioned and undone exactly as the deployment document is. A layer may set only its section's declared keys; anything else is a 422 naming the key. A loop reads every layer from one cached snapshot, so a tick over a thousand repositories makes one freshness query, not a thousand. `GET /config/settings/effective?owner=&repo=` shows each value with the layer it came from and the value it overrides, and the Policy tab edits any layer. The asks section, whose `org_keys` were chosen with this issue in mind (`embry0/settings/sections/asks.py:165-184`), is the first section read per organization.

## 1. What exists today

### Per-organization and per-repository policy

| Mechanism | What it holds | Precedence | Validation | History | Write tier |
|---|---|---|---|---|---|
| `organizations.settings` JSONB (migration 97, `runner.py:2665-2683`) | Seven keys: `auto_register`, `rediscover_hours`, `context_audit_days`, `context_audit_budget_usd`, `context_audit_max_jobs_per_tick`, `context_audit_open_prs`, `slop_meter_bots` (`embry0/services/organizations.py:46-54`) | the owner's value, else an env default or off (`organizations.py:122-172`) | `PATCH /orgs/{owner}` checks with `validate_settings`, which stops at the first problem (`organizations.py:78-119`; `embry0/api/v1/organizations.py:251-256`). `POST /orgs` stores `body.settings` unchecked (`api/v1/organizations.py:193-201`), so every reader re-checks (`organizations.py:122-172`) | an audit row | admin |
| Watcher stack overrides, `watcher_stacks.settings` (one stack per repository, `runner.py:2299-2309`) | 23 keys, each overriding a global attribute (mostly `WATCHER_*`), plus the stack-only `signature_masks` (`embry0/services/watcher_stacks.py:34-68`) | the stack's value, else the env attribute (`StackConfig`, `watcher_stacks.py:259-289`) | typed and strict since #172 (`check_settings`, `watcher_stacks.py:182-199`); unknown keys dropped and echoed as `ignored_settings` (`embry0/api/v1/watcher_stacks.py:89-113`) | an audit row | admin |
| `repo_preferences` columns (`embry0/storage/repositories/repo_preferences.py:19-39`) | facts and choices about one repository: sandbox profile, base branch, commit author, `dev_checks`, `verification`, `pr_review`, `owners`, `execution_mode`, `auth_mode`, notes | the column, else a code default (`pr_review`: `embry0/services/pr_review/settings.py:15-43`) or env (`execution_mode`, `auth_mode`: `docs/configuration.md`, "Five-level precedence") | per route, ad hoc | `repo_config_revisions`, surface `preferences`, with undo (ADR 0019) | any non-read key |
| `repo_qa_configs` (ADR 0003) | the repository's test setup, qa.yaml v2 | the stored document only | pydantic, `extra="forbid"` | `repo_config_revisions`, surface `qa_config` | any non-read key |
| Standards (ADR 0014) | per owner and kind: a shipped schema and the organization's values, and each repository's mode | the active version | every problem listed | superseded versions | admin |

#161's second acceptance criterion, "a watcher stack override with a wrong type is refused at PUT, not at the next tick", holds on `main` already: #172 made the PUT a 422 naming the key, pinned by `tests/unit/api/v1/test_watcher_stacks_api.py:100-111`. The first, "`POST /orgs` with an invalid setting gets the same 422 as `PATCH`", does not (section 12, pull request 1).

### How work finds its organization

Everything that reads policy per repository already knows the repository slug; none knows an organization any other way.

| Reader | Where the repository comes from |
|---|---|
| A job | `jobs.repo`, NULL for a non-git job since migration 34 (`runner.py:893-905`) |
| The ask loop and `GET /asks/due` | each decision's `repo` (`embry0/services/decisions.py:63-104`); an ask names only its decision (`asks` table, `runner.py:3530-3545`) |
| Leads | each target repository (`embry0/api/app.py:2300-2311`) |
| Context audit | the registered repositories under each organization (`embry0/services/context_audit.py:597-604`) |
| Slop-O-Meter, scout | each repository in scope (`embry0/services/slop_meter/loop.py:166`, `embry0/services/scout.py:374`) |
| The watcher | the stack's repository |

The owner is the slug's first segment, lowercased: `owner_of` (`embry0/services/standards.py:142-144`), and the same expression inline in `app.py:2308`, `embry0/api/v1/webhooks.py:208`, `slop_meter/loop.py:166` and `context_audit.py:602`. An organization is "a forge owner, with no slash" (`embry0/storage/repositories/organizations.py:65-67`), so a GitLab project `group/sub/project` belongs to the organization `group`. The credential resolver's owner scope is a different, lossy form (`-` and `.` as `_`, `embry0/services/credentials/resolver.py:107-115`) and is not used here.

### Who may write what

There are global tiers only: `read`, `operate`, `admin`, and the off-ladder `merge` and `propose` keys (`embry0/api/deps.py:17-37`); a session's tier comes from global roles (`embry0/api/session.py:48-55`). An API key can carry `allowed_repos`, exact repository slugs (`embry0/api/repo_allowlist.py:45-56`). Nothing binds a person or a key to an organization: there is no organization owner, member or admin to delegate a layer to.

## 2. Layers and precedence

```
code default  <  deployment document  <  organization layer  <  repository layer
```

- **Per key, the most specific layer that sets it wins.** A layer that does not set a key inherits it. An organization layer applies to every repository under that owner; a repository layer applies to that repository only.
- **Only declared keys.** An organization layer may set a key only when the section lists it in `org_keys`; a repository layer only when it is in `repo_keys`. The two sets are independent: a key in `repo_keys` but not `org_keys` goes straight from the deployment to the repository. A key in neither is the deployment's alone (`asks.enabled`, `asks.tick_seconds`).
- **Replace at the field (L2).** A layer replaces the inherited value of a section's top-level field, a list or a nested object included. Nothing merges below a field. A section that wants part of an object overridable gives that part its own field.
- **Which organization.** `owner_of(repo)`: the first segment of the slug, lowercase. A repository whose owner has no layer reads the deployment's values for every organization key; an owner with no `organizations` row has no layer, since one cannot be written (section 3).
- **Which repository.** The slug lowercased, with `REPO_RENAMES` applied (`normalize_repo_slug`, `embry0/storage/repo_renames.py:111-117`), both when a layer is stored and when a reader asks, so a layer written under a repository's old slug still applies after the rename. If both slugs have a layer, the current one's wins and the other is logged once per generation as `settings_layer_shadowed`.
- **Work with no repository** (a non-git job, a decision with an empty `repo`) reads the deployment's values. So does a reader that names neither owner nor repository.

**Values that do not combine.** Every value is checked by its section where it is written (section 4). A section can also check fields against each other (a `min` not above a `max`); a value can pass in its own layer and be refused together with what it inherits, now or after the layer below it changes. At read time the resolver validates the combined values; if the section refuses them, the keys the most specific layer set and the errors name are dropped and the rest is validated again, then the same for the next layer down. The dropped value reads as the inherited one, is logged once per generation as `settings_layer_value_refused` (scope, section, key, reason), and the effective view marks it. A tick never fails because of it, as for a refused deployment value today (`embry0/settings/document.py:419-456`).

**Stored keys a layer may no longer set.** A release that takes a key out of `org_keys` leaves organization values for it in storage. They are ignored by readers, listed in `unknown_keys` beside the keys a newer release wrote, kept while a write sends them back exactly as stored, and refused when changed, as #160 keeps a key a known section does not have (`document.py:275-381`). A rollback past the release that added a key to `org_keys` is handled the same way.

## 3. Storage (L1)

A layer is a row of `settings_documents`, the table that holds the deployment's document (migration 129, `runner.py:3602-3630`), under its own scope:

| Scope | Example |
|---|---|
| `deployment` | the existing row, unchanged |
| `organization:<owner>` | `organization:acme` |
| `repository:<owner>/<name>` | `repository:acme/widgets`, `repository:group/sub/project` |

- **Same columns, same rules.** `yaml_text` as written, comments kept; `data`, the sparse validated values readers read; `schema_version`; `generation`, bumped in every write's transaction; `updated_by`, `updated_at`. `bootstrapped_keys` is unused by layers at first and becomes the per-key ledger when a feature copies an `organizations.settings` or watcher stack value into a layer (section 9).
- **Revisions** go to `config_revisions` under the same scope string, which was built for it (decision D1 of #160; the column is free text, `runner.py:3587`). The newest 100 per scope are kept, and undo reverses the newest change of that scope only.
- **The store already takes a scope** (`SettingsStore(db, catalog, scope=DEPLOYMENT)`, `embry0/settings/store.py:161`), and every repository method takes one (`embry0/storage/repositories/settings_documents.py:35-82`). Layers reuse the write path whole: `SELECT ... FOR UPDATE`, `If-Match` against the row's generation, validation, the update, the revision.
- **A missing row is an empty layer**, generation 0. Its first write inserts the row at generation 0 and writes it to 1, so `If-Match: "0"` means "create it, unless someone else did first". Unlike the deployment row, which the migration inserts, a layer row is created only by a write that changes the text, so a dry run or an unchanged write leaves no empty rows behind.
- **No DELETE.** `PUT` of an empty text clears a layer, recorded and undoable, as for the deployment (`PUT` of an empty document returns it to the defaults).
- **A layer outlives its owner.** Removing an organization or unregistering a repository leaves the layer and its history, as `repo_preferences` and `repo_config_revisions` outlive a repository (no foreign key, `runner.py:2977-2978`). A layer applies by slug, so it is read again if the repository comes back.
- **A write needs something to apply to:** an organization layer needs an `organizations` row for the owner, a repository layer a `repos` row (matched case-insensitively); otherwise 404, so a typo cannot create a layer nothing reads.
- **Size:** each layer is capped at 256 KiB like the deployment document. A snapshot holds `data` only, never the text.

## 4. Validation and the 422

A layer's write is checked by `check()` (`document.py:275-381`), which gains the layer it checks for and the values it inherits:

1. Everything #160 checks: YAML shape, aliases, keys written twice, unknown sections with a near-miss hint, per-field types and bounds, strictness, unknown parts kept as stored.
2. **Declared keys.** Each key the layer writes must be in the section's `org_keys` (an organization layer) or `repo_keys` (a repository layer). Every refusal is listed in one 422, each at its key. `PUT /config/settings?owner=acme` with `asks: {enabled: true}`:

   ```json
   {"detail": [{"loc": ["asks", "enabled"], "type": "not_overridable",
                "msg": "'enabled' is the deployment's; an organization can set expire_days, hours, max_open_per_person, remind_hours, timezone, weekdays in 'asks'."}]}
   ```

   `PUT /config/settings?repo=acme/widgets` with `asks: {hours: "08-17"}`, a section with no key at that level:

   ```json
   {"detail": [{"loc": ["asks", "hours"], "type": "not_overridable",
                "msg": "A repository cannot set anything in 'asks'."}]}
   ```
3. **The combination.** The layer's values are validated over the values it inherits now (the deployment's for an organization; the deployment's and the organization's for a repository). A refusal that only the combination causes is a 422 at the layer's key, saying the value conflicts with the inherited one. A model-level check with no key is reported at the section (`loc: ["leads"]`).

A deployment write is never refused because of a layer: one repository's override must not stop an operator changing the deployment. Instead the dry run and the `PUT` answer list `layers_refused`, each `{scope, key, reason}`, for every stored layer key the new deployment values would refuse; the resolver then reads those as inherited (section 2).

`POST /orgs` and `PATCH /orgs/{owner}` keep their own check of the seven legacy keys, with `POST` now calling it too (pull request 1). They do not write layers.

## 5. Reading

```python
class Snapshot:                                     # immutable, one per change of any layer
    def get(self, section: Section[M], *, owner: str | None = None, repo: str | None = None) -> M
    def scoped(self, section: Section[M]) -> Scoped[M]

class Scoped(Generic[M]):                           # one section of one snapshot
    def deployment(self) -> M
    def owner(self, owner: str | None) -> M         # deployment + that organization
    def repo(self, repo: str | None) -> M           # deployment + its organization + that repository

def scoped_reader(store, section) -> Callable[[], Awaitable[Scoped[M]]]   # what a many-repository loop is built with
```

- **`snapshot.get(SECTION)` still means the deployment's values,** so every reader on `main` keeps working unchanged. A caller passes `repo=`, from which the owner is derived, or `owner=`, never both. A section with no `org_keys` and no `repo_keys` answers the deployment's model whatever is passed, so a loop can always pass the repository.
- **One snapshot holds every layer.** It is loaded in one statement (`SELECT scope, data, generation, updated_at FROM settings_documents`), so its rows belong together. A tick takes the snapshot once and resolves each repository in memory. Resolution is memoized per `(section, owner, repo)` within the snapshot, and a repository with no layer of its own shares its organization's entry, so a tick over many repositories costs one dictionary merge and one validation per distinct layer combination, and nothing for repositories without layers.
- **The freshness check stays one query returning one token:** `SELECT count(*), COALESCE(sum(generation), 0), max(updated_at) FROM settings_documents`. Every write adds one to its row's generation and no row is ever deleted, so the sum moves on every committed write in any scope; a database restored to an older copy moves it, or the newest write time, in either direction, and the whole snapshot is read again. The token compared next time is computed from the rows the reload read, not from the check that triggered it. The table holds one row per organization and repository that ever had a layer; past roughly ten thousand rows, a single-row head counter bumped by every write would replace the aggregate, with no change to readers.
- **Lazy checking.** A reload reads raw `data`; each layer's values are validated the first time a resolution needs them, then kept in the memo. A write to one layer reloads the table but re-validates only what is read again.
- **Unchanged from #160:** a loop reads one snapshot per tick, so one tick never mixes two versions; a failed check or reload keeps the last snapshot and logs `settings_refresh_failed`; a second orchestrator sees a write within one tick, since the token lives in the rows; the MCP container has no database and reads through the API.

How the loops change is each feature's own pull request. The rule they follow: a loop that works per repository is built with `scoped_reader(store, SECTION)` and calls `scoped.repo(repo)` for each repository it handles; a loop that works per organization calls `scoped.owner(owner)`; a test pins that the production wiring passes the repository or owner (the CLAUDE.md guardrail on optional arguments production cannot work without: a reader that forgets reads the deployment's values and looks exactly like an organization that set nothing).

## 6. Effective values with provenance

`GET /config/settings/effective` takes `?owner=` and `?repo=`. With neither it answers as today. With `owner` it resolves the deployment and that organization; with `repo` all three layers, the owner derived from it (an `owner` given beside it that is not the repository's is a 422).

Each key keeps its current fields (`effective.py:68-82`) and gains:

| Field | Meaning |
|---|---|
| `source` | as today `default`, `document` or `bootstrap` for the deployment's value, plus `organization` or `repository` when a layer supplies it |
| `inherited` | the value the key would have without the layer the view is scoped to: what an override overrides |
| `layers` | `[{layer: "deployment"\|"organization"\|"repository", owner?, repo?, set, value, set_by, set_at, refused}]`, one entry per layer in the view, most general first; `refused` is why a stored value is read as inherited (section 2) |

`overridable` stays the section's declaration. `set_by` and `set_at` come from the newest kept revision of that layer's scope that changed the key, as they do for the deployment now; actors are shown as `GET /audit` shows them. The view reads at most three revision lists, one per layer in it.

**On the Policy tab** (section 8), each key shows its value and, when the selected layer sets it, "overrides *09-18* from the deployment" (or "from acme"); when the layer does not set it, "inherited from the deployment" or "from acme"; a key the selected layer may not set is shown dimmed with "the deployment's" or "set per organization". A refused layer value shows the reason, as a refused deployment value does today (`frontend/src/components/settings/EffectiveSettingsView.tsx:49-53`).

## 7. API, CLI and MCP (L3)

Every route of #160 (`embry0/api/v1/settings.py:77-292`) takes the layer as a query parameter. No parameter is the deployment, as today; `owner` and `repo` are each optional and at most one names the layer written:

| Route | Change |
|---|---|
| `GET /config/settings?owner=acme` or `?repo=acme/widgets` | the layer's text, values, generation (`ETag`), `updated_by`, `updated_at`, plus `scope` and `settable` (per section, the keys this layer may set). 404 when no such organization or repository is registered and no layer is stored |
| `PUT /config/settings?owner=…` or `?repo=…` (`dry_run`, `reason`, `source`, `If-Match` as today) | writes the layer. Both parameters: 422 "name one layer". The answer adds `scope`; a deployment write's answer adds `layers_refused` |
| `GET /config/settings/revisions?owner=…` or `?repo=…` | that layer's revisions |
| `GET /config/settings/revisions/{id}`, `POST …/{id}/undo` | unchanged paths: an id names one revision of one scope, and the answer says which (`scope`). Undo is authorized for that scope |
| `GET /config/settings/effective?owner=&repo=` | section 6 |
| `GET /config/settings/layers` (new) | every stored layer: `{layer, owner\|repo, generation, updated_by, updated_at, keys}` with `keys` the `section.key` it sets; for the dashboard's picker and `export` |

**Auth.** Reads take any key, as for the deployment: no layer holds secrets, and `GET /orgs` already shows every organization's settings to any key. Writes and undo take the admin tier (#160's D4):

- the deployment and an organization layer need a key without a repository allowlist (`enforce_repo_allowlist(request, None, …)`, as `settings.py:126` does now): an organization layer governs every repository under the owner;
- a repository layer takes an admin key whose allowlist covers that repository (`enforce_repo_allowlist(request, repo, …)`); a session and an unrestricted key always can.

No organization owner can write its own layer, because embry0 has no such role (section 1). Delegating one later (an owner allowlist on keys, or organization membership for people) is additive and changes nothing stored.

**Audit.** `settings.updated` and `settings.change_undone` gain `scope` in their details; no new action.

**Ingress.** Unchanged: these are "every other `/api/` route" (`docs/ops/deploy.md`), reachable through the dashboard tunnel and the LAN, 404 through the webhooks tunnel.

**CLI and MCP (#162, built in parallel).**

| #162 verb or tool | Layer |
|---|---|
| `embry0 settings check <file> [--org OWNER \| --repo PATH]` | offline: everything `check()` does without the database, including the declared-key rule (`org_keys` and `repo_keys` are on `main` now). The combination with what the layer inherits needs the stored values, so `diff` reports it |
| `diff`, `apply`, `export` with `--org` or `--repo` | the same routes with `?owner=` or `?repo=` |
| `import-env` | the deployment only; with `--org` or `--repo` it exits 2 saying the environment is the deployment's |
| MCP `settings_get(owner=None, repo=None)`, `settings_set(text, owner=None, repo=None, confirm=False)` | the same parameters |

`--org` names the organization by its owner, as `/orgs/{owner}` does. `managed_by: file` (#162) is a key of a document, so it applies per layer. Whichever of #162 (pull request #230) and pull request 2 merges second adds the flags and parameters.

## 8. Dashboard

The Policy tab (`frontend/src/pages/settings/PolicyTab.tsx:10-44`) gains a layer picker above its three cards: **Deployment**, **Organization** (the registered owners) or **Repository** (the registered repositories), each marked when it has a layer (`GET /config/settings/layers`). The picked layer is in the URL (`/settings?tab=policy&owner=acme`, `&repo=acme/widgets`).

- **Settings document** edits the picked layer's text with the same Preview, Save (with `If-Match`), 422 list and conflict handling (`frontend/src/components/settings/SettingsDocumentEditor.tsx`); above the text it lists the keys this layer may set, per section.
- **Values in effect** shows the view of section 6 for the picked layer.
- **History** lists that layer's revisions with Undo on the newest.

The API functions in `frontend/src/api/settings.ts` take an optional `{owner} | {repo}` and pass it as `params`; the query keys in `frontend/src/hooks/useSettingsDocument.ts` include it. The organization page's Settings tab (`frontend/src/components/orgs/OrgSettingsTab.tsx`) and the repository page link to the Policy tab with their layer picked. Anyone signed in can read; Save and Undo are an admin's, as now.

## 9. Existing per-organization and per-repository mechanisms (L1, L4)

| Mechanism | Fate | When |
|---|---|---|
| `organizations.settings`: `context_audit_days`, `context_audit_budget_usd`, `context_audit_max_jobs_per_tick`, `context_audit_open_prs` | a `context_audit` section with these as `org_keys` (`days`, `job_budget_usd`, `max_jobs_per_tick`, `open_prs`); the deployment's defaults imported once from `CONTEXT_AUDIT_JOB_BUDGET_USD` and `CONTEXT_AUDIT_MAX_JOBS_PER_TICK` | #170, #171 |
| `organizations.settings.slop_meter_bots` | `slop_meter.bots`, in `org_keys` | #169 |
| `organizations.settings.auto_register`, `rediscover_hours` | a `discovery` section, both in `org_keys`; a deployment-wide default becomes possible (today each owner opts in alone) | a new sub-issue of #159, to be filed when this spec is accepted |
| Watcher stack overrides | a `watcher` section whose `repo_keys` are today's `OVERRIDABLE` keys (and `signature_masks`, repository-only); the stack keeps its own fields (`logql`, `tracker`, `tracker_target`, `enabled`) | when the `WATCHER_*` settings move into the document, with the older settings of #159 |
| `repo_preferences` | stays: it holds facts and choices about one repository with no wider default. `pr_review`, `execution_mode` and `auth_mode` have one and may move to a section later, each with an alias | with the older settings of #159 |
| New per-repository policy: #182 (done-when, salvage), #165 (background enrollment) | sections with `org_keys` and `repo_keys`, not new `repo_preferences` columns (L4) | #182, #165 |
| `repo_qa_configs` | stays: a repository's test setup with its own schema and its own merge (defaults, app, `app_local`), not an override of deployment policy | — |
| Standards | stay: an organization's versioned content, not an override of deployment policy | — |
| Credential bindings, `repo_environment` | stay: secrets never go in a settings document | — |

**How a key moves without breaking stored data.** The feature's pull request declares the section and, per field, the legacy key it replaces (`org_import={"days": "context_audit_days", …}`, beside `env`). At every boot, after migrations and the env import, for each organization whose `organizations.settings` holds the legacy key and whose layer does not hold the field and has no ledger entry for it: the value is checked as the section's, written into the layer under a comment naming its origin, recorded as a revision with source `bootstrap` and actor `embry0:bootstrap`, and `section.key` added to that layer row's `bootstrapped_keys`, in one transaction per organization holding the row lock. A value the section refuses is not copied and is logged (`settings_org_value_refused`), as the env import does. The legacy key is left in `organizations.settings` and no longer read; a rollback to the release before the move reads it as it was at the move. For one release `PATCH /orgs/{owner}` with the legacy key writes the field into the layer (a revision with source `person`) and answers a `deprecated` note; the release after refuses it with a hint naming the field. The same rule moves watcher stack overrides into repository layers. #161 specifies this and builds none of it; the first feature that moves a key builds it once, with a test-only section, as #160 built the env import.

## 10. Migration

One migration, 134 (133 is held by another pull request; the number is fixed at merge). It widens the scope check of `settings_documents` and nothing else:

```sql
ALTER TABLE settings_documents DROP CONSTRAINT IF EXISTS settings_documents_scope_check;
ALTER TABLE settings_documents ADD CONSTRAINT settings_documents_scope_check CHECK (
    scope = 'deployment'
    OR scope ~ '^organization:[a-z0-9_][a-z0-9._-]*$'
    OR scope ~ '^repository:[a-z0-9_][a-z0-9._-]*(/[a-z0-9._-]+)+$'
);
COMMENT ON TABLE settings_documents IS
    'spec 2026-10-28 and 2026-10-29: operator policy as YAML documents, one for the deployment and one per organization and repository layer; data = the validated values of yaml_text.';
```

`settings_documents_scope_check` is the name Postgres gave the column check of migration 129 (`runner.py:3616`); the migration's test asserts it exists before and that `DROP ... IF EXISTS` did not leave the old one beside the new.

**The deployment document maps onto the new shape unchanged.** Its row keeps scope `deployment` and every column: text, values, generation (so its `ETag` and any `If-Match` a client holds stay valid), the env import ledger; its revisions keep scope `deployment`. No layer rows are created; an organization or repository has no layer until someone writes one. `config_revisions` needs no change: its `scope` is free text and its index is `(scope, surface, id DESC)` (`runner.py:3585-3597`). What changes on the read side is the freshness token, from one row's `(generation, updated_at)` to the aggregate of section 5, which on a database with only the deployment row is the same information.

## 11. The first layered section: asks

| Candidate | For | Against |
|---|---|---|
| **Asks** | `org_keys` already declared for exactly this (`sections/asks.py:169-172`); off by default, so the smallest blast radius; no stored per-organization data to migrate; #179 asks for it | proves the organization layer only: asks has no `repo_keys` |
| Context audit (#170, #171) | already per organization; proves moving `organizations.settings` onto a layer | needs its section designed first (four org keys, two env imports, the budget bound of #171), and the legacy import; it would turn #161 into #170 |
| Slop-O-Meter bots (#169) | one key | the `slop_meter` section is not designed; one list key proves little |

So asks is the acceptance proof for the organization layer, and a section registered only in tests proves the repository layer in pull request 2, as #160's pull request A proved the document. The first real repository-layer section comes with #182 or #165.

**What changes in asks.** Working hours, weekdays, timezone, reminder and expiry belong to the organization a decision is about; `enabled` and `tick_seconds` stay the deployment's.

- **The loop** (`embry0/services/ask_loop.py:64-86`) is built with `scoped_reader(store, ASKS)`. It reads the decisions first (today it expires first), then: an ask expires by the settings of its decision's repository (`ask_loop.py:67`), an ask whose decision is no longer open by the deployment's (it is withdrawn in the same tick anyway); a new ask is due at the next working time of its decision's organization, not one time for all (`ask_loop.py:76`).
- **The feed** (`embry0/api/v1/asks.py:93-128`) reads one snapshot per request. Whether it is working time (`asks.py:111`), whether a reminder is due, and how many asks a person may hold are each decided by the settings of the ask's decision's organization.
- **`max_open_per_person` counts per person within an organization:** a person asked about decisions in two organizations may hold each organization's number. `release()` (`embry0/services/asks.py:146-162`) counts held asks per `(recipient, owner)` and takes the settings per ask; it stays a pure function, taking a callable from an ask to its settings.
- The asks table needs no column: the owner comes from the decision, which both readers already hold.
- `docs/configuration.md` already lists those six keys as overridable by an organization; the docs gain a "Layers" part and the decisions spec a line.

## 12. Delivery

1. **`fix(api): check an organization's settings on POST /orgs too`** (S). `register_organization` calls `validate_settings` before it probes the forge or stores anything, 422 with the same text as `PATCH`, and stores the values as the check writes them. Independent of the rest; closes #161's first acceptance criterion. Registering an existing owner again used to replace its stored settings whole (`embry0/storage/repositories/organizations.py:78`), so a call naming one setting dropped the others; it now merges them, as `PATCH` does, and `docs/api.md` says so.
2. **`feat(config): layer the settings document by organization and repository`** (M–L). The migration; a `Scope` value type; `check()` with the layer and inherited values; the multi-scope store (write, preview, revisions, undo per scope; one snapshot of all layers; the aggregate freshness token); `Snapshot.get(…, owner=, repo=)`, `Scoped`, `scoped_reader`; the effective view; every route of section 7 with its auth; `layers_refused` on deployment writes; audit details; ADR 0028 ("operator policy is layered: deployment, organization, repository; a section declares what each layer may set"); `docs/api.md` and the "Layers" part of `docs/configuration.md`. Proven with a test-only section that has both `org_keys` and `repo_keys`. No feature changes behaviour.
3. **`feat(frontend): edit organization and repository layers on the Policy tab`** (M). Section 8. Lands no later than 4, so an operator can set an organization's ask hours without raw API calls.
4. **`feat(asks): read the ask policy per organization`** (S–M). Section 11. Closes #161.

Pull requests 3 and 4 branch from 2. Nothing under `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/` or `embry0/execution/` changes, so no image is rebuilt; each deploys with the usual deploy.

## 13. Test plan

Each new test is shown failing on a clean tree of `origin/main` (`git archive` into a temporary directory, the new test file copied in) with its own assertion message. Database tests run with `TEST_DATABASE_URL` set, in a private database.

| Acceptance criterion (#161) | Tests | PR |
|---|---|---|
| `POST /orgs` with an invalid setting gets the same 422 as `PATCH` | for an unknown key, a wrong type and an out-of-range value: `POST` answers 422 with the `detail` `PATCH` gives for the same settings, and the owner is not registered; valid settings are stored | 1 |
| A watcher stack override with a wrong type is refused at PUT | met on `main` by #172 (`test_watcher_stacks_api.py:100-111`); nothing new | — |
| An organization override changes the effective value for its own repositories only | **store:** deployment `asks.hours: "09-18"`, layer of `acme` `hours: "00-24"`: `get(ASKS, repo="acme/widgets")` is `00-24`, `get(ASKS, repo="globex/gadgets")`, `get(ASKS)` and `get(ASKS, repo=None)` are `09-18`; `group/sub/project` reads the layer of `group`. **API:** `effective?repo=acme/widgets` gives `source: organization`, `inherited: "09-18"`; `effective?repo=globex/gadgets` gives `document`. **Feature:** at 20:00 UTC on a weekday, one tick queues the `acme/widgets` decision's ask due now and the `globex/gadgets` decision's ask due at 09:00 next day; the feed releases the first only. With both organizations in working hours, a person holding `acme`'s `max_open_per_person` asks there is still sent one from `globex`. **Wiring:** through the app's lifespan, an organization layer written by `PUT` changes when the production ask loop makes an ask due | 2 (store, API), 4 (feature, wiring) |
| New tests fail on the pre-change tree | fail-first output for each new test file, in the PR description | all |

Also, in pull request 2:

- **The 422.** An organization layer setting a key outside `org_keys`, and a repository layer setting a key outside `repo_keys`, each answer `not_overridable` at the key's `loc`; one document with three such keys answers three entries; a value refused only over the inherited values answers at the layer's key; nothing is stored.
- **Layers.** The first write with `If-Match: "0"` creates the layer and a second such write is 409; a write for an unregistered owner or repository is 404; both `owner` and `repo` is 422; an empty `PUT` clears a layer and undo restores it byte for byte; revisions and undo of one scope never show or reverse another's; a dry run and an unchanged write create no row.
- **Reading.** A layer written through one store is read by a second store's next snapshot (two processes); a tick that resolves a thousand repositories makes one freshness query, counted on a fake pool; a repository with no layer resolves to its organization's memo entry; a layer value refused in combination after a deployment change reads as inherited, is logged once and is marked in the effective view; a stored key a section no longer lets the layer set is ignored, kept on an unchanged write and refused when changed; a layer under a renamed slug applies to the new one; a deployment write that makes a layer key refused answers it in `layers_refused` and is stored.
- **Auth.** A read key reads any layer and writes none; an operate key and an admin key with an allowlist are refused on an organization layer and on the deployment; an admin key whose allowlist holds `acme/widgets` writes that repository's layer and is refused on `acme/gadgets`; undo of a revision is authorized for that revision's scope.
- **Migration.** The new check accepts `organization:acme`, `repository:acme/widgets` and `repository:group/sub/project`, and refuses `organization:Acme`, `organization:acme/widgets`, `repository:acme` and `team:acme`; the deployment row is identical before and after; the migration's test takes over "is the last entry" from 132's.
- **Docs.** The generated reference is unchanged in shape; `docs/api.md` documents the parameters.

In pull request 3: the picker writes the layer into the URL and reads it back; each API call carries it; an override shows the value it overrides; a key the layer may not set is dimmed; a non-admin sees no Save.

The full unit suite runs against Postgres before each pull request is opened, and the failure sets are compared with `main`, not the counts.

## 14. Decided here

These are cheap to change later, or follow from #160 and the issue:

- The order code default, deployment, organization, repository; per key, the most specific layer that sets it wins. No lock that stops a lower layer overriding (additive later).
- The organization of a repository is the slug's first segment, lowercase (ADR 0010; `standards.owner_of`); a GitLab subgroup has no layer of its own.
- Work with no repository reads the deployment's values.
- A missing layer is empty, generation 0; the first write creates it; no DELETE.
- Writing a layer needs a registered organization or repository; a layer outlives both.
- Reads take any key. Writes take the admin tier; the deployment and organization layers need an unrestricted key, a repository layer an allowlist that covers it. No per-organization delegation, since embry0 has no organization role (loosening later is additive; tightening later would break someone).
- Combined values are checked at write (422) and at read (the layer's value reads as inherited); a deployment write is never refused because of a layer.
- A stored key a layer may no longer set is kept as stored, ignored, and listed in `unknown_keys`.
- One snapshot of every layer, reloaded whole on any change, resolved lazily and memoized; the freshness token is an aggregate over the table.
- Repository slugs are stored lowercase, `REPO_RENAMES` applied.
- `source` in the effective view keeps `default`, `document`, `bootstrap` and adds `organization`, `repository`.
- Asks: `max_open_per_person` counts per person within an organization; `enabled` and `tick_seconds` stay the deployment's.
- The legacy-key import (section 9) is specified here and built by the first feature that moves a key.
- CLI flags `--org OWNER` and `--repo PATH` (`group/name` or `group/subgroup/name`); `import-env` and `export --default` are the deployment's only.
- The repository page links to its layer on the Policy tab; its History panel does not merge the two revision tables.

## 15. Decisions

Taken by the maintainer on 2026-10-29, each on the option this spec recommended (A). The options are kept below as they were weighed.

**L1. Where layers live, and whether `organizations.settings` becomes the organization layer.** Decided: A.

- **A (recommended).** Each layer is a document of its own, a row of `settings_documents` with revisions in `config_revisions` (section 3); `organizations.settings` keeps its seven keys until each one's feature moves it into a section, copied once and accepted on `PATCH /orgs` for a release (section 9). Cost: this departs from #161's text ("`organizations.settings` becomes the organization layer, with its keys unchanged"): the keys get section names (`context_audit.days`), and until #169, #170, #171 and one new issue land an organization's policy sits in two places.
- **B (#161 as written).** `organizations.settings` holds the organization layer next to its seven keys, and repository overrides go in a new JSONB column beside `repo_preferences`, recorded in `repo_config_revisions`. Cost: no text, so comments are lost and #162 cannot round-trip a layer file; three write paths, each with its own validation, merge semantics (`PATCH` merges, a document replaces), `If-Match` and undo; `organizations` needs a generation column and `repo_config_revisions` wider surface and source checks; the freshness check and the effective view read three tables.
- **C.** Layers nested in the one deployment document (`organizations: {acme: {asks: …}}`, `repositories: {…}`), extending #160's D5. Cost: undoing one organization's change waits on every newer change anywhere; the document grows with the repositories toward its 256 KiB cap; any write conflicts with every other on `If-Match`; a layer can never be written by anyone but whoever may write the whole document.

Expensive to reverse: moving layers and their revisions between tables later, and changing the file shape #162 exports.

**L2. How a layer overrides a list or a nested object.** Decided: A.

- **A (recommended).** It replaces the value at the section's top-level field; `org_keys` and `repo_keys` stay field names, as the registry checks today (`registry.py:218-221`). Cost: an organization overriding a nested object restates all of it, so section authors give each overridable part its own field (#168's `readiness` becomes several fields).
- **B.** Deep merge: mappings merge key by key, lists replace. Cost: `org_keys` needs dotted paths; provenance, the 422 and the effective view become per leaf; nothing can say "drop this inherited entry".
- **C.** Replace, with a per-field opt-in to merge. Cost: an operator has to know which rule a field follows.

Expensive to reverse: switching later silently changes the effective value of every stored nested override.

**L3. How the API names a layer.** Decided: A, with `--org` and `--repo` on the CLI. This mattered now: #162's CLI and MCP tools are being built against it.

- **A (recommended).** Query parameters on the existing routes: `?owner=acme` or `?repo=acme/widgets` on `/config/settings`, its revisions and effective view; revision ids stay global (section 7). Cost: a layer has no path of its own; every settings route grows a scope parameter.
- **B.** Nested under the resources: `/orgs/{owner}/settings` and `/repos/{repo}/settings`, each with `/revisions` and undo. Cost: three copies of every route; `{repo:path}` routes collide with a repository named `settings` or `revisions` under a group, as `/repos/{repo}/history` can today; #162's client builds three URL shapes.
- **C.** Explicit paths in the settings namespace: `/config/settings/organizations/{owner}`, `/config/settings/repositories/{repo}`. Cost: the same repository-name collision on the sub-routes; two more route sets.

Expensive to reverse: a public API, the MCP tool signatures and #162's client are built on it.

**L4. Where new per-repository policy goes** (#182's done-when and salvage, #165's background enrollment, both of which said "beside `repo_preferences`"). Decided: A; both issues are amended.

- **A (recommended).** In sections with `repo_keys` (and `org_keys` where an organization default makes sense), read through layers; `repo_preferences` keeps facts about a repository that have no wider default. Cost: #182 and #165 are amended; a repository's configuration sits in two places (its preferences and its layer), joined by a link.
- **B.** As #182 and #165 propose: new `repo_preferences` columns, with organization defaults read from the organization layer. Cost: two override mechanisms for per-repository policy; the effective view and #162's file workflow do not cover the columns; each column brings its own validation.
- **C.** A, and also move `pr_review`, `execution_mode` and `auth_mode` out of `repo_preferences` now. Cost: churns settings #159 leaves for later; `PUT /repos/{repo}/preferences` clients need an alias.

Expensive to reverse: once #182 or #165 stores values in one place, moving them needs a migration and an alias release.

## 16. As built (pull request 2)

Where the code settled a detail this spec left open, or differs from it:

- **`settable`, not `overridable`,** is the name of the per-section key list `GET /config/settings` answers: "overridable" already means, in the effective view, which layers may override a key. The deployment's document answers every field.
- **No `GET /config/settings/schema?layer=`.** #162's offline `check` calls `check()` with the layer directly, and the schema's `x-embry0-sections` already lists `org_keys` and `repo_keys` for an editor.
- **`inherited` and `layers` are answered only in a layer's effective view,** so the deployment's view, which the Policy tab reads today, is unchanged; its top-level `refused` stays the deployment's, and each layer entry has its own.
- **Creating a layer.** The store takes a transaction-level advisory lock on the scope before it looks for the row, so two first writes of one layer queue instead of both inserting; the write is an upsert that creates the row at generation 1. A layer that has no row reads as generation 0 and nothing creates one but a write that changes the text.
- **The owner pattern** is `[a-z0-9_][a-z0-9._-]*` (a GitLab group may start with `_`); a repository segment may start with `.` (`.github`); `.` and `..` are refused as segments.
- **The freshness token** is `(count, sum(generation), max(updated_at))`, computed by the check and, for the cached snapshot, from the rows the reload read.
- **`layers_refused`** is answered by `PUT` (when it stored a change), its dry run (when the text would change) and undo. Each entry's `scope` is the `{layer, owner, repo}` object every other `scope` field is. It names at most 100 values, and `layers_refused_total` counts them all. The layers under the document are resolved once, in the snapshot after the change; the snapshot before it, which the write only imagines and which logs nothing, is consulted only for the layers that come out refused.
- **What a layer inherits is read under lock.** A layer's write takes the advisory locks of the documents it inherits from, most general first (deployment, then the organization, then its own), and reads what they hold on its own connection after taking them. A concurrent write to the deployment's document or the organization's layer therefore commits first, and the layer is checked against it. Every writer takes the locks in that order, so none waits on another in a circle. A deployment write takes only its own lock and is never refused because of a layer.
- **With #230.** #230 (the `embry0 settings` CLI and the MCP settings tools) merged first, so this pull request adds `--org` and `--repo` to `check`, `diff`, `apply`, `export` and `revisions`, refuses them on `import-env` and `export --default` (exit 2), and adds `owner` and `repo` to the MCP `settings_get`, `settings_effective`, `settings_set` and `settings_revisions`; `settings_undo` takes the global revision id. `managed_by` is a key of each document, so each layer answers its own on `GET`; a layer's `managed_by` is never logged as a key the layer may not set, and a change to it has no `effective_*` values, as for the deployment's.
- **Renamed repositories are resolved when read, not when the snapshot is built.** The orchestrator installs `REPO_RENAMES` after it reads the first snapshot, so a snapshot keeps every layer under its stored scope, and which stored row a repository reads is worked out on first read with the renames installed then (`stored_rows` in `embry0/settings/scope.py`): the row under its current slug, else the first row stored under a slug the renames map to it. The store uses the same rule for a write: with no row under the new slug, `?repo=<new slug>` opens, writes, lists and undoes the row under the old one, so a save never starts a second layer that would shadow it. `GET /config/settings/layers` names such a row's `stored_as`.

## Out of scope

- Moving any feature's keys onto layers other than asks: `organizations.settings` (#169, #170, #171, and a new issue for `auto_register` and `rediscover_hours`), watcher stack overrides, `repo_preferences` columns.
- The legacy-key import machinery (section 9), built with the first key that moves.
- Delegating an organization's layer to anyone below admin.
- Per-person working hours (#179's `people` document); asks keep organization-level hours until then.
- A form generated from the schema; the Policy tab stays a text editor per layer.
