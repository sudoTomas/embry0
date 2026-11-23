# The provider catalogue as a document

Issue #173, part of #159 (operator policy in documents stored in the database). Record: [ADR 0031](../../adr/0031-provider-catalogue.md).

## Problem

Every model provider embry0 can route an agent to is Python constants in `embry0/agents/providers.py`: prefixes, models, prices per million tokens, turn and time factors, chat-only models and request extras. So:

- adding a model or changing a price takes a release, and a sandbox image rebuild, since the sandbox prices runs from its own copy of the table;
- a price that changes on a set date cannot be expressed;
- the refusal of an unpriced model tells an operator to edit the code;
- enabling a provider is an environment variable (`OPENAI_COMPATIBLE_PROVIDERS`), read on every call but set only with a restart;
- the provider set is repeated: a `Literal` in the credential schema, a database check constraint, the frontend;
- `custom`, any OpenAI-compatible server, prices every model at $0 and doubles every budget, so `budget_usd` never bounds a paid gateway behind it.

## Decisions (2026-11-21)

1. **The catalogue is its own document**, not a section of the settings document: like the progression content pack ([ADR 0027](../../adr/0027-progression-content-pack.md)), it is long, its defaults move with each release, and changing a price is a different act from changing policy.
2. **A shipped seed, and a deployment's overlay merged with it field by field.** The deployment's document names only what differs; a release that changes the seed reaches every field a deployment did not override.
3. **Dated prices**, resolved per agent invocation at its start and recorded on the run's trace. Past jobs are never re-priced: their spend is stored in dollars, and the budget caps were enforced on it.
4. **New `custom` defaults** (factors 1.0, request extras per model, a price or `free: true` required), with a one-time import that keeps what a deployment relied on: the old factors, and `free` for the custom models it actually ran.
5. **Addresses and key sources stay out of the document**, so any API key may read it; writes are admin-only.
6. **Scope: the catalogue and enablement.** Tiers, `disabled_models` and the fallback order stay in `provider_config`.

## The document

```yaml
version: 1
providers:
  <name>:                       # a shipped provider
    runtime: sdk | chat_completions
    base_url: <url>             # the provider's own endpoint (shipped)
    api_root: <url>             # its OpenAI-compatible API (shipped)
    model_prefixes: [<prefix>]
    strip_prefix: false         # the first prefix only routes; the upstream id is the rest
    probe_path: /models
    enabled: false              # the OpenAI-compatible providers are off until enabled
    factors: {turns: 1.0, timeout: 1.0}
    default_price: {in: 0, out: 0}   # a routed model with no entry of its own; none refuses it
    models:
      <id>:
        prices: [{in: 2.0, out: 10.0}, {from: 2026-12-01, in: 4.0, out: 20.0}]
        free: false             # $0 on purpose; a model is priced or free, never neither
        chat_only: false        # served without tool calling
        request_extras: {}      # chat-completions body fields the model needs, from an allowlist
```

The schema is `embry0/agents/catalogue.py`; the seed, `embry0/agents/providers.yaml`, holds today's table.

**Prices** are US dollars per million tokens. The price in force at a moment is the newest entry whose `from` day is not after it; an undated entry applies before the first dated one; a model whose prices all start later has none yet.

**What an overlay may set.** Of a shipped provider: `enabled`, `factors` (key by key), `default_price` (`null` takes it out), `refresh_hours` (OpenRouter's only: the hours between reads of its live list, 0 to 720, 0 stopping the loop) and `models`. A model the seed has takes each field the overlay gives it, with `prices` and `request_extras` replaced whole. A model made `free` drops its prices, and one given prices is no longer free. A new model must be complete, and `null` takes a model out. Everything else of a provider (runtime, addresses, prefixes, how its ids reach its API, its probe) is embry0's, because routing and the executors are built on it: an overlay that names one is refused. Providers embry0 does not ship are refused until the credential and address work below exists.

**Validation** names every problem it finds, each with where it is and what is wrong, never the value. Of an overlay, these come back together: what it may not set, and then either its own shape's problems or, when its shape is right, every problem of the catalogue it makes. The checks:

- the schema's types, strictly (`"5"` is no price, `"true"` no flag);
- prices finite and at least 0, their `from` days in order with only the first undated;
- a model priced or free; every model id starting with its provider's prefix;
- no two providers' prefixes overlapping, so a model id routes to one provider only;
- `anthropic` present; the Agent SDK providers always on;
- a model's `request_extras` naming only fields of an allowlist (`REQUEST_EXTRAS` in the schema module): those that change neither which model answers nor what its tokens cost. Today that is `reasoning_effort`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `response_format` and `chat_template_kwargs`. Anything else is refused where it is named: the executor's own fields, a provider's routing (OpenRouter's `models` and `route`, `provider`, `plugins`), and fields that keep the model but change its price (`service_tier`, `modalities`, `audio`, `prediction`, `web_search_options`). The executor also drops any of its own fields that reach it, and sets its own after a model's extras, so its own always win;
- `null` only for a model (taking it out) or a default price, never for `enabled`, `factors` or `models`.

