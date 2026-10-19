# ADR 0018: OpenAI-compatible providers run on embry0's own tool loop, off until enabled

Status: accepted (2026-10-18)

Agents ran on the Claude CLI, which speaks only the Anthropic API, so a provider was usable only if it served that API. Most providers and every self-hosted model server serve OpenAI chat completions instead. They run on `OpenAICompatibleExecutor`, which drives the tool loop the CLI-free xAI executor already used over chat completions, rather than on a gateway that translates them into the Anthropic API.

Each provider is a catalogue entry, routed by model id prefix, and is off until `OPENAI_COMPATIBLE_PROVIDERS` names it, so an existing deployment routes every model as before. Their keys reach the sandbox under the reserved `EMBRY0_LLM_` names, leaving a repository's own `OPENAI_API_KEY` alone. A custom endpoint's host becomes an egress exception for the job that uses it. xAI joins the family for its probe; its agents keep their runtime.

Evidence: `embry0/agents/providers.py`, `embry0/agents/executor_openai.py`, migration 102.
