# Configuration

embry0 uses environment variables for infrastructure config and API endpoints for runtime config.

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER_MODE` | `anthropic_api` | LLM provider: `anthropic_api`, `claude_max`, `ollama` |
| `ANTHROPIC_API_KEY` | — | API key (for `anthropic_api` mode) |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token (for `claude_max` mode); generate with `claude setup-token` |
| `GITHUB_TOKEN` | — | GitHub personal access token |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `AUTH_DEV_MODE` | `false` | Bypass API key authentication. NEVER use in production. |
| `WEBHOOK_DEV_MODE` | `false` | Bypass webhook HMAC verification. Required for smee.io relay. NEVER use in production. |
| `DATABASE_URL` | `postgresql://embry0:embry0@postgres:5432/embry0` | PostgreSQL connection |
| `MAX_BUDGET_USD` | `20.0` | Default per-job budget |
| `DAILY_BUDGET_CAP_USD` | `100.0` | Daily spending cap |
| `MONTHLY_BUDGET_CAP_USD` | `500.0` | Monthly spending cap |
| `BUDGET_OVERRUN_MODE` | `soft` | `soft` (allow finish) or `hard` (stop immediately) |
| `RATE_LIMIT_PER_AUTHOR_PER_HOUR` | `20` | Issue-author rate limit |
| `API_RATE_LIMIT_PER_MINUTE` | `300` | API request rate limit |
| `MAX_WEBHOOK_BODY_BYTES` | `5242880` | Webhook request-body ceiling (bytes) |
| `ASK_USER_ROUNDS_CAP` | `10` | Agent ask-user rounds per job before `ERR_MAX_AGENT_QUESTIONS` |
| `USER_RETRY_ROUNDS_CAP` | `5` | User "continue retrying" rounds per job |
| `PROD_PORT` | `8200` | Frontend port (nginx) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for notifications |
| `TELEGRAM_WEBHOOK_URL` | — | Public URL for Telegram callback (e.g. Cloudflare tunnel) |
| `TRIGGER_LABELS` | `embry0` | GitHub labels that trigger jobs |
| `ENVIRONMENT_SECRET_KEY` | — | Fernet key for encrypting env var secrets at rest |
| `API_KEY` | — | **Required in production** — bearer key for the orchestrator API. Empty is only tolerated when a dev-mode flag is set. Generate with `python -c 'import secrets; print(secrets.token_hex(32))'`. |
| `PROXY_ADMIN_TOKEN` | — | Required, gates the credential proxies' admin endpoints. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. |
| `PAUSED_JOB_TTL_HOURS` | `48` | Hours before a paused job's sandbox is expired |

See `.env.example` for the complete list.

## Secrets at rest

embry0 encrypts per-repo secret environment variables at rest using Fernet, keyed by `ENVIRONMENT_SECRET_KEY`:

```bash
# Generate a strong key (or use your favorite secret manager)
openssl rand -hex 32
```

In production the orchestrator refuses to start without a real key; in dev mode a default is tolerated with a loud warning. Rotating the key will make previously-encrypted secrets undecryptable; the orchestrator will log `secret_decryption_failed` for each affected key on the next job that needs them.

Per-repo and global env vars are managed through the `/environments` page in the dashboard.

## Runtime configuration (API)

Budget controls, context injection, and sandbox profiles are configurable via the API without restarting:

- **Budget** — `GET/PUT /api/v1/config/budget`
- **Context** — `GET/PUT /api/v1/config/context` (global), `/config/context/repos/{repo}` (per-repo)
- **Sandbox Profiles** — `CRUD /api/v1/sandbox-profiles`

See [api.md](api.md) for request examples.

## Agent execution & auth modes

embry0 invokes Claude through a pluggable executor layer with two orthogonal config dimensions:

| Dimension | Values | Meaning |
|---|---|---|
| `execution_mode` | `sdk` (default), `cli` (Phase 2) | Agent SDK Python wrapper vs direct `claude -p` CLI subprocess |
| `auth_mode` | `oauth` (default), `api_key` | Claude Max OAuth token vs Anthropic API key |

All 4 combinations are valid in Phase 2+. **Phase 1 supports `sdk` only**; requesting `cli` at any level raises `ERR_INVALID_CONFIG` at resolve time.

### Five-level precedence

Later levels win:

1. **Global** — `Embry0Config.default_execution_mode`, `.default_auth_mode` (env vars `DEFAULT_EXECUTION_MODE`, `DEFAULT_AUTH_MODE`).
2. **Per-repo** — `repo_preferences.execution_mode`, `.auth_mode` columns.
3. **Per-job** — `JobCreateRequest.execution_mode_override`, `.auth_mode_override`.
4. **Per-agent-type** — `agent_definitions.execution_mode`, `.auth_mode` columns.
5. **Pipeline config (triage output)** — `pipeline_config.execution_modes[agent_type]`, `.auth_modes[agent_type]`.

NULL at any level falls through to the previous level.

### Credentials

- `auth_mode=oauth` requires `CLAUDE_CODE_OAUTH_TOKEN` in `.env` (generate one with `claude setup-token`). Missing token → `ERR_MISSING_OAUTH_TOKEN`.
- `auth_mode=api_key` requires `ANTHROPIC_API_KEY` in `Embry0Config`. Missing key → `ERR_MISSING_API_KEY`.

### Safety policy (three rings)

1. **Container isolation** — ephemeral Docker sandbox, non-root, tmpfs workspace.
2. **Declarative permissions** — rendered into `/workspace/.claude/settings.json` per run (`permissions.allow` / `permissions.deny`). Enforced by Claude Code before tool dispatch.
3. **Programmable hook** — `evaluate_policy()` runs dangerous-Bash-pattern checks as a `PreToolUse` callable. Fail-closed.

Both execution modes consume the same `SafetyPolicy` data structure via different delivery mechanisms.

## CLI reference

### Production stack (`embry0`)

```bash
embry0 start              # Start the full stack
embry0 start --port 8201  # Start on a custom port
embry0 stop               # Stop the stack
embry0 build              # Build images (clean, no cache)
embry0 build --cached     # Build with Docker cache
embry0 build-sandbox      # Rebuild sandbox image inside DinD
embry0 health             # Check stack health
embry0 config             # Validate and display config (secrets masked)
embry0 purge              # Remove all Docker artifacts
embry0 purge --volumes    # Remove only volumes
```

### Development (`./lab`)

```bash
./lab up      # Start the dev container
./lab down    # Stop the dev container
./lab claude  # Open Claude Code in the container (default)
```
