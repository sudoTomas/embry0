---
name: connect
description: Connect this Claude Code to a running embry0 deployment and check its MCP server answers, or find out why it does not. Use when the person asks to connect, check or fix the embry0 connection, or when the embry0 tools are missing or fail.
---

# Connect to embry0

The plugin's MCP server is `<url>/mcp` with the header `Authorization: Bearer <api_key>`. Both values are the plugin's options, asked in the plugin dialog when it is enabled; the key is a sensitive option, kept out of the settings files. Never ask for the key in this chat and never put it in a command: a key typed here stays in the transcript.

## 1. Check

Look for the plugin's tools: names that start with `mcp__plugin_embry0_embry0__` (Claude Code names them after the plugin name, then the server name in the plugin's `.mcp.json`).

- They exist: call `health`, then `stats`. `health` answers when the deployment is up; `stats` needs a valid key. The server answers 401 to a request without a key, and lists its tools for any non-empty key, valid or not, so a connected server alone does not prove the key works. Report both results, the deployment's `total_jobs` and `queue_depth`, and stop here.
- They do not exist, or a call fails: go on.

```bash
claude mcp list 2>&1 | grep -i embry0      # the plugin's server and its state
claude plugin configure embry0@embry0      # which options are set; prints no secret
```

Use the plugin id `claude plugin list` shows, if it differs.

## 2. Fix what the check shows

| What you see | What it means | What to do |
|---|---|---|
| The server is not listed | The plugin is not installed or is disabled | `/plugin` in this window, then install or enable embry0 |
| Only a hand-added embry0 server is listed, no `plugin:embry0:embry0` | A server added by hand has the plugin's URL, so Claude Code skips the plugin's server and uses that one (the `/plugin` menu shows it as skipped) | `claude mcp remove <name>` with the hand-added server's name, then restart Claude Code |
| Failed to connect, HTTP 401 | The key option is unset or empty | The person runs `/plugin configure embry0@embry0` and fills in the key |
| Failed to connect, ECONNREFUSED or a timeout | Nothing answers at the URL | Check the URL in the options; `curl -fsS <url>/api/v1/health/ready` |
| Failed to connect, HTTP 502 | The deployment answers, its `mcp` service does not | On the deployment's host: `docker compose -f infra/docker-compose.yml ps mcp`, then its logs |
| 401 on a tool call | The key is wrong, revoked or expired | A new key on the dashboard (Settings, then API keys), then `/plugin configure embry0@embry0` |
| 403 on a tool call | The key's scope is too narrow | A key with the admin scope to set up, operate or read to use |
| Every tool twice, under two prefixes | An embry0 server added by hand at another spelling of the URL loads next to the plugin's | `claude mcp remove <name>` with the hand-added server's name (`embry0` in the guide's command) |

The options can also be set from a terminal, outside this chat. The key is read so it is never shown, and only `printf`, a shell builtin, expands it: an argument of a program shows in `ps` while the program runs.

```bash
read -rs EMBRY0_API_KEY
printf '%s' "$EMBRY0_API_KEY" | jq -Rs --arg url "https://embry0.example.com" '{url: $url, api_key: .}' |
  claude plugin configure embry0@embry0 --values-stdin
unset EMBRY0_API_KEY
```

No deployment yet: the `install` skill of this plugin stands one up on this machine and fills in both options.

After a change in the plugin dialog, check with `/mcp` that the server reconnected, and restart Claude Code if it did not. After `claude plugin configure` in a terminal, the person restarts Claude Code. Then run the check again.
