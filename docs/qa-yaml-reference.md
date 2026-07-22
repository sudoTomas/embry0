# `.embry0/qa.yaml` Reference (v2)

How a target repo opts into embry0's QA pipeline. Commit a `.embry0/qa.yaml`
(schema **version 2**) at the repo root; that file is the entire integration
contract — it tells the QA orchestrator which apps to boot, how to boot them,
how to know they're up, and what "passing" means.

- **Authoritative schema:** `embry0/workflows/qa/qa_yaml_v2.py` (Pydantic).
- **Merge logic:** `embry0/workflows/qa/qa_yaml_resolve.py`.
- **Worked examples:** `tests/fixtures/qa-yaml-corpus/v2/` and
  `tests/fixtures/toy-monorepo/.embry0/qa.yaml`.

> **External config store (EMB-48).** The file does not have to live in the
> target repo: an embry0-side `repo-configs/<owner>__<repo>/qa.yaml` (same v2
> schema, gitignored, mounted into the orchestrator via
> `EMBRY0_QA_CONFIG_DIR`) **replaces** the in-repo file entirely when present
> — no merge, exactly one source of truth per repo. Absent, the in-repo file
> is used, so existing integrations run unchanged. See
> [`repo-configs/README.md`](../repo-configs/README.md). Per-app
> `apps/<name>/.embry0/app.yaml` overrides still come from the repo tree in
> both cases.

> **v1 is dead.** The old single-app schema (`version: 1`, top-level `mode` /
> `startup:` / `frontend_url`) is read **only** by the migrator. Convert with
> `embry0 migrate-qa-config` (`--write` backs the old file up to
> `qa.v1.yaml.bak`).

---

## How the file is used at runtime

1. You create a QA job: `POST /api/v1/jobs` with `{repo, pipeline:"qa", branch}`
   (or QA runs automatically as the verify stage of an issue→PR job when triage
   sets `needs_qa=true`).
2. The orchestrator boots a bootstrap sandbox and clones the repo at `branch`.
   The config comes from the external store when one exists for the repo
   (`$EMBRY0_QA_CONFIG_DIR/<owner>__<repo>/qa.yaml` — replaces the in-repo
   file); otherwise it `cat`s `/workspace/.embry0/qa.yaml`. Either way the
   text is validated against the v2 schema; a schema error fails the run
   with `qa.yaml v2 parse failed: …`.
3. The **workspace provider** diffs the branch against its `affected_filter`
   base and computes the **affected set** — which declared apps changed, directly
   or via a changed shared package.
4. With `qa_required: auto`, only the affected apps are QA'd; the orchestrator
   validates the affected set against your `apps:` keys (any affected app **not**
   declared under `apps:` is silently skipped), then fans out up to
   `parallelism.max_concurrent_apps` at a time. Each app is booted with its
   `boot_command`, waited on via `ready_checks`, optionally seeded and e2e-tested,
   then the QA agent drives `frontend_url` against the acceptance criteria.

---

## Top-level schema

```yaml
version: 2                       # required, must be the integer 2

workspace_provider:              # required — how the affected set is computed
  type: npm-workspaces-turbo     # the only shipped provider today
  config: { ... }                # provider-specific (see below)

defaults: { ... }                # baseline applied to every app
cache: { ... }                   # build-cache layers (sane defaults; usually omit)
qa_required: auto                # auto | always | never   (default: auto)
parallelism:
  max_concurrent_apps: 4         # 1..64 (default: 4)

apps: { ... }                    # the apps embry0 may boot (the core section)
packages: { ... }                # shared packages + cascade behaviour
conditional_acceptance_criteria: [ ... ]   # relevance-gated criteria groups (EMB-39)
```

Every block uses Pydantic `extra="forbid"` — **an unknown/misspelled key is a
hard validation error**, not a silent ignore.

### `workspace_provider`

| Field | Type | Notes |
|---|---|---|
| `type` | str, required | `npm-workspaces-turbo` or `static-apps` (EMB-32). |
| `config` | dict | Provider-specific. `config.root` (any provider) runs it on a repo **subdirectory** — e.g. a turbo workspace under `frontend/`; changed-file paths are rebased automatically. |

