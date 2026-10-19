# API Reference

Two-level API: **low-level graph execution** + **high-level job management**. All endpoints live under `/api/v1/` behind the frontend's nginx on port 8200.

Authentication: send `Authorization: Bearer <API_KEY>` on every request (omitted from the examples below for brevity). An empty `API_KEY` is only tolerated when a dev-mode flag is set — see [configuration.md](configuration.md).

See [architecture.md](architecture.md#api-structure) for the full route map.

## Issues

```bash
# Create an issue (with optional auto-triage and GitHub sync)
curl -X POST http://localhost:8200/api/v1/issues \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"title": "Fix the auth bug", "repo": "owner/repo", "auto_triage": true, "github_sync_enabled": true}'
# -> 201 with the issue plus "job_id", "dispatch_error_code" and "dispatch_error".
#    With auto_triage, a non-null "dispatch_error_code" ("ERR_DISPATCH_FAILED")
#    means the issue was created but NO job started (it is back in "open").
#    Check it — the status code is 201 either way, so a retry never creates the
#    issue twice. "dispatch_error" is a safe sentence; the cause is in the
#    orchestrator log. POST /issues/{issue_id}/triage retries and reports the
#    same three fields (status 200).

# List issues
curl http://localhost:8200/api/v1/issues -H "X-Requested-With: XMLHttpRequest"

# Get issue with children and jobs
curl http://localhost:8200/api/v1/issues/{issue_id} -H "X-Requested-With: XMLHttpRequest"

# Answer a triage question
curl -X POST http://localhost:8200/api/v1/issues/{issue_id}/inputs/{input_id}/answer \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"answer": "The bug is in the JWT validation logic"}'
```

## Jobs (high-level)

```bash
# Create a job
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "task": "Fix the auth bug in login.py"}'

# List jobs
curl http://localhost:8200/api/v1/jobs

# Cancel a job
curl -X POST http://localhost:8200/api/v1/jobs/{job_id}/cancel \
  -H "X-Requested-With: XMLHttpRequest"

# Submit a QA job (skips triage; needs branch + pipeline=qa). See docs/running-qa.md.
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "branch": "main", "pipeline": "qa",
       "qa": {"acceptance_criteria": ["home page loads with no console errors"]}}'

# Run a specialist agent: name its template (docs/specialist-agents.md).
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "pipeline_template": "kintsugi-trace",
       "task": "Trace the cracks in this repository."}'

# QA artifact endpoints (presigned-GET redirects + SSE log stream).
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts/1/result
curl http://localhost:8200/api/v1/jobs/{job_id}/artifacts/screenshots/latest
curl "http://localhost:8200/api/v1/jobs/{job_id}/artifacts/logs/gateway?follow=true"
```

## Runtime configuration

```bash
# Budget controls
curl http://localhost:8200/api/v1/config/budget
curl -X PUT http://localhost:8200/api/v1/config/budget \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"max_budget_per_job_usd": 25.0}'

# Context injection
curl http://localhost:8200/api/v1/config/context
curl -X PUT http://localhost:8200/api/v1/config/context \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"system_context": "Use TypeScript strict mode."}'

# Sandbox profiles
curl http://localhost:8200/api/v1/sandbox-profiles
curl -X POST http://localhost:8200/api/v1/sandbox-profiles \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "java-17", "base_image": "embry0-sandbox-java:17", "memory": "12g"}'
```

## Environment variables

```bash
# List global environment variables (secrets masked)
curl http://localhost:8200/api/v1/environment/global \
  -H "X-Requested-With: XMLHttpRequest"

# Set global environment variables
curl -X PUT http://localhost:8200/api/v1/environment/global \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"variables": [{"key": "DATABASE_URL", "value": "postgres://...", "var_type": "secret"}]}'

# Reveal a masked secret (audit-logged)
curl http://localhost:8200/api/v1/environment/global/DATABASE_URL/reveal \
  -H "X-Requested-With: XMLHttpRequest"

# Per-repo environment (same pattern, scoped)
curl http://localhost:8200/api/v1/repos/owner/repo/environment \
  -H "X-Requested-With: XMLHttpRequest"

# Auto-detect env vars from repo's .env.example
curl http://localhost:8200/api/v1/repos/owner/repo/environment/detect \
  -H "X-Requested-With: XMLHttpRequest"
```

A list response carries each secret as `****` plus a `masked_preview`:
eight `•` and the secret's last 2 characters when it has 12 or more, eight
`•` alone otherwise.

A PUT replaces the whole set. A secret sent back as `****`, the value the
list responses carry, keeps its stored value, so a client can write one
variable without revealing the others: the MCP `env_set` tool, and
`env_delete` on the global set, work that way and need only operator tier. A
secret sent as `****` for a key with no stored secret is refused with a 400.

Some keys are refused with a 422, matched case-insensitively. The same check drops them again at sandbox start, from stored rows and from profile `env_defaults`. These keys are the infrastructure credentials and proxy URLs, plus every variable that would redirect a sandbox's traffic or load code into its processes: proxy routing, TLS verification, the dynamic loader, `PATH`/`HOME`/`SHELL`, shell and interpreter startup hooks, and the `GIT_`, `CLAUDE_`, `BUN_` and `GROK_` namespaces. `NODE_OPTIONS` and the JVM option variables (`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`, `JAVA_OPTS`, `MAVEN_OPTS`, `GRADLE_OPTS`) are accepted, but only with memory flags. The authoritative list is `embry0/execution/auth_provider.py`. A 422 on these routes never echoes the submitted value.

## Container logs

`GET /api/v1/logs/query` reads container logs from Loki, newest first:
`container` matches a container name by substring, `containers` is a
comma-separated list of exact names, `search` is a regex line filter,
`since_minutes` goes up to 1440 and `limit` up to 1000, and `logql` replaces
all of them with a raw LogQL query. `GET /api/v1/logs/containers` lists the
container names Loki holds. The dashboard's Logs page and the MCP
`logs_query` tool use them.

Production logs carry personal data, so both routes need operator tier: an
operate or admin key, an SSO session or the master key. A read or merge key
gets a 403. The one read-scope key they admit is the context-proxy's own
service key (`svc-context-proxy`, rotated at every boot), which forwards
allowlisted queries for the sandboxes enrolled with it.

Every line returned goes through the log-text redactor
(`embry0/safety/redaction.py`), whoever asks: secrets, e-mail addresses, long
opaque runs and the values of the `LOG_REDACTION_FIELDS` fields
([configuration.md](configuration.md)) are replaced. A line longer than 2000
characters is cut and ends with `… [line truncated]`. The redactor is
regex-based, so it is not a guarantee.

```bash
curl "http://localhost:8200/api/v1/logs/query?container=api&search=error&since_minutes=30&limit=50"
```

## Credentials

Forge accounts and their bindings ([`docs/credentials.md`](credentials.md)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes a `credential.*` audit row. Secrets are write-only: no endpoint returns one, and there is no reveal endpoint.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/credentials` | read |
| `POST /api/v1/credentials` | admin |
| `PUT /api/v1/credentials/{id}` | admin |
| `POST /api/v1/credentials/{id}/rotate` | admin |
| `POST /api/v1/credentials/{id}/verify` | admin |
| `DELETE /api/v1/credentials/{id}` | admin |
| `GET /api/v1/credentials/resolve?repo=&role=forge` | read |
| `GET /api/v1/credentials/bindings?scope_kind=&scope_value=` | read |
| `PUT /api/v1/credentials/bindings` | admin |
| `POST /api/v1/credentials/import-env` | admin |
| `GET /api/v1/repos/{repo}/credentials` | read |
| `PUT /api/v1/repos/{repo}/credentials` | admin |

```bash
# Create a GitHub account (the response carries masked_preview, never the secret)
curl -X POST http://localhost:8200/api/v1/credentials \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"kind": "forge_token", "provider": "github", "label": "alice", "secret": "github_pat_..."}'

# Bind every acme repo to it
curl -X PUT http://localhost:8200/api/v1/credentials/bindings \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"scope_kind": "owner", "scope_value": "github:acme", "bindings": [{"credential_id": "cred-0123456789ab", "role": "forge"}]}'

# Dry run: which account would a job on acme/widgets use, and why
curl "http://localhost:8200/api/v1/credentials/resolve?repo=acme/widgets&role=forge" \
  -H "X-Requested-With: XMLHttpRequest"

# Move GITHUB_TOKEN* / GITLAB_TOKEN* from the orchestrator env into the registry
curl -X POST http://localhost:8200/api/v1/credentials/import-env \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"replace": false}'
```

### Discovery

`GET /api/v1/github/discoverable-repos` and `GET /api/v1/gitlab/discoverable-projects` fan out across registry credentials for that forge and the env tokens, then drop repos already registered. Behavior is in [credentials.md](credentials.md). `refresh=true` bypasses the cache.

| Field | GitHub | GitLab |
|---|---|---|
| `credential_id` | registry id, or null for an env token | same |
| `credential_label` | registry label; an env row repeats the env key | registry label, or the env key when the row came from the environment |
| `token_env_key` | the env key for an env row; empty for a registry row | not a field on this response |
| `failed` | labels whose listing raised. Never a token | same |

GitHub also returns `cached`, `truncated_env_keys` (an env key, or `credential:<label>` when a registry listing was cut short), and an advisory `push` (GitHub's `permissions.push` for the token's user, not what a fine-grained PAT grants). GitLab returns `cached` and `configured` (`false`, with an empty project list, when the deployment has no GitLab credential — not an error).

## Hosts

The machines that run the code ([`docs/hosts.md`](hosts.md)). Reads need any authenticated principal and a probe needs an operator; every other mutation needs the `admin` tier and writes a `host.*` audit row.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/hosts?source=&include_removed=` | read |
| `POST /api/v1/hosts` | admin |
| `GET /api/v1/hosts/{id}` | read |
| `PATCH /api/v1/hosts/{id}` | admin |
| `DELETE /api/v1/hosts/{id}` | admin |
| `POST /api/v1/hosts/{id}/probe` | operator |
| `POST /api/v1/hosts/import/tailnet` | admin, body `{"confirm": true}` |

```bash
# An ssh host serving acme/widgets, with its logs in journald
curl -X POST http://localhost:8200/api/v1/hosts \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "web-1", "transport": "ssh", "address": "web-1.example.com:22", "roles": ["app"],
       "log_source": {"kind": "journald", "selector": "widgets.service"},
       "repos": [{"repo": "acme/widgets", "role": "app"}]}'

# One host per device of the tailnet bound at global scope
curl -X POST http://localhost:8200/api/v1/hosts/import/tailnet \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"confirm": true}'
```

`GET /api/v1/repos/{repo}` lists the repository's hosts under `hosts`, each with its `role`.

## Standards

An owner's versioned standards and each repository's mode under them ([`docs/standards.md`](standards.md), [ADR 0014](adr/0014-organization-standards.md)). Reads need any authenticated principal and an audit needs an operator; writing a standard and setting a mode need the `admin` tier and write a `standard.*` audit row.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/standards/{owner}` | read |
| `GET /api/v1/standards/{owner}/{kind}` | read |
| `PUT /api/v1/standards/{owner}/{kind}` | admin |
| `GET /api/v1/standards/{owner}/{kind}/versions` | read |
| `GET /api/v1/standards/{owner}/{kind}/repos/{repo}` | read |
| `PUT /api/v1/standards/{owner}/{kind}/repos/{repo}` | admin |
| `POST /api/v1/standards/{owner}/{kind}/audit/{repo}` | operator |

`PUT /standards/{owner}/{kind}` stores a whole new version, `{"schema", "values", "prose", "references"}`, and supersedes the active one; every key of `values` must be a role `schema.roles` declares, or the answer is a 422 listing the problems. `PUT .../repos/{repo}` sets `{"mode": "off" | "advise" | "enforce", "exceptions": [...]}` for a registered repository under the owner; every repository starts in `off`. The audit starts a read-only job on the `design-audit` pipeline template and answers 202 with its `job_id`, or 409 in mode `off`, without an active standard or the template, or while the previous audit runs (of requests sent together, one starts and the others are 409 naming its job); its completed report's `Score: N` line and findings table become the repository's `score` and `findings_open`. `GET /api/v1/repos/{repo}` lists the state under `standards`, one entry per kind.

```bash
# Put acme/widgets in advise mode, then audit it against acme's design standard
curl -X PUT http://localhost:8200/api/v1/standards/acme/design/repos/acme/widgets \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"mode": "advise"}'

curl -X POST http://localhost:8200/api/v1/standards/acme/design/audit/acme/widgets \
  -H "X-Requested-With: XMLHttpRequest"
```

## Organizations

The forge owners embry0 maintains as a whole ([ADR 0010](adr/0010-organizations.md)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes an `organization.*` audit row. Registering an owner changes nothing for any repository until a person acts.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/orgs` | read |
| `GET /api/v1/orgs/{owner}` | read |
| `POST /api/v1/orgs` | admin |
| `POST /api/v1/orgs/{owner}/probe` | admin |
| `PUT /api/v1/orgs/{owner}/status` | admin |
| `POST /api/v1/orgs/{owner}/discover` | admin |
| `GET /api/v1/orgs/{owner}/repos` | read |
| `GET /api/v1/orgs/{owner}/runs` | read |
| `GET /api/v1/orgs/{owner}/needs` | read |
| `POST /api/v1/orgs/{owner}/needs/{id}/dismiss` | admin |
| `POST /api/v1/orgs/{owner}/needs/{id}/reopen` | admin |
| `PATCH /api/v1/orgs/{owner}` | admin |
| `GET /api/v1/orgs/{owner}/webhook` | read |
| `POST /api/v1/orgs/{owner}/webhook` | admin |
| `POST /api/v1/orgs/{owner}/actions` | admin |
| `POST /api/v1/orgs/probe` | admin, stores nothing |

`POST /api/v1/orgs/{owner}/discover` answers 202 and starts one discovery run for the owner; a request while a run is in progress, or for a paused or removed owner, answers 409, and an unknown owner 404. Poll `GET /api/v1/orgs/{owner}/runs` until the newest run is `done` or `error`. `GET /api/v1/orgs/{owner}/repos` returns one fact row per repository, replaced on each run, each with `maintain_hint`, one sentence on what it takes embry0 to maintain the repository, and `maintain_steps`, the needs actions that sentence names in the order they are taken (`register`, `credential`, `hook`, `onboard`, `watch`, `setting`); both are read from the facts and the organization's probe by rules, with no model call, and a repository the latest finished run did not list asks for nothing ([ADR 0015](adr/0015-maintain-hints.md)). `runs` keeps the history of every run with its progress and summary. A row whose `run_id` is not the newest run's is a repository that run did not list; the summary's `vanished` counts them. The listing alone cannot say such a repository is gone: it pages over newest push first, so a push during the listing can move a repository onto a page already read. Each one embry0 still holds as an active registration is therefore confirmed one by one with a direct read, `GET /repos/{owner}/{repo}` with the run's own credential: a 404, a 301 (the forge redirects a renamed or transferred repository's old name) or the record under another full name confirms it gone and stamps the row's `facts.vanished_run` with the run; the same repository, the case aside, means the listing missed it; any other answer (a rate limit, a 5xx) is unknown, which is not gone. The summary's `vanished_confirmed` counts the confirmed ones. A run confirms none, and logs `org_vanished_unchecked` with the reason, when the page cap (20 pages of 100) stopped its listing with a next page unread (the summary's `listing_complete` is then false), or when more than 50 candidates are held. A listing page the forge refuses fails the run rather than ending the listing early, and a run still marked `running` when the orchestrator starts is closed as `error` (it was cut off by the restart).

An owner with the `rediscover_hours` setting on is also rediscovered on a schedule ([ADR 0016](adr/0016-rediscovery-schedule.md)): every few minutes the orchestrator starts a run, the one this endpoint starts, for each active owner whose newest run is that many hours old, and never while one is running. A failed run waits a full interval too, and so does a start refused before any run exists (the owner's credential cannot be resolved), which the orchestrator remembers until it restarts. The audit row's actor is `embry0:org-rediscovery`.

`GET /api/v1/orgs/{owner}/needs` is the needs list ([ADR 0011](adr/0011-organization-needs.md)): what embry0 still needs from a person to maintain the owner, derived from the newest finished discovery run, one need per kind and repository set. `dismiss` and `reopen` keep a person's answer across rediscovery. `repo_vanished` lists the repositories an earlier run listed, the newest did not and confirmed gone with a direct read, that embry0 still holds as active registrations; its action is `deactivate`, which a person runs: nothing is deactivated on its own. Each open need is also a healing case with origin `org`, listed by `GET /api/v1/cases?awaiting_decision=true` ([Healing cases](#healing-cases)); closing that case dismisses the need.

`PATCH /api/v1/orgs/{owner}` sets `display_name` and merges `settings` from a closed list; an unknown key, a wrong type or a value out of range answers 422.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `auto_register` | boolean | off | A webhook delivery for an unregistered repository under the owner registers it first ([webhooks.md](webhooks.md#organization-webhooks-github)) |
| `rediscover_hours` | integer from 1 to 168, or `null` | `null` (off) | Rediscover the owner on a schedule, this many hours after its newest run |

`GET` and `POST /api/v1/orgs/{owner}/webhook` inspect and install the organization's one GitHub webhook, and the receiver's gate handles deliveries for repositories nobody registered ([webhooks.md](webhooks.md#organization-webhooks-github), [ADR 0012](adr/0012-organization-webhook.md)).

`POST /api/v1/orgs/{owner}/actions` runs one action over repositories discovery saw under the owner. Body: `{"action": "register" | "hook" | "onboard" | "watch" | "deactivate", "repos": ["acme/widgets"], "dry_run": false, "force": false}`. The response carries one result per repository: `{"repo", "outcome", "detail"}` with `outcome` one of `done`, `already` (the state exists), `skipped` (a reason the caller can fix) or `failed` (one repository's error, or a refusal from the path the action reuses; the rest still run). `repo` is the spelling the forge reported, whatever case was asked; a repository discovery has not seen, an archived one (for every action but `deactivate`), or one under another owner is `skipped`. Every action reuses an existing path and is idempotent: a second identical call reports `already` for every repository the first one changed, and changes nothing. `dry_run` reports what would happen without side effects and writes no audit row; a real run writes one `organization.action` row with every repository's outcome, plus the rows the reused paths write. A real run with any `done` result starts a discovery run for the owner, the one `POST /orgs/{owner}/discover` starts, so the needs list catches up; the response's `rediscovery` is that run's id, or `null` when nothing changed, a dry run, or a run already in progress.

- `register` adds the repository to the repo registry under the organization's forge, the call `POST /repos` makes. A row under any case counts as `already`.
- `hook` creates a GitHub webhook on the repository posting to `WEBHOOK_PUBLIC_URL` + `/api/v1/webhook` with `GITHUB_WEBHOOK_SECRET`, for issues, issue comments and pull requests ([webhooks.md](webhooks.md#github)). It runs only when the owner's probe found the credential can manage webhooks, and it is skipped while `WEBHOOK_PUBLIC_URL` is unset or a loopback address, or the secret is empty. A hook in the forge's live listing whose URL starts with `WEBHOOK_PUBLIC_URL` is `already`; a hook on the dashboard's origin is not, since that vhost answers 404 on the webhook paths. GitHub only in this release.
- `onboard` starts the onboard job ([onboarding.md](onboarding.md)) on the repository's default branch through the function `POST /jobs {"pipeline": "onboard"}` uses, so it runs the same checks (the calling key's repository allowlist among them: a repository the key may not start work on is `failed` with `repo_not_allowed`) and starts it under the caller's actor, which its `onboard.job_created` audit row names. A repository that is not registered is skipped (the job would fail `ERR_REPO_UNREGISTERED`); a stored QA config or an onboard job in flight is `already`; a repository whose facts carry no boot hint is skipped. When the newest onboard job completed without storing a QA config, the repository is skipped with that job's id unless the body carries `"force": true`.
- `watch` adds a watcher stack for the repository, disabled, filing to the organization's forge, with a placeholder LogQL selector to edit before enabling. It stores through the function `PUT /watcher/stacks/{owner}/{name}` uses and writes its `watcher_stack_updated` audit row. A stack under any case counts as `already`.
- `deactivate` sets the repository's registry row inactive, the call `PUT /repos/{repo}/active` with `{"active": false}` makes: the row stays, hidden from listings, and the same route turns it back on. It answers the `repo_vanished` need, and it reaches an archived repository too: one archived when it was last listed and deleted since is what that need proposes. An inactive row, or no row at all, is `already`.

The MCP tool `org_act(owner, action, repos, dry_run, force, confirm)` calls this endpoint; like every gated tool it needs `confirm=true` on every call, dry runs included.

```bash
# What would registering two repositories do
curl -X POST http://localhost:8200/api/v1/orgs/acme/actions \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"action": "register", "repos": ["acme/widgets", "acme/gadgets"], "dry_run": true}'
```

## Repository preferences

```bash
# Get per-repo sandbox profile override
curl http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "X-Requested-With: XMLHttpRequest"

# Set sandbox profile + language hint for a repo
curl -X PUT http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"sandbox_profile": "java-17", "language_hint": "Java", "notes": "Uses Maven"}'
```

The PUT replaces every field it does not mention — except `verification`
(the orchestrator-run test gate and tier path rules, `docs/verification.md`):
omitted or `null` keeps the stored value, `{}` clears it.

## Docker access (admin)

A sandbox profile with `dind_enabled` only *asks* for the shared Docker daemon
(DinD). A sandbox gets the daemon's client certificates and `DOCKER_HOST` when
its repo also holds this grant. **Code from a granted repo can control the
shared Docker daemon, including the proxies holding your tokens**: the daemon
runs every credential proxy and every other job's sandbox, so its certificates
are root on the Docker host. A profile, a stored qa.yaml or a job cannot set it.

| Endpoint | Tier |
|---|---|
| `PUT /api/v1/repos/{repo}/docker-access` | admin |

```bash
# Grant (or {"enabled": false} to revoke). 404 for an unregistered repo.
curl -X PUT http://localhost:8200/api/v1/repos/owner/repo/docker-access \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"enabled": true}'
```

`GET /api/v1/repos` and `GET /api/v1/repos/{repo}` return the flag as
`docker_access`. Each change writes a `repo_docker_access_granted` or
`repo_docker_access_revoked` audit row. A sandbox whose profile asks for
Docker on a repo without the grant is refused before it starts, with
`ERR_INCOHERENT_CONFIG` and a message naming this endpoint.

## API keys

Database API keys authenticate MCP, CLI and other machine clients with
`Authorization: Bearer <key>`. An operator mints keys for themselves, scoped at
or below their own tier; an admin also mints unowned service keys and sees and
revokes every key. The plaintext appears once, in the mint response. Settings
→ API keys in the dashboard does the same.

| Endpoint | Who |
|---|---|
| `GET /api/v1/apikeys` | operator (own keys), admin (all) |
| `POST /api/v1/apikeys` | operator, admin |
| `PUT /api/v1/apikeys/{key_id}/allowed-repos` | the key's owner, admin |
| `DELETE /api/v1/apikeys/{key_id}` | the key's owner, admin |

### Repository allowlist

A key may be limited to a list of repositories with `allowed_repos`. A
restricted key is refused, with a 403 whose detail starts with
`repo_not_allowed:`, whenever it would start, continue or stop work on any
other repository:

- `POST /jobs` (every pipeline; a job with no repository, such as an `http`
  or `none` context, is refused too), `POST /jobs/{id}/resume`,
  `POST /jobs/{id}/inputs/{input_id}/answer` and `POST /jobs/{id}/cancel`;
- `POST /issues` (an issue without a repository is refused),
  `PUT /issues/{id}` (the issue's repository and, when it changes, the new
  one), `POST /issues/{id}/triage`, `POST /issues/{id}/sync`,
  `POST /issues/{id}/inputs/{input_id}/answer` and `DELETE /issues/{id}`
  (the issue and each child issue it cancels);
- `POST /cases/{id}/accept`, `POST /merge-approvals/{id}/decide`,
  `POST /watcher/run` (the stack ticked; with none named, every watched
  stack, all checked before any ticks), `POST /qa/images/build` and
  `POST /repos/bootstrap`.

The MCP tools reach embry0 through these routes with the caller's key, so
they are covered too. Matching ignores case and follows `REPO_RENAMES`
([configuration.md](configuration.md)): an entry naming a repository's old
slug also matches its current one, and the reverse. Each refusal logs
`api_key_repo_refused` with the key's name and id (never the key), the
repository and the action, and writes an `apikey.repo_refused` audit row.

Omitting the list, or sending an empty one, leaves the key unrestricted;
every key minted before the allowlist existed is unrestricted. The list
governs work only: it does not narrow what the key may read, nor the
settings routes its scope allows. A restricted key may not mint keys or
change any key's allowlist, its own included, since a key it minted would
carry no restriction. For an agent that should only dispatch, mint an
`operate` key with the list.

```bash
# Mint a key that may only start work on acme/widgets
curl -X POST http://localhost:8200/api/v1/apikeys \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "dispatch-bot", "scope": "operate", "allowed_repos": ["acme/widgets"]}'

# Replace the list (or send {"allowed_repos": []} to lift it). Effective at once.
curl -X PUT http://localhost:8200/api/v1/apikeys/key-0123456789ab/allowed-repos \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"allowed_repos": ["acme/widgets", "acme/widgets-docs"]}'
```

Each change writes an `apikey.allowed_repos_changed` audit row with the list
before and after.

## Audit log

```bash
# Newest-first rows; admins see every row, other callers the rows they wrote
curl "http://localhost:8200/api/v1/audit?action=qa.job_created&limit=50" \
  -H "Authorization: Bearer $EMBRY0_API_KEY"

# Every action a row can carry, with its label (the dashboard's filter list)
curl http://localhost:8200/api/v1/audit/actions \
  -H "Authorization: Bearer $EMBRY0_API_KEY"
# -> {"actions": [{"action": "user.login", "label": "Signed in"}, ...]}
```

The action names live in one registry, `embry0/audit/actions.py`. Each
event has one name: a QA, onboard or pull request review job writes one
`qa.job_created`, `onboard.job_created` or `pr_review.job_created` row,
whose actor is the job's actor. Rows written before that also carry a
second, underscore-spelled copy (`qa_job_created`, `onboard_job_created`,
`pr_review_job_created`); the registry does not list those.

## Sandbox visibility

```bash
# List running sandbox containers (ops)
curl http://localhost:8200/api/v1/sandboxes/active \
  -H "X-Requested-With: XMLHttpRequest"
```

## WebSocket streaming

The handshake takes a short-lived token scoped to one job, never an API key.
Mint it with an operator or admin key; a read or merge key gets 403, and can
page the same events from `GET /api/v1/jobs/{job_id}/logs/events?since_seq=`
instead. A browser signed in with SSO needs no token: the session cookie
authenticates the handshake.

```javascript
// 1. Mint a token for this job (expires_in: 900 seconds).
const { token } = await fetch(`http://localhost:8200/api/v1/jobs/${jobId}/events-token`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${apiKey}`, 'X-Requested-With': 'XMLHttpRequest' },
}).then((r) => r.json());

// 2. Present it as the subprotocol; the server echoes it back on accept.
const ws = new WebSocket(`ws://localhost:8200/ws/jobs/${jobId}/events`, [`embry0.token.${token}`]);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // every event: { type, timestamp, event_seq, ...fields }
};
```

Every event carries `type` and `timestamp`; the orchestrator stamps `job_id` and `event_seq` (the replay cursor, also accepted as `?since_seq=`). Sandbox and node events carry `run_id` (one agent run), `node` (graph node id), `agent` (agent type) and, on subagent events, `parent_tool_use_id`. The table is generated from `embry0/execution/events.py` (`python -m embry0.execution.events --render-markdown`); `docs/contracts/events.json` is the same contract as data.

The route closes with code 1000 after forwarding `job_finished`, or straight after the replay when the job is already finished (a terminal row, or one whose PR was since merged or closed): the stream is over and a client must not reconnect. A client keeps the highest `event_seq` it processed as its `?since_seq=` cursor for the next connect, and treats a live event whose `prev_seq` is greater than that cursor as a gap (this job had a persisted event the client never received) to fill from `GET /jobs/{id}/logs/events?since_seq=<cursor>` before applying it. A larger `event_seq` alone is not a gap: ids are `job_logs.id`, shared by every job.

<!-- events:begin -->
| Type | Required fields | Optional fields |
|---|---|---|
| `agent_started` | `max_turns`, `model`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `turn_start` | `model`, `timestamp`, `turn_number`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `turn_usage` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `input_tokens`, `model`, `output_tokens`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `text` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `thinking` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `tool_call` | `summary`, `timestamp`, `tool_id`, `tool_input`, `tool_name`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `redacted`, `run_id` |
| `tool_result` | `content`, `is_error`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `redacted`, `run_id`, `truncated` |
| `subagent_start` | `name`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `subagent_end` | `is_error`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `context_compacted` | `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `pre_tokens`, `prev_seq`, `run_id` |
| `agent_ask_user` | `category`, `importance`, `options`, `question`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `suggested_answer` |
| `cost_update` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `duration_api_ms`, `duration_ms`, `num_turns`, `timestamp`, `tokens_in`, `tokens_out`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `agent_completed` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `duration_ms`, `error_code`, `error_message`, `input_tokens`, `is_error`, `output`, `output_tokens`, `policy_denials`, `timestamp`, `tools_called`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `error` | `message`, `timestamp`, `type` | `agent`, `code`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `tool_name` |
| `node_started` | `attempt`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `round`, `run_id` |
| `node_completed` | `attempt`, `timestamp`, `type` | `action`, `agent`, `deliverables`, `error`, `event_seq`, `job_id`, `job_kind`, `node`, `ok`, `parent_tool_use_id`, `prev_seq`, `round`, `run_id`, `template` |
| `pipeline_graph` | `graph`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `progress` | `message`, `timestamp`, `type` | `agent`, `app`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `interrupt` | `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `questions`, `reason`, `run_id`, `summary` |
| `agent_paused_for_input` | `count`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `review_decision` | `decision`, `summary`, `timestamp`, `type` | `agent`, `duplicates_open_pr`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `validation_result` | `timestamp`, `type`, `validation` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `verification_result` | `blocking`, `gate_verdict`, `tier`, `timestamp`, `type` | `agent`, `browser_qa`, `event_seq`, `human_merge_only`, `job_id`, `mode`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `pr_created` | `branch`, `pr_url`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_apps_resolved` | `apps`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_started` | `app`, `target`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_finished` | `app`, `duration_ms`, `status`, `timestamp`, `type` | `agent`, `event_seq`, `failure_summary`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_status` | `app`, `status`, `timestamp`, `type` | `agent`, `boot_phase`, `duration_ms`, `event_seq`, `failure_summary`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_agent_transient_retry` | `app`, `reason`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `system` | `message`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `job_failed` | `kind`, `message`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `job_finished` | `error_code`, `error_message`, `pr_url`, `status`, `timestamp`, `total_cost_usd`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
<!-- events:end -->

## Healing cases

- `GET /api/v1/cases` and `GET /api/v1/cases/{case_id}` — the ledger's read API (#278): one case per finding, from detection to a healed verdict. See `docs/glossary.md`, "Healing case".
  - `?awaiting_decision=true` is "what is waiting for a person": watcher drafts with a ticket that nobody has accepted or rejected — the list the dashboard shows as **Proposals** above the issue list. It is the proposal cap's own rule without the age window, so a draft that aged out of the cap is still listed, and a draft whose ticket was finished or deleted in its tracker is not. It also lists a finding a `not_healed` verdict sent back for a retry nobody has accepted yet (#658), which holds no slot of the cap, and an organization need waiting for a person (origin `org`, [ADR 0011](adr/0011-organization-needs.md)), which holds none either and is answered on the needs list, never accepted. `?open_only=true` is a different question (every case not yet `closed`, which includes alerts, findings with no ticket, and merged, deployed and verified cases); `?stage=tracked` is not it either (a person's ticket passes through `tracked`, and a draft whose ticket is gone stays there).
- `POST /api/v1/cases/{case_id}/accept` — accept a watcher finding and dispatch the pipeline on its draft, exactly as adding the trigger label would; refuses anything that is not a `tracked` watcher finding of an enabled watcher stack of this orchestrator (#378). A GitHub draft is dispatched directly. A Linear draft gets the trigger label put on the ticket and is dispatched through the Linear trigger path (#460); a refusal says what happened to the ticket, and a ticket whose Linear project resolves to another repo than the case's is refused untouched. A GitLab draft is still accepted in GitLab (`409`). A finding a `not_healed` verdict sent back is accepted here whatever its tracker: the retry is a new job on the issue of the failed attempt, which is reopened if its merge closed it, and the ticket is not touched (#658). Body: `{"accepted_by": "<name>"}`. `202` always carries a `job_id`. On the merge-scope allowlist. See `docs/self-watching.md`, "Accepting a finding without the label".
- `POST /api/v1/cases/{case_id}/close` — settle a case a person has decided: `{"resolution_path": "out_of_band", "note": "…", "closed_by": "<name>"}`, with `resolution_path` required unless the case already carries one; `409` on a closed case. A case whose origin is `org` is an organization need, and closing it dismisses the need, so it needs the admin tier, as `POST /api/v1/orgs/{owner}/needs/{need_id}/dismiss` does; any other case is operator tier.
