# Athanor

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask me follow-up questions until you reach that confidence.
- Build this as modular as reasonably possible. Each subsystem should have clear boundaries, well-defined interfaces, and be independently testable.
- Never defer issues for later. If a code review or self-review finds a problem, fix it now before moving on. No "tracked for later" or "acceptable for now" — all identified issues get resolved immediately.

## Environment

- The user works from a laptop SSH-ed into a home server where Claude Code runs. When starting local servers (dev servers, visual companions, etc.), bind to `0.0.0.0` so they are accessible over the local network. Use `--host 0.0.0.0 --url-host 0.0.0.0` for the brainstorming visual companion.
- `ENVIRONMENT_SECRET_KEY` drives Fernet encryption for per-repo env var secrets. If unset, Athanor falls back to an insecure default and logs a warning. Rotating the key makes prior secrets undecryptable.
- Agents can pause the pipeline to ask the user questions (`athanor.sandbox.ask_user`). Capped at 5 rounds per job to prevent runaway loops; after the cap the job fails with `ERR_MAX_AGENT_QUESTIONS`.
- Reserved env var keys (`ATHANOR_GIT_PROXY_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `GITHUB_TOKEN`) are blocked as user-settable env vars both at the API and at sandbox injection — don't attempt to override infrastructure variables via the environment UI.

## Deployment

- Athanor runs in Docker via `infra/docker-compose.yml`. After any code changes, you **must** rebuild and restart the affected containers:
  - Backend changes: `cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate`
  - Frontend changes: `cd infra && docker compose build frontend && docker compose up -d frontend --force-recreate`
  - Both: `cd infra && docker compose build orchestrator frontend && docker compose up -d orchestrator frontend --force-recreate`
- The frontend is served via nginx on port 8200 and proxies `/api` to the orchestrator container.
- Always verify health after restart: `curl -s http://localhost:8200/health`
- The Cloudflare tunnel at `athanor.example.com` is restricted to webhook paths only (`/api/v1/webhook`, `/api/v1/telegram/callback`). NEVER expose the full app via the tunnel.
- For local development without a public URL, use the smee.io relay: set `DEV_MODE=true` and leave `GITHUB_WEBHOOK_SECRET` empty, then run `npx smee-client --url https://smee.io/<channel> --target http://localhost:8200/api/v1/webhook`. `DEV_MODE=true` is required because smee re-serializes the payload, invalidating GitHub's HMAC signature. See README "Webhook Setup" for full steps. Never use this configuration in production.

## Sandbox

- After code changes to `athanor/sandbox/` or `athanor/safety/`, rebuild the sandbox image and load into DinD:
  ```bash
  docker build -t athanor-sandbox:latest -f infra/Dockerfile.sandbox .
  cd infra && docker save athanor-sandbox:latest | docker compose exec -T orchestrator docker --host tcp://dind:2376 --tlsverify --tlscacert=/certs/client/ca.pem --tlscert=/certs/client/cert.pem --tlskey=/certs/client/key.pem load
  ```
- The sandbox uses OAuth via `CLAUDE_CODE_OAUTH_TOKEN` env var (read from `~/.claude/.credentials.json` on the orchestrator).
- Git auth flows via the credential proxy (port 9101 on the orchestrator): the sandbox's git credential helper curls `$ATHANOR_GIT_PROXY_URL/git-credentials`. `GITHUB_TOKEN` never enters the sandbox env. The proxy is managed by `ProxyManager` in `athanor/execution/proxy/manager.py`.
- Read-only rootfs is disabled (`read_only_root: False`) because Claude CLI needs writable fs.
