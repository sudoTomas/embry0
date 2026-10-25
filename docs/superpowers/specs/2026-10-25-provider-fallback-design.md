# Provider fallback: when an agent's AI account is paused, run on the next ready one

**Why.** An agent runs on the model its definition, the default model, a template or the job names, and that model's provider serves it with whatever account the credential registry (or the environment) holds. When that account is paused (a spent usage window, a provider's capacity 429), every job fails with `ERR_PROVIDER_QUOTA_EXHAUSTED` or `ERR_PROVIDER_RATE_LIMITED` until the window turns over, although another provider's account is ready. Worse, a registry account that was imported from the environment stays in the environment too: once the registry row is quarantined the resolver falls through to the environment copy, the same spent token, and the next job fails the same way.

**What changes.** A deployment-wide fallback order of models. When a job's model has no usable account, the agent runs on the first model of the order whose provider has one, and the job says so.

## The setting

`provider_config.fallback_models` (migration 113), a JSON list of catalogue model ids, in order, for example `["grok-4.7", "openrouter/openai/gpt-6.1-sol"]`.

- **Default `[]`: off.** Nothing is substituted and every job runs exactly as before.
- Edited on the LLM Providers page ("Fallback order", under the tiers) and through `PUT /config/provider` (`fallback_models`), admin only like the rest of the provider settings.
- `PUT /config/provider` answers 422 to an id outside the catalogue, a routed model with no price (it would run unmetered), a chat-only model (most agents use tools), a disabled model, a duplicate, and to disabling a model the order names. Remove it from the order first, as with a tier model.
- The order names models, not providers, so the operator picks the model each provider falls back to. A model of the paused provider itself is skipped.

## When a provider counts as paused

The account pre-check (`_precheck_llm_accounts`, before any container exists) asks the resolver, for each provider the job resolves, whether it has a usable account: `CredentialResolver.llm_pause(repo, provider, job_id)`. It reads the same bindings `resolve_llm` walks (repo, owner, global) and records no use. A provider is paused when:

1. every registry account bound for the job is quarantined or disabled, and the environment holds no account for it, or holds the same secret as one of those accounts (the imported token); or
2. it has no account at all; or
3. an earlier run of this job on that provider ended with `ERR_PROVIDER_QUOTA_EXHAUSTED` or `ERR_PROVIDER_RATE_LIMITED` within the quarantine window for that code (`credential_quota_quarantine_seconds`, `credential_rate_limit_quarantine_seconds`). The account is only quarantined when the job finishes; this lets a resume, a restart's relaunch or a retry of the same job fall back before that.

The pause carries the reason (`quota_exhausted`, `rate_limited`, `auth_rejected`, `missing`, ...) and, when the provider named one, the time it ends (the earliest `quarantined_until`).

A provider the job pinned with **Run with** (a key a person chose) is never paused: it resolves as today and the job fails with `ERR_CREDENTIAL_UNAVAILABLE` if that key cannot run. A registry outage reads as "not paused" too, so a broken probe never moves a job off its provider.

## Substitution

**At the pre-check.** For each paused provider, the first model of the order that is still a catalogue model, priced, not chat-only, not disabled, of another provider, and whose provider is not paused. That provider is added to the job's provider set, so its key is in the sandbox. The paused provider stays in the set when it still resolves (the environment's spent copy, harmless), so an agent that may not move still finds today's account and fails as today; it is dropped when it does not resolve, which would only have failed the job. The plan rides in the graph's `configurable` as `model_fallbacks`, `{paused provider: {model, provider, reason, until}}`, rebuilt on every run and every resume, so a job goes back to its own model as soon as the account is usable.

**At the agent run.** `resolve_agent_invocation` takes the plan. After the model is resolved (definition, default, template, triage, job, then the disabled-model tier fallback) and before the price, chat-only, budget-scaling and account checks, a model whose provider is in the plan is replaced by the plan's model. Everything after applies to the model that runs: its price, its turn and time factors, its account check. A model the job names itself is never replaced: the job's `agent_models` (job create, a model label on the ticket) and Run with's model. The pr-reviewer's per-job model is the executor's (the repository's `pr_review.model` or the heavy tier), so it is replaced like a definition's model.

**Later in the same run.** An agent run that ended with an account code pauses that provider for the rest of the graph run: the agent node reads the job's outputs on state, and the next agent of the job on that provider runs on the first model of the order whose provider's key the sandbox holds. A provider whose key was chosen with Run with does not move. No retry loop is added; only the runs the graph makes anyway move.

## What is recorded

- **The trace**: `model` is the model that ran, as always; the new `traces.fallback` column (migration 113) holds `{"instead_of", "provider", "reason", "until"}` for a substituted run, `NULL` otherwise. `GET /traces` returns it.
- **A job event**: a `progress` event on the agent's node: "developer runs on grok-4.7 instead of claude-opus-5: the anthropic account is paused (quota_exhausted) until 2026-10-26 00:00 UTC."
- **The world**: the quest scroll (`GET /world/quests/{job_id}`) carries `fallbacks`, one entry per agent and substituted model, and the scroll shows them under the party.
- The log: `agent_model_provider_fallback`.

## Limits

- Budgets are unchanged: a substituted run counts against the job's cap like any run, and an unpriced model is refused at save and at run time.
- A stand-in is only a provider whose key is in the run's sandbox: a key cannot reach a container after it exists. The agent node reads the sandbox manager's record of the container's keys; a resume into a sandbox created before the pause keeps its old keys, so the plan moves to the next model whose key it holds, or the run stays on its own model. For a container created before an orchestrator restart there is no record, and the node trusts this run's pre-check. Adding every fallback provider's key to every sandbox was left out; it would put more keys in more sandboxes for a rare case.
- The quarantine itself is unchanged: the account is quarantined when the job finishes, as before.
- Anthropic stays in every job's provider set while it resolves, as before.
