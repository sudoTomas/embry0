# Model providers (EMB-36)

embry0's agent runtime is the Claude Code CLI (via `claude-agent-sdk`),
which honors `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` — so any
Anthropic-SDK-compatible backend is **provider configuration**, not a new
runtime. MCP servers (Playwright QA), the Ring-3 safety hook, session
resume, and token/trace plumbing all apply unchanged.

## Registry

`embry0/agents/providers.py`. Routing is per-agent by model id: when a
resolved model matches a provider prefix (`grok-*` → xAI), the in-sandbox
executor overlays `{ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY}` on the CLI
subprocess for that agent only (`ClaudeAgentOptions.env`). Other agents
in the same job keep their normal Anthropic auth.

| Provider | base_url | key (orchestrator `.env`) | models |
|---|---|---|---|
| xai | `https://api.x.ai` | `XAI_API_KEY` | `grok-4.5` ($2/M in, $6/M out) |

## Using grok on a job

```json
POST /api/v1/jobs
{"repo": "...", "task": "...", "agent_models": {"developer": "grok-4.5"}}
```

or via triage's `pipeline_config.agent_models`. `GET /config/models`
lists providers with an `available` flag (key configured or not).

## Mechanics & caveats

- The provider key is injected into the **sandbox container env** at
  create (reserved key — user repo env can never set or override it) and
  read by the executor in-sandbox; it is never serialized through the
  invocation argv. Missing key ⇒ the agent run fails closed before any
  spend.
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
  dev→review loop + one QA run on grok to confirm tool-use fidelity.
  Requires `XAI_API_KEY` in the orchestrator `.env`.

## SuperGrok OAuth path (EMB-45/EMB-46)

When `xai_proxy_enabled=true` the orchestrator runs an `xai-proxy`
sidecar that injects the rotating SuperGrok subscription bearer at
egress; the orchestrator owns the rotating refresh token in a
Fernet-encrypted store (seeded once from the Grok CLI's auth.json).
Sandboxes authenticate to the proxy with a per-sandbox bearer delivered
to `~/.embry0/xai_proxy_token` (`embry0/sandbox/xai_token.py`).

**Default grok runtime (EMB-46): the Agent SDK over the proxy.** When
the proxy is live, provider-routed grok agents run through the normal
`SdkAgentExecutor` with a per-agent env overlay on the CLI subprocess:
`ANTHROPIC_BASE_URL` = the proxy, `ANTHROPIC_AUTH_TOKEN` = the sandbox
bearer, and `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` stripped — no
Anthropic-subscription OAuth and no console key anywhere on the grok
path, while MCP (Playwright QA), session resume, and the Ring-3 hook
stay native.

**Opt-in fallback: `XAI_DIRECT_EXECUTOR=true`** (orchestrator `.env`)
routes grok to `DirectXaiExecutor` (`embry0/agents/executor_xai.py`)
instead — an embry0-owned, CLI-free Messages-API tool loop with its own
builtin tools, `gate_tool_call` enforcement, token-based cost, and an
MCP stdio client (`embry0/agents/mcp_client.py`) for Playwright parity.
Verified green E2E on 2026-07-22; kept as insurance against a CLI
release breaking on xAI's compat endpoint.

With the proxy down, grok agents fall back to the EMB-36 CLI +
`XAI_API_KEY` path above.
