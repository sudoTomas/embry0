# Configuration

embry0 uses environment variables for infrastructure config and API endpoints for runtime config.

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_AUTH_MODE` | `oauth` | How agents authenticate to Anthropic: `oauth` (a Claude subscription token) or `api_key`. A repo, job or agent can override it; see [Agent execution & auth modes](#agent-execution--auth-modes) |
| `ANTHROPIC_API_KEY` | — | API key, for `DEFAULT_AUTH_MODE=api_key`. Served to jobs when no [credential registry](credentials.md) account is usable |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token, for the default `DEFAULT_AUTH_MODE=oauth`; generate with `claude setup-token` |
| `AGENT_MODEL_PROVIDER_CHECK` | `false` | Refuse to save an agent definition (`POST /agents`, `PUT /agents/{type}`, the MCP `agent_update` tool) whose model routes to a provider with no credential configured, or to no catalogue provider at all, with a 422 that names the provider. Off, the save succeeds and the job fails when it runs. `GET /agents` reports `provider` and `provider_ready` per agent either way; see [provider readiness](providers.md#provider-readiness) |
| `GITHUB_TOKEN` | — | GitHub personal access token |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `WEBHOOK_PUBLIC_URL` | empty | The public base URL forges deliver webhooks to, with no path, e.g. `https://webhooks.example.com` (a webhooks tunnel hostname, not the dashboard's). It is the install target of an organization webhook (`<WEBHOOK_PUBLIC_URL>/api/v1/webhook`), and the only origin organization discovery counts as pointing here: a repository's hook points here only when its URL starts with it. `DASHBOARD_PUBLIC_URL` and `SSO_PUBLIC_ORIGIN` are not webhook origins (the public dashboard answers 404 on webhook paths). Unset or a loopback address, organization webhooks cannot be installed and a repository's hooks are unknown rather than absent, and the needs list asks for this setting. See [webhooks](webhooks.md#organization-webhooks-github) |
| `AUTH_DEV_MODE` | `false` | Turn API authentication off (HTTP, admin gate, WebSocket, SSE). The only setting that allows an empty `API_KEY`. For a local-only instance; startup refuses it when SSO is configured or the root `.env` carries a tunnel token. NEVER use in production. |
| `WEBHOOK_DEV_MODE` | `false` | Skip webhook signature checks on routes with no secret, and nothing else: API auth stays on and `API_KEY` is still required. Required for the smee.io relay. Startup refuses it when SSO is configured or the root `.env` carries a tunnel token. NEVER use in production. |
| `DATABASE_URL` | `postgresql://embry0:embry0@postgres:5432/embry0` | PostgreSQL connection |
| `MAX_BUDGET_USD` | `10.0` | Per-job ceiling — bounds the `budget_usd` triage picks |
| `DAILY_BUDGET_CAP_USD` | `100.0` | Daily spending cap; dispatch is refused at or above it |
| `MONTHLY_BUDGET_CAP_USD` | `500.0` | Monthly spending cap; dispatch is refused at or above it |
| `BUDGET_OVERRUN_MODE` | `soft` | On crossing the per-job cap: `soft` finishes the node then pauses at `max_retries`, `hard` fails the job with `ERR_BUDGET_OVERRUN` |
| `RATE_LIMIT_PER_AUTHOR_PER_HOUR` | `5` | Jobs one actor may dispatch per hour; `0` disables. Buckets on `jobs.dispatched_by` (webhook sender / API principal / PR-comment author). Exempt: the service actors `embry0:watcher`, `embry0:auto-retriage`, `embry0:answer-resume` (spend caps still apply — see architecture.md, layer 3) and jobs with no actor |
| `API_RATE_LIMIT_PER_MINUTE` | `300` | API request rate limit |
| `MAX_WEBHOOK_BODY_BYTES` | `5242880` | Webhook request-body ceiling (bytes) |
| `MAX_RUNNING_JOBS` | `0` | Most jobs running at once across the deployment; `0` is no cap. A job past it waits in `pending` (the board's "queued" lane), its timeline says so, and it starts on its own when a running job ends — it is never failed for waiting. Every kind of job counts once: issue-to-PR, QA (whose own sub-tasks run up to the repo's `parallelism.max_concurrent_apps` sandboxes inside that one slot), onboard, pull request review, watcher and PR-comment jobs. A job paused for an answer (`awaiting_input`, `paused`) holds no slot, since its agent has stopped; its resume takes the next free slot ahead of jobs that have not started. Cancelling a queued job removes it before it runs. The queue lives in the orchestrator's memory, but each job records how to start it (`jobs.launch`), so after a restart the jobs still waiting are started again; only a waiting row written before that record existed (#307) is failed with `ERR_ORPHANED` and a message that it never started. Size it against DinD: [provisioning](ops/provisioning.md#sizing-dind-and-the-job-cap) |
| `SHUTDOWN_DRAIN_SECONDS` | `90` | On SIGTERM (a deploy, a restart) the orchestrator starts no more jobs and gives the running ones this long to reach their next node boundary, where LangGraph has saved them; a job still inside a node after it is stopped where it is. Every job stopped is resumed from its checkpoint when the orchestrator is back. Keep it under the orchestrator's `stop_grace_period` in `infra/docker-compose.yml` (180 s) minus uvicorn's 30 s and the rest of shutdown; see [deploy](ops/deploy.md#deploying-while-jobs-run) |
| `MAX_RESUME_ATTEMPTS` | `3` | How many times boot recovers one job after an unclean stop (a crash or kill, no drain) before failing it with `ERR_RESUME_LIMIT`, so a job that takes the orchestrator down cannot loop. Recoveries after a clean drain do not count. `0` turns recovery off: every in-flight job fails at boot, as before #307 |
| `ASK_USER_ROUNDS_CAP` | `10` | Agent ask-user rounds per job before `ERR_MAX_AGENT_QUESTIONS` |
| `USER_RETRY_ROUNDS_CAP` | `5` | User "continue retrying" rounds per job |
| `PROD_PORT` | `8200` | Frontend port (nginx) |
| `AGENT_RUNTIME` | `dind` | Which daemon runs sandboxes: `dind`, the privileged Docker-in-Docker service, or `rootless`, a dedicated rootless Docker daemon on the host. `rootless` needs `DOCKER_HOST=unix://…` and no `DOCKER_TLS_VERIFY`/`DOCKER_CERT_PATH`, refuses to start unless the daemon reports rootless with enforceable limits, and refuses `dind_enabled` profiles and QA `mode: dind`. Compose sets it through `infra/docker-compose.rootless.yml`; see [provisioning.md §10](ops/provisioning.md#10-a-rootless-sandbox-daemon) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for notifications. Also **the gate on inbound replies**: an update from any other chat is dropped with a silent `204`, and an empty value rejects every inbound update (the bot's secret token proves the update came from Telegram, not that it came from you). |
| `TELEGRAM_ALLOWED_SENDER_IDS` | — | Optional comma-separated Telegram user ids allowed to answer questions. Empty means anyone in `TELEGRAM_CHAT_ID`, which is already closed for a private chat; set it to narrow a shared group chat. Non-numeric entries are ignored. |
| `TELEGRAM_WEBHOOK_URL` | — | Public URL for Telegram callback (e.g. Cloudflare tunnel) |
| `TRIGGER_LABELS` | `embry0` | Comma-separated labels that dispatch a job, on **every** trigger surface (GitHub, GitLab, and Linear alike). Matching is case-insensitive. Fires when an issue is opened carrying one, and when one is applied to an existing issue. An untracked issue is adopted on first trigger label. Only issues in `open` dispatch — a running or closed issue is synced but not re-dispatched. **Precedence:** this env var is the default; once Settings → Integrations has been saved, the `integration_config.trigger_labels` DB row wins. An empty DB value falls back here rather than disabling the trigger. Startup logs `trigger_labels_effective` with the effective label and its source (`db` / `env` / `default`), warns `trigger_labels_env_shadowed` when `.env` disagrees with the DB row, and warns `trigger_labels_env_empty` when the value holds no label (e.g. `,`) — such a value is ignored rather than disabling every trigger. |
| `LINEAR_API_KEY` | — | Linear personal API key. Drives the inbound label trigger and outbound write-back, not just the CLI helper. Also settable in Settings → Integrations, which wins when set. |
| `LINEAR_WEBHOOK_SECRET` | — | Signing secret for `POST /api/v1/webhook/linear`. Linear sends a **bare hex** HMAC-SHA256 in `Linear-Signature` — no `sha256=` prefix, unlike GitHub. Also settable in Settings → Integrations, which wins when set. |
| `LINEAR_REPO_MAP` | — | JSON object keyed by Linear project name **or** team key, mapping to `owner/repo`, e.g. `{"Acme Platform": "acme/backend"}`. An issue whose project is unmapped is ignored. Also settable in Settings → Integrations, which wins when set. |
| `LINEAR_RECONCILE_MODE` | `shadow` | Dispatch reconciler mode: `off` (never runs), `shadow` (takes every decision and changes nothing — no job, no ticket comment, no reopened issue; logs `linear_reconcile_would_dispatch` / `linear_reconcile_would_skip` / `linear_reconcile_would_reopen` once per ticket and again only when the decision changes), `on` (acts). Ships in `shadow`; a human promotes it to `on`. See `docs/webhooks.md`. |
| `VERIFICATION_MODE` | `shadow` | Deterministic verification after an approved review (`docs/verification.md`): `shadow` (decide and store; change no routing, write nothing to a forge), `on` (additionally fail a job on a NEW test failure, prune browser QA by tier, post the verdict and the `human-merge-only` label), `off` (skip the node). Ships in `shadow`; a human promotes it. An unrecognised value — and an absent config — are logged and read as `shadow`. The deployed-target QA fix is structural and applies in every mode. |
| `LINEAR_RECONCILE_INTERVAL_SECONDS` | `300` | Seconds between reconciler sweeps. `0` disables the loop; a sweep is a no-op while no Linear API key is configured. |
| `LINEAR_RECONCILE_STARTUP_DELAY_SECONDS` | `90` | The first sweep after a start waits this long (capped at the interval) instead of a whole interval. The last sweep is not persisted: a sweep is cheap and idempotent, and what it may start is bounded from the jobs table. |
| `LINEAR_RECONCILE_MAX_DISPATCHES` | `3` | Most jobs one sweep may start; the rest wait for the next sweep. |
| `LINEAR_RECONCILE_MAX_DISPATCHES_PER_DAY` | `5` | Most jobs the reconciler may start in a rolling 24 h, counted from `jobs.dispatched_by = 'linear-reconciler'` (survives restarts). Webhook dispatches do not count. |
| `LINEAR_RECONCILE_MAX_ATTEMPTS_PER_TICKET` | `2` | Most jobs the reconciler may ever start for one Linear ticket, counted the same way. Reaching it is commented on the ticket once; re-applying the label (the webhook path) is not bound by it. |
| `LINEAR_RECONCILE_FAILURE_MAX_ATTEMPTS` | `5` | A dispatch that raises before a job exists is retried by later sweeps with exponential backoff (one interval, doubling, capped at 6 h) and abandoned after this many consecutive failures (commented once). Counted in `tracker_dispatch_failures`; a successful dispatch resets it. |
| `MERGE_SYNC_MODE` | `shadow` | Merge sync sweep (#279): `shadow` (the default) logs `merge_sync_would_record` and writes nothing, `on` records, `off` never starts the loop. Its GitHub calls are read-only and it never dispatches, comments or pushes — but it does WRITE here (`jobs.delivery_state`, `jobs.status` `completed → pr_merged`/`pr_closed`, `jobs.pr_url`, the embry0 issue closed on merge, healing-case rows), unattended, so it ships in shadow like every other unattended writer. Promote it to `on` after reading one sweep's `merge_sync_would_record` lines. **The mode gates only the sweep:** the `pull_request` webhook records through the same code in every mode, `off` included — a delivery is a fact that arrived, not an action embry0 chose to take. A value outside the three is logged `CRITICAL` (`merge_sync_mode_invalid`) and treated as `off`; it cannot stop startup. See `docs/webhooks.md`. |
| `REPO_RENAMES` | — | GitHub repos this deployment renamed, as a JSON object of old `owner/repo` to current `owner/repo`, e.g. `{"acme/old-name": "acme/new-name"}`. GitHub redirects an old URL, so it still opens, but it no longer equals the URL GitHub reports for the same PR, and merge sync matches on equality. With a rename configured, merge sync, the `pull_request` webhook and the healing ledger match a stored PR or issue URL under either slug, and an API key's [repository allowlist](api.md#repository-allowlist) matches either slug too; nothing rewrites stored rows. Renames are single-hop: map every old name straight to the current one (an entry pointing at a slug that is itself renamed is refused). Read at startup, by the orchestrator and by `embry0 merge-sync`; a value or entry that does not parse is logged as `repo_renames_invalid` and skipped, it cannot stop startup. Empty renames nothing. |
| `MERGE_SYNC_INTERVAL_SECONDS` | `900` | Seconds between sweeps (the first runs one minute after startup). `0` disables the loop. |
| `MERGE_SYNC_MAX_REQUESTS` | `60` | Most GitHub API requests one sweep may make. A sweep that runs out resumes from its keyset cursor on the next interval (in memory: a restart starts the pass from the oldest unsettled PR again). |
| `MERGE_SYNC_MAX_WRITES` | `200` | Most job rows one sweep may change before it stops and leaves the rest to the next one — the bound a request budget cannot give, since one listing request can yield many facts. An operator's `merge-sync backfill` is deliberate and is not write-budgeted. |
| `MERGE_SYNC_RATE_LIMIT_FLOOR` | `500` | The sweep stops as soon as GitHub reports fewer than this many requests left on the token (`X-RateLimit-Remaining`), or answers 403 / 429, leaving the quota to the pipeline. |
| `MERGE_SYNC_LIST_PAGES` | `1` | Pages (100 PRs, most recently updated first) listed per repo when looking for a PR that someone else opened from a job's branch. |
| `MERGE_SYNC_UNLINKED_LOOKBACK_DAYS` | `30` | Only repos with a code job younger than this and no PR URL are listed. Older history is the backfill's job (`python -m embry0.cli merge-sync backfill`). |
| `LINEAR_RECONCILE_STALE_TRIAGING_MINUTES` | `15` | The reconciler reopens Linear-tracked issues that have sat in `triaging` this long with no live job (the orchestrator died between claiming the issue and inserting the job). `0` disables. |
| `GITLAB_URL` | `https://gitlab.com` | GitLab origin. Set to your host for self-managed; `/api/v4` is appended automatically. Must be an `https://` URL with a hostname — the orchestrator refuses to start otherwise, because plaintext would send the `PRIVATE-TOKEN` in the clear and a scheme-less value builds a nonsense API base that silently falls back to github.com. A trailing slash is stripped; empty means the default. |
| `GITLAB_TOKEN` | — | GitLab access token. A `glpat-` PAT is sent as `PRIVATE-TOKEN`; an OAuth2 token (`gloas-`) as `Authorization: Bearer`. Empty disables the integration. |
| `GITLAB_TOKEN__<NAMESPACE>` | — | Per-namespace override, keyed on the **top-level group** (`acme/products/widgets` → `GITLAB_TOKEN__ACME`). Punctuation becomes `_`. |
| `GITLAB_WEBHOOK_SECRET` | — | Value GitLab sends in `X-Gitlab-Token`. Compared **verbatim** — it is a shared secret, not an HMAC like GitHub's or Linear's. |
| `GITLAB_REPO_MAP` | — | Optional JSON map of GitLab project path → target repo. Usually unnecessary: a GitLab project *is* the repo, so resolution defaults to identity. |
| `GITLAB_CA_BUNDLE` | — | Path **inside the orchestrator container** to a PEM bundle for a self-managed instance behind a private CA. Read once at startup and fanned out to all three clients that break without it — orchestrator `httpx`, the gitlab-proxy, and git inside every sandbox (which gets the system store *plus* this CA written to `/home/agent/.embry0/ca-bundle.pem`, so public hosts keep working). An unreadable path, or one whose contents are not a certificate, **refuses the boot** and names the path. Empty is correct for gitlab.com. |
| `GITLAB_CA_BUNDLE_HOST` | `/dev/null` | Compose-only companion: the PEM's path **on the host**, bind-mounted read-only at `/etc/embry0/gitlab-ca.pem`. Set both this and `GITLAB_CA_BUNDLE` (to that in-container path), or neither — this one in `infra/.env` (compose interpolation), `GITLAB_CA_BUNDLE` in the repo-root `.env` (the container's env, ADR 0005). The two failure modes differ: `GITLAB_CA_BUNDLE` alone refuses the boot (it reads the inert `/dev/null` mount), while this one alone is a silent no-op — the PEM is mounted, never read, and clones fail their handshake later. |
| `DEFAULT_FORGE` | `github` | Forge assumed for repos that were never registered in the repo registry. Empty (`DEFAULT_FORGE=`) refuses them: the job fails with `ERR_REPO_UNREGISTERED` before any sandbox is created, naming the repo and telling the operator to register it. Whatever its value, a **dispatch** to an unregistered repo is refused (#360): a tracker trigger (Linear, GitHub, GitLab), an issue dispatch through the API, an accepted watcher finding and a watcher tick all check the registry first — under every spelling `REPO_RENAMES` gives the repo, case-insensitively — and Linear says so on the ticket once. So the default forge only matters for a job submitted straight to `POST /jobs`. **Registration is manual** — nothing auto-registers a repo on first sight, webhooks included — so every repo must be registered before its first job, via `POST /api/v1/repos` (`{"repo": "<path>", "forge": "gitlab"}`) or the MCP `repo_register` tool. With `DEFAULT_FORGE` empty, a registration that omits `forge` is refused with 422 rather than guessed, and storing a QA config no longer auto-registers (it would have to invent a forge) — register the repo first. |
| `ENVIRONMENT_SECRET_KEY` | — | Fernet key for encrypting env var secrets at rest |
| `API_KEY` | — | **Required** unless `AUTH_DEV_MODE=true` — bearer key for the orchestrator API. Startup refuses an empty value otherwise, `WEBHOOK_DEV_MODE` included. Generate with `python -c 'import secrets; print(secrets.token_hex(32))'`. |
| `PROXY_ADMIN_TOKEN` | — | Required, gates the credential proxies' admin endpoints. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. |
| `REGISTRY_AUTH_DIR` | — | **Set by compose, not `.env`.** Directory holding the `username` and `password` the `registry-auth-init` service generated for the in-stack registry, which refuses anonymous clients (#523). At startup the orchestrator writes that login into its docker CLI config (`$DOCKER_CONFIG`, else `~/.docker`) for the host of `IMAGE_REGISTRY`. DinD needs no credential: the CLI sends it with each pull and push. Set but unreadable, or set with `IMAGE_REGISTRY` empty, **refuses the boot**. Empty: no login is written. |
| `IMAGE_PINS_FILE` | — | **Set by compose, not `.env`.** The digests `init-push-images` recorded as it pushed the embry0 images, one `<name>:<tag> <digest>` per line (#523). Installed at startup; from then on each pinned image launches as `<registry>/<name>:<tag>@<digest>`, so an image pushed later under the same tag is never what runs. A missing or malformed file, or one without a pin for `embry0-proxy:latest` and every builtin sandbox image, **refuses the boot**. Empty: images run by tag, and startup warns `image_pins_not_configured` when `IMAGE_REGISTRY` is set. |
| `OFFHOST_BACKUP_S3_PATH` | — | `<bucket>/<prefix>` the `offhost-backup` compose service (profile `offhost-backup`) copies the Postgres dumps and every MinIO bucket to, off the host. Compose interpolation (`infra/.env`, or the root `.env` with `embry0 start`), passed to that service and to the orchestrator; set, the orchestrator reports `embry0_offhost_backup_enabled 1`, which arms the staleness alert. Set it together with the profile. See [backup-restore.md](ops/backup-restore.md#off-host-copy) |
| `OFFHOST_BACKUP_S3_ENDPOINT` | — | The bucket's S3 endpoint URL; empty for AWS. Compose interpolation only, like the rest of `OFFHOST_BACKUP_*` below |
| `OFFHOST_BACKUP_S3_PROVIDER` | `Other` | rclone's S3 provider name (`AWS`, `Cloudflare`, `Wasabi`, `Minio`, ...); `Other` works for most S3-compatible servers |
| `OFFHOST_BACKUP_S3_REGION` | — | Region, for providers that need one |
| `OFFHOST_BACKUP_S3_ACCESS_KEY_ID` | — | Access key for the bucket: list, read and write on the path, no delete |
| `OFFHOST_BACKUP_S3_SECRET_ACCESS_KEY` | — | Its secret |
| `OFFHOST_BACKUP_INTERVAL_SECONDS` | `3600` | Seconds between passes. The staleness alert (28 h) assumes daily dumps and this default |
| `OFFHOST_BACKUP_STATUS_DIR` | `/run/embry0/offhost-backup` | **Leave unset.** Where the orchestrator reads the service's status volume; compose mounts it at this default |
| `PAUSED_JOB_TTL_HOURS` | `48` | Hours before a paused job's sandbox is expired |
| `VOLUME_REAPER_ENABLED` | `true` | DinD volume reaper (#274): a sweep at startup and every `VOLUME_REAPER_INTERVAL_SECONDS` that removes per-job QA volumes whose owning job is terminal or has no `jobs` row. Ownership is read from the `embry0.managed` / `embry0.volume_scope` / `embry0.job_id` labels stamped at volume creation; volumes of live jobs, per-repo volumes and anything embry0 did not create are never touched, and removal never uses `--force`. **Instance scoping (not a setting):** every volume is also stamped `embry0.instance=<id>`, where the id is derived at startup from PostgreSQL's cluster `system_identifier` plus the database name (`embry0/storage/instance_id.py`; logged as `volume_instance_id_resolved`). The reaper ignores any labelled volume whose id differs, so an orchestrator booted against an empty, rebuilt or wrong database — or a second stack on the same daemon — removes nothing. The id survives restarts, redeploys and an in-place `psql` restore; a cluster re-created by `initdb` (including `pg_upgrade`) gets a new id, after which volumes stamped with the old one are left alone and must be pruned by hand |
| `VOLUME_REAPER_DRY_RUN` | `false` | Log `volume_reaper_would_remove` (volume, job_id, reason) for each candidate and remove nothing |
| `VOLUME_REAPER_INTERVAL_SECONDS` | `3600` | Seconds between sweeps (minimum 60) |
| `VOLUME_REAPER_ADOPT_LEGACY_NAMES` | `false` | Also claim unlabelled volumes named exactly `embry0-qa-vol-job-<12 hex>` — those created before ownership labels existed. Enable once (ideally with dry-run first) to drain that backlog, then unset |
| `VOLUME_REAPER_UNKNOWN_OWNER_GRACE_MINUTES` | `60` | A volume whose owning job has no `jobs` row is kept until it is this old |

### Watched stacks (#378)

What embry0 watches is a **row per stack** in `watcher_stacks`, not a set of
env vars: `repo` (the scope key every watcher table already uses), its LogQL,
which tracker its findings are filed into, and `settings` — **overrides
only**, so an absent key inherits the `WATCHER_*` default below.

```
GET    /api/v1/watcher/stacks                 # every stack + the overridable keys
PUT    /api/v1/watcher/stacks/{owner}/{name}  # create or replace (admin)
DELETE /api/v1/watcher/stacks/{owner}/{name}  # stop watching (admin); its runs, signatures and cases stay
```

A deployment upgrading from the env-var watcher seeds its stack from
`WATCHER_REPO` / `WATCHER_LOGQL` / `WATCHER_LINEAR_*` at startup, **once, and
only while the table is empty** — after that the table is the source of truth
and re-seeding would resurrect a stack someone deleted. The `WATCHER_*`
settings below remain the defaults every stack inherits.

An unknown key in `settings` is dropped on write and echoed back as
`ignored_settings`, so a typo cannot become configuration nothing reads.

| `WATCHER_ENABLED` | `false` | Start the scheduled log-watcher/proposer loop (Loki scan → analysis job → draft ticket). The ticket is filed through the stack's **draft filer** (`embry0/services/draft_filers.py`): a Linear team today, or a GitHub repo for a stack whose tracker is GitHub Issues. Filing never applies the trigger label — that is the human's acceptance step. |
| `WATCHER_LOGQL` | — | LogQL selecting the watched stack's error lines, e.g. `{compose_project="my-stack"} \|~ "(?i)(error\|exception\|traceback)" \| regexp "^\\S+\\s+(?P<lvl>TRACE\|DEBUG\|INFO\|TRC\|DBG\|INF)\\s" \| lvl = ""`. The `\|~` match is on the message **body**, so without the trailing stage expression it also selects DEBUG/INFO lines that merely contain the word "error" — 17.6% of the stream as measured on a production stack (#368). The `regexp` stage drops those by **level**; unlevelled lines (nginx, Java stack frames, Postgres continuation SQL — 54% of what survives, and mostly genuine) are kept, because a `regexp` stage that does not match sets no labels and no `__error__`, so `lvl = ""` passes them through. `embry0/services/log_levels.py` applies the same rule in-process, so a DEBUG/INFO/TRACE line never reaches the analysis prompt even if this value omits the stage. **Needed when `WATCHER_ENABLED=true`** — there is no code default. Write plain double quotes in `.env`: a backslash-escaped quote reaches Loki verbatim and draws `400`. Probed against Loki when the watcher starts, together with `WATCHER_DEPLOY_LOGQL`, and again every interval while unhealthy. A fault never stops the orchestrator (the watcher is optional): it logs CRITICAL, turns `GET /api/v1/health/ready` `degraded`, and **stops the ticks** until a probe passes — no restart needed once fixed. `checks.watcher` says which fault: `missing_logql` (unset/blank, `watcher_logql_missing`), `invalid_logql` (Loki answered 400/422, `watcher_logql_invalid`), `loki_misconfigured` (any other 4xx — wrong `WATCHER_LOKI_URL`, not a Loki, or auth; `watcher_loki_misconfigured`), `tick_unrecordable` (the tick-attempt mark could not be written three times in a row, so no tick runs — check the database and the `watcher_tick_attempts` table; `watcher_tick_attempt_unrecorded`). A fault in `WATCHER_DEPLOY_LOGQL` alone alarms and degrades but does not stop ticks, since deploy markers are a fail-open briefing section. An unreachable Loki (connection error, timeout, 429, 5xx) only warns (`unverified`, readiness unaffected) and ticking continues. |
| `WATCHER_STARTUP_DELAY_SECONDS` | `90` | The first tick after a start is scheduled from the last **recorded** tick (`watcher_runs.created_at`, any action, manual ticks included) and the last tick **attempt** (`watcher_tick_attempts`, written when a scheduled tick starts), not from process start. If a tick is overdue it runs this long after start — so a deploy does not compete with an analysis job and the startup LogQL probe has a verdict; otherwise it runs when `WATCHER_INTERVAL_SECONDS` (default `3600`) since the newer of the two has elapsed. A restart therefore never moves two ticks further apart than interval + this delay (for a process that lives longer than the delay), and a crash loop starts at most one analysis job per interval, because a tick killed mid-analysis still left its attempt mark. If the marks cannot be read, or the attempt cannot be written, the loop waits a full interval without ticking; three unwritable marks in a row turn `checks.watcher` `tick_unrecordable` and degrade readiness. |
| `WATCHER_JOB_BUDGET_USD` | `2.0` | Per-job cap for one watcher analysis job. Always enforced as a hard stop (`ERR_BUDGET_OVERRUN`) regardless of `BUDGET_OVERRUN_MODE`, and still bounded by the fleet per-job cap. Checked between agent turns, so worst case is the cap plus one full turn (#330). `0`, non-finite or invalid values fall back to the default — never "unlimited". |
| `WATCHER_MAX_LOOKBACK_SECONDS` | `21600` | A tick's Loki window starts where the last **analysed** window ended (`no_issue` / `proposed` / `skipped_duplicate`), so analysis duration and skipped or errored ticks leave no hole; this bounds how far back that can reach (never below one `WATCHER_INTERVAL_SECONDS`). Time left out is recorded on the run row as `unanalysed_seconds`. Windows are contiguous in **time** only: the excerpt is still the newest `WATCHER_MAX_LOG_LINES` matches, so a widened window over the cap drops its oldest lines (`log_lines_truncated` on the row). Quiet ticks do not advance the window, so it grows each quiet tick until it holds `WATCHER_MIN_LOG_LINES`, then one analysis covers the stretch; past the look-back it is a sliding window of this length. A quiet tick still counts as a **verdict** for the dead-man gauge (`embry0_watcher_last_verdict_tick_timestamp_seconds`, so `Embry0WatcherNoVerdict3h`) without touching the analysed one: a stack that logs nothing is the loop finding nothing. A LogQL that matches nothing forever is `Embry0WatcherOnlyQuiet2d`'s to catch. |
| `LOG_CLUSTERING_MODE` | `shadow` | Deterministic log-signature clustering (#289). `off` / `shadow` / `on`. `shadow` clusters, persists and logs but leaves the raw tail in the prompt so the agent's own grouping stays in effect and the two can be compared; `on` replaces the tail with the ranked cluster table. An unrecognised value reads as `off` — a typo must not silently change what the model is shown. The persisted history also feeds the signature dedupe (#290) and `WATCHER_SKIP_UNCHANGED_MODE`, so `off` disables both. |
| `WATCHER_SKIP_UNCHANGED_MODE` | `shadow` | Skip the paid analysis when the window holds nothing new, spiking or regressed (#291). `off` / `shadow` / `on`. In `shadow` the decision is computed and logged as `watcher_skip_shadow` and the analysis runs anyway, so the ticks it *would* have skipped can be checked against what they actually found. In `on` such a tick records `no_change` and creates no job. A `no_change` tick counts as analysed — it advances the query window and refreshes the dead-man gauge, because it did judge its window; if it did not advance the window, every quiet tick would widen it and the growing counts would read as a spike. The hole that leaves (a broken comparator skipping everything in silence) is covered by `Embry0WatcherNeverAnalyses24h`. A window is also skipped when every notable signature already has an open healing case. Unreadable ledger, no clustering, or no signature history ⇒ analyse: the expensive direction is the safe one. |
| `LOG_REDACTION_FIELDS` | — | Comma-separated payload field names whose **values** are personal data in the watched stack's logs, e.g. `phone,customer_name` (#294). Matched as whole key names, case-insensitively, in `key=value`, `key: value` and `"key": "value"` shapes. Everything sent to a model provider or filed in a tracker is already redacted by `embry0/safety/redaction.py` — the watcher's analysis task (briefing and excerpts), its draft tickets, context-proxy results (watcher and developer alike), the evidence and review prompts: known token formats, auth headers, URL credentials, secret-named fields, e-mail addresses, any long opaque run, and the process's own secrets. This adds the operator's own fields; the context proxy receives it at launch. Agent stderr diagnostics keep the secrets-only rules. Regex-based, so not a guarantee. |
| `WATCHER_SPIKE_FACTOR` / `WATCHER_SPIKE_MIN_COUNT` | `3.0` / `10` | How much louder than its previous window a signature must be to count as spiking, and the absolute floor it must also clear. Both are needed: log volume is bursty, and without the floor a signature going from 1 line to 3 reads as a 3x emergency. Per-deployment today; #297 moves the watcher's config into the DB per repo and these go with it. |
| `WATCHER_REGRESSION_SILENCE_SECONDS` | `21600` | How long a signature must be absent before its return counts as a regression rather than a gap. Six hours is six missed hourly ticks. |
| `CONFIRM_HEALED_ENABLED` | `true` | After a case's deploy marker, count its signature and record a `healed` / `not_healed` / `inconclusive` verdict with the counts behind it (#312). **On** rather than shadow, unlike every other autonomy stage, because it only reads the ledger and comments on the ticket — its one ledger write besides the verdict is sending a `not_healed` case back to `tracked`, at most once per case (#451), where a person accepts the retry or closes the case (#658); it dispatches nothing. The observation window is scaled to the rate the signature ran at *before* the fix — a signature at 500/h settles in 15 minutes, one at 3/day needs days — so silence is only read as a fix when silence would be surprising. Below a rate at which even 7 days would not expect 3 occurrences the verdict is `inconclusive`, never `healed`. Runs for out-of-band fixes too, since it keys on `deployed_at` rather than on who merged. |
| `CONFIRM_HEALED_INTERVAL_SECONDS` / `CONFIRM_HEALED_STARTUP_DELAY_SECONDS` | `1800` / `120` | Sweep cadence. Every write carries an `event_key` derived from the case, its deploy anchor and the verdict, so a sweep that runs twice over unchanged state writes once — the interval is safe to shorten. |
| `MERGE_APPROVAL_MODE` | `dry_run` | Merge approvals (#319 phase 2). A completed job's PR on an enabled repo is asked about in the chat the operator chooses; a reply relayed through the `merge_decide` MCP tool is re-checked here (approval chat, PR head sha unchanged since the ask, every CI check green, verification gate not blocking, GitHub mergeable) and then squash-merged pinned to that sha. `dry_run` runs every check and records "would merge" with no GitHub write; `on` merges; anything else is `dry_run`. Refusals for CI, the gate or mergeability leave the ask `pending`, answerable again; a moved head makes it `stale` and posts a fresh ask. Denylisted paths (migrations, auth, infra, CI, dependencies, secrets) are flagged in the ask but do not block — the denylist stops machine merges, and this is a human's. See `docs/merge-approvals.md`. |
| `MERGE_APPROVAL_REPOS` | — | Comma-separated `owner/repo` whose PRs are asked about, e.g. `acme/backend,acme/frontend`. Empty asks about no PR at all; startup logs `merge_approvals_no_repos` so a deployment cannot lose its asks silently. |
| `MERGE_APPROVAL_CHAT_ID` | — | The only chat whose answers count. Empty refuses every decision. |
| `MERGE_APPROVAL_CI_WAIT_MINUTES` | `45` | An ask is posted once its commit's CI settles, so its recommendation reads real CI; after this long it is posted anyway, saying CI is still running. |
| `MERGE_APPROVAL_EXPIRY_DAYS` | `7` | An unanswered ask retires as `expired` after this long; one whose PR is merged or closed on GitHub retires as `closed` at once. |
| `MERGE_APPROVAL_SWEEP_SECONDS` | `60` | How often the sweep posts settled asks and retires stale ones. Posted asks are rechecked at most every 10 minutes. |
| `WATCHER_DEPLOY_LOGQL` | — | LogQL whose every matching line is one deploy marker in the analysis briefing, e.g. `{job="my-stack-deploy"}`; a `deployed <sha>: <services>` line also anchors healing cases to the deploy that shipped their fix. No code default (#527): which lines mark a deploy is deployment-specific. Empty skips the briefing section and the deploy sync, and is not probed. A watched stack can override it with its `deploy_logql` setting. |

A stack that includes embry0's own logs needs a different selector and is covered by the recursion guard: [`docs/self-watching.md`](self-watching.md).
| `AUTH0_ISSUER` | — | Auth0 SSO (RAV-1069): tenant issuer URL. All six SSO vars are all-or-nothing — a partial set refuses startup |
| `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` | — | Confidential-client credentials on your Auth0 tenant |
| `SESSION_SECRET` | — | Signs the SSO session cookie (>=32 chars); rotation logs everyone out |
| `SSO_PUBLIC_ORIGIN` | — | Public dashboard origin, e.g. `https://embry0.example.com` |
| `DASHBOARD_PUBLIC_HOSTNAME` | — | Compose-only, set in **`infra/.env`** (compose interpolation, never the root `.env`): the public dashboard's hostname, e.g. `embry0.example.com` — the host part of `SSO_PUBLIC_ORIGIN`, with no scheme, port or path. The frontend renders a dedicated nginx vhost for exactly that Host: `/api/v1/webhook*`, `/api/v1/telegram/*` and `/api/v1/internal/*` return 404, `X-Forwarded-Proto` is `https`, and it sends CSP and HSTS. Unset means no public vhost, so every Host gets the LAN vhost without that hardening. The same value renders the dashboard tunnel's ingress (`cloudflared-dashboard`, profile `dashboard-tunnel`), which **refuses to start** while it is unset or invalid, and is the CNAME `provision.sh` creates. A value that is not one plain hostname stops the frontend container at start. Applied by `infra/deploy.sh frontend` and `docker compose --profile dashboard-tunnel up -d cloudflared-dashboard`. See [sso.md](sso.md#ingress) |
| `SSO_ROLES_CLAIM` | — | ID-token claim carrying the Auth0 roles, e.g. `https://example.com/roles`. Deployment-specific, so it has no default and is one of the all-or-nothing fields (#393): a claim that does not match what the tenant stamps yields no roles for every user, and default-deny then refuses them all silently. Must be on the ID token, not the access token |
| `SSO_SESSION_MAX_AGE_SECONDS` | `43200` | Absolute session lifetime (12h) |

See `.env.example` for the complete list.

Unknown variables are ignored rather than rejected (the env is shared with Docker and the shell), so at startup — and in `embry0 config` — a variable that near-misses a setting is flagged as `unknown_env_var name=AUTH_DEV_MOD did_you_mean=AUTH_DEV_MODE`; it is a warning only and never blocks boot.

`embry0 config` masks every credential setting, and so does a config object written to a log. A secret shorter than 24 characters is shown as `****`; a longer one keeps its first and last four characters, enough to tell two tokens apart. An unset secret is shown empty. Any URL keeps its scheme, user, host, port and path but not its password (`DATABASE_URL=postgresql://embry0:****@postgres:5432/embry0`), and query parameters such as `password=`, `token=` or `sig=` are masked too.

## Secrets at rest

embry0 encrypts per-repo secret environment variables at rest using Fernet, keyed by `ENVIRONMENT_SECRET_KEY`:

```bash
# Generate a strong key (or use your favorite secret manager)
openssl rand -hex 32
```

The same key also protects the [credential registry](credentials.md) (`credentials`): forge tokens, and LLM and SSH credentials in later releases. Tailnet API keys, which a [hosts](hosts.md) import and probe read devices with, are registry credentials too.

In production the orchestrator refuses to start without a real key; in dev mode a default is tolerated with a loud warning. The key is rotatable without orphaning anything: move the current key into `ENVIRONMENT_SECRET_KEYS_OLD` (comma-separated, newest first), set a fresh `ENVIRONMENT_SECRET_KEY`, and restart — decryption tries every listed key while encryption always uses the current one. Replacing the key *without* carrying the old one in `ENVIRONMENT_SECRET_KEYS_OLD` makes previously encrypted values undecryptable: an env-var secret logs `secret_decryption_failed` on the next job that needs it, and a registry credential is marked `disabled` with `status_reason=decryption_failed` on its next resolution and skipped until `POST /api/v1/credentials/{id}/rotate` re-enters its secret.

Per-repo and global env vars are managed through the `/environments` page in the dashboard.

## Runtime configuration (API)

Budget controls, context injection, and sandbox profiles are configurable via the API without restarting:

- **Budget** — `GET/PUT /api/v1/config/budget`. A saved row wins over the env vars above for every budget setting; a cap of `0` means unlimited. See [architecture.md](architecture.md#budget-controls) for where each layer is enforced.
- **Context** — `GET/PUT /api/v1/config/context` (global), `/config/context/repos/{repo}` (per-repo)
- **Sandbox Profiles** — `CRUD /api/v1/sandbox-profiles`. A profile's `dind_enabled` only asks for the shared Docker daemon; a sandbox gets it when an admin has also granted its repo Docker access (`PUT /api/v1/repos/{repo}/docker-access`, or the repo page). Code from a granted repo can control the shared Docker daemon, including the proxies holding your tokens. Without the grant, a sandbox on such a profile (the builtin `qa-jvm` included) is refused at creation. A profile cannot weaken a sandbox's isolation: `cap_drop` must include `ALL`, `cap_add` must be empty, `security_opt` may hold only `no-new-privileges`, `base_image` must name an image in embry0's own registry without a registry host, and `extra_networks` may hold only `sandbox-internet`. The API refuses other values with a 422, and a sandbox on a stored profile that holds one is refused at creation. A sandbox's memory and CPU limits are its profile's `memory` and `cpus`; there is no global setting. The builtin profiles are re-seeded at every startup, overwriting edits, so set different limits on a profile of your own.

- **Hosts**: `GET/POST /api/v1/hosts`, `GET/PATCH/DELETE /api/v1/hosts/{id}`, `POST /api/v1/hosts/{id}/probe` and `POST /api/v1/hosts/import/tailnet`. The machines that run the code, how embry0 reaches each one, where its logs come from, and which repositories it serves. A tailnet import and a tailnet probe read devices with a `tailnet_api_key` credential bound at global scope with role `tailnet`; no environment variable configures it. See [hosts.md](hosts.md).

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
- `auth_mode=api_key` requires an Anthropic `llm_api_key` account: one in the [credential registry](credentials.md) (dashboard: Infra → Credentials, bound at global scope or to the repo), or, when `DEFAULT_AUTH_MODE=api_key`, `ANTHROPIC_API_KEY` in `.env`. A repo, job or agent that switches to `api_key` while the deployment default is `oauth` needs the registry account. No account → `ERR_CREDENTIAL_MISSING`; an OAuth account resolved for an api-key run → `ERR_MISSING_API_KEY`.

The registry is consulted before the env vars; an OAuth token in `.env` is its last step.

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
embry0 build-sandbox      # Rebuild the sandbox and proxy images (prints the push step)
embry0 health             # Check stack health
embry0 config             # Validate and display config (secrets masked)
embry0 purge              # Remove all Docker artifacts
embry0 purge --volumes    # Remove only volumes
```
