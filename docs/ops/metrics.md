# Metrics

The orchestrator serves Prometheus metrics at **`GET /metrics`** on port 8000:
generic HTTP metrics from `prometheus-fastapi-instrumentator`, the standard
process/platform/GC collectors, and the self-healing-loop metrics below
(#285, plan item SH-P2.1).

Alert rules (#286) and the Grafana dashboard (#287) are built on the names
here. **This table is their contract**: renaming a metric or changing a label
breaks expressions living in another repo, so treat a rename as a breaking
change and update your Prometheus rules in the same wave.

## Where it is exposed

Your Prometheus scrapes the orchestrator directly over the compose network.
Attach it to `embry0_backend` and add this job:

```yaml
  - job_name: embry0_orchestrator_metrics
    scrape_interval: 30s
    static_configs:
      - targets: ['embry0-orchestrator:8000']
```

If that scrape file is a **single-file bind mount**, an edit needs
`docker compose up -d prometheus` in your Prometheus stack — a `/-/reload`
silently keeps the old inode. The 30 s scrape interval matches the 30 s
gauge cache below.

`/metrics` is **not** reachable from the internet, and must stay that way:
nginx proxies no `/metrics` location on either vhost (a request falls through
to the SPA), and the webhooks tunnel admits only `/api/v1/webhook` and
`/api/v1/telegram/callback`. It carries no secrets, but it does expose queue
depth, spend and failure codes. `tests/unit/metrics/test_endpoint.py` pins the
nginx half; the tunnel side is ingress config, not this repo.

## How it is collected

Two kinds of metric, for two different failure modes:

- **Event metrics** (counters, the duration histogram, the watcher's tick
  timestamps) are moved by `LoopMetrics` at the place the event happens. They
  reset when the orchestrator restarts, which `rate()` and `increase()` handle.
  The two tick timestamps are the exception: they are **seeded from
  `watcher_runs` at startup**, so a deploy does not look like an outage to the
  dead-man alert.
- **State gauges** (open cases, queue depth, cap occupancy) are read from
  Postgres when Prometheus scrapes, cached 30 s, single-flight, all sources
  under one shared 2 s deadline. A source that fails or times out increments
  `embry0_loop_snapshot_errors_total{source}`, keeps its last value, and the
  scrape still returns 200. **Alert on
  `embry0_loop_snapshot_last_success_timestamp_seconds` going stale** — a
  gauge that stops being refreshed otherwise reads as a healthy flat line.

Metrics are optional: if they fail to install the API still starts, with no
`/metrics` and a `CRITICAL` `loop_metrics_install_failed` line.

## Cardinality rules

Every label is either an enum value or passes a `BoundedLabel` ceiling; an
unrecognised value becomes `other`. **Nothing is ever labelled by job id,
ticket id, case id, PR url, image or error message.** The first time a label
hits its ceiling it logs `metric_label_overflow` once with the label name —
from then on every new value reads `other` until a restart, which is invisible
in the metric itself. A recurring line across restarts means the ceiling is
too low, not that the data is unbounded.

Two conventions worth knowing before writing an expression:

- The two error labels speak different vocabularies. `error_code="none"`
  means the job carries no code at all — every completed job.
  `error_category` belongs to the **failure taxonomy** (`category_for_code`,
  #352), which is authoritative: `infra`, `agent`, `verification`, `budget`,
  `config`, `cancelled`, and `unknown` for a missing or unrecognised code —
  the same seven values `/stats.failure_categories` reports. So a completed
  job reads `error_code="none", error_category="unknown"`; **count failures
  with `status="failed"`, never by filtering on a category.**
  `error_category="uncategorised"` is not a taxonomy value and should never
  appear: it means this build exposes no categoriser to ask, so a series
  carrying it is a signal that the taxonomy module changed shape.
- `pipeline_template="unassigned"` means the job had no template at that
  moment — triage had not routed it yet — not that a template is missing.

## Watcher

| Metric | Type | Labels |
|---|---|---|
| `embry0_watcher_ticks_total` | counter | `action` (the `watcher_runs` action; unknown → `other`) |
| `embry0_watcher_ticks_blocked_total` | counter | — |
| `embry0_watcher_last_tick_timestamp_seconds` | gauge | — |
| `embry0_watcher_last_analysed_tick_timestamp_seconds` | gauge | `stack` |
| `embry0_watcher_last_verdict_tick_timestamp_seconds` | gauge | `stack` |
| `embry0_watcher_health` | gauge 0/1 | `state`: `ok`, `unverified`, `invalid_logql`, `missing_logql`, `loki_misconfigured` |
| `embry0_watcher_enabled` | gauge 0/1 | — |
| `embry0_watcher_analysis_cost_usd_total` | counter | — |
| `embry0_watcher_unanalysed_seconds` | gauge | — |
| `embry0_watcher_log_seconds_lost_total` | counter | — |
| `embry0_watcher_ticks_log_truncated_total` | counter | — |
| `embry0_watcher_briefing_failures_total` | counter | `section` |
| `embry0_watcher_open_proposals` | gauge (DB) | `stack` |
| `embry0_watcher_open_proposals_cap` | gauge | `stack` |

`open_proposals` and `open_proposals_cap` are each stack's own numbers (#448): the
drafts that stack's cap counts, inside that stack's window, and that stack's
`max_open_proposals` (the global default when the row does not override it). They
are what the stack's tick decides on. Compare them series for series —
`embry0_watcher_open_proposals >= embry0_watcher_open_proposals_cap` matches on
`stack` — never as a sum: a sum against one stack's cap fires with no stack
capped and stays quiet while a small-cap stack is blind.

`ticks_blocked` counts scheduled ticks withheld because `WATCHER_LOGQL` is
faulty — they write **no** `watcher_runs` row, so they are invisible in
`ticks_total`. `unanalysed_seconds` is the last tick's window gap;
`log_seconds_lost_total` accumulates only when an analysed tick moves the
window past a gap, i.e. when those logs are gone for good.
`ticks_log_truncated_total` counts **ticks**, not lines.
`last_analysed_tick_timestamp_seconds` moves on a tick that consumed its
window (`no_issue` / `proposed` / `deferred` / `skipped_duplicate` /
`no_change`). `last_verdict_tick_timestamp_seconds` — the dead-man's gauge —
moves on those and on `skipped_quiet`, which found too little to judge and
is the loop finding nothing; `skipped_backlog`, `skipped_analysis_cap`,
`skipped_unparseable` and `error` move neither. Both are seeded from
`watcher_runs` at startup with the same sets.

## Healing cases (the ledger)

| Metric | Type | Labels |
|---|---|---|
| `embry0_healing_cases_open` | gauge (DB) | `stage`, `origin` |
| `embry0_healing_oldest_open_case_age_seconds` | gauge (DB) | `stage` |
| `embry0_healing_cases_awaiting_verdict` | gauge (DB) | — |
| `embry0_healing_cases_closed` | gauge (DB) | `resolution_path` |
| `embry0_healing_cases_opened_total` | counter | `origin` |
| `embry0_healing_case_events_total` | counter | `event`, `effect` |
| `embry0_healing_cases_resolved_total` | counter | `resolution_path` |

Age is measured from `<stage>_at`. A case whose PR closed unmerged goes back
to `accepted` with its first `accepted_at`, so its age there includes the
round trip. A case a `not_healed` verdict reopens waits at `tracked`, aged
from the reopen, and its `accepted_at` is cleared: the `accepted` age never
measures a failed attempt's acceptance, and a reopened case is not in it
until a person accepts the retry. `awaiting_verdict` uses `fix_deployed` — the merge
is deployed and the verdict is still `pending` — never the stage.
`resolved_total{resolution_path="embry0"}` against the rest is the **bypass
rate**: how often a finding was fixed by someone other than the pipeline.
Case events are counted only after the case's transaction commits.

## Jobs

| Metric | Type | Labels |
|---|---|---|
| `embry0_jobs_finished_total` | counter | `pipeline_template`, `status`, `error_code`, `error_category` (see the conventions above) |
| `embry0_job_duration_seconds` | histogram | `pipeline_template`, `status` |
| `embry0_jobs_in_flight` | gauge (DB) | `status`: `pending`, `running`, `awaiting_input`, `paused` |
| `embry0_agent_spend_usd_total` | counter | `pipeline_template`, `provider`, `model_family` |
| `embry0_job_events_persist_failed_total` | counter | — |

Outcomes are counted at the terminal status transition and at boot-time
orphan recovery — the two ways a live job ends. Spend is counted per `traces`
row, one per agent run, which is also what `GET /jobs/{id}` bills from.
`model_family` drops the version (`claude-opus`, `grok`), so a model bump
does not split a series.

A persist failure means a live client saw an event no reconnecting client
can replay. Alert `Embry0JobEventsNotPersisted`
(`infra/prometheus/job-events.rules.yml`) fires on any increase in 15
minutes.

## Linear dispatch reconciler

| Metric | Type | Labels |
|---|---|---|
| `embry0_linear_reconciler_enabled` | gauge 0/1 | — |
| `embry0_linear_reconciler_sweeps_total` | counter | `mode`, `result`: `ok`, `disabled`, `error` |
| `embry0_linear_reconciler_decisions_total` | counter | `decision`: `dispatched`, `would_dispatch`, `skipped`, `failed`; `reason` |
| `embry0_linear_reconciler_last_sweep_tickets` | gauge | `decision`, plus the sweep totals `listed`, `deferred`, `reopened`, `would_reopen` |
| `embry0_linear_reconciler_last_sweep_timestamp_seconds` | gauge | — |
| `embry0_linear_reconciler_daily_dispatches` | gauge (DB) | — |
| `embry0_linear_reconciler_daily_dispatch_cap` | gauge | — |

`decisions_total` counts a standing decision **once per change**, not once
per sweep: a ticket the reconciler keeps skipping every five minutes would
otherwise add ~2,016 increments a week and make `increase()` read as
2,016 tickets. A `dispatched` decision is an action, so it is counted every
time. `last_sweep_tickets` is the standing view of the last sweep, and the
`decision` values it shares with `decisions_total` mean the same thing.

**Guard every reconciler alert with `embry0_linear_reconciler_enabled`**:
`mode=off` returns before a sweep, so the sweep gauges stay at 0 and read
exactly like a stuck reconciler.

## Runner and infrastructure

| Metric | Type | Labels |
|---|---|---|
| `embry0_sandbox_runner_stale_image_total` | counter | `outcome`: `argv_fallback`, `config_too_large` |
| `embry0_state_channel_folded_concurrent_writes_total` | counter | `key`, `channel` |
| `embry0_volume_reaper_volumes_total` | counter | `outcome`: `removed`, `would_remove`, `failed`, `kept`; `reason` |
| `embry0_volume_reaper_sweeps_total` | counter | `result`: `ok`, `list_failed`, `status_fetch_failed` |

Both of the first two are bug detectors, not throughput: a state-channel fold
always means a routing bug dispatched two nodes in one superstep, and a stale
image means a prebaked sandbox image the deploy recipe does not rebuild.
Any increase is worth a look.

## Off-host backup

| Metric | Type | Labels |
|---|---|---|
| `embry0_offhost_backup_enabled` | gauge 0/1 | — |
| `embry0_offhost_backup_last_success_timestamp_seconds` | gauge | `dataset`: `postgres`, `minio` |

Read from the files the `offhost-backup` service writes to its status volume,
mounted read-only into the orchestrator, on every scrape
(`embry0/metrics/offhost_backup.py`, #581). `postgres` is the modification
time of the newest dump found in the bucket, `minio` the time the last MinIO
copy finished; both are read back from the bucket after each pass, not taken
from the copy's exit code. `enabled` is 1 when `OFFHOST_BACKUP_S3_PATH` is
set. Both timestamps exist from the first scrape and read 0 until a copy has
been read back, so an alert on their age fires for a copy that never once
succeeded. **Guard it with `enabled`, joined `on (instance, job)`**: the
timestamps carry a `dataset` label that `enabled` does not, so a plain `and`
matches nothing. The rule and its `promtool` tests are in `infra/prometheus/`;
the procedure is in [backup-restore.md](backup-restore.md#off-host-copy).

## The snapshot itself

| Metric | Type | Labels |
|---|---|---|
| `embry0_loop_snapshot_last_success_timestamp_seconds` | gauge | — |
| `embry0_loop_snapshot_errors_total` | counter | `source`: `queue`, `cases`, `watcher_open_proposals`, `reconciler_daily_dispatches` |

The timestamp moves only when **every** source succeeded in one refresh.

## Code map

| File | What lives there |
|---|---|
| `embry0/metrics/loop.py` | `LoopMetrics` — every metric name, one method per event; `NULL_METRICS` |
| `embry0/metrics/labels.py` | `BoundedLabel`, enum/provider/model-family label helpers, `ErrorLabels` |
| `embry0/metrics/snapshot.py` | `LoopSnapshot` and the DB-backed gauge sources |
| `embry0/metrics/wiring.py` | `install_metrics`, `build_snapshot`, `seed_watcher_ticks` — the only FastAPI-aware module |

Producers take an optional `LoopMetrics` and call one method at the event
site; nothing else imports `prometheus_client`. A module built without one
gets `NULL_METRICS`, which records nothing. Two producers cannot take the
facade: LangGraph constructs the state channels, so folds go through
`channels.set_fold_observer`, and `execution/agent_runner.py` is baked into
the sandbox image (which ships neither `embry0.metrics` nor
`prometheus_client`), so it takes a plain callable.