`static-apps` (EMB-32) — declared apps, no discovery, for repos where
workspace discovery doesn't map (mixed-language monorepos, non-npm stacks):

```yaml
workspace_provider:
  type: static-apps
  config:
    apps:
      hub: { prefixes: ["apps/hub/", "shared/"] }   # affected when a changed file matches
      leads: {}                                     # no prefixes -> always affected
# or the short form (every app always affected):
#   config: { apps: [hub, leads] }
```

`npm-workspaces-turbo` `config` keys (see the toy-monorepo fixture):

| Key | Example | Meaning |
|---|---|---|
| `affected_filter` | `"[origin/main]"` | Diff base for "what changed". |
| `turbo_config_path` | `turbo.json` | Where the Turbo task graph lives. |
| `apps_glob` | `apps/*` | Mirror your npm `workspaces`. |
| `packages_glob` | `packages/*` | Mirror your npm `workspaces`. |

> An operator can override **just** the `workspace_provider` per-repo from the
> dashboard admin UI (`/qa/admin/providers/{repo}`). When such a row exists the
> orchestrator prefers it over the committed file; the file remains the fallback.

### `defaults` (applied to every app unless overridden)

| Field | Type | Default | Notes |
|---|---|---|---|
| `mode` | `process` \| `dind` | `process` | **Repo-wide** — there is no per-app `mode`. `process` for plain dev servers; `dind` when boot needs Docker. |
| `sandbox_profile` | str | `slim` | e.g. `slim`, `qa-node`, `qa-jvm`, `qa-external` (browser + LAN egress, no DinD — for QA against an externally deployed instance; clone it to set `extra_hosts` vhost aliases). |
| `ready_checks` | list of check | `[]` | See below. An app whose merged ready_checks end up empty **by omission** fails validation (`infra_error` — boot would "pass" with zero verification). To deliberately skip verification, set `ready_checks: []` explicitly on the app or its app-local config (acknowledged opt-out). `deployed` apps always require non-empty checks. |
| `boot_timeout_seconds` | int 1..3600 | `600` | |
| `seed_command` | str \| null | none | Optional data seed after boot. |
| `seed_timeout_seconds` | int 1..1800 | `120` | |
| `e2e` | e2e block \| null | none | See below. |
| `auth` | auth block \| null | none | Pre-authenticate the QA browser via a Playwright storageState — see the `auth:` section below. |
| `acceptance_criteria_template` | list of str | `[]` | Default criteria the QA agent drives per app. |
| `guardrails` | list of str | `[]` | EMB-31: absolute DO-NOT rules for the QA agent (e.g. "never click Send"). Injected into `job.json` and enforced by the agent's system prompt; violations are reported as `anomalies[]` with `category: guardrail_violation` and surfaced prominently on the dashboard. **Merged as a UNION** with per-app/app-local guardrails — a narrower layer can add rules but never remove inherited ones (unlike acceptance criteria, which replace). |

### `apps:` — `{ <app_name>: AppEntry }`

`app_name` must match what the workspace provider reports. Lightweight overrides
only — heavy overrides go in `apps/<name>/.embry0/app.yaml` (below).

| Field | Type | Required | Notes |
|---|---|---|---|
| `target` | `managed` \| `deployed` | | Default `managed`. `deployed` = the app is **already running outside the sandbox** — see below. |
| `boot_command` | str | ✅ for `managed` | Command that starts the app. **Forbidden for `deployed`** — the app is already running. |
| `frontend_url` | str (http/https) | ✅ | URL the QA agent's headless browser hits. Must be reachable **from inside the sandbox** (in `dind`, a container hostname — not `localhost:<host_port>`). |
| `sandbox_profile` | str | | Overrides `defaults`. |
| `ready_checks` | list | | Replaces `defaults` (not merged). `[]` here is an explicit opt-out of boot verification. |
| `boot_timeout_seconds` | int 1..3600 | | |
| `seed_command` | str | | **Forbidden for `deployed`** — never seed an externally running instance. |
| `e2e` | e2e block | | |
| `guardrails` | list of str | | EXTENDS `defaults.guardrails` (union) — never replaces. |
| `auth` | auth block | | Overrides `defaults`. **Allowed for `deployed`** — pre-authenticating against an external instance is the primary use case. |

