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

The providers, their models and prices are the provider catalogue
([ADR 0031](adr/0031-provider-catalogue.md)): its schema is
`embry0/agents/catalogue.py`, and embry0 ships its values in
`embry0/agents/providers.yaml`, which `embry0/agents/providers.py` routes by.
A deployment changes it with an overlay of its own (below,
[Changing the catalogue](#changing-the-catalogue)).
Routing is per-agent by model id: when a
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

To bring a new grok model into service, add it with its `prices` under
`xai`'s `models` in the catalogue overlay. What the flat rates leave out (long-context
and cached-input pricing) is listed under the limits below.

The account is a row in the credential registry (`docs/credentials.md`): an xAI `llm_api_key` is injected as `XAI_API_KEY`, and an xAI `llm_oauth` is served by the xai-proxy for that credential id. `XAI_API_KEY` in the orchestrator `.env` is the resolver's last step, and an import source, not the only place a grok job can authenticate.

## OpenAI-compatible providers

Most model providers, and every self-hosted model server, speak OpenAI chat
completions rather than the Anthropic API. embry0 treats them as one family
(the catalogue's `chat_completions` providers, `OPENAI_COMPATIBLE` in `embry0/agents/providers.py`). An agent whose model
routes to one runs on `OpenAICompatibleExecutor`
(`embry0/agents/executor_openai.py`): the tool loop the direct xAI executor
uses (`embry0/agents/_tool_loop.py`), over `POST <API root>/chat/completions`.
The Claude CLI is not involved, so the CLI's MCP, hooks and session resume are
replaced by the loop's own: builtin tools and MCP stdio servers, every call
gated by `gate_tool_call` in the sandbox, and the text-transcript resume.

**Off by default.** The catalogue's `enabled` turns a provider on:
`providers: {deepseek: {enabled: true}}` in the overlay
([Changing the catalogue](#changing-the-catalogue)), in force at once, with no
restart. A provider that is off routes no model, is listed on no catalog
surface (`GET /config/models`, the LLM Providers page, `GET /agents`), and
takes no credential. The sandbox does not need it.

`OPENAI_COMPATIBLE_PROVIDERS` named them before the catalogue did; it is no
longer read.

| Provider | Model ids | API root | Key (env fallback) | Listed models, $/Mtok in / out | Tool calling |
|---|---|---|---|---|---|
| `openai` | `gpt-…` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-6-luna` 0.10 / 0.50, `gpt-6.1-sol` 2.00 / 10.00 | `gpt-6-luna` yes (embry0 sends `reasoning_effort: none`, which OpenAI requires for tools on chat completions); `gpt-6.1-sol` **chat-only** |
| `openrouter` | `openrouter/<vendor>/<model>` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | every model of [OpenRouter's live list](#openrouters-live-model-list) at OpenRouter's price; `openrouter/openai/gpt-6.1-sol` 2.00 / 10.00 until the list is first read | yes: the list keeps only models that take tools |
| `google` | `gemini-…` | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-3.8-flash` 0.75 / 3.75, `gemini-4-argon` 2.00 / 10.00 | yes: Gemini's OpenAI-compatible endpoint takes `tools` |
| `groq` | `groq/<model>` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` 0.15 / 0.60, `groq/openai/gpt-oss-20b` 0.075 / 0.30 | yes: Groq states every hosted model supports tool use |
| `deepseek` | `deepseek-…` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `deepseek-flash` 0.30 / 1.20, `deepseek-v4-pro` 1.32 / 3.96 | yes |
| `custom` | `custom/<model>` | the operator's: the credential's `metadata.base_url`, or `CUSTOM_LLM_BASE_URL` | `CUSTOM_LLM_API_KEY` | any model the server serves that the catalogue lists, at its prices or `free: true` ([below](#the-custom-providers-models)) | what the server and model support (below) |

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
as for grok (#219); to bring another into service, add it under its provider's
`models` in the catalogue overlay, with its `prices`. OpenRouter's models and prices come from its
live list instead (below). A `custom/` model too runs only once the
catalogue lists it, with its `prices`, or `free: true` for one on the
operator's own hardware ([The custom provider's models](#the-custom-providers-models)):
a custom server may be a paid gateway, which a job's `budget_usd` and the
background caps then bound.

### Gemini's thinking tokens

Google bills a Gemini model's thinking as output: its thinking documentation
says that when thinking is on, a response is priced as the sum of its output
tokens and its thinking tokens. Whether its OpenAI-compatible endpoint, the one
embry0 calls, counts those thinking tokens in `usage.completion_tokens` is
**unconfirmed** (#653). Google's page on OpenAI compatibility does not say, and
embry0 has not measured it. The endpoint may count them there, report them
apart (as OpenAI's own API does, in
`usage.completion_tokens_details.reasoning_tokens`), or not report them at all.

embry0 reads three fields of a chat completion's usage: `prompt_tokens`,
`completion_tokens` and `prompt_tokens_details.cached_tokens`. The agent
executor (`embry0/agents/executor_openai.py`) and the background features'
direct calls (`embry0/services/background_models.py`) read the same three. No
model proxy carries a chat-completions provider, so a Gemini run is charged
what its sandbox read (the `sandbox` row of
[Metering at the proxies](#metering-at-the-proxies)).

What that means for a Gemini run:

- If `completion_tokens` counts the thinking tokens, the run is metered as
  Google bills it.
- If it leaves them out, a run in which the model thinks is metered below what
  Google bills: by its thinking tokens, at the output rate. The gap grows with
  how much the model thinks, most with a high `reasoning_effort`, and the job's
  recorded spend, which its `budget_usd` is checked against, under-counts by as
  much.

Until that is settled, take a Gemini run's recorded cost as a lower bound when
its model thinks, and check it against Google's own billing.

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

When it is read: while the catalogue turns `openrouter` on, whenever the list in
force is older than OpenRouter's `refresh_hours` in the catalogue (24 as
shipped), and on demand with **Read the list now** on the OpenRouter card, which is
`POST /api/v1/config/providers/openrouter/catalog/refresh` (admin; returns
`{"source", "models", "fetched_at", "skipped", "last_error", "last_error_at",
"refresh_hours"}`). `0` turns the schedule off. The loop looks each minute
whether OpenRouter is on and at its interval, so a change to either applies
without a restart. `OPENROUTER_CATALOG_REFRESH_HOURS` set the interval before
the catalogue did; it is no longer read. A
read that fails, or that yields fewer than ten models, changes nothing in
force: the last good list stays and the error is shown beside it, and the loop
tries again within the hour. With no list ever read, the static entry above
serves.

A run on a model of the list carries its rates to the sandbox in its provider
record ([Pricing a run](#pricing-a-run)), since the sandbox has no copy of the
list. A sandbox image built before records reads the rates alone
(`pricing_usd_per_mtok`); one built before those ends a finished run as "cost
unknown", and the orchestrator then prices the run from its token counts and
keeps the outcome the agent reached, logging
`catalog_model_priced_by_orchestrator` until the image is rebuilt.

### Pricing a run

Each agent run carries its provider's record, resolved by the orchestrator from
the deployment's catalogue when the run starts: the provider's runtime, the id
its API knows the model by, the model's `request_extras`, its
`max_output_tokens` and, for a custom model, its `context_window`, the rates
in force
that day, the cache factors (a cached input token at 0.1 of the input rate, a
cache write at 1.25), the provider's address and the sandbox variable of its
key. The executors in the sandbox read the record, not a table of their own,
so a change of the catalogue reaches the next run with no image rebuild. The
run's trace keeps what it was priced with (`pricing` on `GET /traces`:
`{"provider", "model", "rates": {"in", "out"}, "cache": {"read", "write"},
"resolved_at", "charged_from"}`), so a run is never priced again at a later
rate.

What a run is charged:

| Provider | Charged | The catalogue's rates |
|---|---|---|
| Anthropic, on the Agent SDK | what its CLI reports (`total_cost_usd`, net of a resumed session's earlier runs) | estimate each turn's cost as it runs |
| any other on the Agent SDK (xAI's grok by default) | the catalogue's rates on the run's tokens, as the CLI's result counts them for every call it made (its `modelUsage`, a compaction's and the session title's calls included), less what a resumed session restored | the charge |
| a chat-completions provider, or grok's direct executor | the catalogue's rates on the run's tokens | the charge |

The CLI knows Anthropic's pricing better than flat rates do (long-context
rates, cache lifetimes, a model it maps to another), so its figure stands for
Anthropic and the catalogue's rates are an estimate there. For any other
provider the CLI prices each call by its own table of Claude models, which
knows nothing of the provider's: its figure is logged beside the charge
(`sdk_run_charged_from_catalogue`) and not counted. The tokens are not the
stream's: the CLI sends each content block of a response as its own message,
each with the usage the response started with (one output token), and the
calls a compaction or the session title make send no message at all. The
result's `modelUsage` counts them all; a resumed session's counts include its
earlier runs, which the session file's saved totals give, and those are taken
off. No count is charged below the result's `usage` (the main loop's calls),
so a `modelUsage` this release cannot read is never charged $0; a run that
ends with no result is charged the calls the stream reported, each
once, which undercounts their output. A model with no rates in
the record is never metered at $0: a chat-completions run of it ends as "cost
unknown", and the resolver refuses such a model before any run.

A sandbox image built before records reads its own table, and for a provider
other than Anthropic the rates alone when the orchestrator sends them (the
chat-completions providers and grok's direct executor are then charged as
above, an SDK run what its CLI reports). The records need the sandbox images
rebuilt (`docs/ops/deploy.md`).

### Metering at the proxies

A run whose provider's calls all pass a model proxy is charged what that proxy
counted, not what its sandbox reported: an xAI run through the `xai-proxy`
(the SuperGrok account, or an API key the proxy holds) and every
chat-completions provider's run (OpenAI, OpenRouter, Google, Groq, DeepSeek,
custom) through the `llm-proxy` (ADR 0033). Each proxy reads the usage of every answer it
forwards (`embry0/execution/proxy/metering.py`); the orchestrator declares each
run to the proxies its sandbox is enrolled with just before the run starts and
ends it there after, so the calls count under the run, never under a run the
sandbox names (`embry0/execution/run_meter.py`). It reads every proxy's tallies
every 5 seconds into `run_meter`, and once more before the proxies stop.

The charge is the run's provider record's rates, from the run's start, on the
proxy's count (`embry0/agents/run_charge.py`):

| The proxy's tally of the run | Charged |
|---|---|
| whole: declared, its end read, one proxy process all along | the proxy's count (`source: proxy`) |
| not whole: the proxy process changed during the run, the run's end could not be read, or the run was never declared there (its calls then counted under the sandbox's calls under no run: what those grew by during the run is the proxy's count, all of them when where they stood at its start could not be read) | the larger of the proxy's count and the sandbox's figure (`proxy_gap` or `sandbox_gap`) |
| complete, but declared on a proxy image that does not order requests (see below) | the larger of the two (`proxy_unordered` or `sandbox_unordered`) |
| at the llm-proxy, a tally that names another provider than the run's, or none (a proxy image from before) | the larger of the two (`proxy_gap` or `sandbox_gap`) |
| the run's provider is carried by no metering proxy (Anthropic) | the sandbox's figure, as above (`sandbox`) |
| the record has no rates | the sandbox's figure (`sandbox_unpriced`) |

**A run is held to its provider.** A provider's path at the llm-proxy takes no
call outside a run: before a sandbox's first run is declared, and after its run
ends, `/p/<provider>/v1` is refused (403, `run_not_declared`), so every call
there is some run's (the custom provider's `/v1` still forwards outside a run,
counted as the sandbox's calls under no run). The declaration names the run's
provider (its record's), and the proxy echoes it. While the run is current, the llm-proxy
refuses the sandbox's calls on any other provider's path, `/v1` included
(403, `provider_not_declared`), so a run's tally there holds its own
provider's calls, says which provider that is, and is priced at the run's
rates. The xai-proxy refuses a sandbox whose current run is on any provider but
xAI the same way, on every path: such a run is charged from its own provider's
proxy, so what it spent there would be counted under it and never charged. A
proxy that does not echo the provider (an image from before) is not taken as
whole for the run.

**A run is held to its model.** The declaration also names the ids the run's
model goes by: the one its provider knows it by (its record's upstream model,
which the chat-completions executor sends) and the one it was routed by (which
the Claude Code CLI and the direct xAI executor send), `qwen3.8-27b` and
`custom/qwen3.8-27b` for a custom model. Both metering proxies refuse a call of
the run naming any other model, or none, exactly as written (403,
`model_not_declared`, in the wire's shape): checked and charged at the run's
rates, a dearer model on the same route would cost more than either says. On a
provider's run the CLI's small, fast model, which it calls for its own
background work, and the `haiku`, `sonnet` and `opus` its subagents may ask for
are set to the run's model (`ANTHROPIC_SMALL_FAST_MODEL`,
`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_OPUS_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`), so none of its
calls names another. A fallback to another model is a new run, declared with
its own. A run with no provider record is held to no model, and a proxy that
does not echo the models (an image from before) is not taken as whole for the
run.

A cancelled run is ended on the proxies in the background, so its declaration
does not linger; what they counted is kept in `run_meter`, but the run is not
charged from it, and its trace stays `running` like that of a run a restart
interrupts (#694).

A call still in flight when a tally was last read is charged its ceiling, as
the proxy reported it: the request's size as input and its output limit, times
the answers a chat completion asks for, as output. So is a call whose answer broke off before
its usage was read: the upstream cut it, it could not be decoded, or the
sandbox went away while it came. The provider may have generated all of it, so
what was read is no measure: its output is that ceiling, and its input the
counts the answer did give (the request's size when it gave none). An answer
whose usage was read in full is charged that usage however it ended, and an error answer
still costs nothing. A streamed chat completion goes upstream asking for
exactly its final usage, which counts only from a chunk that finishes the
answer. A completion request the proxy cannot read the way every server would
is refused (400) and never forwarded: a body that is not one JSON object, a
top-level key named in two spellings that match once case is folded (some
servers match keys so, the last winning), or a `stream` neither true nor
false. A key named twice exactly, anywhere in the body, goes upstream once, with
the value the proxy read; the output limit is the larger of `max_tokens` and
`max_completion_tokens`, since a server may honour either, and must be a whole
number of tokens (`"1000000"` or `1e6` is refused: a server may read it as that
many). The ceiling assumes the server honours those two: a chat completion that
names another server's output limit, at its top level or in its `options`
(llama.cpp's `n_predict`, Ollama's `num_predict`, `max_new_tokens`,
`max_output_tokens`, `max_tokens_to_sample`, compared with case folded), is
refused. A call that names neither is taken to produce at most 128,000 tokens an
answer, which assumes the server stops by then: vLLM and llama.cpp, given no
limit, generate up to the model's context, so with a longer context such a call
can produce more than its ceiling. A chat completion asking for several
answers (`n`, llama.cpp's `n_cmpl` or older vLLM's `best_of`, compared with
case folded, each a positive whole number or refused) has its limit times the
largest as its ceiling; no server of the Messages API reads them, so they do
not count there. A call whose ceiling is above 10,000,000 tokens is refused on
both wires, so that a call charged its ceiling is charged a figure a trace can
hold.

A completion request carrying what its bytes do not bound is refused too, budget
or not (400, `unmetered_content`; on the Messages API an
`invalid_request_error` whose message starts with that code): the budget check
prices a call by its bytes and its output ceiling, and what a provider fetches
or runs for the call, or bills at a price of its own, is neither. Media goes
only as an image given inline, a chat completion's `image_url` part whose URL is
a `data:` URL or a Messages API `image` block of a `base64` source (in a
`tool_result` too), of a media type billed as an image (`image/png`,
`image/jpeg`, `image/gif`, `image/webp`; a PDF named an image is refused), each
held at 5,000 input tokens besides its bytes, more than a hosted provider bills
one at its largest. Refused: an image by URL or file, an audio, file or video
part, a document (PDFs bill by the page, inline too), OpenAI's
`web_search_options`, OpenRouter's `plugins`, its `:online` model ids, its
fallback `models`, its `provider` preferences and its `route` (another upstream,
at its own prices), xAI's `search_parameters`, `audio` and any `modalities` but
text, `prediction` (rejected prediction tokens bill as output), a `service_tier`
billed above the standard rates, Anthropic's `mcp_servers` and `container`, and
a tool the provider runs itself (a chat completion's tools are `function` or
`custom`, a Messages API tool has no type or `custom`). A chat completion may
carry no object-valued field beyond a known set that changes neither the model
nor its prices: `response_format`, `tool_choice`, `stream_options`,
`logit_bias`, `metadata`, `chat_template_kwargs` (vLLM's and SGLang's template
arguments), `options` (Ollama's and llama.cpp's settings, an output limit among
them refused) and an `extra_body` holding only Gemini's `thinking_config` (not
its `cached_content`, a server-side cache billed as input). A system prompt that
is not text, a message's content that is neither text nor a list of parts, and
messages or tools that are not a list are refused rather than passed over, and
so is a message, content part or tool naming a key in two spellings that match
once case is folded. The list is in one place, `_unmetered` in
`embry0/execution/proxy/metering.py`.
embry0's own agents send text, their tool conversation and screenshots inline,
none of it refused; a Claude Code agent whose tool result is a document (a PDF
it read) has the calls carrying it refused.
A trace's token counts are held to their columns' range as it is written, and a
count held is logged (`trace_count_held_to_column`): one past them would fail
the write and leave the trace `running`. A run whose calls counted under the
sandbox's calls under no run (it could not be declared) is given what those grew
by, each call in flight at its end counted at its ceiling; a call in flight at
its start is taken off only when the orchestrator charged an earlier run for it
(it remembers which, by the id the proxy gives each call in flight), so a call
is charged once across runs, and one that began between runs is charged to the
run it ends in. Where it cannot tell (after a restart, or from a proxy that
names no ids), the call is charged again: more, never less. A proxy process that
went away mid-run leaves its last read in `run_meter` with its calls in flight,
and the recreated proxy is given the run back after the sandbox's enrollment,
under a new process (`boot_id`), so the run's calls are the two tallies summed
and the charge is the larger figure.

**A run is held to its budget.** The orchestrator declares a run with the scopes
it draws on: its job's cap, and the deployment's daily and monthly caps, where
each is set ([Budget Controls](architecture.md#budget-controls)), each with what
it had left once every settled run (one whose trace holds its charge) was
charged, and with the run's rates (`embry0/orchestration/run_budget.py`). The
run meter keeps a ledger of the runs declared and not settled yet and turns each
scope into a purse at the proxy that serves the run's provider: the runs there
that draw on one share it as they go, the proxy counting each member's charges
and its calls in flight, so runs at the same time (a QA fan-out's apps, jobs
under one day's cap) never each spend what was left. A scope held at two proxies
at once is split between them, and a deployment purse takes no more than the job
purses at its proxy can spend, so one proxy never takes the whole day from
another. A run settles once its trace holds its charge: it leaves its purses,
and the proxy is given their figures worked out again; a run whose figures
cannot be worked out again leaves its purses lowered by its charge. The proxy
refuses a call that would take a purse past what it has left: 402,
`budget_exceeded`, in the wire's error shape, with the figures in its message,
and nothing is sent. The check prices a call's output ceiling (its limit times
its answers) in full, the part a call left to hang can inflate, and its input,
for one call of the run in flight at a time and up to the largest request of the
run read in full, at what the run's input has cost a request byte so far, cache
reads included; past that size, and for the run's other calls in flight at once,
at the uncached rate and one byte a token, the most a byte-level tokenizer
counts. One call at a time is the run's conversation; its parallel subagents,
mostly on small contexts, hold little at the uncached rate, and a purse can be
passed by no more than one call's input at its own cost a byte against that
rate. A broken-off call whose answer counted no input, or a call in flight at
the run's end, is charged no more input than the check held for it, so a purse
bounds what is recorded as well as what is spent. So `{"stream": true, "n": 78}`
naming no limit, about $60 of output at $6 per million, is refused while the job
has $10 left; twenty calls in flight at $1.20 of output each are let through
only as far as their ceilings fit; a Claude Code–style run, its conversation on
a 600 KB context and then three subagents at once on 40 KB ones, on a $2 cap, is
first refused past $1.30 charged; and calls hung up after a cached one, sixty at
once whose answers count their input uncached, 8 MiB requests after one of 600
KB, or large uncached requests after a small cached one, all stay within the
purse. A refusal is routed as crossing its limit is: for the job's cap, by the
job's overrun mode (`soft` pauses the job for a person, as a crossing does,
saying a model call was refused, and a continue raises the cap; `hard` fails the
job with `ERR_BUDGET_CALL_REFUSED`, its overrun 0, wherever a hard crossing
would, a QA fan-out's apps included); for the day's or the month's, by failing
the run with `ERR_BUDGET_CALL_REFUSED`, since crossing those caps stops no
running job (the dispatch gate checks them only when a job starts), and the job
goes on as after any failed run. A run that fails after a call of it was refused
carries `ERR_BUDGET_CALL_REFUSED` (set by the orchestrator from the proxies'
count, whatever the sandbox's image reads from the message), and its trace's
`metering` counts the refusals (`budget_refused`, `budget_refused_by`). No call
is checked for a free run (an unpriced model, or rates of 0), a run with no cap
set, a declaration from an older orchestrator, a run whose declaration failed
(its calls count under no run), or at a proxy image from before (the
orchestrator logs `proxy_run_budget_not_held`). The deployment purses are given
their figures again on every read of the proxies (every 5 seconds), so what
background features spend reaches them. After an orchestrator restart the ledger
starts empty, as the proxies do, and what the tallies kept of the runs the
previous process left count them (the figures their close charges them from) is
taken off a run's scopes until each is closed: nothing waits. A proxy recreated
mid-run is given its purses back less what their members could have spent at the
process that went away, their charges and their calls in flight at their
ceilings as its last read said (every 5 seconds); a member never read there
leaves its purses nothing more (fail closed).

**Every admin request is ordered.** The orchestrator numbers each request it
sends a proxy (an enrollment, its end, a change of push branches, a run
declared or ended, a token pushed) when it decides it, and a replay sends each
held request with its own number again. A proxy takes a request only when its
number is newer than the last it took for the same sandbox (or credential, for
a token); an older one is refused (409, `stale_request`), logged and counted
(`embry0_proxy_stale_admin_requests`). So a request that reaches the proxy
late (a `docker exec` that ran after its client gave up) or a replayed older
one can never take a sandbox from a newer run or enrollment, however late.
"When it decides it" is meant exactly: a run's ends are numbered when the run
ends (a cancelled run's before its end is handed to the background), so the
sandbox's next run is always declared with a newer number, and a token push
carries the number its token was fetched with, so of two pushes for one
credential the proxy keeps the token fetched last. The numbers start at the
wall clock and grow (`embry0/execution/admin_seq.py`); a clock that goes back
across an orchestrator restart cannot lock a proxy out, because `start()`
relaunches every proxy and a proxy never compares numbers of two processes.
Keeping a proxy across restarts would need the counter seeded from the numbers
the proxies last took.

Metering never holds a run up for long: a replay that times out is given up
at once, and a run's declaration or end waits at most 30 seconds for a replay
before it goes ahead (the wait only spares it finding the sandbox not yet
enrolled again, which would leave its tally not whole). What a given-up replay
did not send is sent in the background once the proxy answers again, each
entry only if still held as it was, so what a sandbox did since (enrolled
again, began or ended a run) stands. `embry0_meter_replay_abandoned` is 1 while
it waits, and `Embry0MeterReplayGivenUp` fires when it waits 15 minutes (the
proxy is not answering). A proxy image that predates ordered requests does not
echo a declaration's number, and a run declared on it is charged as one whose
tally is not whole (`unordered` on its trace, `proxy_unordered` or
`sandbox_unordered`), apart from a run whose tally was lost (`unreached`).

The trace keeps both figures: `metering` (the source, whether the tally was
whole, the proxy's cost, the sandbox's, each proxy's processes, calls, calls
estimated, calls charged their ceiling and tokens by model) and
`sandbox_cost_usd`; `cost_usd` and the token columns are the charge. When the
two figures differ by more than 10% and $0.05, the orchestrator logs
`run_meter_figures_disagree` and counts it
(`embry0_run_meter_disagreements_total`). Three alerts watch it
(`infra/prometheus/run-meter.rules.yml`, `docs/ops/metrics.md`):
`Embry0RunMeterUnderCounted` (a sandbox reported more than its proxy counted),
`Embry0MeterReadsFailing` (a proxy's tallies not read for 2 minutes),
`Embry0MeterCallsOutsideRuns` (calls from a sandbox with no declared run; they
are kept in `run_meter` and charged to no job), with
`Embry0MeterReplayGivenUp` above. The proxies still forward a
call from a sandbox with no declared run (`METER_REQUIRE_RUN` is not set).

**A run that never ended** (#694). A run's trace is written `running` before
its exec and finished after it. A run the orchestrator stopped in the middle of
(a restart, a deploy, a crash) is closed at the next start-up: the traces still
`running` are taken before any run of the new process begins, closed for up to
10 seconds before recovery, and the rest in the background. A run that was
cancelled (its job cancelled, or a shutdown that could not wait for it), from
its exec to its own finish, is closed at the cancel, waiting at most 3 seconds
before its cancellation goes on; one whose exec had already returned keeps its
own outcome (success or its error), with the same charge. Either is closed `error` with
`ERR_RUN_INTERRUPTED` and charged what is known of it
(`embry0/agents/run_close.py`):
- a run the metering proxies counted: the tallies kept of it in `run_meter`,
  every proxy process summed and each call in flight at its ceiling. Its end was
  never read, so the tally is not whole, and the larger of that and the
  sandbox's figure is charged;
- any other run: the sandbox's last figure, from the run's own events: its
  `cost_update` when one came, else its `turn_usage` events, put together into
  model calls. A call's events are those of one parent (the run, or a subagent)
  up to that parent's next `tool_result`, which always lies between two calls.
  Within a call, an event with the call's own input side is an older image's
  repeat of the whole response, counted at its largest, and one adding no input
  is what a newer image's later block adds (#676), summed. A call with no cost is
  priced at the run's provider record (`traces.pricing`);
- a run with neither is closed at $0, and says so.

`traces.metering.closed` records the cause, what the run was charged from and
why; the closing is logged (`interrupted_run_closed`, a warning when nothing
was known) and counted (`embry0_interrupted_runs_closed_total`). The charge
reaches the job's total as the run's `cost_update` would have, less what one
that came had already added, in the same transaction as the close: neither is
ever written without the other. A run whose exec crashed is charged the same
sandbox figure, not $0.

**Each agent call names an output limit.** The chat-completions executor asks
a model for at most its catalogue entry's `max_output_tokens` on every call
(`max_tokens`, or OpenAI's `max_completion_tokens`, which its reasoning models
require), 32,000 when the entry names none: room for a turn's answer and the
reasoning a reasoning model counts as output at its default effort, within
what the hosted models allow. A model whose provider allows less, or that needs
more, names its own; a limit in its `request_extras` wins. A custom model whose
entry names none names no limit in a run with no budget, so its server's default
stands (vLLM's is the room the request leaves), and 32,000 in a run held to a
budget, since a metering proxy holds a call naming none to 128,000 output
tokens. Within a custom model's known context window, a call asks for no more
than the room its request leaves by the executor's estimate, less a margin for
the estimate's error (the estimate itself until the server has counted a
request, an eighth of it after), when that is at least the room the fit keeps for
the answer (an eighth of the window, 1,024 to 8,192 tokens): below that, the
request may be nearer the window's end than estimated, and it names no limit, so
the server answers in the room it truly leaves. A call whose limit the server
refuses as past the window (`maximum context length`) goes again once naming
none: naming a limit never fails a request that naming none would not. A call
naming none is held at a metering proxy to 128,000 output tokens; the run of a
free model, such as one on the operator's own hardware, is not checked. The check holds a
call's whole limit, so with the 32,000 default a hosted model's calls on a $2
cap are first refused at some 60 to 90% of it, the earlier the dearer its output
(32,000 tokens at $15 a million hold $0.48); a smaller `max_output_tokens` lets
a run spend closer to its cap. An answer that stops at its limit
(`finish_reason` `length`, or the Messages API's `stop_reason` `max_tokens` on
the direct xAI executor) fails the run with `ERR_AGENT_ANSWER_CUT` rather than
end it as done: the answer, or a tool call in it, stopped part-way, and asking
again would stop at the same limit. Raise the model's `max_output_tokens`. The
executor runs in the sandbox: a sandbox image from before asks for no limit, its calls
are held to the 128,000, and it takes a cut answer as done.

### Changing the catalogue

The shipped catalogue moves with each release. A deployment's changes are an
overlay merged into it field by field, stored in `provider_catalogue`
(migration 152) as YAML, comments and all. It names only what differs: per
provider embry0 ships, `enabled`, `factors` (`turns`, `timeout`),
`default_price`, OpenRouter's `refresh_hours`, and `models`, where a new id
comes with its `prices` (`[{in, out}]`, US dollars per million tokens, each
with an optional `from` day) or `free: true`, a listed one changes the fields
the overlay names, and `null` takes one out. A provider's runtime, addresses,
prefixes and key variables are embry0's, and an overlay that names one is
refused.

```yaml
# DeepSeek for the light tier, at the off-peak rate
providers:
  deepseek:
    enabled: true
    models:
      deepseek-flash: {prices: [{in: 0.15, out: 0.60}]}
```

| Route | Who | What |
|---|---|---|
| `GET /api/v1/config/providers/catalog` | any key | the overlay as written (`yaml_text`) and its values, its `generation` (also the `ETag`), who changed it last, `catalogue` (the shipped one with the overlay merged in, what every job runs on), `shipped` (the shipped one alone, which the overlay changes), and `refused` (below); `?format=yaml` answers the overlay alone |
| `GET /api/v1/config/providers/catalog/shipped` | any key | the shipped catalogue, as YAML |
| `PUT /api/v1/config/providers/catalog` | admin, no repository allowlist | replace the overlay with the body (`Content-Type: application/yaml`); `?dry_run=true` stores nothing and answers the diff; `If-Match: "<generation>"` refuses a write over a newer one (409); `?reason=` is recorded |
| `GET /api/v1/config/providers/catalog/revisions[/{id}]` | any key | its changes, newest first, or one with its text before and after |
| `POST /api/v1/config/providers/catalog/revisions/{id}/undo` | admin, no repository allowlist | put back what the newest change replaced, while it is still what is stored (else 409) |

The MCP tools are `provider_catalogue_get`, `provider_catalogue_set` (whole
text, `expected_generation`, `dry_run`, `confirm=true`),
`provider_catalogue_revisions` and `provider_catalogue_undo`.

**On the dashboard**, the LLM Providers page's **Provider catalogue** card
shows every provider embry0 ships and its models, each marked shipped,
changed, yours or taken out. An admin turns a provider on or off (an Agent SDK
provider is always on), changes a model's price, makes it free or chat-only,
adds one, takes a shipped one out, or puts one back as embry0 ships it; a
model with dated prices is edited under **Advanced**, the overlay's YAML,
where comments are kept. Either way **Review changes** first asks the server
what the save would change (the dry run's diff, or every problem it would
refuse), and **Save** stores exactly what was reviewed, refused with 409 if
the catalogue changed since the page read it. A save from the form writes the
overlay again from its values, so a comment in the stored text is not kept:
the review says so.

A `from` day is stored as its ISO text and read back as a day, so a dated
price written in one process is in force, dated, in every other.

A write is in force at once in the process that took it, and every other
process installs it within ten seconds: routing, the model pickers, the
checks that a model is offered and priced, the turn and time budgets, and
what the orchestrator prices, and each agent run that starts after it: the
run carries its provider's record, resolved from the catalogue at its start
([Pricing a run](#pricing-a-run)). A run already under way keeps the rates it
started with. It is refused, with nothing stored,
by a 422 naming every problem and never a value: the YAML's own (a syntax
error, an alias, a key written twice), past 256 KiB (413), an overlay field
it may not set, a catalogue it makes that cannot be (a chat-completions model
without a price, prefixes of two providers that are on overlapping), a
`request_extras` or `background_request_extras` value past 4,096 bytes as
JSON, nested deeper than 32 or holding a number JSON cannot hold (`.inf`,
`.nan`, `1e400`), a `context_window` on a model that is not a custom
one (1,024 to 10,000,000 tokens), a `max_output_tokens` on a model of an
Agent SDK provider (1 to 1,000,000 tokens), and any model a tier, the fallback order, an
agent definition, a pipeline template's step (its `config.model`, or a legacy
top-level `model`), a repository's review model (`pr_review.model`) or a
`background_models` value names that the change would take out, leave
without a price or turn off (`loc` `references.<where>`). Change what names it first.
A write of any of those, in turn, is checked against the stored catalogue
with its row held, so a change of the catalogue and a write naming a model
never pass each other.

A number may be typed in exponent form: `1e-7` and `5E-1` are numbers, as YAML
1.2 and JSON read them (YAML 1.1 read them as text, #756). A value meant as
text that looks like one goes in quotes. An overlay stored before that is read
again from its text when it is installed (at the start and after each write)
and when it is read. When the only difference from the values stored with it
is such a number, stored as text, the values it reads to now are in force and
are the `data` a read answers, which the Providers page's form edits
(`provider_catalogue_read_again` in the log); the next save stores them. Any
other difference keeps the stored values in force
(`provider_catalogue_text_not_read_again`), and a read says why in
`text_not_read_again`, which the Providers page shows: the text does not read
(`unreadable`), reads to other values than those stored with it (`differs`: a
text changed beside its values, by a hand at the database), or reads to a part
the catalogue refuses (`refused`: `1e400`, which is now infinite).

**Everything in the document is readable by every key**, a key with a
repository allowlist included: the YAML as written with its comments, each
change's reason, the text before and after each change, and every value,
`request_extras` such as `chat_template_kwargs` among them. Keep secrets out of
it; a key belongs in the [credential registry](credentials.md).

**When a release moves the shipped catalogue.** A stored overlay meets the
shipped catalogue of whatever release reads it, which may have taken out a
model or a provider the overlay names. A write is always checked whole, but a
stored overlay is read part by part:

| What the overlay names | What is in force |
|---|---|
| `null` for a model the release took out too | as asked: the model is gone, and nothing is reported |
| a provider the release no longer ships | the rest; that provider's entry is left out |
| fields of a model the release took out | when they give it `prices` of its own: the deployment's own model at those prices, chat-only and with the request extras it was last shipped with unless the overlay says (each write records what the shipped catalogue said of every model it names, and keeps what it recorded of a model a release dropped, in `provider_catalogue.shipped`; an undo puts back the record of the side it restores), named in `notes`; else that model's entry is left out |
| a field the release made its own, a key it no longer knows, a value a bound now refuses | the rest; that field, or that model's entry, is left out |
| a top-level key the release does not know | the rest; that key is left out |
| no overlay at all at its top level (not a mapping, another `version`) | the shipped catalogue, with that problem |

A price is never replaced by one the deployment did not set. When a model's
entry that set its `prices` or `free` is left out, the model is unpriced
(`withheld` in `refused`), so the resolver and every write refuse it until the
catalogue is written again; when a provider's `default_price` is left out, the
provider has none, so its models without a price of their own are unpriced.
The provider stays on for its other models: that is narrower than turning it
off.

At a process's start every part that still applies is in force; nothing the
deployment turned on is turned off because something else in the overlay no
longer applies. A process already running when
a later overlay this release refuses part of is written keeps the catalogue it
runs on. Either way each part left out is named in `refused` by
`GET /api/v1/config/providers/catalog`, whose `in_force` says `in_part` or
`earlier` (and `in_force_generation` which overlay); it is logged
(`provider_catalogue_refused`), audited once a generation
(`provider_catalogue.refused`), and counted by the gauge
`embry0_provider_catalogue_refused_parts`, on which the shipped rule
`Embry0ProviderCatalogueRefused` fires after 5 minutes
([metrics.md](ops/metrics.md#provider-catalogue)). Write the catalogue again
without those parts: the write names each one, and puts the whole overlay in
force.

### The custom provider's models

The `custom` provider is any OpenAI-compatible server the operator points
embry0 at: its own hardware (vLLM, Ollama, LM Studio), or a gateway that bills
per token. It ships no models and no default price, and its turn and time
factors are 1.0 (#173). A `custom/<model>` runs only once the overlay lists
it, with its `prices`, or `free: true` for one that bills nothing; one with
neither is refused wherever a model is configured or a run resolves, like any
unpriced model. Per model it may also say what its server needs:

- `structured_output: json_schema`: the server can hold the model to a JSON
  schema while it decodes, so a background call (the briefs, the judge) sends
  its answer's schema as `response_format`. A model without it is sent none.
- `request_extras`: chat-completions fields from the allowlist above, sent
  with every call of the model, an agent's and a background call's; a call's
  own fields win.
- `background_request_extras`: fields from the same allowlist that only a
  background call sends, after (so over) the model's `request_extras`; an
  agent's run never sends them. A local server's thinking switch goes here
  when the briefs and the judge should run without thinking while agents keep
  the server's default.

```yaml
providers:
  custom:
    enabled: true
    factors: {turns: 2.0, timeout: 2.0}   # a slower server
    models:
      custom/qwen3-32b:
        free: true
        structured_output: json_schema
        background_request_extras:
          chat_template_kwargs: {enable_thinking: false}
      custom/gateway-model: {prices: [{in: 0.4, out: 1.2}]}
```

**What a deployment relied on, kept once.** Before #173 every `custom/` model
was priced at $0, its turn and time budgets doubled, and every background
call to it sent its schema with the server's thinking turned off. At the first
start of the release that changed that, each custom model the deployment's
stored configuration names (a tier or the default model, the fallback order,
an agent definition, a pipeline template's step, a repository's review model,
a `background_models` value at any layer, a job's `agent_models` override) or
a run was traced on is written into the overlay as `free: true`,
`structured_output: json_schema` and
`background_request_extras: {chat_template_kwargs: {enable_thinking: false}}`:
background calls keep their schema and run without thinking, and agents keep
the server's default, as before. Each factor the overlay does not set gets its
old value (2.0). A model the overlay already lists keeps everything it sets,
its price included, and gains only `structured_output` and
`background_request_extras` where it sets neither.
With a `default_price` of the overlay's own the models are written at that
price instead of `free`: a model's entry needs prices of its own or `free`, so
they are pinned at it, and a later change of the default does not move them.
It is one revision (source `upgrade`) and one
`provider_catalogue.custom_imported` audit row, and the `imported` ledger
(`custom.relied_on`) keeps it from running again, whether or not there was
anything to keep. An overlay the catalogue would refuse with these entries is
left as it is and not marked: the start logs
`provider_catalogue_custom_import_refused` at error level, and the first
start after the overlay is fixed keeps them.

### Adding a key

On the dashboard, an admin adds or replaces a provider's key on its card on the
LLM Providers page (**Add key** / **Replace key**): the key is stored in the
credential registry and becomes the provider's global binding, in place of the
global key it had, so every repository without a binding of its own uses it.
The steps below are the same by hand.

1. Turn the provider on in the catalogue
   ([Changing the catalogue](#changing-the-catalogue)).
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
(Credentials, **Add credential**, kind **LLM API key**) offers every provider
of the provider catalogue (`GET /api/v1/config/providers/catalog`, in its
order), and for `custom` asks for the base URL; a provider this deployment
has not enabled is listed as not enabled and cannot be picked. The catalogue
names the LLM providers, so one a release adds is offered with no change to
the dashboard, the credential API or the database: `POST /credentials`
checks an `llm_*` credential's provider against the catalogue in force. A
repository's **LLM accounts** card binds an account at repo scope. It has a
row for each enabled provider the catalog marks available, and for any
provider the repository is bound to or resolves an account for, even one
the catalogue no longer lists, so neither is hidden. It counts the other
enabled providers in one line and names the ones that are not enabled.
Until the provider catalogue answers it shows none.

No chat-completions provider's key enters a sandbox (ADR 0033). The sandbox is
enrolled at the llm-proxy with each provider its job resolved: the
credential's id, the provider's API root above, its key, and the address the
root's host resolves to on the orchestrator, checked and pinned as a custom
server's is ([Local models](#local-models)). The run's provider record points
at the provider's path there, `http://llm-proxy:9108/p/<provider>/v1`, and the
run authenticates with the sandbox's bearer file. The proxy forwards it to that
provider with its key, and only to the providers the sandbox was enrolled
for. The custom provider's root is the proxy's `/v1`, which the sandbox gets as
`EMBRY0_LLM_CUSTOM_BASE_URL`. The `EMBRY0_LLM_` namespace stays reserved (the
names `EMBRY0_LLM_<PROVIDER>_API_KEY` a release before this put the keys under
among them), since a repository value there could swap the account an agent
bills or send its conversation to another server; a repository that sets its
own `OPENAI_API_KEY` for its app keeps it.

A job on one of these providers does not start without the llm-proxy, or when
a provider's root resolves to nothing, or to this machine or link-local. A
resumed job's sandbox is enrolled again with the credentials the job resolves
then. A paused job whose sandbox an older release created with a provider's
key in its env is not resumed: its egress is closed and the resume fails with
`ERR_SANDBOX_ROUTE_RETIRED`; dispatch it again. A run on a sandbox image
whose runner predates the route is refused before any call, with
`ERR_SANDBOX_IMAGE_STALE`: rebuild the image. The orchestrator's own calls to
these providers (background calls, probes) still use their keys directly.

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
operator's network, which the sandbox egress drop blocks. Every sandbox reaches
it through the llm-proxy, enrolled with its own credential's server, and
nothing is opened: no model server's host is ever an egress exception
([Local models](#local-models), ADR 0032).

### Mechanics and limits

- **Not verified against the real APIs.** The executor's tests run it against
  a fake OpenAI-compatible HTTP server (`tests/fixtures/openai_compatible_server.py`).
  Run one developer and review loop per model before relying on it, as for grok.
- **Image rebuild.** The executor runs in the sandbox, so it takes effect after
  the sandbox images are rebuilt ([`docs/ops/deploy.md`](ops/deploy.md)).
- **Turn and time budgets.** These providers declare no turn or time factor,
  `custom` included since #173: a slower server's are raised in the overlay,
  and a deployment that used custom models before keeps the old 2.0 (see
  [Turn and time budgets](#turn-and-time-budgets)).
  How many turns the hosted providers' models need for embry0's agents is not
  measured.
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
- The first save of any provider setting (the fallback order, the local model
  card, a model's toggle) stores the tiers and the default model as they were
  in force: `MODEL_HEAVY`, `MODEL_MEDIUM`, `MODEL_LIGHT` and `DEFAULT_MODEL`,
  else the built-in defaults. From then on the saved row wins over those
  variables.
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
provider is enabled and the catalogue lists it, priced or `free`
([The custom provider's models](#the-custom-providers-models)). Put it last, so the local server only takes
the work no other provider can.

## Spent account waits

A spent or rate-limited account never fails a job. The job moves to a ready
model of the fallback order, or it waits for the account's reset
([the design](superpowers/specs/2026-10-25-spent-account-waits-design.md)).
Whether it waits, and how long, is the settings document's `account_wait`
section ([below](#whether-a-job-waits)).

**What counts.** A pause with a known end:

- a registry account quarantined until a time;
- a run of the job that ended with `ERR_PROVIDER_QUOTA_EXHAUSTED` or
  `ERR_PROVIDER_RATE_LIMITED`: until the reset the provider named, else the
  quarantine window for the code;
- the local model with every slot taken (`local_busy`): five minutes
  (`account_wait.local_busy_retry_seconds`).

A wait's end is cut at eight days (`account_wait.max_wait_seconds`) from
when the job is parked: the job then asks again, and waits again while the
account is still spent.

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
records the same action. It asks with the default `account_wait` values,
not the repository's: a job that would fail at once is not started either.

### Whether a job waits

The settings document's `account_wait` section
([the reference](configuration.md#account_wait)):

```yaml
account_wait:
  enabled: true                   # false: with nowhere to move, the job fails at once
  max_wait_seconds: 691200        # the furthest one wait's end may be: eight days
  max_total_wait_seconds: 2592000 # all of a job's waits together: thirty days
  local_busy_retry_seconds: 300
```

A document that sets none of it keeps what every job did before the
section, and bounds all of a job's waits together at thirty days.
`enabled: false` is for a team that would rather see the failure than wait
for it. A spent account still moves the job to a ready model of the fallback
order. With none, the job fails at once with the account's own code, as jobs
did before waits:

- `ERR_PROVIDER_QUOTA_EXHAUSTED` for a spent usage window or an xAI
  spending limit;
- `ERR_PROVIDER_RATE_LIMITED` for a rate limit;
- `ERR_LOCAL_MODEL_BUSY` for the local model's slots.

Found by the account pre-check, it fails before any sandbox exists; found
by an agent run, the run is not started (or its failure stands) and the job
fails as a run the account refused does. Nothing is parked and no
`account_wait` is written, in every pipeline. The run that finds an account
spent still quarantines it at once, so the next job's pre-check sees it.

A forced run on the local model still waits for a free slot inside the run
first, for `local_models.slot_wait_seconds` (900 by default), whatever this
section says. Only when no slot came free does it park for
`local_busy_retry_seconds`, or, when the job may not wait, fail with
`ERR_LOCAL_MODEL_BUSY`.

**The total.** `max_total_wait_seconds` bounds all of a job's waits
together, over every wait, resume and relaunch. The job's row keeps the sum
(`account_waited_seconds`, migration 160): each park records when it began,
by the database's clock in the park's own write, and the account-wait loop
adds the time since then when it takes the job back (Try now does the same).
A new wait is cut to what is left of the total, and once it is spent the job
fails with the account's own code, as with `enabled: false`; its error says
the total was reached. What is not a wait for an account does not count: the
five minutes a woken job waits when the proxies would not take its sandbox
back, and a wait parked before the release that added the sum.

The total belongs to the job. A relaunch of the same job (one parked before
it started, a qa, onboard or pr-review job, a start after a restart) keeps
it; **Run again** in the world starts a new job, with a total of its own.

An organization's layer and a repository's may set every key. A job reads
the section for its repository when it starts and keeps those values through
every pause, answer and restart, so a change reaches the next job that
starts: a job already waiting goes on waiting. A job that starts again from
its beginning (a `relaunch`: one parked before it started, or a qa, onboard
or pr-review job) reads the section again, and keeps the time it has waited.

Both reads fail open. When a resumed job's checkpoint cannot be read, it
runs on the section's defaults (`enabled: true`) and logs
`account_wait_settings_unread`; when its row cannot be read, it counts
nothing waited and logs `account_waited_unread`. Neither fails a job, but a
row holding a value too deep to read fails it, as every read of a job's run
does (`ERR_STORAGE`).

## Local models

A model server on the operator's own hardware (vLLM, Ollama, LM Studio) runs
agents through the `custom` provider: any `custom/<model>` it serves that the
catalogue lists, `free` for one on the operator's own hardware
([The custom provider's models](#the-custom-providers-models)), with tool calling as the server and the model provide it
([what tool calling requires](#what-tool-calling-requires)). Such a server is
usually shared hardware on the operator's network, so three things keep it
safe to point embry0 at.

**A custom credential is its server** (ADR 0032): its API root
(`metadata.base_url`, `http://host:port/v1`) and its key, in the registry, or
`CUSTOM_LLM_BASE_URL` with `CUSTOM_LLM_API_KEY`. A server that checks no key
still needs one stored, and any placeholder will do. Credentials bound to
different repositories may name different servers: each job reaches the one
its credential names.

**The llm-proxy.** The orchestrator always starts `llm-proxy` on
`sandbox-restricted`, configured with no server. It answers to
`local-llm-proxy` too, its name before the rename, which a sandbox created
before then holds in its env, and so it is started only once no container
holds that name: a `local-llm-proxy` left by an earlier release is removed
first. While one cannot be removed, the llm-proxy is not started (the log
says `llm_proxy_held`, and a custom job is refused saying why); it starts
within a minute of that container's removal. It enrolls each sandbox whose
job resolved the custom provider with that credential's id, API root and key,
and forwards that sandbox to that server and nowhere else (each other
chat-completions provider's the same way, at `/p/<provider>/v1`, ADR 0033):

- only `POST /v1/chat/completions` and `GET /v1/models`; any other path is
  404 and never reaches a server;
- only for a sandbox enrolled with it. Its bearer is checked and dropped
  there, and the server's key is added by the proxy, so neither the key nor
  the server's address is in the sandbox. An answer that names a key the proxy
  holds (an error message may quote the key it was sent) has it replaced with
  `***REDACTED***` on its way back, however the stream splits it. Each key is
  matched as written, not in an escaped or encoded form (a JSON `\u` escape,
  base64), and a key shorter than 8 characters (a placeholder) is not
  scrubbed. A server is asked for uncompressed answers (`Accept-Encoding:
  identity`); one that answers in an encoding the proxy does not decode
  (anything but gzip or deflate) is refused with 502, `unreadable_answer`, and
  none of it goes back. A host name in the root is sent as
  the address it resolves to on the orchestrator, since DinD resolves none;
  an https root keeps its name, for the certificate;
- a request over `local_models.max_request_bytes` (8 MiB) is refused with
  413, saying `request_too_large`, so the run ends as
  `ERR_CONTEXT_WINDOW_EXCEEDED`; a server that has not answered within
  `local_models.request_timeout_seconds` (900) is a 504. Both are read when a
  sandbox is enrolled, so a change reaches the next sandbox, never past the
  ceilings the proxy starts with (64 MiB, an hour), and hold on every path
  the sandbox calls: its custom server's and each provider's (`/p/<provider>/v1`).

No egress exception is declared for a server's machine, so a sandbox cannot
reach anything else on it. A job on the custom provider does not start
without the proxy, or on a credential that names no server; a resumed job's
sandbox is enrolled again with the server its credential names then. A paused
job whose sandbox an older release created with the custom provider's key or a
server's address in its env is not resumed: its egress is closed and the resume
fails with `ERR_SANDBOX_ROUTE_RETIRED`; dispatch it again.

**Which hosts may be a server.** Never this machine (`localhost`, a loopback
address, `0.0.0.0`) or the link-local range, where a cloud's metadata service
answers (169.254.0.0/16, fe80::/10). A host is read the way the resolver reads
it, not by its spelling: an address in any form (`2130706433`, `0x7f000001`,
`127.1`, `169.254.43518`, an IPv4-mapped IPv6 address) is that address, and a
name counts for every address it resolves to (`localtest.me` is 127.0.0.1). A
credential naming one is refused when it is written or imported, a job on it
does not start, no background call, startup probe or credential check goes
there, and the proxy refuses to be enrolled with one. Every other host is
trusted, the operator's own network included, because only a deployment admin
writes a custom credential's address (an admin key with no repository
allowlist, or the environment).

**Every call connects to the address that was checked.** Where embry0 calls a
custom server (the llm-proxy for a job, a background call, a probe), it
resolves the host once, checks every address, and connects to one of them,
never to the name again: a name that would answer otherwise a second later (DNS
rebinding) gets no second question. The name is still the server's `Host`
header and, for https, the name its certificate is checked for (SNI), on a
connection of its own: connections are kept per name, so two servers that share
an address never share one, and each handshake checks its own name. For a job
the orchestrator resolves it, since DinD resolves no name, and enrolls the
sandbox with the address. A name that resolves to nothing here starts no job
and makes no call; a credential may still be written with one, and is checked
again where a call is made. Calls the
orchestrator makes itself (proposal and job briefs, the Slop-O-Meter's
judgments; see [Background models](#background-models)) reach the same server
directly, with the same address and key.

**At startup** each stored custom credential that is not disabled is probed
once (`GET <base_url>/models` with its key), and one that would not work is
named in the log, `custom_credential_check_failed` with a `reason`:
`no_server`, `refused_host` (this machine or link-local, below; not probed),
`unreachable`, `key_refused` (the server refused its key: a placeholder on a
server that checks one), `unexpected_answer` or `unreadable`. The check only
logs.

**`LOCAL_LLM_UPSTREAM` and `LOCAL_LLM_API_KEY` route nothing.** Before ADR
0032 they sent every custom run in a sandbox to one root with one key,
whatever its credential named. `embry0 credentials import-env` copies them
once into the environment's custom credential, `custom/api-key`: its address
from `LOCAL_LLM_UPSTREAM`, its key from `LOCAL_LLM_API_KEY`, else
`CUSTOM_LLM_API_KEY`, else the placeholder `no-key`. A `custom/api-key` that
already names another address or key is left as it is and named in the
report, unless the import is run with `replace=true`. Every other stored
custom credential keeps the address and key it was written with: point it at
its server if it carried a placeholder address before. While either variable
is set, startup logs `settings_env_retired`, and `embry0 config` names it.

**The limits** are the settings document's `local_models` section, one set
for every server of the deployment, read where they are used (no restart):
`max_runs`, `slot_wait_seconds`, `request_timeout_seconds` and
`max_request_bytes` ([configuration](configuration.md)). The Providers page
sets `max_runs` too. Before #174 `max_runs` was `local_max_runs` in the
provider settings, which the upgrade carries into the section (a value other
than 1), and the wait and the cap were `LOCAL_LLM_SLOT_WAIT_SECONDS` and
`LOCAL_LLM_MAX_REQUEST_BYTES`, no longer read.

**One run at a time, per server.** `local_models.max_runs` (default 1, at
most 64) is how many calls embry0 sends each of the operator's servers at
once. A server is the custom credential a call is
made with, so two credentials bound to different repositories are two
servers, each with its own slots; the environment's credential
(`CUSTOM_LLM_API_KEY`) is one more. One machine may go by several names (an
address, a name, a trailing dot), so the credential, not the address, tells
servers apart. A lease taken before ADR 0032 while `LOCAL_LLM_UPSTREAM` was
set names `env:LOCAL_LLM_UPSTREAM`, and frees itself by its expiry.

Each run on the custom provider holds a slot of its server, a row in
`local_model_leases`, from just before it starts until it ends, however it
ends. A lease expires by itself five minutes after the run's own time limit,
so one left by a process that died frees its slot. A run that has to use the
local model (it is the fallback, or the job named it) waits for a slot up to
`local_models.slot_wait_seconds` (900), saying so in a `progress` event. Then
the job is parked for `account_wait.local_busy_retry_seconds` (five minutes)
and tries again, or, when it may not wait, fails with `ERR_LOCAL_MODEL_BUSY`
([Whether a job waits](#whether-a-job-waits)).

A background call to a local model (a proposal or job brief, a
Slop-O-Meter judgment) holds a slot of its server for the length of the
call. It never waits: with every slot taken the call is not made
(`background_model_busy` in the log). A brief writer's tick stops there and
the next one tries again; the Slop-O-Meter scores the repository without
that judgment, provisionally, as when the caps refuse one. Background work
never crowds out the runs the server is for.

**Preferring the local model.** A background feature that prefers a model
(the watcher's log analyses, scheduled context audits; see
[Background models](#background-models)) stamps it on each job it starts
(`pipeline_config.local_first_model`) unless the job runs with a model a
person chose. For each agent of such a job, when the model is a
`custom/<model>`:

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
custom model, the chat-completions executor reads its window from the
catalogue, the model's `context_window` (`providers.custom.models.<model>`;
only a custom model takes one), which reaches the run in its provider record,
else from the server's `/models` (`max_model_len`, `context_length` or
`context_window`). `LOCAL_LLM_CONTEXT_TOKENS`, one window for every custom
model, is no longer read. Before each request
it estimates the conversation's tokens, kept honest by the server's own
`prompt_tokens`; over the window less room for the answer, it replaces the
oldest tool results with a one-line note, keeping the task and the last
messages whole. A conversation that still does not fit fails the run with
`ERR_CONTEXT_WINDOW_EXCEEDED`, as does a request any server refuses as over
its window, instead of being sent until the turn cap.

## Background models

Some of embry0's work runs without anyone asking for it: the watcher's log
analyses, scheduled context audits, the scout, the plain-words briefs of
owner proposals and of jobs, and the Slop-O-Meter's judgments of outlier
commits. Which model each one runs on is the `background_models` section of
the [settings document](configuration.md#settings-document): a `default`,
and one key per feature that names its own model, `null` to run on the
default, or `false` for none. A key left out runs on the default, except
`scout`, which is `false` until a model is named for it, so setting the
default never starts the scout. No feature falls back on another's model, so
changing one changes no other unless it runs on the default.

```yaml
background_models:
  default: custom/qwen3-32b   # what the features below run on, unless they name their own
  scout: custom/qwen3-32b     # the scout runs only on a model named for it
  proposal_brief: gpt-6-luna  # a hosted model: its spend counts against the caps
```

| Key | Runs | Takes | With no model |
|---|---|---|---|
| `default` | every feature below that names no model of its own (never the scout unless `scout: null`) | `custom/<model>` only | each runs on none |
| `watcher` | the watcher's analysis jobs prefer it ([preferring the local model](#local-models)) | `custom/<model>` only | they run on their agents' own models |
| `context_audit` | scheduled audit jobs prefer it; a model chosen with Run with wins | `custom/<model>` only | they run on their agents' own models |
| `scout` | every scouting job is pinned to it: a run waits for it | any model whose agents call tools | the scout starts nothing (the default) |
| `proposal_brief` | writes each owner proposal's brief, asked over chat completions | any chat-completions model | none is written; the feed shows each proposal's summary |
| `job_brief` | writes each job's brief, asked over chat completions | any chat-completions model | none is written |
| `slop_judge` | judges whether an outlier commit is slop, over chat completions | any chat-completions model | outliers stay unjudged and the score provisional |

Every feature reads the section at each tick, so a change needs no restart.
An organization may set every key for its own repositories (a layer, see
[Layers by organization and repository](configuration.md#settings-document));
a repository may not, since a key whose allowlist covers one repository
would otherwise move the deployment's shared spend.

**Which model may go where.** `watcher`, `context_audit` and `default` take
only a model of the operator's own server, `custom/<model>`: those
features *prefer* their model while a local slot is free, and a preference
for a hosted model, which never runs out of slots, would pin every run to
it. A hosted model may be named for the briefs, the judge and the scout,
whose calls and jobs are metered and capped. Every value's shape is checked
whenever it is read, and a write is also checked against this deployment's
catalogue: the model must be one `GET /api/v1/config/models` lists, or a
`custom/<model>` with the custom provider enabled, and priced; the scout's
agents call tools, so not a chat-only model; the briefs and the judge are
asked over chat completions, which only an OpenAI-compatible provider or
`custom/` serves; `default` must serve all of them. A 422 names each key it
refuses, never the value. A stored model is never refused against the
catalogue when it is read, or when a write leaves it as it is: a provider
turned off later makes the feature log that its model cannot be called.

**Hosted models, metered and capped.** The watcher, the context audit and the
scout run on their model inside jobs, metered and capped like every job.
The briefs and the judge call their model directly, outside any job, and
each call goes the way a job's run would:

- its key is resolved with the repository the call is about, so that
  repository's and its owner's [key bindings](credentials.md) apply;
- a call to a hosted model first asks the budget gate jobs are dispatched
  through, built by the same factory over the same budget policy (the
  `budget` section of the settings document): past the daily or monthly cap
  it is not sent, the feature logs `background_model_refused` (a warning,
  then once an hour while it lasts) and its loop goes on, trying again on
  its next tick.
  A `custom/` model the catalogue marks `free` bills nothing and is never
  refused for spend; one it prices (a paid gateway behind the server) is held
  to the caps as a hosted one is;
- the call's body carries the model's `background_request_extras` (a local
  server's thinking switch, say), then its `request_extras`, the call's own
  fields winning over both, and its answer's
  JSON schema as `response_format` only for a model whose entry says
  `structured_output: json_schema`;
- a call to a `custom/` model holds a slot of its server while it is made,
  and is not made while every slot is taken: the feature logs
  `background_model_busy` and goes on as when the caps refuse a call
  ([Local models](#local-models));
- a model with no price is never called (`background_model_unpriced`, a
  warning once an hour per model);
- the tokens the provider reports are priced as a job's run is and recorded
  under the feature (`background_spend`): the budget caps count them, and
  `GET /api/v1/stats` adds them to the lifetime, the day's and the month's
  spend (`total_cost_usd`, `daily_cost_usd`, `monthly_cost_usd`) and lists
  each feature's spend for today and this month under `background_spend`,
  with the lifetime sum in its `total_usd`. The month's figures are a range of
  the table's index, and the lifetime sum reads that index alone. The daily
  digest reports the day's spend beside the jobs' and the watcher's
  (`background_spend_usd`, by feature in `background_spend_by_feature`), as a
  range of the same index. Usage a provider sends badly (a count that is a
  string, an infinity, or past any real count) is read as none: the call is
  still recorded, with no tokens, and `background_model_usage_missing` says
  so;
- a call whose record cannot be stored is kept in memory, dated when the
  call was made, and stored before the feature's next call. Until it is,
  the feature's hosted calls are refused (`background_model_refused` with
  `limit=spend_unrecorded`) and `background_spend_record_failed` is logged at
  error level, the first time and then once a minute. Each row carries its
  call's own id, so a row stored again after a database error that hid its
  commit is stored once. Past 1000 held rows a feature drops the ones that
  cost nothing first, then the oldest, logged: a cap that cannot see a spend
  cannot hold it.

`background_spend` has no retention, like the jobs' and traces' spend:
`JOB_LOG_RETENTION_DAYS` rewrites job events only.

**On the Providers page.** The **Background models** card edits `default`
alone (`PATCH /api/v1/config/settings/background_models`), and offers and
accepts only a `custom/<model>`. It says where the value came from and lists
what each feature runs on. Anyone signed in sees it; only an admin can
change it. Each feature's own key, a hosted model for the briefs, the judge
or the scout included, is set in Settings → Policy.

**Upgrading.** Before this section, the watcher ran on `watcher_local_model`
of the provider settings, the proposal briefs on `PROPOSAL_BRIEF_MODEL` or
else that, the job briefs on `JOB_BRIEF_MODEL` or else the proposal briefs'
model, the judge on the job briefs' model, the scout on `SCOUT_MODEL` and the
context audit on `CONTEXT_AUDIT_LOCAL_MODEL`. Those variables are no longer
read: each feature runs on what this section names. `PUT /config/provider` refuses
`watcher_local_model` with a 422 naming where it went; its column is kept,
unread, so a rollback finds its value, and a later release drops it.

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
  by side, not merged. The LLM Providers page shows all of them. A key with a
  repository allowlist reads `available` and the models, and no
  `credential_source`, `last_probe` or `base_url` the environment sets:
  they are the deployment's own ([api.md](api.md#repository-allowlist)).
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
provider can scale both (its `factors`, `turns` and `timeout`, in
`embry0/agents/providers.yaml`). `resolve_agent_invocation` applies them once
the model is final, and the "Turn budget" advisory appended to the prompt
states the scaled number.

xAI doubles both. grok-4.7 makes about three times the tool calls of a
Claude developer run (median 111 vs 37) and takes about twice as long at
p90; on Claude-sized budgets half of its first developer runs died at the
turn cap. A grok developer therefore gets 80 turns and 3600 s, a review 50
turns and 600 s, triage 30 turns and 360 s. The job's `budget_usd` still
bounds spend.

`custom` declares 1.0 for both. A model on the operator's own server is
often slower per call than a hosted one (on Claude-sized budgets log analyses
on a 27B model on one card timed out), so raise them in the overlay
(`providers.custom.factors`). A deployment that used custom models before
#173 keeps the old 2.0 for both, written once into its overlay at the first
start of that release: a context audit on a `custom/` model there still gets
80 turns and 40 minutes. A `free` custom model is not bounded by
`budget_usd`; the turn and time caps and `local_models.max_runs` are; a
priced one is bounded like any other.

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
MCP stdio client (`embry0/agents/mcp_client.py`) for browser-backend parity (verified against playwright-mcp and vibium 26.8.21, and against vibium 26.11.16, which the QA image runs). A line a server sends past the client's 16 MiB limit fails the request it was answering with an `McpClientError` naming the limit (`ERR_MCP_SERVER_FAILED` when it ends the run), and the client reads on from the line after it (#466).
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
reading. A key with a repository allowlist reads `not_configured`, and
nothing is asked of OpenRouter for it. The OpenRouter card shows it. These are the provider's own figures,
unlike the list prices above; no other provider answers this with an ordinary
key, so the route is 404 for them.
