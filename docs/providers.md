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

## Direct-xAI executor (EMB-45)

The OAuth-subscription path replaces the CLI subprocess for grok agents:
when `xai_proxy_enabled=true` the orchestrator runs an `xai-proxy` sidecar
that injects the rotating SuperGrok bearer at egress (Phase A), and any
agent resolved to `provider == "xai"` in a sandbox that has
`EMBRY0_XAI_PROXY_URL` routes to `DirectXaiExecutor`
(`embry0/agents/executor_xai.py`) — an embry0-owned tool-use loop with its
own Read/Write/Edit/Bash/Glob/Grep tools, per-call `gate_tool_call`
enforcement, and token-based cost (Phase B). When the agent's definition
declares `mcp_servers` (the QA agent's playwright-mcp stdio server), the
executor spawns them via `embry0/agents/mcp_client.py`, exposes their
tools as `mcp__<server>__<tool>` filtered to the agent allowlist, and
routes matching tool calls over MCP — giving grok QA agents Playwright
parity with the CLI path (Phase C). The executor authenticates to the
proxy with the per-sandbox bearer delivered to
`~/.embry0/xai_proxy_token` (`embry0/sandbox/xai_token.py`); with the
proxy down, grok agents fall back to the EMB-36 CLI + `XAI_API_KEY`
path above.
