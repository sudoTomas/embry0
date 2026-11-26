# Changelog

All notable changes to embry0 are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version
numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) as
[docs/releasing.md](docs/releasing.md) applies it to embry0.

## [Unreleased]

Nothing yet. Until `v0.1.0` is tagged, everything on `main` is part of 0.1.0.

## [0.1.0] - not yet tagged

The first public release. Nothing was released before it, so this entry
describes what embry0 does rather than listing changes.

### Added

- **Issue-to-PR pipeline.** A triage agent plans each job, a developer agent
  writes the code and opens the pull request, and a reviewer agent gates it.
  Orchestration runs on LangGraph and the agents on the Claude Agent SDK, with
  grok models available through xAI ([docs/providers.md](docs/providers.md)).
- **QA gate.** Optionally boots the target application's compose stack inside
  Docker-in-Docker and checks its acceptance criteria in a headless browser,
  with Playwright or vibium. Screenshots, console, network and container logs
  are kept as evidence. Each repository's QA config (`qa.yaml` v2) is stored in
  embry0's database ([docs/qa-yaml-reference.md](docs/qa-yaml-reference.md)).
- **Sandboxes and credential proxies.** Every job runs in its own Docker
  container. Forge tokens stay in proxies that each sandbox enrolls with
  separately, and git reaches only the job's repository.
  [SECURITY.md](SECURITY.md) is the threat model, including what it does not
  protect.
- **Triggers.** GitHub and GitLab webhooks, a Linear label, the REST API and
  the dashboard ([docs/webhooks.md](docs/webhooks.md)).
- **Questions to a human.** An agent can pause a job to ask; the answer can come
  from the dashboard, Telegram or a GitHub comment.
- **Dashboard.** A React app with an issue tracker that syncs both ways with
  GitHub, and live agent activity, costs and QA evidence over WebSocket. SSO is
  optional ([docs/sso.md](docs/sso.md)).
- **Budget caps** per job, per day and per month.
- **Credential registry** for forge tokens and LLM accounts
  ([docs/credentials.md](docs/credentials.md)).
- **Log watcher and merge approvals.** The watcher turns faults in a
  deployment's logs into draft issues ([docs/self-watching.md](docs/self-watching.md));
  merge approvals ask a human in a chat before embry0 merges a pull request it
  opened ([docs/merge-approvals.md](docs/merge-approvals.md)).
- **Specialist agents.** Nine read-only built-in agents, each run only when
  a job names its pipeline template, that return one report on a repository:
  kintsugi, gardener, interrogator, why, how, blast-radius, comment-sicko,
  design-auditor and context-auditor
  ([docs/specialist-agents.md](docs/specialist-agents.md)).
