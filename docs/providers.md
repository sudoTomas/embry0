# Model providers (EMB-36)

embry0's agent runtime is the Claude Code CLI (via `claude-agent-sdk`),
which honors `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` — so any
Anthropic-SDK-compatible backend is **provider configuration**, not a new
runtime. MCP servers (the QA browser backends — Playwright/Vibium), the Ring-3 safety hook, session
resume, and token/trace plumbing all apply unchanged.

A provider that speaks only OpenAI chat completions (OpenAI, OpenRouter,
Gemini, Groq, DeepSeek, or a model server of your own such as Ollama, vLLM or
LM Studio) runs on embry0's own tool loop instead, off until the deployment
enables it: see [OpenAI-compatible providers](#openai-compatible-providers).

## Registry

`embry0/agents/providers.py`. Routing is per-agent by model id: when a
resolved model matches a provider prefix (`grok-*` → xAI), the in-sandbox
executor overlays `{ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY}` on the CLI
subprocess for that agent only (`ClaudeAgentOptions.env`). Other agents
in the same job keep their normal Anthropic auth.

| Provider | base_url | account | models |
|---|---|---|---|
| xai | `https://api.x.ai` | credential registry, then `XAI_API_KEY` | `grok-4.5`, `grok-4.7` (each $2/M in, $6/M out) |

A model has to be in this catalogue to be offered by `GET /config/models`
(so by the dashboard's model pickers), to be accepted as the default model,
and to be priced. An unlisted `grok-*` id would still route to xAI by prefix,
but xAI reports no cost, so the price table is the only accounting for it. An
id that routes to a provider without a price entry is therefore refused:

- with a 422 wherever a model is configured: a job's `agent_models`, an agent
  definition's `model`, a pipeline template's `agent_models` or step
  `config.model`, and the default and tier models in `PUT /config/provider`;
- with `ERR_INVALID_CONFIG` when an agent's model resolves to one anyway (a
  `MODEL_HEAVY`-style env value, triage's pick, a row saved before the check),
  before the sandbox runs it;
- and, if a run on one still happens, the direct executor records the run as
  an `ERR_INVALID_CONFIG` error naming the model instead of a $0 cost.

To bring a new grok model into service, add it to `models` and
`pricing_usd_per_mtok` together. What the flat rates leave out (long-context
and cached-input pricing) is listed under the limits below.

The account is a row in the credential registry (`docs/credentials.md`): an xAI `llm_api_key` is injected as `XAI_API_KEY`, and an xAI `llm_oauth` is served by the xai-proxy for that credential id. `XAI_API_KEY` in the orchestrator `.env` is the resolver's last step, and an import source, not the only place a grok job can authenticate.

## OpenAI-compatible providers

Most model providers, and every self-hosted model server, speak OpenAI chat
completions rather than the Anthropic API. embry0 treats them as one family
(`OPENAI_COMPATIBLE` in `embry0/agents/providers.py`). An agent whose model
routes to one runs on `OpenAICompatibleExecutor`
(`embry0/agents/executor_openai.py`): the tool loop the direct xAI executor
uses (`embry0/agents/_tool_loop.py`), over `POST <API root>/chat/completions`.
The Claude CLI is not involved, so the CLI's MCP, hooks and session resume are
replaced by the loop's own: builtin tools and MCP stdio servers, every call
gated by `gate_tool_call` in the sandbox, and the text-transcript resume.

**Off by default.** `OPENAI_COMPATIBLE_PROVIDERS` in the root `.env` names the
providers this deployment offers, comma-separated, for example
`OPENAI_COMPATIBLE_PROVIDERS=openai,deepseek,custom`. A provider it does not
name routes no model, is listed on no catalog surface (`GET /config/models`,
the LLM Providers page, `GET /agents`), and takes no credential. With the
setting unset every model routes as before. The orchestrator reads it per
request; the sandbox does not need it.

| Provider | Model ids | API root | Key (env fallback) | Listed models, $/Mtok in / out | Tool calling |
|---|---|---|---|---|---|
| `openai` | `gpt-…` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-6-luna` 0.10 / 0.50, `gpt-6.1-sol` 2.00 / 10.00 | `gpt-6-luna` yes (embry0 sends `reasoning_effort: none`, which OpenAI requires for tools on chat completions); `gpt-6.1-sol` **chat-only** |
| `openrouter` | `openrouter/<vendor>/<model>` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | `openrouter/openai/gpt-6.1-sol` 2.00 / 10.00 | per model: OpenRouter lists the ones that take tools at `openrouter.ai/models?supported_parameters=tools` |
| `google` | `gemini-…` | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-3.8-flash` 0.75 / 3.75, `gemini-4-argon` 2.00 / 10.00 | yes: Gemini's OpenAI-compatible endpoint takes `tools` |
| `groq` | `groq/<model>` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` 0.15 / 0.60, `groq/openai/gpt-oss-20b` 0.075 / 0.30 | yes: Groq states every hosted model supports tool use |
| `deepseek` | `deepseek-…` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `deepseek-flash` 0.30 / 1.20, `deepseek-v4-pro` 1.32 / 3.96 | yes |
| `custom` | `custom/<model>` | the operator's: the credential's `metadata.base_url`, or `CUSTOM_LLM_BASE_URL` | `CUSTOM_LLM_API_KEY` | any model the server serves, recorded at $0 | what the server and model support (below) |

The `openrouter/`, `groq/` and `custom/` prefixes only route: embry0 strips
them, so the provider is asked for `openai/gpt-oss-120b`, or for the
`llama3.3:70b` of `custom/llama3.3:70b`. The other providers' own ids carry a
prefix of their own.

Prices are the providers' published list prices, read from their pricing
pages on 2026-10-18, and like the rest of this catalogue they are metadata,
not billing truth. What they leave out: Gemini's rates above are
introductory (`gemini-4-argon` becomes $4 / $20 on 2026-12-01,
`gemini-3.8-flash` $1.50 / $7.50 on 2027-01-01); DeepSeek bills half outside
its peak hours and the table carries the peak rate; OpenRouter passes the
upstream price through and charges a fee on credit purchases, which is not
modelled. A model of these providers runs only when it is listed with a price,
as for grok (#219); to bring another into service, add it to `models` and
`pricing_usd_per_mtok` together. A custom server bills nothing per token, so
any `custom/` model is priced at $0 instead of refused, and a job's
`budget_usd` does not bound it. A `custom/` id is not listed, so it cannot be
the default model; an agent definition or a job's `agent_models` can name it.

### Adding a key

On the dashboard, an admin adds or replaces a provider's key on its card on the
LLM Providers page (**Add key** / **Replace key**): the key is stored in the
credential registry and becomes the provider's global binding, in place of the
global key it had, so every repository without a binding of its own uses it.
The steps below are the same by hand.

1. Name the provider in `OPENAI_COMPATIBLE_PROVIDERS` and restart the
   orchestrator.
2. Store the key in the [credential registry](credentials.md) and bind it,
   at global scope for every repository or at repo or owner scope:

   ```bash
   curl -X POST "$EMBRY0/api/v1/credentials" -H 'X-Requested-With: XMLHttpRequest' \
     -H 'Content-Type: application/json' \
     -d '{"kind": "llm_api_key", "provider": "deepseek", "label": "api-key", "secret": "sk-..."}'
   curl -X PUT "$EMBRY0/api/v1/credentials/bindings" -H 'X-Requested-With: XMLHttpRequest' \
     -H 'Content-Type: application/json' \
     -d '{"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": "cred-...", "role": "llm"}]}'
   ```

   A `custom` key also carries its API root:
   `"metadata": {"base_url": "http://10.0.0.5:11434/v1"}` (http or https, no
   credentials, query or fragment in the URL); the credential's host is that
   URL's. A server that checks no key (Ollama, LM Studio, vLLM started
   without `--api-key`) still needs one stored: any placeholder will do.
   These providers take `llm_api_key` only.
3. Or put the key in the root `.env` under the variable in the table (for
   `custom`, with `CUSTOM_LLM_BASE_URL`). That is the resolver's last step;
   `embry0 credentials import-env` moves it into the registry and binds it
   globally.

The LLM Providers page then shows the provider as available. **Test
connection** calls `GET <API root>/models` with the key a job with no
repository binding would get (the global pool, then the env); OpenRouter's
`/models` answers without a key, so for it the test calls `/key`. The
outcome is the provider's `last_probe`, read back from the audit log like
every other provider's. The dashboard's create-credential dialog
(Credentials, **Add credential**, kind **LLM API key**) offers every provider,
and for `custom` asks for the base URL; a provider this deployment has not
enabled is listed as not enabled and cannot be picked. A repository's
**LLM accounts** card binds an account at repo scope. It has a row for
each enabled provider the catalog marks available, and for any provider
the repository is bound to or resolves an account for, so neither is
hidden. It counts the other enabled providers in one line and names the
ones that are not enabled. Until the catalog answers it shows Anthropic
and xAI.

The sandbox receives the key as `EMBRY0_LLM_<PROVIDER>_API_KEY`
(`EMBRY0_LLM_OPENAI_API_KEY`, …, `EMBRY0_LLM_CUSTOM_API_KEY`) and a custom
root as `EMBRY0_LLM_CUSTOM_BASE_URL`, not under the usual names: a repository
that sets its own `OPENAI_API_KEY` for its app keeps it. The `EMBRY0_LLM_`
namespace is reserved, since a repository value there could swap the account
an agent bills or send its conversation to another server. Like every LLM
credential in a sandbox (ADR 0008), any code the job runs can read it: use a
key with a spending limit.

### What tool calling requires

An embry0 agent works through tools (Read, Write, Edit, Bash, Glob, Grep, and
for QA the browser's MCP tools). embry0 sends them as chat-completions
function tools; the model must answer with `tool_calls`, which the sandbox
runs after `gate_tool_call` and answers with `tool` messages until the model
stops calling tools. So a model runs an agent only when its provider serves it
with tool calling on this API.

A model a provider serves without it is **chat-only**: `chat_only_models` in
its catalogue entry, `"tool_calling": false` in `GET /config/models`, and
"(chat-only)" on the LLM Providers page. The resolver refuses it for an agent
that has tools, with `ERR_INVALID_CONFIG` before the sandbox runs, rather than
run the agent with its tools dropped; an agent with no tools can run on it.
`PUT /config/provider` answers 422 to a chat-only default or tier model, since
the agents those settings reach have tools.
Today that is `gpt-6.1-sol`: OpenAI serves it on chat completions without tool
calling and offers its tools only on the Responses API, which embry0 does not
speak.

For a `custom` server, tool calling is the server's and the model's: Ollama
needs a model with tool support, vLLM needs `--enable-auto-tool-choice` and a
`--tool-call-parser` for the model, LM Studio needs a model with tool use. A
server that ignores `tools` makes the agent answer in prose and stop; the run
ends with no tool calls and nothing done. A custom server usually sits on the
operator's network, which the sandbox egress drop blocks, so a job that runs
on one declares that host as an egress exception for its sandbox. The
exception is the whole address, every port: put the model server on a machine
that serves nothing else.

### Mechanics and limits

- **Not verified against the real APIs.** The executor's tests run it against
  a fake OpenAI-compatible HTTP server (`tests/fixtures/openai_compatible_server.py`).
  Run one developer and review loop per model before relying on it, as for grok.
- **Image rebuild.** The executor runs in the sandbox, so it takes effect after
  the sandbox images are rebuilt ([`docs/ops/deploy.md`](ops/deploy.md)).
- **Claude-sized budgets.** These providers declare no turn or time factor;
  how many turns their models need for embry0's agents is not measured.
- **Retries, no fallback.** 408, 409, 429 and 5xx answers are retried twice;
  then the run fails, 429 as `ERR_PROVIDER_RATE_LIMITED`, 401 and 403 as
  `ERR_AUTH_REJECTED`. A failing provider is not swapped for another.
- **Cached input** is read from `usage.prompt_tokens_details.cached_tokens` and
  priced at a tenth of the input rate, the estimate used for every provider.
- **xAI is a member of the family** for its readiness probe, the same
  `GET https://api.x.ai/v1/models`; its agents keep the runtime above (the
  Agent SDK over the Anthropic-compatible endpoint, or `DirectXaiExecutor`).
  Running grok on the chat-completions executor would change existing grok
  jobs, so it is not done here.

## Using grok on a job

```json
POST /api/v1/jobs
{"repo": "...", "task": "...", "agent_models": {"developer": "grok-4.7"}}
```

or with the `grok` label on a Linear ticket (every agent of that job runs
`grok-4.7`), or for every job with the default model below. `GET
/config/models` lists providers with an `available` flag (key configured or
not).

## Default model

`default_model` in the provider settings (Settings → LLM Providers →
"Default model", or `PUT /api/v1/config/provider`) is the model every agent
type runs on — triage, developer, review, QA, analysis, research, ops,
onboarding and the [specialist agents](specialist-agents.md) — unless something
more specific names another. Empty means no
default: each agent keeps the model of its own definition.

A model is resolved per agent run. Later wins:

1. the agent definition's `model`
2. `default_model`, when set
3. the pipeline template step's model (`config.model` on the step's node;
   templates have no separate model map)