### `target: deployed` — QA against an already-running deployment

For a `deployed` app the pipeline skips boot and seed entirely; `ready_checks`
become a **liveness gate on the external URL** (probed from inside the sandbox,
same as always) before any agent time is spent. Rules and behavior:

- `boot_command` and `seed_command` are schema errors; `mode` is forced to
  `process` (no DinD, no per-subtask network).
- `frontend_url` and every declared ready_check must NOT point at
  `localhost`/`127.0.0.1` — inside the sandbox that's the sandbox's own
  loopback, where nothing listens. Use the host's LAN IP, or a vhost name
  mapped via a cloned `qa-external` sandbox profile's `extra_hosts`.
- At least one ready_check must survive the merge (schema/resolution error
  otherwise) — an unreachable external instance should fail fast, not burn an
  agent run.
- Deployed apps **always run** when QA runs: a git diff cannot be mapped onto
  an externally running instance, so the affected-set never filters them. Note
  the semantics: in a PR-gate run the live deployment does *not* include the
  PR's changes — deployed-target QA verifies the running instance (liveness +
  regression), which is most valuable in a post-merge/post-deploy pipeline.
- `workspace_provider` may be omitted entirely when **all** declared apps are
  `deployed` (there is no workspace topology to map). One `managed` app makes
  it required again.
- Use the `qa-external` sandbox profile (browser + LAN egress) or a clone of
  it with `extra_hosts` set for Host-header-routed vhosts.

Minimal all-deployed example:

```yaml
version: 2
qa_required: always
defaults:
  sandbox_profile: qa-external-corvin   # clone of qa-external with extra_hosts
apps:
  quoting:
    target: deployed
    frontend_url: "http://ai-quoting-dev.raven-cargo.app/"
    ready_checks:
      - http: "http://ai-quoting-dev.raven-cargo.app/"
        expect_status: [200, 302]
```

### Guardrails & the assert-but-don't-click pattern

QA against a shared live instance with real customer data needs enforceable
"don't touch" rules: buttons that email customers, spend paid API credits, or
mutate records. Declare them in `guardrails:`; the agent treats each entry as
an absolute prohibition that outranks every acceptance criterion.

The first-class way to still *cover* a forbidden control is
**assert-but-don't-click**: a criterion verifies the control exists, is
enabled, and is labelled correctly — without activating it. Example criterion:
"The Re-price button is present and enabled on a deal card (do NOT click it)."
If a criterion appears to require a forbidden action, the agent marks it
`inconclusive` and explains the conflict rather than acting. Violations the
agent commits are self-reported as `anomalies[]` entries with
`category: guardrail_violation` — never hidden.

### `ready_checks` entry

| Field | Type | Default | Notes |
|---|---|---|---|
| `http` | str | required | Must be an `http(s)` URL. |
| `expect_status` | int **or** list[int] | `200` | Each code in 100..599. **Use the list form for auth-gated apps** whose `/` returns a redirect/401/403 before login but whose server is up, e.g. `expect_status: [200, 302, 401, 403]`. |
| `expect_body_regex` | str \| null | none | Optional body match. |

### `e2e` block

| Field | Type | Default |
|---|---|---|
| `command` | str (non-empty) | required |
| `timeout_seconds` | int 1..3600 | `600` |

### `auth:` — pre-authenticated QA via Playwright storageState

For apps behind a real login (Auth0, OIDC, session cookies), driving the login
form with the QA agent is slow and fragile. Declare an `auth:` block instead
and the orchestrator materializes a Playwright **storageState** (cookies +
localStorage JSON) at `/workspace/.qa/storage-state.json` before the agent
starts; the sandbox's `playwright-mcp` server loads it at browser-context
creation (via `PLAYWRIGHT_MCP_STORAGE_STATE`), so the agent begins **already
logged in** and `job.json` carries `pre_authenticated: true` telling it never
to drive a login form.

