---
name: setup-embry0
description: Set up a running embry0 deployment from this session over its MCP server (model access, a forge credential, an organization and its repositories, webhooks, testing and spend limits), then check each step. Use when the person asks to set up, configure or connect embry0, or to add an organization or repositories to a deployment.
---

# Set up embry0

The procedure is one text: `SETUP_PROMPT` in `embry0/mcp_server/setup_prompt.py`, the same text the MCP prompt `/mcp__embry0__setup` serves. Read it and follow it. The human guide is `docs/claude-code.md`.

Before the procedure:

1. Check the MCP connection. If tools named `mcp__embry0__*` are available, call `health` through them. If they are not, the person connects the server in their own terminal, as "Connect the session" in `docs/claude-code.md` shows, and starts a new session. Never take the API key in this chat: a key typed here is kept in the transcript.
2. If this checkout is the deployment's own and the stack is not running (`docker compose -f infra/docker-compose.yml ps` lists no `embry0-orchestrator`), stand it up with the person first: the quick start in `README.md`, or `docs/ops/provisioning.md` for a new host.
3. Settings in the root `.env` (`WEBHOOK_PUBLIC_URL`, `GITHUB_WEBHOOK_SECRET`, `DEFAULT_AUTH_MODE`) take a restart. Change them only on the person's word, back the file up first, and never print a value from it.

Secrets never pass through this session: the person adds each credential on the dashboard or with the API from their own terminal, and you bind it by id.
