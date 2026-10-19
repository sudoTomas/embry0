# Model providers (EMB-36)

embry0's agent runtime is the Claude Code CLI (via `claude-agent-sdk`),
which honors `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` — so any
Anthropic-SDK-compatible backend is **provider configuration**, not a new
runtime. MCP servers (the QA browser backends — Playwright/Vibium), the Ring-3 safety hook, session
resume, and token/trace plumbing all apply unchanged.

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
`claude-` for Anthropic, `grok-` for xAI. Whether that provider can run it
depends on a credential: for Anthropic `CLAUDE_CODE_OAUTH_TOKEN` or
`ANTHROPIC_API_KEY`, for xAI `XAI_API_KEY` or the OAuth proxy, or for either a
row in the credential registry. The runtime notices a missing credential only
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
  `provider_ready` for every agent. The Agents page shows them as a column.
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
- `GET /config/models` marks as `default` the provider that serves the
  default model when one is set, and Anthropic otherwise.
- The agent form names the provider of the chosen model, a custom id
  included, and whether it is ready; it matches by `model_prefixes`, the rule
  the backend routes by. The model picker offers every tier and catalogue
  model plus the free-text custom id. A model whose provider is not ready is
  labelled "(no credential)" and cannot be picked anew; an agent already
  saved on one opens on it, with the not-ready badge beside it. Readiness is
  what the API process sees, and credentials may be injected only where jobs
  run, so the picker marks a model rather than hiding it.

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