```yaml
auth:
  storage_state_from:
    # exactly ONE of:
    secret: QA_STORAGE_STATE               # name of a qa-scoped env var holding the storageState JSON
    command: "node scripts/qa-login.mjs"   # in-sandbox login script that writes $QA_STORAGE_STATE_PATH
    timeout_seconds: 300                   # 1..1800 (default 300) — budget for the command/secret step
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `storage_state_from.secret` | str | — | Name (not value!) of a **qa-scoped** env var set via `/environments` with `scope=qa`. Must match `QA_[A-Z0-9_]*` and not be a reserved infra key. Its value is the storageState JSON — capture it once with a scripted login (e.g. `npx playwright open --save-storage`) and refresh it when the session expires. |
| `storage_state_from.command` | str | — | Repo-provided script run **inside the sandbox** (after boot/seed, before e2e/exploratory) that performs the login itself and writes the JSON to `$QA_STORAGE_STATE_PATH`. Any deps it needs must already be in the sandbox profile or vendored in the repo. |
| `storage_state_from.timeout_seconds` | int 1..1800 | `300` | |

Rules and behavior:

- Declared on `defaults:` (applies to every app), per-app, or in
  `apps/<name>/.embry0/app.yaml` — standard last-wins resolution.
- Exactly one of `secret` / `command` (schema error otherwise).
- **Survives `target: deployed`** (unlike `seed_command`) — pre-authenticating
  against an external deployment is the primary use case. The login command
  runs in-sandbox against the external URL.
- The produced file is validated as JSON with `cookies`/`origins`; a missing
  secret, failing login script, or malformed file marks the app
  **`inconclusive`** ("couldn't authenticate, so couldn't verify") with an
  `auth setup failed: …` summary — never `failed` and never an infra error.
- `QA_STORAGE_STATE_PATH` and `PLAYWRIGHT_MCP_STORAGE_STATE` are reserved,
  orchestrator-owned env vars — user rows with those keys are dropped.

See `tests/fixtures/qa-yaml-corpus/v2/deployed-with-auth.yaml` for a worked
example.

### `packages:` — `{ <package_name>: { no_cascade: bool } }`

By default a change to a shared package **cascades** — every app that depends on
it gets QA'd. Set `no_cascade: true` for low-risk packages (config, manifests)
so their churn doesn't re-QA the whole repo.

### `conditional_acceptance_criteria` — relevance-gated criteria (EMB-39)

Costly or side-effectful criteria (a flow that spends real API credits or GPU
inference) should not run on every QA. Declare them in named groups guarded by
a `when:` predicate over the run's change context; the orchestrator evaluates
the predicates at pipeline build and **appends** matching groups' criteria to
the resolved per-app set.

```yaml
conditional_acceptance_criteria:
  - name: pricing                       # unique; the force-knob handle
    when:
      changed_paths:                    # globs vs the run's changed files
        - "platform/api/**/pricing/**"
        - "apps/quoting/**/pricing*/**"
    criteria:
      - "Exercise Price Now on a NET-NEW lane … (the sanctioned mutation)"
  - name: hub-deep
    when:
      affected_apps: ["hub"]            # diff-derived MANAGED affected set
      labels: ["qa:deep"]               # issue labels (issue→PR runs)
    apps: ["hub"]                       # append only to these apps ([] = all)
    criteria:
      - "Walk every hub list view"
