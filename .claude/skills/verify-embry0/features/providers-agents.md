# Providers and agents

The LLM providers page lists each provider in the catalogue with a live probe, per-model toggles and tier models; agent definitions name a model, and the model's prefix decides the provider that serves it.

## Sub-features

- `prov-list` lists catalogue providers with their models and probe state.
- `prov-models` lists enabled models and tier assignments from `/api/v1/config/models`.
- `agents-list` lists agent definitions with their model and, on branches that carry it, the provider and its readiness.
- `agents-form` offers catalogue models plus a custom id when editing an agent.

## How to get to it (user POV)

- `LLM providers` under Settings in the sidebar.
- `Agents` under Infra in the sidebar, and an agent's edit form.
- The API: `/api/v1/config/models`, `/api/v1/agents`.

## Driving it with control-embry0

Preconditions:

- `doctor` reports `worth_driving: true`.

- **Providers page.** Run `control-embry0 probe --route /providers`. `breadcrumbs` ends with `LLM providers` and `errors` is empty.
- **Models.** Run `control-embry0 api /api/v1/config/models`. Every model in the response belongs to a provider the page lists.
- **Agents.** Run `control-embry0 api /api/v1/agents` and `control-embry0 read --route /agents --selector main`. Every agent's model in the API appears on the page.
- **Readiness.** On a branch that adds it, each agent in `/api/v1/agents` carries its provider and a ready flag, and the page shows the same.

## Gotchas

- The provider probe button calls the provider; it is a write-free call but it spends a request, so the recipe reads the stored probe state instead of pressing it.
- A model id whose prefix no catalogue provider declares routes to Anthropic, the implicit default; read the resolved provider, not the id.
