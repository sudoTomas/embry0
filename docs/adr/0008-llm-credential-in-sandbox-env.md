# ADR 0008: The agent's LLM credential is in the sandbox env, and nothing inside the sandbox protects it

Status: accepted (2026-10-08)

A sandbox's environment carries the agent's LLM credential: `CLAUDE_CODE_OAUTH_TOKEN` for a Claude subscription, or `ANTHROPIC_API_KEY`, `XAI_API_KEY` for an xAI API key, and an OpenAI-compatible provider's key under a reserved `EMBRY0_LLM_` name (`SandboxManager._inject_llm_env`). Subscription OAuth stays supported, and the Claude CLI reads its credential from its environment, so the credential is not proxied. Public egress is open, so any code a job runs can read it and send it out. The command denylist and the CLI's permission rules are therefore defence in depth, not the compensating control that [ADR 0002](0002-credential-proxies-per-sandbox-enrollment.md) called them, and [`SECURITY.md`](../../SECURITY.md) lists this exposure as accepted. Only the xAI subscription token stays behind `xai-proxy`.

Evidence: the owner's decision of 2026-10-08 to keep subscription OAuth (#534), and #525, which found the token injected as-is while `SECURITY.md` said credentials never enter the sandbox. This refines ADR 0002.
