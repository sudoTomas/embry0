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
  is off until `OPENAI_COMPATIBLE_PROVIDERS` names it
  ([docs/providers.md](docs/providers.md#openai-compatible-providers)).
- **MCP server** (`embry0-mcp`, and `embry0-mcp-http` for the hosted endpoint).
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
