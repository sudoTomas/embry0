# Deploying embry0

How to ship a change to a running embry0 stack, rebuild the images that jobs
run in, and what each ingress exposes. Standing up a host from nothing is
[provisioning.md](provisioning.md); the gates to run before a commit are
[verification.md](verification.md); Postgres backups, restores and password
rotation are [backup-restore.md](backup-restore.md).

## Deploy a change

- **Two `.env` files — know which one you're editing.** Container runtime env comes from the **repo-root `.env`** (`env_file: ../.env` on the orchestrator service): all secrets and tokens (`GITHUB_TOKEN`, `GITHUB_TOKEN__<OWNER>`, `CLAUDE_CODE_OAUTH_TOKEN`, `PROXY_ADMIN_TOKEN`, `API_KEY`, tunnel tokens) must be edited there. `infra/.env` feeds only docker-compose `${…}` interpolation (`POSTGRES_*`, `PROD_PORT`, `COMPOSE_PROFILES`, resource caps) and is not loaded into any container. The values both files carry (`POSTGRES_*`, `MINIO_ROOT_*`, `CLOUDFLARED_TUNNEL_TOKEN`) must be equal (`infra/.env.example`); any other secret still in `infra/.env` is dead weight. When another doc says "`.env`" for a secret, it means the root file.
- embry0 runs in Docker via `infra/docker-compose.yml`. After any code changes, you **must** rebuild and restart the affected containers — with the deploy script, which also leaves the deploy marker the self stack needs (below):
  - Both: `infra/deploy.sh` (from the `main` checkout; refuses on any other branch, and refuses while a job is `running` unless `EMBRY0_DEPLOY_FORCE=1`: the recreate interrupts it, and although it resumes, the step it was in runs again — see [Deploying while jobs run](#deploying-while-jobs-run))
  - Backend only: `infra/deploy.sh orchestrator` — Frontend only: `infra/deploy.sh frontend`
  - What it runs, if you need it by hand: `cd infra && docker compose build orchestrator frontend && docker compose up -d orchestrator mcp frontend --force-recreate`, then `infra/deploy.sh marker <sha> orchestrator frontend` for the marker.
  - `mcp` runs the **orchestrator image** (`image: embry0-orchestrator`, no build of its own), so every orchestrator rebuild leaves it on the old image until it is recreated too; the script recreates it with the orchestrator. Left out of the manual recipe until 2026-09-23, when a deploy shipped a four-day-old MCP server next to a fresh orchestrator.
  - **Deploy marker.** On success the script pushes one line, `deployed <sha>: <services>`, to Loki under `{job="embry0-deploy"}` (URL: `EMBRY0_DEPLOY_LOKI_URL`, else `WATCHER_LOKI_URL` from the root `.env`; unset = no marker, said on stderr). The self stack's `deploy_logql` override selects that job label, and that is how a self case gets past `merged` — a deploy done without the marker leaves its cases there. See `docs/self-watching.md`.
- `infra/deploy.sh` builds the orchestrator and frontend, or the services you name. It does not rebuild the sandbox, QA or proxy images: a change to the code baked into them needs a [rebuild recipe](#images-registry-pins-and-rebuilds) too.
- **The release that adds skill sources** (migration 101) brings a compose change and a sandbox change:
  - The `skills-store` volume, mounted at `/skills` in `orchestrator` and at `/skills:ro` in `dind`, is created by the first `docker compose up` after the release. Because `dind`'s configuration changed, that first `infra/deploy.sh` also recreates `dind` (compose converges a changed dependency of a service it brings up), which restarts the sandbox daemon and kills every sandbox, the sandboxes of paused and awaiting-input jobs included: deploy this release with no job active, not just none `running`. The orchestrator image rebuild creates `/skills` owned by the `orchestrator` user, and the empty named volume takes that ownership on first use.
  - `embry0/agents/config_builder.py`, `embry0/agents/invocation.py` and `embry0/sandbox/runner.py` are baked into the sandbox images, so run the sandbox [rebuild recipe](#images-registry-pins-and-rebuilds) too. Until then the skill trees are mounted into sandboxes and the agents ignore them.
  - At first boot the orchestrator syncs the builtin `superpowers` and `local` sources in the background, which needs outbound https to the `superpowers` repository's host; `GET /api/v1/skills/sources` shows `pending` until each is `ready`. To check a deployment: both sources `ready`, `GET /api/v1/skills` lists `superpowers:*`, and a job's `agent_started` event carries `skill_sources`.
- The frontend is served via nginx on port 8200 and proxies `/api` to the orchestrator container.
- Always verify health after restart: `curl -s http://localhost:8200/health`

### Deploying while jobs run

Sandboxes run on the agent runtime (the `dind` service, or the rootless
daemon), not in the orchestrator container, and LangGraph saves each job's
graph in Postgres after every node. Recreating the orchestrator therefore
stops a job without losing it (#307):

1. **Drain.** On SIGTERM uvicorn closes its connections (up to 30 s), then the
   orchestrator starts no more jobs: anything dispatched now, and every job
   waiting for a run slot, stays `pending` with its launch record. Running
   jobs get `SHUTDOWN_DRAIN_SECONDS` (default 90) to finish the node they are
   in; each stops at that boundary. One still inside a node at the deadline
   (an agent turn can take far longer) is stopped where it is. Every job
   stopped or held back gets `jobs.interrupted_at` and an "Interrupted" line
   on its timeline, and the agent it left running in its sandbox is killed.
   The orchestrator's `stop_grace_period: 180s` in `infra/docker-compose.yml`
   covers all of this; compose's default 10 s would kill it mid-drain, and
   systemd's `docker compose down` gets `TimeoutStopSec=300` for the same
   reason. Raising `SHUTDOWN_DRAIN_SECONDS` means raising both
   (`tests/unit/infra/test_shutdown_grace.py` holds the arithmetic).
2. **Recovery.** At boot a `running` issue-to-PR job with a checkpoint
   resumes from it: the stale agent in its sandbox is killed, the sandbox is
   re-enrolled with the proxies, and the node that was cut off runs again
   from its start — its agent turn's spend is spent twice. A job that never
   started, or stopped before its graph began, starts again from its launch
   record. Each says so on its timeline.
3. **What still fails.** A QA or onboard job that had begun its graph
   (their sub-sandboxes are not re-enrolled), a job with nothing to resume
   from, and a job whose sandbox is gone (the daemon restarted with it:
   `ERR_SANDBOX_GONE`) fail as before. One the drain stopped reads
   `ERR_INTERRUPTED_BY_SHUTDOWN`; one a crash left behind reads
   `ERR_ORPHANED`. Both are stamped `finished_at`, and their issues reopen.
4. **Crash loops are bounded.** A recovery after an unclean stop (a crash,
   an OOM kill, no drain stamp) spends one of `MAX_RESUME_ATTEMPTS` (3); the
   next boot after the last fails the job with `ERR_RESUME_LIMIT`. Clean
   drains never count, so a long job may cross any number of deploys.

Restarting `dind` (or the rootless daemon) still kills every sandbox, and
with them the jobs; `infra/deploy.sh` touches it only when a release changes
`dind`'s own compose configuration (compose then recreates it as a dependency,
as the skill-sources release above does). An orchestrator recreate is safe;
the deploy script still asks before one while a job runs because of the
re-run node in 2.

## Ingress

- The Cloudflare tunnel runs as a containerized service (`embry0-cloudflared` in compose), only with the `webhook-tunnel` compose profile. Configuration (hostname, target service, allowed paths) lives in the Cloudflare-side tunnel config, not in this repo. Create a tunnel in the Cloudflare dashboard, then put its token in `infra/.env` as `CLOUDFLARED_TUNNEL_TOKEN`, next to `COMPOSE_PROFILES=webhook-tunnel`, and in the root `.env` as both `CLOUDFLARED_TUNNEL_TOKEN` and `TUNNEL_TOKEN` (`docs/webhooks.md`, step 2). Webhooks-only by design, and its path restriction is configured on the Cloudflare side, not here. The dashboard has its own tunnel (`cloudflared-dashboard`, profile `dashboard-tunnel`), behind SSO, through the hardened public vhost rendered from `DASHBOARD_PUBLIC_HOSTNAME` (`docs/sso.md`). Without that tunnel, the dashboard and the full API stay LAN-only at `:8200` — provided the webhooks tunnel is restricted to its paths (below).
- **Or with OpenTofu.** [`infra/tofu/modules/cloudflare-tunnel`](../../infra/tofu/modules/cloudflare-tunnel/README.md) creates either tunnel with its DNS record, in place of the dashboard clicks and `infra/cloudflared-dashboard/provision.sh`, which keep working. With `exposure = "webhooks"` it writes the webhooks tunnel's ingress itself: only the webhook routes in the table below, anchored, then a 404, and its plan fails if the ingress would route anything more. Its `tunnel_token` output is the `CLOUDFLARED_TUNNEL_TOKEN` above. Keep your root module and state outside this repository ([`infra/tofu/README.md`](../../infra/tofu/README.md)).
- For local development without a public URL, use the smee.io relay: set `WEBHOOK_DEV_MODE=true`, leave `GITHUB_WEBHOOK_SECRET` empty and set an `API_KEY` (or use `AUTH_DEV_MODE=true` for an instance with no API auth at all), then run `npx smee-client --url https://smee.io/<channel> --target http://localhost:8200/api/v1/webhook`. `WEBHOOK_DEV_MODE=true` is required because smee re-serializes the payload, invalidating GitHub's HMAC signature. See `docs/webhooks.md` for full steps. Never use this configuration in production.

### What each ingress reaches

Check a new or changed route against this table. The orchestrator publishes
no host port; everything below reaches it through the frontend's nginx.

| Path | Dashboard tunnel | Webhooks tunnel | LAN, `:8200` |
|---|---|---|---|
| The dashboard (`/`, `/assets/`) | yes | no | yes |
| `/api/v1/webhook`, `/api/v1/webhook/linear`, `/api/v1/webhook/gitlab` | 404 | yes | yes |
| `/api/v1/telegram/callback` | 404 | yes, when Telegram is configured | yes |
| `/api/v1/webhook/alertmanager` | 404 | no | yes |
| `/api/v1/internal/` | 404 | no | yes |
| Every other `/api/` route, and `/ws/` | yes | no | yes |
| `/mcp` | yes | no | yes |
| `/health` | yes | no | yes |
| `/metrics` | no | no | no |

- **Dashboard tunnel** (`cloudflared-dashboard`): its ingress, rendered from
  `infra/cloudflared-dashboard/config.yml.template`, sends only
  `DASHBOARD_PUBLIC_HOSTNAME` to nginx and answers 404 for every other
  hostname. nginx serves that hostname from the hardened public vhost
  (`frontend/nginx/dashboard-public.conf.template`), which refuses the webhook,
  Telegram and internal paths and adds a Content-Security-Policy and HSTS.
- **Webhooks tunnel** (`cloudflared`, profile `webhook-tunnel`):
  remote-managed, so its hostname and path rules live in Cloudflare, not in
  this repo. It points at `http://frontend:80`, whose LAN vhost serves
  everything. The "yes" and "no" in its column hold only once the tunnel is
  restricted to the webhook routes (plus the Telegram callback, when
  `TELEGRAM_WEBHOOK_URL` points at it); unrestricted, its hostname reaches the
  dashboard and the whole API, without the public vhost's hardening. The
  `cloudflare-tunnel` module with `exposure = "webhooks"` applies that
  restriction and refuses a plan that would widen it.
- **Authentication applies on every path,** whichever ingress reaches it.
  "API authentication" in [SECURITY.md](../../SECURITY.md#what-holds) lists
  the routes that take something other than an API key or an SSO session.
- **`/api/v1/webhook/alertmanager`** is meant for an Alertmanager on the same
  network, which posts to the orchestrator directly; do not expose it.
- **`/metrics`** is served only on the orchestrator's port 8000, which
  Prometheus scrapes over the compose network. nginx proxies no `/metrics`
  location, so a request to `:8200/metrics` gets the dashboard
  ([metrics.md](metrics.md)).

## Images: registry, pins and rebuilds

- **Under `AGENT_RUNTIME=rootless`** ([provisioning.md](provisioning.md#10-a-rootless-sandbox-daemon)) there is no registry, no pins and no `init-push-images`: the sandbox and proxy images are built on the rootless sandbox daemon itself and run by local tag. Every recipe below then becomes the same `docker compose --profile images build …` prefixed with `DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock`, followed by `docker compose up -d --force-recreate orchestrator` (no `init-push-images`). Commands below that run `docker exec embry0-dind docker …` run as `DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock docker …` on the host, with a bare image name instead of `registry:5000/…`.
- The `embry0-sandbox`, `embry0-sandbox-dev-python`, `embry0-sandbox-dev-jvm-python`, `embry0-sandbox-qa`, and `embry0-proxy` images are pushed to the in-stack `registry:5000` sidecar at bootstrap by the `init-push-images` compose service; DinD pulls them on first reference. The `IMAGE_REGISTRY` env var controls the prefix at orchestrator-launch time and **must be set in the root `.env`** — `IMAGE_REGISTRY=registry:5000` for compose, your registry URL on K8s. The code default is EMPTY (`config.py:image_registry`), not `registry:5000`: without it the orchestrator asks DinD for bare image names, gets `pull access denied`, and crash-loops at startup.
- The registry refuses anonymous clients and embry0 images run by digest (#523): `registry-auth-init` generates the login into the `registry-auth` volume, `init-push-images` records each pushed digest in the `image-pins` volume, and the orchestrator refuses to start without either. The pins are read at startup, so a rebuild must recreate `init-push-images` and the orchestrator together (every recipe below does). The docker CLI inside `embry0-dind` has no login: it can run images DinD already holds but cannot pull. Details: `docs/ops/provisioning.md`, "The registry login and the image pins".
- **MinIO runs Silo** (`docker.io/pgsty/silo`, pinned by tag and digest in `infra/docker-compose.yml`), the community fork of MinIO, since #573: MinIO's own images can no longer be pulled. Silo uses MinIO's on-disk format, variables, S3 API and `/data` path, so a deployment that ran `quay.io/minio/minio` switches on the same `minio-data` volume, with no migration. `infra/deploy.sh` does not recreate `minio`, and the orchestrator no longer depends on it, so a deployment keeps running its cached image until you switch. Do not prune that image before then: MinIO could not start again. To switch, copy the volume and save the old image first ([backup-restore.md](backup-restore.md#minios-data-volume)), then:
  ```bash
  cd infra
  docker compose pull minio
  docker compose up -d minio
  docker inspect embry0-minio --format '{{.State.Health.Status}}'   # healthy within a minute
  ```
  The orchestrator needs no restart. It prepares the `qa-artifacts` bucket in the background and retries until MinIO answers (`qa_minio_unavailable` warnings, then `qa_minio_ready`). Until then the QA routes answer 503 and QA jobs fail at sandbox setup. A plain `docker compose up -d` or `embry0 start` also switches, because the `minio` service changed.
- **First-time bootstrap (fresh deploy).** From `infra/` (a new host also needs the rest of [provisioning.md](provisioning.md)):
  ```bash
  cd infra
  docker compose --profile images build   # builds frontend, orchestrator, sandbox, dev-python, dev-jvm-python, qa, proxy
  docker compose up -d                    # registry-auth-init → registry → init-push-images → dind → orchestrator → frontend
  ```
- **After code changes to `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/`, or the sandbox-baked `embry0/execution/` modules** (rebuild + repush + restart orchestrator):
  ```bash
  cd infra
  docker compose --profile images build sandbox-image-builder dev-python-image-builder dev-jvm-python-image-builder qa-image-builder
  docker compose up -d --force-recreate init-push-images orchestrator
  ```
  The BASE sandbox image (not just QA) bakes in `embry0/sandbox/`, `embry0/safety/`, `embry0/agents/`, and `embry0/execution/{events,agent_runner,auth_provider,docker_client}.py` (see `infra/Dockerfile.sandbox`) — a change to any of those is invisible to agents until the images are rebuilt. `dev-python-image-builder`, `dev-jvm-python-image-builder` and `qa-image-builder` must build alongside the base sandbox: `init-push-images` pushes `embry0-sandbox-dev-python:latest`, `embry0-sandbox-dev-jvm-python:latest` and `embry0-sandbox-qa:latest` too, and fails startup if any of those tags is missing from the host daemon (`tests/unit/infra/test_image_wiring.py` keeps the push list and the profile seeds in agreement) (the QA image additionally bakes `embry0/execution/sandbox_manager.py`).
  **Pre-built per-repo QA images are NOT rebuilt by that recipe.** `embry0 build-qa-image` makes them by `docker commit` of a QA sandbox, so each one carries the sandbox code of the day it was built. Every build records the base image it was committed from (`qa_image_tags.base_image` / `.base_image_digest`); once `embry0-sandbox-qa` is rebuilt, QA selection skips the now-stale image (one `qa_prebuilt_image_stale` warning per repo; the run falls back to the base image + warm volume — slower, correct) and `GET /api/v1/stats` lists it under `qa_prebuilt_images.stale`. Nothing rebuilds it automatically. After the recipe above:
  ```bash
  # which repos need a rebuild (stale = base rebuilt since; unknown = built before digests were recorded)
  # (the key goes to curl on stdin, so it never appears in a process list)
  docker exec embry0-orchestrator sh -c 'printf "Authorization: Bearer %s\n" "$API_KEY" | curl -s -H @- localhost:8000/api/v1/stats' | jq .qa_prebuilt_images
  # rebuild one (minutes; clones the repo and runs its install) — an unchanged lockfile no longer skips a stale image
  docker exec embry0-orchestrator embry0 build-qa-image --repo <owner>/<repo> --branch <branch>
  # verify a given image carries today's runner — prints a count >= 1 (0 = stale runner)
  docker exec embry0-dind docker run --rm --network none --entrypoint grep registry:5000/<image_tag> -c -e '--config-stdin' /app/embry0/sandbox/runner.py
  ```
  The `embry0` console script works inside the orchestrator container and defaults to the in-container API (`EMBRY0_API_URL=http://localhost:8000`, set in the image); the image build fails if it ever stops working.
- **After code changes to `embry0/execution/proxy/`**:
  ```bash
  cd infra
  docker compose --profile images build proxy-image-builder
  docker compose up -d --force-recreate init-push-images orchestrator
  ```
  `infra/deploy.sh` does not rebuild the proxy image, so deploying a release that changed this directory needs this recipe too (the credential registry's first release did: the proxies now accept an empty baked token). Then run `embry0 credentials import-env` inside the orchestrator (`docker exec embry0-orchestrator embry0 credentials import-env`), so the credential registry holds the accounts the proxies are given ([credentials.md](../credentials.md)).
