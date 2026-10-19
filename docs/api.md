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

## Skills

The skills agents load, and where they come from ([architecture](architecture.md#skills-store-and-mounts)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes an audit row (`skill_source.created`, `skill_source.updated`, `skill_source.synced`, `skill_source.deleted`, `inline_skill.written`, `inline_skill.deleted`).

A skill is named `<plugin_name>:<skill>`, for example `superpowers:writing-plans`: the plugin name from its source's `.claude-plugin/plugin.json` (the source id when there is none), then the skill's directory or frontmatter name. A source is `git` (a public repository pinned to a tag, branch or commit), `path` (a directory inside the orchestrator container) or `inline` (the SKILL.md documents written through `/skills/inline`, published as the builtin `local` source). Two sources are builtin: `superpowers` (git, pinned in code) and `local` (inline).

| Endpoint | Tier | Body and answer |
|---|---|---|
| `GET /api/v1/skills` | read | The catalog, sorted by name: `[{name, source_id, description, available, status}]`. `available` is true when the source is `ready` and enabled; `status` is the source's, or `disabled` |
| `GET /api/v1/skills/sources` | read | Every source row (`id`, `kind`, `url`, `ref`, `resolved_sha`, `plugin_name`, `skills`, `status`, `error`, `enabled`, `is_builtin`, `synced_at`, ...) plus `syncing` |
| `GET /api/v1/skills/sources/{id}` | read | One source; 404 when there is none |
| `POST /api/v1/skills/sources` | admin | `{id, kind, url, ref, enabled}`; 201 with the row in `pending`, and a sync starts in the background. 422 for a refused source (`id` not `^[a-z][a-z0-9-]{0,39}$`, a git url that is not `https://`, carries credentials, or names an IP literal or a local name, a relative path, any `credential_id`); 409 when the id exists |
| `PATCH /api/v1/skills/sources/{id}` | admin | `{ref?, enabled?}`; a `ref` change starts a sync. 403 for a `ref` on a builtin (its pin is in code); 422 for a `ref` on a non-git source |
| `POST /api/v1/skills/sources/{id}/sync` | admin | 202 `{"id", "status": "syncing"}`; 409 while a sync of that source runs |
| `DELETE /api/v1/skills/sources/{id}` | admin | Removes the row and its store directory. 403 for a builtin; 409 with `agents` and `templates` naming what still references its skills |
| `GET /api/v1/skills/inline` | read | The inline skill rows: `name`, `description`, `body`, `created_at`, `updated_at` |
| `PUT /api/v1/skills/inline/{name}` | admin | `{description, body}`, upsert; `body` is the SKILL.md content below the frontmatter, at most 64 KiB. Re-syncs `local`. 422 for a name not `^[a-z][a-z0-9-]{0,63}$` |
| `DELETE /api/v1/skills/inline/{name}` | admin | Re-syncs `local`. 409 with `agents` and `templates` while anything references `local:<name>` |

```bash
# Add a pinned public skill repository; it answers "pending" and syncs in the background
curl -X POST http://localhost:8200/api/v1/skills/sources \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"id": "team", "kind": "git", "url": "https://example.com/acme/skills.git", "ref": "v1.2.0"}'

# Write a skill in place; agents name it local:release-notes
curl -X PUT http://localhost:8200/api/v1/skills/inline/release-notes \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"description": "Write release notes from merged PRs", "body": "# Release notes\n\nList each merged PR ..."}'
```

A sync failure never fails the request that started it: the source shows `status: error` and the redacted message in `error`, and the tree it had stays in the store. A sync clones or copies into a staging directory and renames it into place, so a sandbox created mid-sync sees the old tree or the new one.

**Validation and effective skills.** `POST /agents`, `PUT /agents/{type}`, `POST /agents/{type}/skills/{name}` and template saves check only the names the write adds: every name on a create, and on an update only the names the stored agent or template does not have yet, so names already saved keep saving even while the catalog is empty. An added name no source knows is a 422 that names the closest known names. A known name whose source is pending, disabled or in error, or a name prefixed by such a source before its first sync lists it, is accepted, and the response lists it in `warnings`; these write responses always carry `warnings`, empty when there is nothing to say.

**Repository skills.** A name without a colon, for example `commit-standard`, is a skill the target repository ships under `.claude/skills`. The catalog cannot list those, so such a name is never checked, never warned about and never an orphan: every write accepts it, and it passes through to the agent's allowlist as written. List it whenever the agent's effective skills are non-empty and it should keep loading.

An agent runs with its *effective* skills: the definition's list, then the template node's delta (`config.skills`), then triage's (`pipeline_config.agent_skills[<type>]`). A delta is `{"add": [...], "remove": [...]}` with `remove` applied first, or `"remove": "*"` to drop everything before the adds; a bare list is the older replace form, read as `{"add": list, "remove": "*"}`. When the effective list is non-empty it is an allowlist, so a skill the target repository ships under `.claude/skills` loads only when it is listed too; when it is empty, the repository's own skills load unfiltered.

## Tools

`GET /api/v1/tools` (read) lists the tool names an agent definition can name, as `[{name, source, description}]`: the Claude Code builtins under `source: "builtin"` (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`, `Task`), each registered browser backend's MCP tools under `source: "mcp:<backend>"` (`embry0/workflows/qa/browser_backends.py`), and the log tool under `source: "mcp:context"`.

## Agents

Agent definitions: the model, tools, skills and prompt each agent type runs with, and how it looks on the dashboard. Reads need any authenticated principal; every mutation needs the `admin` tier.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/agents` | read |
| `GET /api/v1/agents/{type}` | read |
| `POST /api/v1/agents` | admin |
| `PUT /api/v1/agents/{type}` | admin |
| `DELETE /api/v1/agents/{type}` | admin; 403 for a builtin |
| `POST /api/v1/agents/{type}/reset` | admin |
| `POST /api/v1/agents/{type}/skills/{name}` | admin |
| `DELETE /api/v1/agents/{type}/skills/{name}` | admin |

**Identity.** Every definition carries four identity fields, which `POST` accepts, `PUT` changes and both reads return:

| Field | Default | Rule |
|---|---|---|
| `display_name` | `""` | At most 60 characters; empty shows the slug |
| `avatar` | `golem` | One of the roster ids |
| `color` | `slate` | One of the palette ids |
| `tagline` | `""` | At most 140 characters |

The roster, the palette and the builtins' own values are in [`docs/contracts/identity.json`](contracts/identity.json), written from `embry0/agents/identity.py`; an id outside them is a 422 that lists the allowed ones. These columns are not nullable, so a `PUT` that sends one as `null` is a 422. `POST /agents/{type}/reset` restores a builtin's identity with the rest of the row.

**One skill at a time.** `POST /agents/{type}/skills/{name}` adds one skill and `DELETE` removes one, each returning the agent; adding a skill it already has, or removing one it does not, changes nothing and writes no audit row. Otherwise each writes `agent.skill_added` or `agent.skill_removed`. An unknown name is a 422 naming the closest known ones; a name from a source that is not ready is added and listed in `warnings`. `GET /agents/{type}` also returns `orphan_skills`: the agent's catalog-shaped skills that no source lists (repository skills are never orphans). They keep running, and a write that keeps them is accepted; only adding a name no source knows is refused.

```bash
# Give the developer one more skill
curl -X POST http://localhost:8200/api/v1/agents/developer/skills/superpowers:writing-plans \
  -H "X-Requested-With: XMLHttpRequest"
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

Some keys are refused with a 422, matched case-insensitively. The same check drops them again at sandbox start, from stored rows and from profile `env_defaults`. These keys are the infrastructure credentials and proxy URLs, plus every variable that would redirect a sandbox's traffic or load code into its processes: proxy routing, TLS verification, the dynamic loader, `PATH`/`HOME`/`SHELL`, shell and interpreter startup hooks, and the `GIT_`, `CLAUDE_`, `BUN_` and `GROK_` namespaces. `NODE_OPTIONS` and the JVM option variables (`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`, `JAVA_OPTS`, `MAVEN_OPTS`, `GRADLE_OPTS`) are accepted, but only with memory flags. The authoritative list is `embry0/execution/auth_provider.py`. A 422 on these routes never echoes the submitted value.

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

`POST /api/v1/orgs/{owner}/discover` answers 202 and starts one discovery run for the owner; a request while a run is in progress, or for a paused or removed owner, answers 409, and an unknown owner 404. Poll `GET /api/v1/orgs/{owner}/runs` until the newest run is `done` or `error`. `GET /api/v1/orgs/{owner}/repos` returns one fact row per repository, replaced on each run, and `runs` keeps the history of every run with its progress and summary.

`GET /api/v1/orgs/{owner}/needs` is the needs list ([ADR 0011](adr/0011-organization-needs.md)): what embry0 still needs from a person to maintain the owner, derived from the newest finished discovery run, one need per kind and repository set. `dismiss` and `reopen` keep a person's answer across rediscovery. Each open need is also a healing case with origin `org`, listed by `GET /api/v1/cases?awaiting_decision=true` ([Healing cases](#healing-cases)); closing that case dismisses the need.

`PATCH /api/v1/orgs/{owner}` sets `display_name` and merges `settings` from a closed list (`auto_register`, a boolean, off by default); an unknown key or a wrong type answers 422. `GET` and `POST /api/v1/orgs/{owner}/webhook` inspect and install the organization's one GitHub webhook, and the receiver's gate handles deliveries for repositories nobody registered ([webhooks.md](webhooks.md#organization-webhooks-github), [ADR 0012](adr/0012-organization-webhook.md)).

`POST /api/v1/orgs/{owner}/actions` runs one action over repositories discovery saw under the owner. Body: `{"action": "register" | "hook" | "onboard" | "watch", "repos": ["acme/widgets"], "dry_run": false, "force": false}`. The response carries one result per repository: `{"repo", "outcome", "detail"}` with `outcome` one of `done`, `already` (the state exists), `skipped` (a reason the caller can fix) or `failed` (one repository's error, or a refusal from the path the action reuses; the rest still run). `repo` is the spelling the forge reported, whatever case was asked; a repository discovery has not seen, an archived one, or one under another owner is `skipped`. Every action reuses an existing path and is idempotent: a second identical call reports `already` for every repository the first one changed, and changes nothing. `dry_run` reports what would happen without side effects and writes no audit row; a real run writes one `organization.action` row with every repository's outcome, plus the rows the reused paths write. A real run with any `done` result starts a discovery run for the owner, the one `POST /orgs/{owner}/discover` starts, so the needs list catches up; the response's `rediscovery` is that run's id, or `null` when nothing changed, a dry run, or a run already in progress.

- `register` adds the repository to the repo registry under the organization's forge, the call `POST /repos` makes. A row under any case counts as `already`.
- `hook` creates a GitHub webhook on the repository posting to `WEBHOOK_PUBLIC_URL` + `/api/v1/webhook` with `GITHUB_WEBHOOK_SECRET`, for issues, issue comments and pull requests ([webhooks.md](webhooks.md#github)). It runs only when the owner's probe found the credential can manage webhooks, and it is skipped while `WEBHOOK_PUBLIC_URL` is unset or a loopback address, or the secret is empty. A hook in the forge's live listing whose URL starts with `WEBHOOK_PUBLIC_URL` is `already`; a hook on the dashboard's origin is not, since that vhost answers 404 on the webhook paths. GitHub only in this release.
- `onboard` starts the onboard job ([onboarding.md](onboarding.md)) on the repository's default branch through the function `POST /jobs {"pipeline": "onboard"}` uses, so it runs the same checks (the calling key's repository allowlist among them: a repository the key may not start work on is `failed` with `repo_not_allowed`) and writes the same `onboard_job_created` audit row. A repository that is not registered is skipped (the job would fail `ERR_REPO_UNREGISTERED`); a stored QA config or an onboard job in flight is `already`; a repository whose facts carry no boot hint is skipped. When the newest onboard job completed without storing a QA config, the repository is skipped with that job's id unless the body carries `"force": true`.
- `watch` adds a watcher stack for the repository, disabled, filing to the organization's forge, with a placeholder LogQL selector to edit before enabling. It stores through the function `PUT /watcher/stacks/{owner}/{name}` uses and writes its `watcher_stack_updated` audit row. A stack under any case counts as `already`.

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
`repo_not_allowed:`, whenever it would start or continue work on any other
repository:

- `POST /jobs` (every pipeline; a job with no repository, such as an `http`
  or `none` context, is refused too), `POST /jobs/{id}/resume` and
  `POST /jobs/{id}/inputs/{input_id}/answer`;
- `POST /issues` (an issue without a repository is refused),
  `PUT /issues/{id}` (the issue's repository and, when it changes, the new
  one), `POST /issues/{id}/triage`, `POST /issues/{id}/sync` and
  `POST /issues/{id}/inputs/{input_id}/answer`;
- `POST /cases/{id}/accept`, `POST /merge-approvals/{id}/decide`,
  `POST /watcher/run` (the stack ticked), `POST /qa/images/build` and
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

## Sandbox visibility

```bash
# List running sandbox containers (ops)
curl http://localhost:8200/api/v1/sandboxes/active \
  -H "X-Requested-With: XMLHttpRequest"
```

## WebSocket streaming

```javascript
const ws = new WebSocket('ws://localhost:8200/ws/jobs/{job_id}/events');
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
| `agent_started` | `max_turns`, `model`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `skill_sources` |
| `turn_start` | `model`, `timestamp`, `turn_number`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `turn_usage` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `input_tokens`, `model`, `output_tokens`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `text` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `thinking` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `tool_call` | `summary`, `timestamp`, `tool_id`, `tool_input`, `tool_name`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `tool_result` | `content`, `is_error`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
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
