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
| `openrouter` | `openrouter/<vendor>/<model>` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | every model of [OpenRouter's live list](#openrouters-live-model-list) at OpenRouter's price; `openrouter/openai/gpt-6.1-sol` 2.00 / 10.00 until the list is first read | yes: the list keeps only models that take tools |
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
`pricing_usd_per_mtok` together. OpenRouter's models and prices come from its
live list instead (below). A custom server bills nothing per token, so
any `custom/` model is priced at $0 instead of refused, and a job's
`budget_usd` does not bound it. A `custom/` id is not listed, so it cannot be
the default model; an agent definition or a job's `agent_models` can name it.

### OpenRouter's live model list

OpenRouter serves several hundred models, and its own list says which take
tools and at what price. embry0 reads `GET https://openrouter.ai/api/v1/models`
(with the key a job with no repository binding runs with, when there is one; the
list answers without), keeps what an agent can run, stores it in
`provider_catalogs` (migration 114) and puts it in force in place of the static
entry: the model selectors, the LLM Providers card, `GET /config/models`, the
prices a run is metered at, and every check that a model is known, priced and
calls tools all read it. A model OpenRouter stops listing stops being offered,
and a job, tier or fallback order that still names it is refused as unpriced.

What is kept: models whose `supported_parameters` include `tools`, at a price.
`pricing.prompt` and `pricing.completion` are USD per token and are kept per
million tokens; where an entry names more than one rate (`overrides` for long
prompts or hours of the day, a separate `internal_reasoning` rate), the highest
counts, so a job's budget never under-counts a run. What is left out, counted
by reason on the card: models without tool calling; `:batch` variants
(asynchronous); `:free` variants (rate-limited to a few requests a minute,
which a tool loop exceeds within one run); `:online` variants (a per-request
search fee the token price does not show); a negative price (OpenRouter's
routers, priced per request); a zero or missing price on a model that is not
free.

When it is read: at start when the stored list is older than
`OPENROUTER_CATALOG_REFRESH_HOURS` (24 by default), then on that interval while
`openrouter` is in `OPENAI_COMPATIBLE_PROVIDERS`, and on demand with **Read the
list now** on the OpenRouter card, which is
`POST /api/v1/config/providers/openrouter/catalog/refresh` (admin; returns
`{"source", "models", "fetched_at", "skipped", "last_error", "last_error_at",
"refresh_hours"}`). `0` turns the schedule off. A read that fails, or that
yields fewer than ten models, changes nothing in force: the last good list
stays and the error is shown beside it, and the loop tries again within the
hour. With no list ever read, the static entry above serves.

A run on a model of the list carries its rates to the sandbox
(`pricing_usd_per_mtok` in the runner config), since the sandbox has no copy of
the list. A sandbox image built before this reads no rates and ends a finished
run as "cost unknown"; the orchestrator then prices the run from its token
counts and keeps the outcome the agent reached, and logs
`catalog_model_priced_by_orchestrator` until the image is rebuilt.

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
operator's network, which the sandbox egress drop blocks. With
`LOCAL_LLM_UPSTREAM` set, sandboxes reach it through the local-llm-proxy and
nothing is opened ([Local models](#local-models)). Without it, a job that runs
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

- **Fallback only on a paused account.** If the default model's provider has
  no usable account (spent, rate-limited, missing), its agents run on the
  [fallback order](#provider-fallback) when one is set. A provider that is
  down or answers errors with a usable account is not a paused one: the job
  fails with that provider's error.
- **Anthropic stays required.** The account pre-check resolves an Anthropic
  account for every job, default model or not.
- **Flat-rate cost.** The direct xAI executor prices a run at the flat
  per-token rate above. xAI doubles both rates for prompts of 200K tokens
  and more and bills cached input lower; neither is modelled, so a
  long-context run is under-reported and a cache-heavy run over-reported.
- **Not verified on grok-4.7.** The verification run below was done for
  `grok-4.5` only.

## Provider fallback

`fallback_models` in the provider settings (Settings → LLM Providers →
"Fallback order", or `PUT /api/v1/config/provider`) is a list of catalogue
models, in order. Empty, the default, is off: nothing below happens. Set, an
agent whose model's provider has no usable account runs on the first model
of the list whose provider has one
([the design](superpowers/specs/2026-10-25-provider-fallback-design.md)).

A provider counts as paused for a job when, at the account pre-check (before
its sandbox exists, on every run and every resume):

- every registry account bound for the job (repository, owner, global) is
  quarantined or disabled, and the environment holds no account for it or
  holds the same secret as one of those accounts (an account imported into
  the registry keeps its environment copy, which would serve the spent token
  again); or it has no account at all;
- or an earlier run of the same job on it ended with
  `ERR_PROVIDER_QUOTA_EXHAUSTED` or `ERR_PROVIDER_RATE_LIMITED`, within the
  quarantine window for that code
  (`CREDENTIAL_QUOTA_QUARANTINE_SECONDS`,
  `CREDENTIAL_RATE_LIMIT_QUARANTINE_SECONDS`). The run that finds a
  registry account spent quarantines it at once
  ([Spent account waits](#spent-account-waits)); this covers an account the
  environment serves, which has no registry row to quarantine.

For each paused provider the pre-check picks the first model of the list
that is of another provider, still a catalogue model, priced, with tool
calling and enabled, and whose provider is not paused. That provider's key
is resolved with the job's others, so it is in the sandbox. The paused
provider stays in the sandbox while it still resolves. Within a run, an
agent run that ends on a spent or rate-limited account runs again at once on
the first model of the list whose provider's key the sandbox already holds,
and the job's next agents on that provider move there too. A stand-in is
always a provider whose key the run's sandbox holds: a resume into a sandbox
created before the pause keeps the keys it was created with, so it moves to
the next model whose key is there, or not at all. With nowhere to move, the
job waits for the account instead of failing
([Spent account waits](#spent-account-waits)).

What never moves:

- a model the job names itself: `agent_models` on `POST /jobs`, a model
  label on the ticket, the model chosen with Run with (the pr-reviewer's
  per-job model is the executor's, and moves);
- a provider whose key was chosen with Run with: that key runs. A key that
  is quarantined until a time makes the job wait for it; one that cannot
  run at all fails the job with `ERR_CREDENTIAL_UNAVAILABLE`.

A substituted run counts against the job's budget like any run. Each one is
recorded: the trace's `model` is the model that ran and its `fallback`
(`GET /api/v1/traces`) is `{"instead_of", "provider", "reason", "until"}`;
a `progress` event on the agent's node says "developer runs on grok-4.7
instead of claude-opus-5: the anthropic account is paused (quota_exhausted)
until ..."; the job report in the guild lists it under *Fallback models*; the
log says `agent_model_provider_fallback`.

`PUT /config/provider` answers 422 to a list with an unknown, unpriced,
chat-only, disabled or repeated id, and to disabling a model the list names.
A `custom/<model>` of the operator's own server is accepted while the custom
provider is enabled: no catalogue lists its models, and it is priced at $0
([Local models](#local-models)). Put it last, so the local server only takes
the work no other provider can.

## Spent account waits

A spent or rate-limited account never fails a job. The job moves to a ready
model of the fallback order, or it waits for the account's reset
([the design](superpowers/specs/2026-10-25-spent-account-waits-design.md)).

**What counts.** A pause with a known end:

- a registry account quarantined until a time;
- a run of the job that ended with `ERR_PROVIDER_QUOTA_EXHAUSTED` or
  `ERR_PROVIDER_RATE_LIMITED`: until the reset the provider named, capped at
  eight days, else the quarantine window for the code;
- the local model with every slot taken (`local_busy`): five minutes.

No key at all, a disabled key, a refused token and a provider this
deployment does not enable still fail the job, because waiting would not end
them.

**At once.** The run that finds its account spent quarantines it then, not
when the job ends, so the next job's pre-check already sees the pause. If
the fallback order has a model whose provider is ready and whose key the
sandbox holds, the same run runs again on it. A model or key a person chose
never moves. The node sees both attempts, the stand-in's last, and the spent
attempt's trace keeps its error.

**Waiting.** Where nothing ready can take the work, the job is parked:
`status` `paused`, `account_wait` on the job (migration 116) =
`{provider, reason, until, model, agent, chosen, watch, mode}`, `until` in
UTC.

- **Before it starts.** The account pre-check checks the job's main
  provider (the default model's, else the one most agent definitions name)
  and every provider a person chose for it. A spent one with no stand-in
  parks the job before any sandbox exists (`mode: relaunch`). A job that
  prefers its local model while a slot is free is not parked for its main
  provider, and neither is a job whose Run with names every agent's model.
- **Inside the job.** A run whose provider is spent with no stand-in,
  found either before it runs (it is then not started) or after it failed,
  parks the job:
  - in a pipeline whose graph resumes from a checkpoint (every pipeline but
    qa, onboard and pr-review), the graph stops inside that node, and the job
    is parked with `mode: resume`. Its sandbox stays, and the node runs
    again;
  - pr-review and onboard park from their `cleanup` node, once the sandbox
    is destroyed. Nothing has been posted to the forge or written to the
    config store or setup proposal by then. qa parks from `orchestrate_qa`,
    once every app's run has released its sandbox and before the results are
    stored or reported. These start again from the beginning
    (`mode: relaunch`): the job's checkpoint, sandbox and volumes are
    removed when it parks;
  - a qa run of any pipeline (the qa stage of issue-to-pr too) never parks
    from inside the fan-out. Each app's run leaves its wait on its result, and
    `orchestrate_qa` parks after them all, with the latest reset.

  A model the executor sets per job (the pr-reviewer's) is not one a person
  chose: it moves like any other.

Every minute the account-wait loop goes on with a parked job whose `until`
has passed. A wait read from the registry (`watch`) goes on earlier, as soon
as the registry says the account is usable again: a person cleared the
quarantine or added an account. A `resume` wait continues from the last
checkpoint, so the stopped node runs again; a `relaunch` wait starts the job
from its launch record. The pre-check runs first either way, so a job whose
account is still spent is parked again.

**What people see.** An `interrupt` event with reason `account_wait` and a
`system` event in words. The job page shows what the job waits for and when
it goes on, in the viewer's own time, with **Try now** and **Cancel**. In the
world the party is waiting, and the quest scroll says what for.

A qa job that fails with an app whose agent run the account refused (a
refused token, or a spent account that named no end) carries that account
code as its own `error_code`, not `ERR_QA_INCONCLUSIVE`.

**The watcher.** Before a tick pays for an analysis, it asks the same
question about the analysis job it would start. If the job would wait, the
tick records `skipped_account_paused`, starts no job and leaves the window
for the next tick. An analysis parked mid-run is cancelled, and the tick
records the same action.

## Local models

A model server on the operator's own hardware (vLLM, Ollama, LM Studio) runs
agents through the `custom` provider: any `custom/<model>` it serves, priced
at $0, with tool calling as the server and the model provide it
([what tool calling requires](#what-tool-calling-requires)). Such a server is
usually shared hardware on the operator's network, so three things keep it
safe to point embry0 at.

**The local-llm-proxy.** `LOCAL_LLM_UPSTREAM` names the server's API root
(`http://host:port/v1`). Set, the orchestrator starts `local-llm-proxy` on
`sandbox-restricted`, which forwards to that one root and nowhere else:

- only `POST /v1/chat/completions` and `GET /v1/models`; any other path is
  404 and never reaches the server;
- only for a sandbox enrolled with it, which is a sandbox whose job resolved
  the custom provider. Its bearer is checked and dropped there, and the
  server's own key (`LOCAL_LLM_API_KEY`, when it checks one) is added by the
  proxy, so neither the key nor the server's address is in the sandbox;
- a request over `LOCAL_LLM_MAX_REQUEST_BYTES` (8 MiB) is refused with 413.

No egress exception is declared for the server's machine, so a sandbox
cannot reach anything else on it. The custom provider still needs an account
(the registry or `CUSTOM_LLM_API_KEY` with `CUSTOM_LLM_BASE_URL`; for a
server that checks no key any placeholder will do): it is what makes the
provider ready, pinnable and quarantinable. With the proxy on, the account's
base URL is not used; every custom run goes to `LOCAL_LLM_UPSTREAM`.

**One run at a time.** `local_max_runs` in the provider settings (default 1,
at most 64) is how many agent runs embry0 sends the server at once. Each run
on the custom provider holds a slot, a row in `local_model_leases`, from just
before it starts until it ends, however it ends. A lease expires by itself
five minutes after the run's own time limit, so one left by a process that
died frees its slot. A run that has to use the local model (it is the
fallback, or the job named it) waits for a slot up to
`LOCAL_LLM_SLOT_WAIT_SECONDS` (900), saying so in a `progress` event. Then
the job is parked for five minutes and tries again
([Spent account waits](#spent-account-waits)).

**Proposal briefs on the local model.** The same `watcher_local_model` writes
each owner proposal's plain-words brief (title, problem, fix, size) unless
`PROPOSAL_BRIEF_MODEL` names another OpenAI-compatible model: a few a minute,
in the background, with thinking off and a JSON answer asked for. With
neither set, no briefs are written and the feed shows each proposal's
summary ([proposal briefs](superpowers/specs/2026-10-25-proposal-briefs-design.md)).

**Local-first for the watcher.** `watcher_local_model` in the provider
settings (`""`, the default, is off) is a `custom/<model>` the watcher's log
analyses run on first. The watcher stamps it on each analysis job
(`pipeline_config.local_first_model`) unless the tick runs with a model a
person chose. For each agent of such a job:

- with a slot free, the agent runs on the local model; a `progress` event
  says "analysis runs on the local model custom/... instead of
  claude-sonnet-5: this job prefers it while a slot is free";
- with every slot taken, it runs on its own model at once, and a `progress`
  event says so. It never waits;
- it is a preference, not a pin: an agent whose model the job names keeps
  it, and when the custom provider is paused or does not resolve for the
  job, the job runs as if it had none. The job's own models' keys stay in
  its sandbox, so the fall back to them is always there.

The trace's `model` is the model that ran, local or not.

**The context window.** A model on one card often has a small window. For a
custom model, the chat-completions executor reads its window from
`LOCAL_LLM_CONTEXT_TOKENS` when set, else from the server's `/models`
(`max_model_len`, `context_length` or `context_window`). Before each request
it estimates the conversation's tokens, kept honest by the server's own
`prompt_tokens`; over the window less room for the answer, it replaces the
oldest tool results with a one-line note, keeping the task and the last
messages whole. A conversation that still does not fit fails the run with
`ERR_CONTEXT_WINDOW_EXCEEDED`, as does a request any server refuses as over
its window, instead of being sent until the turn cap.

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

The proxy forwards a request of at most `MODEL_PROXY_MAX_REQUEST_BYTES`
(32 MiB; the auth-proxy takes the same). Every agent turn resends the whole
conversation, so a long run's requests reach megabytes: a 300k-token prompt
is more than 1 MiB of JSON, the web server's default the proxy used to keep.
A request over the cap gets a 413 `request_too_large`, and the run ends as
`ERR_CONTEXT_WINDOW_EXCEEDED`.

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
