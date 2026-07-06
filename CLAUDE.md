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
- **API Auth flags (2026-04-28 split).** `AUTH_DEV_MODE` and `WEBHOOK_DEV_MODE` are independent dev-mode flags (split from the legacy `DEV_MODE` in 2026-04-28). The legacy variable is honoured for one release as a compat shim; both new flags default to `false`. Do not enable in production — startup logs `CRITICAL` and writes a `dev_mode_enabled` audit row when either is true. `AUTH_DEV_MODE=true` bypasses API key authentication; `WEBHOOK_DEV_MODE=true` bypasses webhook HMAC verification and is required for the smee.io relay flow.

## Deployment

- Athanor runs in Docker via `infra/docker-compose.yml`. After any code changes, you **must** rebuild and restart the affected containers:
  - Backend changes: `cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate`
  - Frontend changes: `cd infra && docker compose build frontend && docker compose up -d frontend --force-recreate`
  - Both: `cd infra && docker compose build orchestrator frontend && docker compose up -d orchestrator frontend --force-recreate`
- The frontend is served via nginx on port 8200 and proxies `/api` to the orchestrator container.
- Always verify health after restart: `curl -s http://localhost:8200/health`
- The Cloudflare tunnel runs as a containerized service (`athanor-cloudflared` in compose). Configuration (hostname, target service, allowed paths) lives in the Cloudflare-side tunnel config, not in this repo. To provision a tunnel for a fresh deployment, use `~/workspace/cloudflare/scripts/tunnel-create.sh` and paste the resulting token into `.env` as both `CLOUDFLARED_TUNNEL_TOKEN` and `TUNNEL_TOKEN`. Webhooks-only by design — the orchestrator's full API and the dashboard stay LAN-only at `:8200`.
- For local development without a public URL, use the smee.io relay: set `WEBHOOK_DEV_MODE=true` and leave `GITHUB_WEBHOOK_SECRET` empty, then run `npx smee-client --url https://smee.io/<channel> --target http://localhost:8200/api/v1/webhook`. `WEBHOOK_DEV_MODE=true` is required because smee re-serializes the payload, invalidating GitHub's HMAC signature. See README "Webhook Setup" for full steps. Never use this configuration in production.

## Sandbox

- The `athanor-sandbox` and `athanor-proxy` images are pushed to the in-stack `registry:5000` sidecar at bootstrap by the `init-push-images` compose service; DinD pulls them on first reference. The `IMAGE_REGISTRY` env var (defaults to `registry:5000`) controls the prefix at orchestrator-launch time — leave at default for compose, set to your registry URL on K8s.
- **First-time bootstrap (fresh deploy).** From `infra/`:
  ```bash
  cd infra
  docker compose --profile images build   # builds frontend, orchestrator, sandbox, proxy
  docker compose up -d                    # registry → init-push-images → dind → orchestrator → frontend
  ```
- **After code changes to `athanor/sandbox/` or `athanor/safety/`** (rebuild + repush + restart orchestrator):
  ```bash
  cd infra
  docker compose --profile images build sandbox-image-builder dev-python-image-builder
  docker compose up -d --force-recreate init-push-images orchestrator
  ```
  `dev-python-image-builder` must build alongside the base sandbox: `init-push-images` pushes `athanor-sandbox-dev-python:latest` too, and fails startup if the tag is missing from the host daemon.
- **After code changes to `athanor/execution/proxy/`**:
  ```bash
  cd infra
  docker compose --profile images build proxy-image-builder
  docker compose up -d --force-recreate init-push-images orchestrator
  ```
- Proxies (`git-proxy`, `github-proxy`, `auth-proxy`) run as DinD containers on the `sandbox-restricted` network. Sandboxes reach them by Docker DNS (`http://git-proxy:9101`, etc.), not via `host.docker.internal`. Restart the orchestrator after changing `GITHUB_TOKEN` or `ANTHROPIC_API_KEY` in `.env` — the proxies are recreated on each `proxy_mgr.start()`.
- The sandbox uses OAuth via `CLAUDE_CODE_OAUTH_TOKEN` env var (read from `~/.claude/.credentials.json` on the orchestrator).
- Git auth flows via the credential proxy: a `git-proxy` container running in DinD on the `sandbox-restricted` network injects the orchestrator's `GITHUB_TOKEN` when the sandbox's git credential helper curls `$ATHANOR_GIT_PROXY_URL/git-credentials` (which resolves to `http://git-proxy:9101` via Docker DNS). `GITHUB_TOKEN` never enters the sandbox env. The proxy is managed by `ProxyManager` in `athanor/execution/proxy/manager.py`.
- Read-only rootfs is disabled (`read_only_root: False`) because Claude CLI needs writable fs.
- **Proxy enrollment.** As of 2026-04-28, the credential proxies (git-proxy, github-proxy, auth-proxy) require a per-sandbox bearer token enrolled via the orchestrator. The shared admin secret `PROXY_ADMIN_TOKEN` (`.env`) gates the proxies' `/admin/enroll` endpoints. Required in production; auto-generated in `AUTH_DEV_MODE=true` or `WEBHOOK_DEV_MODE=true` with a warning. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.

## QA — repo integration (`.athanor/qa.yaml`)

- A target repo opts into the QA pipeline by committing a `.athanor/qa.yaml` (schema **v2**) at its root — it declares the `workspace_provider`, per-app `boot_command`/`frontend_url`/`ready_checks`, and acceptance criteria. That file is the whole integration contract.
- **Full field-by-field reference + a worked `command-center` (24-app monorepo) example:** [`docs/qa-yaml-reference.md`](docs/qa-yaml-reference.md). Authoritative schema is `athanor/workflows/qa/qa_yaml_v2.py`; merge order is `qa_yaml_resolve.py`; examples live in `tests/fixtures/qa-yaml-corpus/v2/`.
- **v1 is dead** — read only by the migrator (`athanor migrate-qa-config`). The README's `.athanor/qa.yaml` snippet still shows v1; the reference doc supersedes it.

## Postgres Backup / Restore

Athanor runs a daily `pg_dump` via the `athanor-postgres-backup` compose service. Backups are stored in the `backup-data` named volume:

- `/backups/daily/` — 7 most recent daily dumps
- `/backups/weekly/` — 4 most recent Sunday dumps
- `/backups/monthly/` — 6 most recent month-end dumps
- `/backups/last/` — symlink to the most recent dump

**Restore from the latest backup:**
```bash
# List available backups
docker exec athanor-postgres-backup ls -lh /backups/last/

# Restore (replace <pass> with actual POSTGRES_PASSWORD from .env)
LATEST=$(docker exec athanor-postgres-backup ls /backups/last/ | sort -r | head -1)
docker exec athanor-postgres-backup sh -c \
  "gunzip -c /backups/last/${LATEST} | psql postgresql://athanor:<pass>@postgres:5432/athanor"
```

**Upgrading POSTGRES_PASSWORD on a live deploy:**
```bash
# 1. Update password inside Postgres
docker exec athanor-postgres psql -U athanor -c "ALTER USER athanor PASSWORD '<new-password>';"

# 2. Update .env: POSTGRES_PASSWORD=<new-password>
# 3. Recreate orchestrator and backup containers (postgres keeps running)
cd infra && docker compose up -d --force-recreate orchestrator postgres-backup
```

Note: The `backup-data` volume persists across redeployments. Rotate backup credentials by updating `POSTGRES_PASSWORD` (above) — the backup container reads it from the environment.