4. the job's explicit `agent_models` (`POST /jobs`, the MCP `job_create`
   tool, a model label on the ticket)

Triage's own picks (`pipeline_config.agent_models`) sit between 3 and 4, and
count **only while no default is set**. With a default, triage is told not to
choose models and anything it names anyway is ignored
(`triage_model_ignored_default_model_set` in the log). Without one, triage
picks among the configured tiers: `model_heavy` for the developer,
`model_medium` for review, `model_light` for routine review.

Every step a template routes to applies its model, the QA step included. The
template's `triage` root is different: triage picks the template, so it runs
before one is chosen. A model on the triage node applies only when the job
names its template (`pipeline_template` on `POST /jobs`); a template that
triage picks, or a job kind's default, cannot change the model of the triage
that picked it.

After that order has produced a model, the enablement check still applies: a
disabled model falls back to the agent's tier model.

Rules on the setting itself:

- It must be a catalogue model. `PUT /config/provider` answers 422 to an
  unknown id, to a default that is disabled, and to disabling the current
  default.
- A stored value that is not in the catalogue — saved before this check
  existed, or coming from the `DEFAULT_MODEL` env var of a deployment whose
  provider settings were never saved — is ignored at run time and logged as
  `default_model_unknown_ignored`.
- `TRIAGE_MODEL` and `DEFAULT_MODEL` in the environment set the model of the
  triage agent's *definition* (level 1), so the default model outranks them.