**Adding a field to the allowlist.** First check, against the providers that accept it, that the field changes neither which model serves the request nor how its tokens are priced. Then add it to `REQUEST_EXTRAS` and to the test's own list of allowed fields (`tests/unit/agents/test_provider_catalogue.py`, which compares the two), with a case that a model takes it.

**Kept out of the document.** Where each key is read from is code (`ENVIRONMENT` in the schema module). The custom provider's address comes from its credential or `CUSTOM_LLM_BASE_URL`.

**Bounds on a write** (PR 2, which opens the write path): the stored text is at most 256 KiB; a `request_extras` value is at most 4,096 bytes as JSON and nests at most 32 levels; and a problem's location names at most 12 parts, a deeper one ending in `...`, so a value nested a thousand deep is named by where it starts.

## The stored document (PR 2)

One row, `provider_catalogue` (migration 152): the overlay's YAML as written, comments included, its values, a `generation` and `imported`, the ledger of the one-time environment import. Its changes are `config_revisions` rows with surface `provider_catalogue` (the newest 100 kept), each undoable while it is the newest and the stored text is still what it wrote. `embry0/services/provider_catalogue_store.py` holds it; `embry0/api/v1/provider_catalogue.py` and four MCP tools serve it (`docs/providers.md`, "Changing the catalogue").

**What names a model.** A write is refused, naming each, when it takes out, leaves without a price or turns off a model that the catalogue offers now and that one of these names: a tier or the default model, the fallback order, an agent definition, or a `background_models` value at any layer. A model nobody could run before does not hold a change up. The other way round, a write of any of those checks its model against the stored catalogue with the catalogue's row held `FOR SHARE` until it commits: a settings write reads it on its own transaction (`embry0.settings.write_context`), and a tier, fallback or agent write runs inside `CatalogueStore.held`. A change of the catalogue takes the row `FOR UPDATE` before it reads what names a model, so of the two one waits for the other and is checked against what it wrote.

**The environment, once.** `OPENAI_COMPATIBLE_PROVIDERS` (its names into each provider's `enabled`) and `OPENROUTER_CATALOG_REFRESH_HOURS` (into OpenRouter's `refresh_hours`) are `EnvImport` entries: at the first start of this release each set variable is merged into the overlay under a comment naming it, as one revision with source `bootstrap` and a `provider_catalogue.bootstrapped` audit row; every key goes into the ledger, set or not, so neither variable is read again, and a start that still finds one set logs `provider_catalogue_env_ignored`. A name that is no shipped OpenAI-compatible provider is dropped with a warning. Both entries are removed before 0.1.0, with the variables.

## Readers

`embry0.agents.providers` keeps its functions and answers every one from the catalogue installed in the process (`catalogue.current()`): the seed until a deployment's own is installed. The orchestrator imports the environment and installs the stored catalogue at startup, before anything routes a model; a write installs it at once in the process that took it, and every other process reads the stored generation every ten seconds and installs a new one; a stored overlay this release refuses part of is read as "Seed drift" below says. OpenRouter's live-list loop reads its switch (`enabled`) and its interval (`refresh_hours`) from the installed catalogue each minute. The resolver sends every model routed to a provider other than Anthropic its rates in force with the run (`pricing_usd_per_mtok`, which sandbox images already read), so a sandbox, which carries the shipped catalogue, meters a model the overlay re-priced or added at the overlay's rates where embry0 prices a run from its tokens (the chat-completions providers, grok's direct executor); its request extras and the rest of the provider record follow in PR 3. An SDK-runtime run (grok on the Agent SDK) is charged what the CLI reports, its per-turn estimates priced from the sandbox's shipped table, so the catalogue's prices for an SDK-runtime provider drive no charge until PR 3.

**What every key reads.** The document holds no address an operator gives and no key's source, so any key reads all of it: the YAML with its comments, each revision's reason and its text before and after, and every value, `request_extras` such as `chat_template_kwargs` included. The docs tell operators to keep secrets out of it.

### Seed drift

