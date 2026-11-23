# ADR 0031: The provider catalogue is a document: a schema embry0 ships, a seed it moves each release, and a deployment's overlay

Status: accepted (2026-11-21)

The model providers an agent's model can route to, their models and prices are one catalogue: a document whose schema is code (`embry0/agents/catalogue.py`) and whose values are data. embry0 ships them as a seed, `embry0/agents/providers.yaml`, and a deployment's own catalogue is an overlay that names only what differs and is merged with the seed field by field, so a release that changes a price or adds a model reaches every field a deployment did not override. An overlay sets what an operator decides about a shipped provider: whether it is on, its turn and time factors, its default price, and its models (add one, change one, take one out); the provider's runtime, addresses, prefixes and probe are embry0's, because routing and the executors are built on them.

Prices are dated: the one in force at a moment is the newest whose day has come, resolved when an agent's run starts and recorded with it, and a past job is never re-priced, since its spend is stored in dollars and the budget caps were enforced on it. A model is priced or `free`, never neither: a provider that reports no cost of its own is accounted only from here (#219).

The catalogue is its own document, not a section of the settings document ([ADR 0026](0026-settings-document.md)), for the content pack's reasons ([ADR 0027](0027-progression-content-pack.md)): it is long, its defaults move every release, and a price is not policy. Two things are never in it: an address an operator gives (a server of their own is reached through its credential) and where a key is read from (code). So any API key may read the catalogue; only an admin writes it.

Consequences: adding a model or changing a price needs no release, and, once each run carries its rates, no sandbox image rebuild. Enabling a provider moves from `OPENAI_COMPATIBLE_PROVIDERS` into the document, imported once. Tiers, per-model enablement and the fallback order stay in `provider_config`.

Evidence: `embry0/agents/catalogue.py`; `embry0/agents/providers.yaml`; [the design](../superpowers/specs/2026-11-23-provider-catalogue-design.md); issue #173.