```

| Field | Type | Notes |
|---|---|---|
| `name` | str, required | Unique per file, `[A-Za-z0-9][A-Za-z0-9._- ]{0,99}`. Used by the per-job force knob and shown in the affected-set view. `*` is reserved. |
| `when.changed_paths` | list of glob | Matched against repo-relative changed files. `*`/`?` stay within one path segment; `**` is zero-or-more whole segments (`a/**/b` matches `a/b`); a trailing `/**` requires at least one segment under the prefix. Bad globs are parse errors. |
| `when.affected_apps` | list of app names | Matches the **diff-derived managed** affected set. Deployed apps are never diff-affected — naming one here is a parse error. |
| `when.labels` | list of str | Case-insensitive exact match against the originating issue's labels. Empty on standalone runs. |
| `apps` | list of app names | Which apps get the appended criteria when the group fires. `[]` = every app in the run. |
| `criteria` | list of str, ≥1 | Appended (deduped) to each target app's resolved criteria. |

Semantics: within one list **OR**; between non-empty `when` fields **AND**;
groups are independent (union of appends). At least one predicate list must be
non-empty.

**Deployed/standalone runs default OFF.** A standalone `pipeline:"qa"` run on
the default branch (and any deployed-target run) has an empty diff, so
`changed_paths`/`affected_apps` can never match. Only two things fire a group
there: a `labels:` match (issue→PR path) or the per-job force knob —
`POST /jobs {"qa": {"force_conditional_groups": ["pricing"]}}` (`["*"]` forces
every group). An unknown forced name fails the run (`infra_error`) before
fan-out rather than silently ignoring a typo.

Which groups fired (and whether by match or force) is persisted per run and
shown in the dashboard's affected-set view
(`GET /qa/runs/{run_id}/affected_set` → `conditional_groups`).

### `qa_required`

- `auto` (default) — QA only the affected apps.
- `always` — QA every declared app on every run.
- `never` — skip QA (a standalone `pipeline:"qa"` job, or a per-job
  `force_all_apps`, can still force it).

### `cache` (optional — defaults are sane; usually omit)

```yaml
cache:
  prebaked_image: { enabled: true, rebuild_on: [lockfile_change] }
  shared_volume:  { enabled: true, scope: per-job }   # per-job | per-repo | per-org
  turbo_remote:   { enabled: true }
```

These keep monorepo QA from paying full cold-install/build costs every run.

---

## Per-app overrides — `apps/<name>/.embry0/app.yaml`

Heavy, app-local overrides live next to the app, not in the root file:

```yaml
sandbox_profile: qa-jvm
ready_checks:
  - http: "http://localhost:8080/health"
    expect_status: 200
boot_timeout_seconds: 900
seed_command: "npm run seed:e2e"
e2e:
  command: "npm run e2e"
  timeout_seconds: 1800
auth:
  storage_state_from:
    secret: QA_STORAGE_STATE
acceptance_criteria:               # REPLACES defaults.acceptance_criteria_template
  - "orders table paginates"
```

### Resolution order (last wins)

1. Built-in model defaults
2. Root `defaults:`
3. Root `apps.<name>:`
4. App-local `apps/<name>/.embry0/app.yaml`
5. Per-job `acceptance_criteria` override (whole-list **replace**, see below)
6. Fired `conditional_acceptance_criteria` groups **append** last — after
   everything, including the per-job replace. On issue→PR runs triage supplies
   its criteria through that same replace path, so appending any earlier would
   mean conditional groups never fire on the primary use case.

`acceptance_criteria` in the app-local file **replaces** (does not extend)
`acceptance_criteria_template`. `ready_checks` likewise replace rather than
merge. `mode` is taken from `defaults` only.

### Per-job API overrides

`POST /jobs` accepts a `qa` override object (`QAJobOverrides`) that layers over
the file at run time. The file is the durable contract; these are one-off:

- `acceptance_criteria` — non-empty ⇒ **replaces** every app's resolved
  criteria for this run (fired conditional groups still append on top).
- `force_conditional_groups` — force named conditional groups ON regardless of
  diff relevance; `["*"]` forces all. Unknown names fail the run.
- `force_all_apps` — ≈ `qa_required: always` for one job.
- `base_branch`, `sandbox_profile`, `qa_timeout_seconds`.

---

## Worked example — `acme/monorepo-demo` (npm-workspaces + Turborepo, 24 apps)

`monorepo-demo` (a fictional `acme/monorepo-demo` monorepo: root `package.json`
workspaces `apps/*` + `packages/*`, `turbo ^2`, `turbo.json` present) is the
canonical multi-app target. Apps are Next.js packages named `@acme/<app>`; the
workspace name — not the directory — is what `npm --workspace=` takes (e.g. the
`apps/hub` directory is the package `@acme/hub-console`). The dashboards are
Auth0/Supabase-gated, so `/` does not return 200 pre-login — hence the
`expect_status` list.

```yaml
version: 2

workspace_provider:
  type: npm-workspaces-turbo
  config:
    affected_filter: "[origin/main]"
    turbo_config_path: turbo.json
    apps_glob: apps/*
    packages_glob: packages/*

defaults:
  mode: process                       # Next.js dev servers — no Docker
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:${port}"
      expect_status: [200, 302, 401, 403]   # auth-gated: server is up even when / redirects
  boot_timeout_seconds: 300
  acceptance_criteria_template:
    - "page loads with no console errors"
    - "primary nav renders"

qa_required: auto
parallelism:
  max_concurrent_apps: 4

apps:
  dashboard:
    boot_command: "npm --workspace=@acme/dashboard run dev"    # dev script pins --port 3004
    frontend_url: "http://localhost:3004"
  admin:
    boot_command: "npm --workspace=@acme/admin run dev"        # --port 3001
    frontend_url: "http://localhost:3001"
  reports:
    boot_command: "npm --workspace=@acme/reports run dev"      # --port 3005
    frontend_url: "http://localhost:3005"
  billing:
    boot_command: "npm --workspace=@acme/billing run dev"      # --port 3007
    frontend_url: "http://localhost:3007"
  hub:
    boot_command: "PORT=3000 npm --workspace=@acme/hub-console run dev"  # hub's dev has no fixed port
    frontend_url: "http://localhost:3000"
  # … one entry per remaining app (read each app's dev script for its port)

packages:
  "@acme/ui": {}             # change cascades QA to every consuming app
  "@acme/auth": {}           # security-critical — want the cascade
  "@acme/db": {}
  "@acme/config":
    no_cascade: true         # config churn shouldn't re-QA all apps
  "@acme/app-manifest":
    no_cascade: true

conditional_acceptance_criteria:
  - name: billing-mutation
    when:
      changed_paths: ["apps/billing/**", "packages/db/**/invoices/**"]
    apps: ["billing"]
    criteria:
      - "Create a draft invoice for the QA-owned test account and assert totals render (do NOT send it)"
```

**Why each choice:** `npm-workspaces-turbo` because the repo *is* npm workspaces
+ Turbo; `mode: process` because apps boot as `next dev`; the `expect_status`
list because the dashboards are auth-gated; `qa_required: auto` + bounded
`max_concurrent_apps` because you never want to boot all 24 apps per PR; and the
`packages` cascade/`no_cascade` split so shared-UI/auth changes ripple but
config noise doesn't.

---

## Gotchas

- **Unknown keys fail.** `extra="forbid"` everywhere — a typo is a parse error.
- **`app_name` must match the provider.** A declared app the provider never
  reports is inert; an affected app you didn't declare is silently skipped.
- **`frontend_url` resolves inside the sandbox.** In `dind`, use a container
  hostname, not `localhost:<host_port>`.
- **`ready_checks`/`acceptance_criteria` replace, they don't merge.** Re-state
  the full list when overriding.
- **Empty `ready_checks` ⇒ no boot verification** — boot "passes" right after the
  command. Fine for fire-and-forget; risky if the server can crash on startup.
- **Conditional groups never fire on deployed/standalone runs by default.** An
  empty diff means `changed_paths`/`affected_apps` can't match — that's the
  point (cost-gated criteria stay off). Fire them explicitly via an issue label
  or `qa.force_conditional_groups` on the job request.
- **Conditional appends survive the per-job criteria replace.** A job-level
  `acceptance_criteria` override (including triage's, on issue→PR runs)
  replaces the base list first; fired conditional groups append after.
