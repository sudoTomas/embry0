# Legion

## Rules

- Do not make any changes until you have 95% confidence in what you need to build. Ask me follow-up questions until you reach that confidence.
- Build this as modular as reasonably possible. Each subsystem should have clear boundaries, well-defined interfaces, and be independently testable.
- Never defer issues for later. If a code review or self-review finds a problem, fix it now before moving on. No "tracked for later" or "acceptable for now" — all identified issues get resolved immediately.

## Environment

- The user works from a laptop SSH-ed into a home server where Claude Code runs. When starting local servers (dev servers, visual companions, etc.), bind to `0.0.0.0` so they are accessible over the local network. Use `--host 0.0.0.0 --url-host 0.0.0.0` for the brainstorming visual companion.

## Deployment

- Legion runs in Docker via `infra/docker-compose.yml`. After any code changes, you **must** rebuild and restart the affected containers:
  - Backend changes: `cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate`
  - Frontend changes: `cd infra && docker compose build frontend && docker compose up -d frontend --force-recreate`
  - Both: `cd infra && docker compose build orchestrator frontend && docker compose up -d orchestrator frontend --force-recreate`
- The frontend is served via nginx on port 8200 and proxies `/api` to the orchestrator container.
- Always verify health after restart: `curl -s https://legion.alchymielabs.com/health`
