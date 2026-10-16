# Webhook Setup

embry0 reacts to events from three trackers/forges plus one monitoring source, each on its own endpoint. Every route shares one ingress layer (`embry0/api/webhook_ingress.py`) and one dev-mode policy, but **each sender authenticates deliveries differently** — this is the number-one production footgun, so it's spelled out here:

| Forge | Endpoint | Header | Verification shape | Secret env var |
|---|---|---|---|---|
| GitHub | `POST /api/v1/webhook` | `X-Hub-Signature-256` | HMAC-SHA256 of the raw body, **`sha256=`-prefixed** hex | `GITHUB_WEBHOOK_SECRET` |
| Linear | `POST /api/v1/webhook/linear` | `Linear-Signature` | HMAC-SHA256 of the raw body, **bare hex — no prefix** | `LINEAR_WEBHOOK_SECRET` |
| GitLab | `POST /api/v1/webhook/gitlab` | `X-Gitlab-Token` | The shared secret sent **verbatim in plaintext** — not an HMAC | `GITLAB_WEBHOOK_SECRET` |
| Alertmanager | `POST /api/v1/webhook/alertmanager` | `Authorization: Bearer` | The shared secret sent **verbatim as a bearer token** — Alertmanager cannot HMAC a body | `ALERTMANAGER_WEBHOOK_SECRET` |

`WEBHOOK_DEV_MODE=true` bypasses verification on every one of these routes that has no secret configured (dev only — never in production). It touches nothing else: API authentication stays on, and `API_KEY` is still required. Startup refuses the flag when SSO is configured or the orchestrator's environment carries a tunnel token, because either means the instance has a public origin. Linear and GitLab secrets are also settable in Settings → Integrations; a saved DB value wins over the env var.

Because embry0 usually runs on a private network, you need a way to get the forge's webhook POSTs into your instance. Two supported approaches:

| Approach | Use when | Signature verification |
|----------|----------|------------------------|
| **Cloudflare Tunnel** | Production / always-on demo / shared team instance | **Required** — real secret |
| **smee.io relay** | Local dev on a laptop / ephemeral testing | **Skipped** — WEBHOOK_DEV_MODE=true, no secret |

## Option A — Cloudflare Tunnel (production)

The compose stack ships a `cloudflared` service that runs in remote-managed mode. It is optional: it runs only with the `webhook-tunnel` compose profile. You create a tunnel once in the Cloudflare dashboard (or via their API), paste the token into the env files, enable the profile, and bring up the container. Any other tunnel/reverse-proxy solution (ngrok, Tailscale Funnel, a plain reverse proxy on a VPS) works the same way — the only requirement is that HTTPS POSTs reach the frontend's `/api/v1/webhook` route.