- The default model's provider is resolved before the sandbox is created,
  like the models named by agent definitions and tiers.

Limits, stated rather than fixed:

- **No provider fallback.** If the default model's provider is down or
  rate-limited, the job fails with that provider's error; embry0 does not
  retry it on another provider.
- **Anthropic stays required.** The account pre-check resolves an Anthropic
  account for every job, default model or not.
- **Flat-rate cost.** The direct xAI executor prices a run at the flat
  per-token rate above. xAI doubles both rates for prompts of 200K tokens
  and more and bills cached input lower; neither is modelled, so a
  long-context run is under-reported and a cache-heavy run over-reported.
- **Not verified on grok-4.7.** The verification run below was done for
  `grok-4.5` only.

## Provider readiness

A model is served by the catalogue provider whose id prefix it carries:
`claude-` for Anthropic, `grok-` for xAI, and the prefixes of the enabled
[OpenAI-compatible providers](#openai-compatible-providers). Whether that
provider can run it depends on a credential: for Anthropic
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, for xAI `XAI_API_KEY` or the
OAuth proxy, for an OpenAI-compatible provider its key variable (`custom`
with `CUSTOM_LLM_BASE_URL`), or for any of them a row in the credential
registry. A provider with none is not ready, and a job whose model routes to
it fails with `ERR_CREDENTIAL_MISSING` before its sandbox exists. The runtime notices a missing credential only
when the agent runs, after the job was dispatched. Two surfaces answer before
that, from the same sources (`embry0/services/provider_readiness.py`):

- `GET /config/models` carries per provider `available`, `credential_source`,
  `model_prefixes` and `last_probe`. `last_probe` is the outcome of the newest
  "Test connection" run (`POST /config/providers/{name}/test`) as
  `{"status", "checked_at"}`, or `null` when none was run or retention pruned
  it. It is read back from the audit log entry the test writes
  (`provider_connection_tested`), so it survives a restart. A credential whose
  last test failed still counts as available: the two facts are reported side
  by side, not merged. The LLM Providers page shows all of them.
- `GET /agents` and `GET /agents/{type}` carry `provider` (the catalogue
  provider the agent's `model` routes to, `null` when no prefix matches) and
  `provider_ready` for every agent.
  The stored `model` is not always what runs: a default model replaces it,
  and a disabled one falls back to a tier model. So each agent also carries
  `effective_model`, the model a job that names none runs, resolved by the
  code the executor uses (`effective_agent_model` in
  `embry0/agents/resolver.py`), with `effective_model_source` (`agent`,
  `default_model`, `disabled_fallback`, `builtin_default` or
  `per_job_heavy_tier`), `effective_provider` and `effective_provider_ready`.
  A job's own models, a pipeline template's and triage's picks still apply on
  top of it. The `pr-reviewer` reports `per_job_heavy_tier`: every pr-review
  job names its model, the heavy tier (the provider_config `model_heavy`,
  then `MODEL_HEAVY`, then the built-in default), so neither its stored model
  nor the default model moves it. A repository's `pr_review.model` overrides
  the heavy tier for that repository's reviews.
- The Agents page's Model and Provider columns show the effective model, its
  provider and its readiness. When something other than the agent's own
  model chose it, a line under the model says what, and names the stored
  model when that differs. Against a backend without the `effective_*`
  fields, the columns show the stored ones.
- `GET /config/models` marks as `default` the provider that serves the
  default model when one is set, and Anthropic otherwise.
- The agent form names the provider of the chosen model, a custom id
  included, and whether it is ready; it matches by `model_prefixes`, the rule
  the backend routes by. The model picker offers every tier and catalogue
  model plus the free-text custom id. A model whose provider is not ready is
  labelled "(no credential)" and cannot be picked anew; an agent already
  saved on one opens on it, with the not-ready badge beside it. Readiness is
  what the API process sees, and credentials may be injected only where jobs
  run, so the picker marks a model rather than hiding it. Editing an agent,
  the form also says when jobs will not run the chosen model. The default
  model and a per-job tier replace any pick. A disabled-model stand-in is
  named only while the saved model is chosen, the model it was resolved for.

`AGENT_MODEL_PROVIDER_CHECK=true` turns the answer into a gate: creating or
updating an agent definition (`POST /agents`, `PUT /agents/{type}`, the MCP
`agent_update` tool) whose model has no ready provider is refused with a 422
that names the provider and the model, or says that no configured provider
serves the model. It runs after the price and enablement checks. Off, the
default, nothing changes: the save succeeds and the job fails at run time.

The gate reads the agent definition's own `model`. The default model and the
tier models, which can replace it at run time, are checked for catalogue
membership and price by `PUT /config/provider`, not for readiness.

## Turn and time budgets

Every agent's `max_turns` and `timeout_seconds` are sized for Claude. A
provider can scale both (`turn_budget_factor` and `timeout_factor` in
`embry0/agents/providers.py`). `resolve_agent_invocation` applies them once
the model is final, and the "Turn budget" advisory appended to the prompt
states the scaled number.

xAI doubles both. grok-4.7 makes about three times the tool calls of a
Claude developer run (median 111 vs 37) and takes about twice as long at
p90; on Claude-sized budgets half of its first developer runs died at the
turn cap. A grok developer therefore gets 80 turns and 3600 s, a review 50
turns and 600 s, triage 30 turns and 360 s. The job's `budget_usd` still
bounds spend.

A run that reaches its cap is reported as `ERR_AGENT_MAX_TURNS` by both
executors. That is what lets triage and review resume once to decide from
what they already found, and the developer node commit and push the
unfinished work instead of losing it.

## Mechanics & caveats

- The resolved account is injected into the **sandbox container env** at
  create (reserved key — user repo env can never set or override it) and
  read by the executor in-sandbox; it is never serialized through the
  invocation argv. No registry row and no `XAI_API_KEY` ⇒ the agent run
  fails closed before any spend.
- `api.x.ai` is public — the EMB-29 egress model (private-range
  blocklist) does not restrict it.
- Prompt caching on the compat layer is xAI's behavior, not Anthropic's:
  treat `cache_read_tokens`/`cache_creation_tokens` trace columns as
  best-effort on grok runs (usage parsing is tolerant; absent fields
  record 0).
- `claude_max`/OAuth stays Anthropic-native — grok agents run under the
  API-key env overlay regardless of the job's auth mode.
- grok-4.5 is unavailable to EU API consoles (xAI restriction).
- **Verification spike before default-enabling** (per EMB-36): one full
  dev→review loop + one QA run on the grok model to confirm tool-use
  fidelity. Run it per model — before making one the default model too.
  Requires an xAI account in the credential registry, or `XAI_API_KEY`
  in the orchestrator `.env` as the fallback.

## SuperGrok OAuth path (EMB-45/EMB-46)

When `xai_proxy_enabled=true` the orchestrator runs an `xai-proxy`
sidecar that injects the rotating SuperGrok subscription bearer at
egress; the orchestrator owns the rotating refresh token in a
Fernet-encrypted store (seeded once from the Grok CLI's auth.json).
Sandboxes authenticate to the proxy with a per-sandbox bearer delivered
to `~/.embry0/xai_proxy_token` (`embry0/sandbox/xai_token.py`).

The orchestrator's token loop refreshes and pushes every registered xAI
account every 10 minutes, and reads the proxy's per-account upstream counts
every minute: 429 quarantines the account as rate limited, 401 as auth
rejected, and a stale-token 403 forces a refresh of that account. How the
other xAI 403s are read is in `docs/credentials.md`.

**Default grok runtime (EMB-46): the Agent SDK over the proxy.** When
the proxy is live, provider-routed grok agents run through the normal
`SdkAgentExecutor` with a per-agent env overlay on the CLI subprocess:
`ANTHROPIC_BASE_URL` = the proxy, `ANTHROPIC_AUTH_TOKEN` = the sandbox
bearer, and `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` stripped — no
Anthropic-subscription OAuth and no console key anywhere on the grok
path, while MCP (the QA browser backends), session resume, and the Ring-3 hook
stay native.

**Opt-in fallback: `XAI_DIRECT_EXECUTOR=true`** (orchestrator `.env`)
routes grok to `DirectXaiExecutor` (`embry0/agents/executor_xai.py`)
instead — an embry0-owned, CLI-free Messages-API tool loop with its own
builtin tools, `gate_tool_call` enforcement, token-based cost, and an
MCP stdio client (`embry0/agents/mcp_client.py`) for browser-backend parity (verified against both playwright-mcp and vibium 26.8.21).
Verified green E2E on 2026-07-22; kept as insurance against a CLI
release breaking on xAI's compat endpoint.

With the proxy down, grok agents fall back to the EMB-36 CLI +
`XAI_API_KEY` path above.

### What a key has spent

`GET /api/v1/config/providers/openrouter/usage` (read) answers what OpenRouter
says the key a job with no repository binding runs with has spent:
`{"provider", "status", "checked_at", "key": {"spend_today_usd",
"spend_week_usd", "spend_month_usd", "spend_total_usd", "limit_usd",
"limit_remaining_usd", "limit_reset", "limit_resets_at", "expires_at"},
"account": {"credits_usd", "used_usd", "remaining_usd"}}`, read from its `/key`
and `/credits` and kept five minutes (`?fresh=true` asks again). `account` is
`null` when the key may not read the account's credit; `status` is
`not_configured`, `unauthorized`, `unreachable` or `error` when there is no
reading. The OpenRouter card shows it. These are the provider's own figures,
unlike the list prices above; no other provider answers this with an ordinary
key, so the route is 404 for them.
