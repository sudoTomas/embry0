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

**What an overlay may set.** Of a shipped provider: `enabled`, `factors` (key by key), `default_price` (`null` takes it out) and `models`. A model the seed has takes each field the overlay gives it, with `prices` and `request_extras` replaced whole. A model made `free` drops its prices, and one given prices is no longer free. A new model must be complete, and `null` takes a model out. Everything else of a provider (runtime, addresses, prefixes, how its ids reach its API, its probe) is embry0's, because routing and the executors are built on it: an overlay that names one is refused. Providers embry0 does not ship are refused until the credential and address work below exists.

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

## Readers

`embry0.agents.providers` keeps its functions and answers every one from the catalogue installed in the process (`catalogue.current()`): the seed until a deployment's own is installed. The orchestrator installs the merged catalogue at startup and after every write. The sandbox reads no table once each invocation carries its provider record (PR 3).

## Plan

Each PR is reviewed and merged before the next.

1. **Schema, seed and merge, no behaviour change.**
   - `catalogue.py`; `providers.yaml`, pinned by a test to the table it replaces;
   - the overlay merge and its validation;
   - `providers.py` building its constants from the seed and answering from the installed catalogue.

   `OPENAI_COMPATIBLE_PROVIDERS` still enables providers, beside the document's `enabled`. A few modules still import the shipped constants (`provider_probe`, the credential schema, `api/v1/config`, `api/app`, `metrics/labels`, `agents/executor`), which the seed answers identically until a catalogue is installed. The sandbox group names pydantic and PyYAML, which it already installed through langchain-core.
2. **Store and API.**
   - the document table with its revisions (`GET`/`PUT /config/providers/catalog`, a dry run, `If-Match`, undo), MCP tools, and installing on every write;
   - the one-time import of `OPENAI_COMPATIBLE_PROVIDERS` and `OPENROUTER_CATALOG_REFRESH_HOURS`;
   - checking settings writes against the stored catalogue, and refusing to take out a model that a tier, the fallback order, an agent or `background_models` names;
   - the remaining constant readers moved onto the installed catalogue.
3. **Sandbox independence.** Each invocation carries its provider record: runtime, upstream id, request extras, the rates in force at its start, cache factors, and the key's variable. The rates are recorded on the trace. This needs a sandbox image rebuild.
4. **Credentials.** The LLM names leave `credentials_provider_check`; a credential's provider is checked against the catalogue; the frontend reads the list from the API.
5. **`custom` defaults** with the one-time import of what a deployment relied on; the local-only request fields move to per-model `request_extras`.
6. **The Providers page** edits the catalogue (models, prices, enabled, where each field comes from) with a preview, and the docs.
7. **The spent-account wait** (`account_wait` in the settings document).
