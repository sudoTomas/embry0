# ADR 0023: A sandbox reaches the operator's own model server through a proxy, not an egress exception

Status: accepted (2026-10-25). Amends ADR 0018 for the `custom` provider.

ADR 0018 let a `custom` OpenAI-compatible endpoint be reached by opening its host as an egress exception for the job's sandbox. An operator's own model server is usually shared hardware on the operator's network, so an exception for its host opens every port and service on that machine to code a job runs, and the server's key would sit in the sandbox. When `LOCAL_LLM_UPSTREAM` names the server's API root, the orchestrator starts a `local-llm-proxy` on `sandbox-restricted` that forwards to that one root and nowhere else: only `POST /v1/chat/completions` and `GET /v1/models`, only for a sandbox enrolled with it (its job resolved the `custom` provider), with the sandbox's bearer checked and dropped and the server's key (`LOCAL_LLM_API_KEY`) added by the proxy, and a request over `LOCAL_LLM_MAX_REQUEST_BYTES` refused with 413. No egress exception is declared for the server's machine. A run on the `custom` provider holds a slot in `local_model_leases` while it runs, at most `local_max_runs` at once, because one server serves a few runs well and many badly.

Consequences: a sandbox never holds the server's address or key. The custom provider still needs an account, which makes it ready, pinnable and quarantinable. Calls the orchestrator makes itself (proposal and job briefs, the Slop-O-Meter's judgments) do not go through the proxy: they use the account's base URL and key and take no slot.

Evidence: `embry0/execution/proxy/local_llm_proxy.py`; the enrolment in `embry0/execution/proxy/manager.py`; `embry0/storage/repositories/local_model_leases.py`; [Local models](../providers.md#local-models).