A write is checked whole (`catalogue.merge`). A stored overlay meets the seed of whatever release reads it, which may have taken out a model or a provider the overlay names, made a field embry0's own, or tightened a bound. Losing the whole overlay then would turn off every provider the deployment turned on and drop every price it set (the custom provider back to $0), on an ordinary release. So a stored overlay is read part by part (`catalogue.merge_stored`); a part is a provider's entry, a model's entry, or a provider's field:

| Drift | Decision |
|---|---|
| `null` for a model the seed no longer has | what it asked for holds: dropped, not reported |
| a provider the seed no longer ships | its entry is left out, reported |
| fields of a model the seed dropped | kept as the deployment's own model only when they state `prices` of their own (a price of the release that dropped the model is no price to trust, and `free: true` alone says nothing of what it costs); it keeps the chat-only flag and request extras it was shipped with (`shipped`, below), so a chat-only model is never sent tools; named in `notes`. Without prices that model's entry is left out, reported |
| a field embry0 now sets, a key it no longer knows, a value a bound now refuses | that provider field, or that model's entry, is left out, reported; the seed's value stands, but never for a price (below) |
| a top-level key embry0 does not know | that key is left out, reported |
| a top level that is no overlay (not a mapping, another `version`, `providers` not a mapping) | nothing of it applies: the seed, reported |

**A price fails closed.** A price is never replaced by one the deployment did not set. When a model's entry that stated `prices` or `free` is left out, the model is withheld (`Catalogue.withheld`): unpriced whatever the seed or a provider default says, so the resolver refuses it and every write that names it is refused; `refused` says so (`withheld`). When a provider's `default_price` is left out, it is set to null rather than to the seed's: the provider's models without a price of their own are unpriced. Of the two fail-closed choices, an unpriced model and a provider turned off, the unpriced model is narrower, so the provider stays on for its other models.

**What a write records.** Each write stores, beside the overlay, the shipped entry of every model the overlay names that this release ships (`provider_catalogue.shipped`, JSON), and carries over every entry an earlier write recorded of a model this release no longer ships, whether or not the overlay names it now: the records are small, and a later write naming the model again finds it. That is how a later release knows a dropped model's chat-only flag and extras. Each revision keeps the record beside its text, and an undo puts back the record of the side it restores over the one stored, so an undo brings back exactly what was read before the change. A write checks a dropped model against that record too, so writing the drifted text again keeps it, and a write without prices of its own refuses it (`no_longer_shipped`).

**JSON form.** The overlay's values are stored as JSON: a price's `from` day as its ISO text, read back as a day (`catalogue.stored_form`, `catalogue.restored`), so a dated price written in one process is in force in every other.

Every problem the release finds is attributed to its part, the parts are left out, and the rest is merged again until it merges; each pass leaves out at least one part, so it ends.

**Where it is read.** At a process's start (a store's first install), every part that applies is installed: never the bare seed for enablement while any of the overlay applies. A process already running keeps the catalogue it runs on when a later overlay this release refuses part of is written: a rolling deploy's old process reading a newer release's overlay, or a hand at the database. A write and an undo are strict, so writing the drifted text again names each part, and a write without them puts the whole overlay back in force.

**Raised, not only logged.** Each refused part is named by `GET /config/providers/catalog` (`refused`, with `in_force`: `whole`, `in_part` or `earlier`, and `in_force_generation`), logged (`provider_catalogue_refused`), audited once a generation in each process (`provider_catalogue.refused`), and counted by the gauge `embry0_provider_catalogue_refused_parts`. The gauge is seeded 0 when the process starts, so the shipped rule `Embry0ProviderCatalogueRefused` (`infra/prometheus/provider-catalogue.rules.yml`, `(max(...) or vector(0)) > 0` for 5 minutes) has a series to read from the first scrape; its `promtool` tests show it quiet at 0, firing at 2, firing on one process of two, and clearing once fixed.


The modules that still import a shipped constant read only what an overlay cannot change: a provider's name, address, prefixes or probe path (`provider_probe`, `metrics/labels`, the credential schema's list of the family), or run in the sandbox (`agents/executor`, PR 3). The sandbox reads no table once each invocation carries its provider record (PR 3).

## Pricing a run (PR 3)

