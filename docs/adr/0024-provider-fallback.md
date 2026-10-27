# ADR 0024: An agent whose provider is paused runs on the next ready model of an operator's fallback order

Status: accepted (2026-10-25)

A job used to fail when its provider's account was spent or rate-limited, though another provider with a usable key could have done the work. The provider settings hold `fallback_models`, an ordered list of catalogue models, empty and so off by default. Set, an agent whose model's provider is paused for the job (every account bound for it quarantined or disabled with no other source, or an earlier run of the job on it ended spent or rate-limited within the quarantine window) runs on the first model of the list that is of another provider, priced, with tool calling, enabled, and whose provider is not paused and whose key the job's sandbox holds. The check runs at the account pre-check before a sandbox exists, and again within a run when an agent's account turns out spent. A model or key a person chose (the job's `agent_models`, a ticket's model label, Run with) never moves.

Consequences: a substituted run counts against the job's budget like any run, and is recorded on the trace (`fallback`), in a `progress` event and in the log (`agent_model_provider_fallback`). With nowhere to move, the job waits for the account (ADR 0025) instead of failing. `PUT /config/provider` refuses a list with an unknown, unpriced, chat-only, disabled or repeated model.

Evidence: `embry0/agents/fallback.py`; [Provider fallback](../providers.md#provider-fallback); [the design](../superpowers/specs/2026-10-25-provider-fallback-design.md).