**1. Create the tunnel** (one-time): in [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels → Create a tunnel, add a public hostname (e.g. `webhooks.example.com`) pointing at `http://frontend:80`, and copy the tunnel token. Restricting the tunnel to the webhook paths (via the tunnel config or a Cloudflare Access policy) is strongly recommended — allow only `/api/v1/webhook`, `/api/v1/webhook/linear`, and `/api/v1/webhook/gitlab`, and keep the dashboard and full API LAN-only. The Alertmanager route is **not** one to expose: Alertmanager runs on the same LAN and posts to `http://embry0-orchestrator:8000` directly. A tunnel rule's path is a regular expression matched anywhere in the request path, so anchor each one (`^/api/v1/webhook$`): a bare `/api/v1/webhook` also matches the Alertmanager route. [`infra/tofu/modules/cloudflare-tunnel`](../infra/tofu/modules/cloudflare-tunnel/README.md) with `exposure = "webhooks"` creates the tunnel, its DNS record and this restriction in code.

**2. Paste the token and enable the profile.** Compose hands the container `CLOUDFLARED_TUNNEL_TOKEN` from the file it interpolates: `infra/.env` when you run compose from `infra/`, as below. The root `.env` gets the token too, which tells the orchestrator the instance has a public origin, so it refuses the dev modes:

```bash
echo "CLOUDFLARED_TUNNEL_TOKEN=<your-tunnel-token>" >> infra/.env
echo "COMPOSE_PROFILES=webhook-tunnel" >> infra/.env
echo "CLOUDFLARED_TUNNEL_TOKEN=<your-tunnel-token>" >> .env
echo "TUNNEL_TOKEN=<your-tunnel-token>" >> .env
```

If `infra/.env` already sets `COMPOSE_PROFILES`, add `webhook-tunnel` to its comma-separated list instead. A stack started with `embry0 start` reads only the root `.env`: add `COMPOSE_PROFILES=webhook-tunnel` there, and in step 3 run `docker compose -f infra/docker-compose.yml -p embry0 --env-file .env up -d cloudflared` from the repo root.

**3. Bring up the tunnel container:**

```bash
cd infra
docker compose up -d cloudflared
sleep 8
docker logs embry0-cloudflared --tail 20 | grep 'Registered tunnel'
```

You should see at least one `Registered tunnel connection` log line. Webhooks posted to `https://webhooks.example.com/api/v1/webhook` now flow through the tunnel into `orchestrator:8000`. With the profile on and no token, the container instead exits with `"cloudflared tunnel run" requires the ID or name of the tunnel` and restarts until one is set.

**4. Configure the forge** to send webhooks to your hostname — see the per-forge sections below, using `https://webhooks.example.com` as the base URL.

**Tearing down:** `docker compose stop cloudflared`, then delete the tunnel and its DNS record in the Cloudflare dashboard.

## Per-forge configuration

### GitHub

Repo → Settings → Webhooks → Add webhook:

- **Payload URL:** `<base>/api/v1/webhook`
- **Content type:** `application/json`
- **Secret:** the value of `GITHUB_WEBHOOK_SECRET` in `.env`
- **Events:** Issues, Issue comments, Pull requests

GitHub signs each delivery with `X-Hub-Signature-256: sha256=<hex HMAC>`. A job dispatches when an issue is opened carrying a trigger label (default `embry0`, see `TRIGGER_LABELS`), or when a trigger label is applied to an existing open issue.

`issue_comment.created` also carries answers to agent questions: `/answer <input_id>: <text>` (canonical — the id is stable no matter what other channels answer in the meantime, and embry0 prints it under each question in the question comment) or `/answer <n>: <text>`, where `<n>` is the question's position *in that one question comment*, not in the live pending list. The positional form is sugar bound to a single comment: embry0 refuses it whenever the issue has questions outstanding in more than one comment, or a blocking question whose comment it could not map — it cannot tell which list you are replying to, since GitHub issue comments carry no `in_reply_to`. Anything embry0 cannot resolve — an ambiguous `<n>`, a stale one, or a question already answered from another channel — is rejected with a reply asking for the id (or the dashboard), never applied to a neighbouring question. `/skip <ref>` takes the same reference. Directives are honored only from comment authors whose `author_association` is in `COMMENT_DISPATCH_AUTHOR_ROLES` (default `OWNER,MEMBER,COLLABORATOR`); everyone else is ignored silently and logged as `answer_rejected_author`.

**Pull request review (#30).** With review turned on for the repository, `pull_request` (`ready_for_review`, or `opened` as non-draft) and an `issue_comment` on a pull request with a line starting `/embry0 review` start an advisory review that embry0 posts on the pull request; by default a ready pull request is reviewed only when its author has write access (the repository's `pr_review.ready_authors`). Such a comment is only a review request, never an `/answer` or a follow-up. The `pull_request` events also keep feeding merge sync, as before. See [pull request review](pr-review.md).

### Linear

Linear workspace → Settings → API → Webhooks → New webhook:

- **URL:** `<base>/api/v1/webhook/linear`
- **Signing secret:** copy it into `LINEAR_WEBHOOK_SECRET` (or Settings → Integrations, which wins)
- **Event types:** Issues (the handler acts on issue `create`/`update` events; everything else is ignored)

⚠️ Linear's signature is a **bare hex** HMAC-SHA256 in the `Linear-Signature` header — no `sha256=` prefix, unlike GitHub. If you port verification code between the two, this is the bug you will write.

Beyond the webhook itself, the Linear trigger path needs two more pieces of configuration (see `docs/configuration.md`):

- `LINEAR_API_KEY` — drives the inbound trigger *and* outbound write-back. Leave empty to disable the integration entirely (the route answers `"not configured"`).
- **Repo routing** — an issue must resolve to an `owner/repo`: first via a `repo: owner/name` stamp in the Linear project's summary/description, then via the `LINEAR_REPO_MAP` env var (JSON keyed by project name or team key). An issue whose project resolves to nothing is ignored.

A job dispatches when an issue create/update carries a trigger label; dedupe is on the Linear identifier, so label churn on an already-dispatched issue does not re-trigger.

**Workflow state.** Only tickets whose workflow-state *type* is `backlog`, `unstarted` or `started` dispatch — an allowlist, because workspaces define further types (`triage`, `duplicate`) beyond `completed`/`canceled`. The webhook and the reconciler apply the same check.

**Retrying a failed ticket.** Remove and re-apply the trigger label. The label counts as re-applied only when Linear's issue history dates the add after the failed job ended — the test the reconciler makes — so a redelivered or late webhook describing the label that started the job is not a retry (#462). A ticket dispatches again only when its embry0 issue is `open`, no job is live (`pending`, `running`, `awaiting_input`, `paused`), no job of the ticket left a pull request that still stands, and the latest job ended `failed`, `cancelled` or `expired` (or there is no job at all). Delivered work (`completed`, `partial`, `pr_merged`, `pr_closed`) never re-dispatches from a label. A pull request is judged by `delivery_state`, not by the job's status (#351): a merged one is delivered work and blocks; one closed unmerged no longer blocks; an open one left by a `failed`, `cancelled` or `expired` job does not block either, because the next run **continues on that pull request** (same branch, same PR, told why the earlier run stopped, #349); any other open one, or a `pr_url` with no delivery fact yet, blocks. A job that fails after pushing its branch gets that pull request in the first place: labelled `human-merge-only`, with a body that says why it stopped. An abandoned job opens none. The rule lives in `embry0/trackers/redispatch.py`.

**When a label does not dispatch,** embry0 says why in a comment on the ticket — unmapped project, dedupe (naming the blocking job or PR), the daily/monthly budget, the per-author dispatch cap, or a dispatch that errored — at most once per reason per ticket until the ticket is next dispatched successfully (ledger: `tracker_dispatch_notices`). Linear posts API comments under the key's owner, so every comment embry0 writes on a Linear ticket — these notices, agent questions, answers and job outcomes — opens with `embry0 (automated):` (added once, in `LinearSyncService.post_comment`). Job-outcome comments state the job's `error_code` and a fixed sentence (`embry0/notifications/public_errors.py`), never `jobs.error_message` or spend. The notice texts live in `embry0/services/linear_notice_texts.py`; their advice depends on whether the reconciler is `on` ("nothing to do") or not ("remove the label and add it again").

**Lost webhooks.** A reconciler sweeps every `LINEAR_RECONCILE_INTERVAL_SECONDS` (default 300): it lists the tickets in a `backlog`, `unstarted` or `started` workflow state that carry a trigger label and sends them through the same dispatch path. It starts tickets that have no job, and failed tickets whose label was re-applied *after* the failed job (read from Linear's issue history) — a label that merely stayed on a failed ticket is never retried automatically. It also reopens Linear-tracked issues stuck in `triaging` with no live job for `LINEAR_RECONCILE_STALE_TRIAGING_MINUTES`. The effective trigger labels are the same as the webhook's (Settings → Integrations over `TRIGGER_LABELS`).

It ships in **shadow mode** (`LINEAR_RECONCILE_MODE=shadow`): every decision is taken and nothing is changed — it logs `linear_reconcile_would_dispatch` (`identifier`, `repo`, `reason`, cap usage), `linear_reconcile_would_skip` (`identifier`, `reason`, `detail`) and `linear_reconcile_would_reopen`, once per ticket and again only when the decision changes. A ticket whose row is stuck in `triaging` logs `would_reopen` and then `would_dispatch` with `after_reopen=true` — what `on` would do. **Before setting the mode to `on`:** review a week of `linear_reconcile_would_*` logs, set `DASHBOARD_PUBLIC_URL` to an address colleagues can open (a loopback URL is left out of ticket comments), and configure a budget cap (Settings → Budget, or `DAILY_BUDGET_CAP_USD` / `MONTHLY_BUDGET_CAP_USD`). In `on` mode its jobs carry `dispatched_by = linear-reconciler` (so the per-author rate limit and the budget caps apply) and it is bounded per sweep, per rolling 24 h, per ticket, and per consecutive dispatch failure — see `docs/configuration.md`.

### GitLab

Project (or group) → Settings → Webhooks → Add new webhook:

- **URL:** `<base>/api/v1/webhook/gitlab`
- **Secret token:** the value of `GITLAB_WEBHOOK_SECRET` in `.env`
- **Trigger:** Issues events (the handler acts on `object_kind: issue` with action `open`/`reopen`/`update`; `close` syncs status), plus **Merge request events** and **Comments** for [pull request review](pr-review.md) (a merge request marked ready or opened as non-draft, and a `/embry0 review` note on a merge request; these reach the reviewer even when the GitLab issue sync is not set up); everything else is ignored

⚠️ GitLab does **not** sign the body at all: `X-Gitlab-Token` carries the configured secret **verbatim**. Treat that secret like a bearer token — it is only as safe as the TLS channel it rides on, so never expose the endpoint over plain HTTP.

A job dispatches when an issue carries a trigger label (label events arrive as `update` actions with the label list attached). Self-managed GitLab works the same — set `GITLAB_URL` to your host.

## Option B — smee.io relay (local development)

For testing real GitHub events against a local embry0 instance on your laptop, with no public hostname needed. smee.io re-serializes the webhook body before forwarding, which invalidates GitHub's HMAC — so this flow uses `WEBHOOK_DEV_MODE=true` and no secret.

This is for a local instance only. Startup refuses `WEBHOOK_DEV_MODE` (and `AUTH_DEV_MODE`) while SSO (`AUTH0_*` / `SSO_PUBLIC_ORIGIN`) is configured, or while the orchestrator's environment (the repo-root `.env`) carries a tunnel token (`CLOUDFLARED_TUNNEL_TOKEN` / `TUNNEL_TOKEN`), so leave those unset there on the instance you relay to.

**1. Get a smee channel:** visit [https://smee.io](https://smee.io), click **Start a new channel**, and copy the channel URL (e.g. `https://smee.io/aBcDeF1234`).

**2. Start the relay** (Node 20+ required):

```bash
npx smee-client --url https://smee.io/aBcDeF1234 --target http://localhost:8200/api/v1/webhook
```

Leave this running in a terminal pane — it prints every forwarded event.

The same relay flow works for Linear and GitLab — point `--target` at their endpoint instead (`.../api/v1/webhook/linear` or `.../api/v1/webhook/gitlab`) and leave the corresponding secret empty. One smee channel per forge.

**3. Enable WEBHOOK_DEV_MODE** in `.env`, clear the webhook secret, and set an API key:

```
WEBHOOK_DEV_MODE=true
GITHUB_WEBHOOK_SECRET=
API_KEY=<python -c 'import secrets; print(secrets.token_urlsafe(32))'>
```

`WEBHOOK_DEV_MODE` does not switch API authentication off, so startup refuses an empty `API_KEY` here as it does anywhere else; the dashboard asks for the key on first load. For a throwaway instance with no API authentication at all, set `AUTH_DEV_MODE=true` instead of an `API_KEY`. That opens the whole API to anyone who can reach it.

Rebuild the orchestrator so the new config is picked up:

```bash
cd infra && docker compose build orchestrator && docker compose up -d orchestrator mcp --force-recreate
```

**4. Configure the GitHub webhook** — repo → Settings → Webhooks → Add webhook:

- **Payload URL:** your smee channel URL (e.g. `https://smee.io/aBcDeF1234`)
- **Content type:** `application/json`
- **Secret:** *(leave blank)*
- **Events:** Issues, Issue comments, Pull requests

**5. Verify:** trigger an event in the repo. You should see the event appear in the smee-client terminal AND in `docker logs -f embry0-orchestrator | grep webhook_received`.

> **Note:** smee caches recent events and replays them on reconnect, which can cause duplicate job triggers after restarting the relay. For demos or production, always use Cloudflare Tunnel with HMAC verification.

## Merge sync — what became of a PR

The `pull_request` event (`opened`, `reopened`, `synchronize`, `closed`) does not start anything; it records what became of the PR a job produced. The PR is matched to jobs by its stored URL — and, for a job that never stored one, by the branch the job pushes to, re-derived from the job row: `embry0/<issue id>-<slug>`, or `embry0/unknown-<slug>-<job hex>` for a job with no issue (`<job hex>` is the first 8 characters of the job id after `job-`; a job of an issue adds it too when an earlier job's branch held the name, and jobs with no issue created before the suffix are still matched on `embry0/unknown-<slug>`), which is how a PR that a person opened from a failed job's branch is recognised. Every matched job gets `delivery_state` (`pr_open` / `merged` / `closed`) **whatever its status**; `status` itself only follows where it already had a meaning (`completed → pr_merged` / `pr_closed`, and back to `completed` if a closed PR is reopened). A merge also closes the embry0 issue and is reported to the healing-case ledger with GitHub's merge time. Deliveries may be repeated or arrive out of order: each fact is dated by GitHub, a merge is final, and otherwise the newer fact wins.

**Missed deliveries and repos without a webhook.** A sweep runs every `MERGE_SYNC_INTERVAL_SECONDS` (default 900) and asks GitHub directly, read-only and with the per-owner tokens (`GITHUB_TOKEN__<OWNER>`), about every tracked PR that is not settled, then lists the most recently updated PRs of repos that have recent jobs without a PR URL. It is bounded on both axes — `MERGE_SYNC_MAX_REQUESTS` GitHub calls and `MERGE_SYNC_MAX_WRITES` job rows per sweep — and stands down when the token's rate limit runs low (`MERGE_SYNC_RATE_LIMIT_FLOOR`).

It **ships in `shadow`** (`MERGE_SYNC_MODE`): it logs `merge_sync_would_record` and changes nothing. Its GitHub calls are read-only, but it writes `delivery_state`, job status, `pr_url`, closes the embry0 issue of a merged PR and moves healing cases, with no human in the loop — so a human reads one sweep's worth of `merge_sync_would_record` and then sets `MERGE_SYNC_MODE=on`.

**The mode gates the sweep only.** The `pull_request` webhook records through the same code in every mode, `MERGE_SYNC_MODE=off` included: a delivery is a fact that arrived, not something embry0 decided to do. Installing the webhook on a repo is therefore still worth it even with the sweep on — it makes the record immediate instead of up to one interval late.

**Check and backfill.** From inside the orchestrator container:

```bash
docker exec -w /app embry0-orchestrator python -m embry0.cli merge-sync check      # read-only; exit 1 if the DB disagrees with GitHub
docker exec -w /app embry0-orchestrator python -m embry0.cli merge-sync backfill   # record GitHub's answer for ALL history; idempotent
```

Both look at every tracked PR in every state and every page of each repo's PR list; `check` prints exactly the jobs and PRs a backfill would change, and neither is write-budgeted — an operator running these is deliberate.

Exit codes: **0** the database matches GitHub · **1** (`check` only) it does not, and the differing rows are listed · **3** everything readable matched, but some PRs could not be read at all. **Exit 3 is not a failure.** It is the normal answer whenever history contains a PR whose repo was deleted or is outside the token's reach — for example `acme/retired` — so a healthy production `check` can exit 3 with an empty change list. Each unreadable PR is printed with its reason; treat a non-empty change list, not the exit code, as the signal.

## Without webhooks

You can trigger jobs manually via the dashboard — open the Issues page, find your issue, and click **Send to Agent**. No webhook setup required.


## Alertmanager as a detection source (#296)

Point your Prometheus Alertmanager at embry0. A firing alert becomes a
**healing case**, readable with the `case_list` / `case_get` MCP tools like
any watcher finding. Leave the receivers you already have in place.

**1. Share a secret.** Alertmanager cannot sign a body, so it presents the
secret whole as a bearer token:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
# the SAME value in both places:
#   embry0's ROOT .env : ALERTMANAGER_WEBHOOK_SECRET=<value>
#   the monitoring stack's .env : ALERTMANAGER_WEBHOOK_TOKEN=<value>
```

**2. Map alerts to repos.** `healing_cases.repo` is `NOT NULL`, and guessing
would file against the wrong repo — so an alert that resolves to no repo is
dropped with a logged reason rather than filed somewhere plausible. Resolution
order is: the alert's own `repo` label, then `ALERTMANAGER_REPO_MAP` matched
against the `service` / `container_name` / `name` / `job` label *values*, then
`WATCHER_REPO` if one is set.

```bash
ALERTMANAGER_REPO_MAP={"backend": "acme/backend"}
```

The cleanest long-term answer is a `repo` label on the alert rule itself, which
ends the question and needs no map.

**3. Add the receiver** in your Prometheus Alertmanager config.
Two details are load bearing:

- `continue: true` on a catch-all child route. Alertmanager stops at the first
  matching child otherwise, so without it the new receiver would take alerts
  *away* from the receivers you already have instead of adding to them.
- `send_resolved: true`. The resolved edge is what closes the case as
  `self_resolved`; without it every transient alert leaves a case open forever.

### What becomes a case

| Source | Origin | Dedup key | Resolved edge |
|---|---|---|---|
| Alertmanager alert | `alert` | Alertmanager's own `fingerprint` (stable per label set) | `status: resolved` → close as `self_resolved` |
| GitHub `workflow_run` failing on the default branch | `ci` | `repo` + workflow + branch | the same workflow going green → close |

A repeated firing alert counts a **recurrence** on the existing case rather
than filing again — Alertmanager resends every `repeat_interval` (4h here). A
firing alert whose previous case was **closed** opens a new case linked to it
via `parent_case_id`, so "we already fixed this once" stays findable.

CI failures are scoped to the default branch on purpose. A red feature branch
is the pipeline working, and #305 already reacts to those on embry0's own PRs;
a red `main` means the deployment target is broken. The fingerprint is keyed on
the workflow and branch rather than the run id, so every failing attempt of the
same build is one case.