**The record.** Each agent invocation carries a `ProviderRecord`, resolved by the orchestrator from the installed catalogue at the run's start: the provider's name and runtime, the id its API knows the model by (the routing prefix stripped), the model's request extras, the (input, output) $/Mtok in force that day (OpenRouter's live list first, as `model_rates` reads it; none for a withheld model), the cache factors (0.1 read, 1.25 write, embry0's), its address (an Agent SDK provider's `base_url`, a chat-completions provider's `api_root`), the sandbox variables of its key and of an operator-given API root, and when it was resolved. Anthropic's record carries its own price of the model and no address or key variable. The sandbox's executors read the record instead of their table: the executor is chosen by its runtime, a chat-completions run calls its endpoint with its key, upstream id and extras, the Agent SDK points the CLI at its address and key, and every price comes from its rates and factors. A runner that predates records ignores it; one that gets none (an orchestrator that predates it) reads its own table, as before. A record the runner cannot read is none.

**What is recorded.** The trace keeps `pricing` (`traces.pricing`, migration 154): the provider, the upstream id, the rates, the cache factors, `resolved_at` and `charged_from`, written with the running row, before the sandbox starts. No address or key variable. Past runs keep the pricing they ran with; nothing is priced again.

**Reconciling the CLI's figure.** An Agent SDK run's CLI reports `total_cost_usd`, computed from its own table of Claude models.

- Anthropic: the CLI's figure is the charge (net of a resumed session's earlier runs, #501). It knows what flat rates do not: long-context rates, cache lifetimes, a model it maps to another. The record's rates estimate each turn (`turn_usage`) as the run goes, and the trace says `charged_from: cli`.
- Any other provider on the Agent SDK (xAI's grok): the CLI's figure prices a model it does not know by Claude's rates, so it is not counted. The charge is the record's rates on the run's tokens: each API call's usage taken once by its message id (the CLI may send a call's usage again with each content block), summed over the calls this run made, so a resumed session's earlier calls are not counted again; the result's usage when no call reported one. The CLI's figure is logged beside the charge (`sdk_run_charged_from_catalogue`). Every call of such a run goes to that provider, so every call is priced at its rates; the trace says `charged_from: catalogue`.
- A chat-completions run, and grok's direct executor: the record's rates on the run's tokens, as before, now from the record.

**Older images.** An image that predates records reads `pricing_usd_per_mtok` (sent for every model routed to a provider other than Anthropic since PR 2) or its own table, and the orchestrator still prices a run such an image ended as "cost unknown" (`_priced_from_the_catalog`). Its SDK runs keep the CLI's figure. The records take effect once the sandbox images are rebuilt.

## Plan

Each PR is reviewed and merged before the next.

1. **Schema, seed and merge, no behaviour change.**
   - `catalogue.py`; `providers.yaml`, pinned by a test to the table it replaces;
   - the overlay merge and its validation;
   - `providers.py` building its constants from the seed and answering from the installed catalogue.

   `OPENAI_COMPATIBLE_PROVIDERS` still enables providers, beside the document's `enabled`. A few modules still import the shipped constants (`provider_probe`, the credential schema, `api/v1/config`, `api/app`, `metrics/labels`, `agents/executor`), which the seed answers identically until a catalogue is installed. The sandbox group names pydantic and PyYAML, which it already installed through langchain-core.
2. **Store and API** ("The stored document" above).
   - the document table with its revisions (`GET`/`PUT /config/providers/catalog`, a dry run, `If-Match`, undo), MCP tools, and installing on every write and in every process;
   - the one-time import of `OPENAI_COMPATIBLE_PROVIDERS` and `OPENROUTER_CATALOG_REFRESH_HOURS`, after which neither variable turns anything on;
   - checking settings, tier, fallback and agent writes against the stored catalogue, and refusing to take out a model that a tier, the fallback order, an agent or `background_models` names;
   - the bounds on a write; the credential code, the OpenRouter loop and the messages reading the installed catalogue.
   - a stored overlay read part by part against a moved seed ("Seed drift"), with its gauge, audit row and alert rule.
3. **Sandbox independence.** Each invocation carries its provider record: runtime, upstream id, request extras, the rates in force at its start, cache factors, and the key's variable. The rates are recorded on the trace. An SDK-runtime run (grok on the Agent SDK) is charged from the catalogue's rates and the run's tokens, not the CLI's `total_cost_usd`, and its per-turn estimates use the same rates, so re-pricing a `grok-` model changes its charge. This needs a sandbox image rebuild.

   Built ("Pricing a run" below): `ProviderRecord` (`embry0/agents/invocation.py`), resolved by `providers.provider_record` in the resolver, sent as the runner config's `provider_record` beside `pricing_usd_per_mtok` (for images that predate records), and kept on the trace as `traces.pricing` (migration 154).
4. **Credentials.** The LLM names leave `credentials_provider_check`; a credential's provider is checked against the catalogue; the frontend reads the list from the API.
5. **`custom` defaults** with the one-time import of what a deployment relied on; the local-only request fields move to per-model `request_extras`.
6. **The Providers page** edits the catalogue (models, prices, enabled, where each field comes from) with a preview, and the docs.
7. **The spent-account wait** (`account_wait` in the settings document).