- **OpenAI-compatible providers.** Agents can run on OpenAI, OpenRouter,
  Gemini, Groq, DeepSeek or a model server of the operator's own (Ollama,
  vLLM, LM Studio) through embry0's own tool loop over chat completions. Each
  is off until the provider catalogue turns it on
  ([docs/providers.md](docs/providers.md#openai-compatible-providers)).
- **Provider catalogue.** The providers, their models and dated prices are a
  document embry0 ships, and a deployment's overlay of it, changed at runtime
  with a dry run, history and undo; a change may not take out a model that
  something names ([docs/providers.md](docs/providers.md#changing-the-catalogue),
  [ADR 0031](docs/adr/0031-provider-catalogue.md)).
- **MCP server** (`embry0-mcp`, and `embry0-mcp-http` for the hosted endpoint).
- **Claude Code plugin** ([plugins/embry0](plugins/embry0/README.md)), with this
  repository as its marketplace: it stands up a deployment, connects to its MCP
  server with the URL and key asked in the plugin dialog, and sets it up from the
  chat window ([docs/claude-code.md](docs/claude-code.md)).
- **Code retention.** `JOB_LOG_RETENTION_DAYS` removes the code from ended
  jobs' events after that many days; `AGENT_SESSION_RETENTION_DAYS` deletes
  ended jobs' saved agent sessions (and with them the resume of a pull request
  comment follow-up); `QA_IMAGE_RETENTION_DAYS` removes pre-built QA images and
  per-repo volumes nothing has used for that long. All three are off by
  default, and an admin can preview each first ([SECURITY.md](SECURITY.md#data-retention),
  [ADR 0017](docs/adr/0017-code-retention.md)).

### Upgrade notes

- **Reserved names.** The specialist agents reserve nine agent names
  (`kintsugi`, `gardener`, `interrogator`, `why`, `how`, `blast-radius`,
  `comment-sicko`, `design-auditor`, `context-auditor`) and nine pipeline
  template names (`kintsugi-trace`, `garden`, `interrogate`, `why`, `how`,
  `blast-radius`, `comment-pass`, `design-audit`, `context-audit`). The
  template seeder upserts by name, so a deployment's own template with one of
  those names is overwritten at the first start: duplicate it under another
  name before you upgrade. A custom
  agent with one of those names is kept, and its specialist template runs it
  in place of the built-in
  ([reserved names](docs/specialist-agents.md#reserved-names)).
- **Reserved environment namespace.** A repository environment variable that
  starts with `EMBRY0_LLM_` is refused, and a stored one is no longer
  injected: the OpenAI-compatible providers' keys and base URL use that
  namespace in the sandbox. Rename such a variable before you upgrade.
- **The custom provider has one route.** Every sandbox on the `custom`
  provider reaches its own credential's server through the `local-llm-proxy`,
  and no model server's host is an egress exception any more
  ([ADR 0032](docs/adr/0032-custom-provider-through-the-local-llm-proxy.md)).
  `LOCAL_LLM_UPSTREAM` and `LOCAL_LLM_API_KEY` route nothing: run
  `embry0 credentials import-env` after the upgrade, which copies them into
  the environment's custom credential (`custom/api-key`; pass `replace=true`
  when that credential already names another server), then remove them. Every
  other stored custom credential keeps its own address and key, so one that
  carried a placeholder address while `LOCAL_LLM_UPSTREAM` stood in for it
  must be pointed at its server. Startup logs `custom_credential_check_failed`
  for each stored custom credential whose server cannot be reached or refuses
  its key, and `settings_env_retired` while either variable is set
  ([docs/providers.md](docs/providers.md#local-models)). The proxy image must
  be rebuilt. A paused custom job whose sandbox was created with the key or
  the server's address in its env is not resumed
  (`ERR_SANDBOX_ROUTE_RETIRED`): dispatch it again. A custom credential may
  not name this machine or a link-local address, in any spelling or by a name
  that resolves there; one stored that way is refused when a job or a call uses
  it.
- **Policy variables are no longer read.** The policy a feature read from
  environment variables before it moved into a document stored in the database
  (the settings document's sections, the provider catalogue) is set there only:
  `OPENAI_COMPATIBLE_PROVIDERS`, `OPENROUTER_CATALOG_REFRESH_HOURS`, the budget
  caps, `LEADS_*`, the scout's, the asks' and the briefs' policy and models,
  the Slop-O-Meter's pace and repository lists, and the others listed in
  [docs/configuration.md](docs/configuration.md#variables-no-longer-read). No
  start imports them any more. While one is still set, startup logs
  `settings_env_retired` naming it and `embry0 config` says where its setting
  lives now: set it there, then remove the variable from `.env`.
- **Upgrading from before the settings document.** The imports also carried
  state no variable names: a saved `budget_config` row's caps, the repositories
  the scout and the Slop-O-Meter read before their enrollment was explicit, and
  leads running by default on a deployment that existed before. A deployment
  that has not started the last release carrying the imports (`main` just
  before #168 retired them) must start it once before this one; otherwise
  its caps read as the section's defaults, nothing is enrolled and leads are
  off. Startup logs `settings_upgrade_skipped` and writes
  `settings.upgrade.flagged` when it finds such state never imported and not
  set by hand since in the deployment's document or an organization's layer,
  and the meter removes nothing until `background.slop_meter` is set there
  ([docs/configuration.md](docs/configuration.md#variables-no-longer-read)).
- **A repository's old areas file is no longer read.** The builds that moved
  a repository's areas into embry0 (migration 145), every build of main from
  commit `45d07a6f` (#458) to the commit before pull request #706, imported
  each registered repository's `.embry0/areas.yml` once; that import is gone,
  and embry0 reads no configuration of its own from a repository. A
  deployment that has not started one of those builds must start one once
  before this one, or write or map those repositories' areas; a repository
  whose read failed 24 times needs its areas written or mapped. Until then,
  startup logs `areas_import_skipped` at error level, counting the
  repositories still due a read and those given up on, and writes
  `repo.areas_import_skipped`. The file may be deleted
  ([docs/areas.md](docs/areas.md#the-old-file)).
