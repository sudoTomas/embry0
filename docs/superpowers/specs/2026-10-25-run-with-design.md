# Run with: choose the model and the key for work started from the dashboard

**Goal.** A person who starts agent work from the dashboard can say what it runs on: one of the models embry0 offers and one of the LLM keys stored in embry0, say the team's OpenRouter key instead of the Anthropic account the bindings would pick. Every button that starts agent work offers it; left alone, the work runs as it always has.

**Non-goals.** No per-agent picker on the buttons (`POST /jobs` keeps `agent_models` for that). No new kind of key or binding. No change to the scheduled loops, the webhooks or the trackers' own triggers: they carry no person's choice.

## The choice

`run_with: {model?, credential_id?}` on every route that starts agent work: jobs (every pipeline), issue triage, a watcher draft's accept, an owner proposal's accept, the test-setup proposal, a standards audit, a context-audit run, the organization onboard action, and a watcher tick by hand.

- **The model** runs every agent of the job. It rides as `agent_models_override["*"]`, the lowest job-level entry: an agent the request names itself keeps its model (the setup proposal's onboarding agent takes the chosen model in place of its medium tier). It must be priced and enabled, the checks an agent model already passes.
- **The key** serves its provider for the job. It must be a usable LLM key bound to the repository, its owner, or the global pool (the keys the repository's jobs can already reach), and of the chosen model's provider when both are chosen.
- Both are checked before anything starts, with a 422 that says what to change.

## How it runs

The job's `pipeline_config.run_with` holds what was chosen, with the key's provider and label. The credential resolver reads a job's pins by its id: before a sandbox exists, and whenever another sandbox of the job resolves, the chosen key is served for its provider ahead of every binding. A chosen key that is disabled, quarantined, gone or undecryptable fails the job (`ERR_CREDENTIAL_UNAVAILABLE`), as a pinned binding does: a person chose that key, and another one must not run in its place. A derived sandbox takes the job's chosen key over its parent's.

Routes that reach the executor through shared paths (the GitHub and Linear accepts, the retry, the PR-review dispatcher, the context-audit schedule, the watcher) pass the choice only when there is one, so a webhook's dispatch is the call it always was.

## The dashboard

One control, "Run with: each agent's model, its usual key", next to each such button: closed it is one line and reads nothing; open, a model select (the offered models with their provider) and a key select (the stored keys the repository may use, of the chosen model's provider, with their scope; disabled ones shown and not selectable). Changing to a model of another provider drops a key that cannot serve it. Where one control serves a list of several repositories (the proposals lists, the watcher's tick, the org panel, the standards table), it offers the keys every repository may use. The job page says "Ran with <model> · <key>".

## Tests

The choice on the job and read back; every agent runs the model unless named; the scopes; the resolver serving the chosen key, outranking a parent's, and failing the job for each way it cannot serve; the keys route; the 422s; the routes handing the choice on, and the usual run unchanged; the control (closed reads nothing, keys by provider, dropping a mismatched key, disabled keys); a proposal's yes carrying it; the job page.
