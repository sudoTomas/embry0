# Watching embry0 itself

embry0's log watcher can watch the deployment it runs in: its own
orchestrator, MCP server, frontend, Postgres and registry containers. A fault
in those logs becomes a draft issue on embry0's own repository, deduped by
signature like any other stack, and a human accepts it by adding the trigger
label. Nothing about the self stack is special in the code — it is one more
`watcher_stacks` row — except the one thing that would otherwise make it
unsafe, the recursion guard below.

Issue: #378. Per-stack configuration: `docs/configuration.md` (the
`WATCHER_*` table gives the global defaults a row can override) and
`PUT /api/v1/watcher/stacks/{owner}/{name}`.

## The recursion guard

The watcher logs. A broken watcher logs errors. A stack whose logs include
embry0's own would therefore see the watcher's `watcher_tick_failed` lines,
analyse them, and — if that analysis fails — produce more of them for the
next tick: a loop, and a paid one. Measured on a production deployment
over the week before the guard, 43 of the 60 lines the self-stack selector
matched were the watcher's own.

So the tick drops every structured line whose `event` starts with one of
`OWN_EVENT_PREFIXES` (`watcher_`, `log_signatures_`, `log_clustering_`)
before counting, clustering or analysing its window
(`embry0/services/watcher_recursion.py`). A window that was nothing but
watcher lines is a quiet tick. This holds whatever the stack's LogQL says; a
test reads every `logger.<level>("…")` event name in the watcher family from
the source and fails if one falls outside the prefixes.

The same guard drops embry0's own log reads. An analysis drills down through
`GET /api/v1/logs/query`, and so do the dashboard's Logs page and
`logs_query` over MCP. Each read leaves lines that quote its query: every
hop's access line carries it in the URL, and the route logs it as
`logs_queried`. A query is made of the words a selector looks for
(`Traceback`, `ERROR`), so before this guard each analysis seeded the next
tick with its own drill-down, and the tick paid to read it: 10 of 50 runs on
one deployment. A line counts as a log read when it carries
`/api/v1/logs/query?` or its `event` is `logs_queried` (`LOG_QUERY_URL`,
`LOG_QUERY_EVENTS`); a traceback raised inside the route is a separate line
and is kept.

"The watcher is broken" is deliberately not the watcher's to report. The
dead-man alerts in the monitoring stack (`Embry0WatcherNoVerdict3h`,
`Embry0WatcherBacklogStall`, `Embry0WatcherAnalysisFailureRate`,
`Embry0WatcherNeverAnalyses24h`, `Embry0WatcherOnlyQuiet2d`) evaluate per
stack and do that.

A quiet tick (`skipped_quiet`: fewer than `WATCHER_MIN_LOG_LINES` matches
once the watcher's own lines are gone) is a **verdict**, not a stall: a stack
that logs nothing for a night is the loop finding nothing. It refreshes the
dead-man gauge (`embry0_watcher_last_verdict_tick_timestamp_seconds`), and
so does the startup seed after a restart — but it does not advance the query
window, which keeps widening until it holds enough lines for one analysis,
and it leaves `embry0_watcher_last_analysed_tick_timestamp_seconds` alone.
Until 2026-09-27 the dead-man read the analysed gauge, so it paged for this
stack on every quiet night; a busy application stack never showed it because it is
never quiet. The hole that opens — a LogQL that matches nothing, forever,
looks exactly like a calm stack — is `Embry0WatcherOnlyQuiet2d`'s to cover:
verdicts fresh, no analysis in two days. Both are seeded gauges, so a
counter-reset on deploy cannot fake either condition.

## The selector

embry0's orchestrator writes structured JSON (`{"event": …, "level": "error",
…}`); uvicorn, nginx, Postgres and the registry write plain text. Two
consequences shape the selector:

- The body-text regex an application stack typically uses (`(?i)(error|exception|traceback)`)
  is far too loud here — 429 lines a day, almost all of them the watcher
  logging its own LogQL string, `docker_cmd` debug lines quoting SQL, and
  Postgres statements. The level is what matters, and for a JSON line the
  level is a field.
- No `| json` stage. On a stream that mixes JSON and plain text the stage
  errors on every plain-text line, and while a log query still returns those
  lines (tagged `__error__`), every **metric** query over the pipeline fails
  outright (`pipeline error: 'JSONParserErr'`) — which is what the partition
  checks below, and any dashboard, would run. Line filters (`|~`, `!~`) never
  touch a plain-text line. The level of a JSON line is therefore read
  in-process, by parsing it (`embry0/services/log_levels.py`); the selector
  only narrows on the literal `"level": "(error|critical)"`.

The selector; the second line is `LOGQL_OWN_EVENTS_EXCLUSION` and the third
`LOGQL_LOG_QUERY_EXCLUSION`, both from `embry0/services/watcher_recursion.py`,
so neither can drift from the guard:

```
{compose_project="embry0"}
  |~ "\"level\": \"(error|critical)\"|Traceback"
  !~ "\"event\": \"(watcher_|log_signatures_|log_clustering_)"
  != "/api/v1/logs/query?" !~ "\"event\": \"(logs_queried)\""
```

Measured on a production deployment (2026-09-24, Loki 2.9.8):

| query, over 7 days | lines |
|---|---:|
| `{compose_project="embry0"}` | 254,522 |
| `… \|~ "\"level\": \"(error\|critical)\"\|Traceback"` | 60 |
| of which the watcher's own events (dropped by the guard) | 43 |
| after the exclusion | 17 (≈2.4 a day) |

Each filter was shown to partition the stream exactly (matched + inverse =
total), which is the proof that nothing is lost to a parse error — the same
check #368 used for the level filter.

Deliberately **not** selected:

- Postgres `ERROR:` lines. They are real when they come from the
  orchestrator, but the same container may also serve unit-test scratch
  databases (the test recipe in `CLAUDE.md`, "The local test baseline is not
  green", used it until #529 moved it to a throwaway server), and every test
  run logs foreign-key and unique-constraint violations that are the tests'
  own. On this deployment that was eight bursts in a week, every one
  a test run.
- The registry's logfmt `level=error` lines. `blob unknown` and `manifest
  unknown` are how a `docker push` asks whether a layer exists; the bootstrap
  push produces a burst of them on every restart.

## Caps

Around two matching lines a day means the global `WATCHER_MIN_LOG_LINES`
(5 per hourly window) would never let a tick analyse. The self stack sets
`min_log_lines: 1` — a single error in embry0 is worth reading — and tighter
spend caps to go with the lower floor. All of these are row `settings`
overrides; every key is in `OVERRIDABLE`
(`embry0/services/watcher_stacks.py`).

```json
{
  "min_log_lines": 1,
  "max_open_proposals": 2,
  "max_full_cap_analyses_per_day": 2,
  "job_budget_usd": 1.0
}
```

The `skip_unchanged` comparator (#291) and signature dedupe (#290) apply as
on any stack, so the same fault firing every hour is analysed once.

## What the analysis job carries

Since #378 an analysis job carries its stack's `repo` (before, `NULL`). That
is what lists it under the repo and routes its notifications, so proposals,
jobs and runs about embry0 reach whatever the deployment's webhook consumer
routes that repo to. The workspace stays `type: none` — nothing is cloned,
the log excerpt rides the task — but the repo **must be registered** (#360
refuses jobs for unregistered repos: a tick on an unregistered stack records
an `error` naming `ERR_REPO_UNREGISTERED`, before any logs are fetched or an
analysis is paid for). This applies to every stack, not only the self stack.

## Setting it up

The self stack is configuration: it lives in the database of the deployment
that watches itself, never in this tree. `<owner>/embry0` below is that
deployment's fork or clone of this repository.

1. **A GitHub token that can read the repo and write its issues.** The
   filer uses the orchestrator's per-owner routing
   (`GITHUB_TOKEN__<OWNER>`, falling back to `GITHUB_TOKEN`). Check before
   enabling — a token that returns 404 for the repo files nothing and every
   tick ends with a deferred finding:
   ```bash
   docker exec embry0-orchestrator sh -c 'curl -s -o /dev/null -w "%{http_code}\n" \
     -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/embry0'
   ```
2. **Register the repo** (`POST /api/v1/repos`, `repo_register` over MCP, or
   the dashboard).
3. **Write the stack row.** `tracker: github` files into the stack repo
   itself; `tracker_target` may name another repo to file into.
   ```bash
   curl -X PUT -H "Authorization: Bearer $API_KEY" -H "X-Requested-With: XMLHttpRequest" \
     -H "Content-Type: application/json" \
     http://localhost:8200/api/v1/watcher/stacks/<owner>/embry0 -d '{
       "logql": "{compose_project=\"embry0\"} |~ \"\\\"level\\\": \\\"(error|critical)\\\"|Traceback\" !~ \"\\\"event\\\": \\\"(watcher_|log_signatures_|log_clustering_)\" != \"/api/v1/logs/query?\" !~ \"\\\"event\\\": \\\"(logs_queried)\\\"\"",
       "tracker": "github",
       "settings": {"min_log_lines": 1, "max_open_proposals": 2, "max_full_cap_analyses_per_day": 2, "job_budget_usd": 1.0},
       "enabled": true
     }'
   ```
   Writes are admin-tier and, like every state-changing call, need the
   `X-Requested-With` header (the CSRF guard answers 403 "Missing
   X-Requested-With header" without it). Unknown `settings` keys are dropped
   and echoed back under `ignored_settings` — read the response. A known key
   with the wrong type or a value out of range is refused with a 422 that
   names it (`settings.auto_accept_max_per_day: Input should be a valid
   integer`): numbers are JSON numbers, switches JSON booleans, modes and
   severities one of their words. A value stored before these checks that no
   longer passes is ignored with a `watcher_stack_setting_invalid` warning,
   and the deployment's value applies.
4. **Restart the orchestrator.** Stacks are read once at startup
   (`watcher_stacks_loaded`); a new or re-enabled row starts ticking after
   the next restart. `GET /api/v1/health/ready` then lists it under
   `checks.watcher_stacks`, and the per-stack metrics
   (`embry0_watcher_ticks_total{stack="<owner>/embry0"}`) appear after its
   first tick.

## The deploy marker

A case walks `merged → deployed` when `DeployLedgerSync` finds a deploy
marker — a `deployed <sha>: <services>` line matched by the stack's
`deploy_logql` — whose commit contains the case's merge (proved through the
forge's compare API). The global `WATCHER_DEPLOY_LOGQL` selects the *watched
application's* deploys; embry0's own are a different stream, so the self
stack overrides it:

```
PUT /api/v1/watcher/stacks/{owner}/{name}   settings: {"deploy_logql": "{job=\"embry0-deploy\"}", …}
```

`infra/deploy.sh` pushes that line to Loki after a healthy deploy (the URL
is `EMBRY0_DEPLOY_LOKI_URL`, else `WATCHER_LOKI_URL` from the root `.env`);
`infra/deploy.sh marker <sha> <services>` pushes it for a deploy done by
hand. The line format is pinned to the parser by
`tests/unit/infra/test_deploy_marker.py`. Until 2026-09-27 nothing pushed one
for embry0, so every self case stopped at `merged` — the fix was live and
the ledger could not know.

## Accepting a finding without a person

A stack whose `auto_accept` setting is on accepts its own drafts once they
clear its bar, so a finding does not wait days for someone to say yes (spec
2026-10-25). Each tick, after it reconciles drafts a person cancelled, it
takes the stack's tracked drafts oldest first and accepts each that has
waited `auto_accept_after_hours` (24), is at least `auto_accept_min_severity`
(`high`) and whose signature was seen at least `auto_accept_min_occurrences`
(10) times, until `auto_accept_max_per_day` (2) were accepted for this
stack's repository in the last 24 hours; each stack's cap is its own. A retry after a `not_healed` verdict, and a draft an owner declined or
snoozed as a proposal, are left for a person. The acceptance is the one below,
recorded as `embry0:auto-accept` with a `case_auto_accepted` audit row; the
actor is rate-limited and the spend caps apply. Off by default, per stack:

```
PUT /api/v1/watcher/stacks/{owner}/{name}
{"logql": "...", "settings": {"auto_accept": true, "auto_accept_min_severity": "medium"}}
```

## Accepting a finding without the label

A draft embry0 files on its own repository is a GitHub issue, and GitHub's
label trigger needs a webhook installed on that repository. The self stack
does not depend on one: a watcher finding is accepted through the API, and
the acceptance is the same dispatch the label webhook would have run — the
embry0 issue row mirrors the draft, the job is linked to the case, and the
case walks `accepted → pr_open → merged → deployed → verified` from there.

```
POST /api/v1/cases/{case_id}/accept        {"accepted_by": "<who said yes>"}
→ 202 {"case_id", "repo", "tracker_key", "issue_id", "job_id", "status": "dispatched"}
```

The `watcher.proposed` notification carries the `case_id` (and `tracker`,
`repo`), so whoever relays the human's "accept it" — a chat relay,
through the `case_accept` MCP tool, or the Accept button on the dashboard's
Proposals list — has the address in hand. Refusals, with nothing created:
`404` unknown case; `409` not a watcher finding, no draft yet, already
accepted, closed, the draft closed on GitHub, or a GitLab draft (those keep
the label: add it in GitLab); `403` a repo that is not an **enabled** watcher
stack of this orchestrator; `502` GitHub could not serve the draft. Accepting
twice starts no second job: the issue row is already `triaging`.

### A draft filed in Linear

A stack whose drafts go to Linear is accepted through the same route (#460).
In Linear the label is not only a trigger, it is a precondition — the
dispatch refuses a ticket that does not carry it — and it is how the ticket
shows that it was accepted. So the acceptance does what the person would
have done by hand, in this order:

1. **Decide first, touching nothing.** The ticket is read and the whole
   dispatch decision is taken as a dry run with the label assumed present:
   the ticket's state, a job already on it, the repo its project resolves
   to, the dispatch caps. Anything that would refuse the job is answered
   here, and the ticket is left exactly as it was.
2. **Check the repo.** The dispatch takes the repo from the Linear project's
   `repo:` stamp (or `LINEAR_REPO_MAP`); the case carries the stack's repo.
   If they differ the job would run against the other repo and never be
   linked to this case: `409`, nothing labelled.
3. **Put the trigger label on the ticket** (`issueAddLabel`). The label is
   the configured trigger label as Linear holds it for that ticket's team.
4. **Dispatch through the Linear trigger path** — the function the label
   webhook and the reconciler end in, with `created_by="case-accept"` on
   the issue row and the accepting person as the job's dispatcher.

Step 1 comes before step 3 for a reason that is easy to miss: once the label
is on, Linear sends its label webhook, and that delivery is a dispatch
request in its own right. Taking the label off a moment later does not
recall it. A ticket that should not be dispatched must therefore never be
labelled, rather than labelled and cleaned up.

That same webhook arrives after every successful acceptance too. The
orchestrator that put the label on expects it and ignores it once (for 15
minutes, in memory); an orchestrator that has no such memory — a second
process, or the same one after a restart — falls back to the ordinary dedupe
on the tracked ticket (`already dispatched`). Either way one acceptance is
one job.

Refusals specific to a Linear draft. Every one says what was done to the
ticket:

| Status | When | The ticket |
|---|---|---|
| `503` | Linear is not configured on this orchestrator | untouched |
| `502` | Linear could not serve the ticket | untouched |
| `502` | Linear did not confirm the label (it refused, or did not answer) | no job; a label that landed after all is an ordinary label, dispatched by its own webhook |
| `409` | the ticket's state does not take a job, it is already dispatched, its project names no repo, it resolves to another repo, or Linear has no trigger label for its team | untouched |
| `429` | the dispatch rate limit or the budget cap is reached | untouched |
| `409` / `429` / `502` | the dispatch was refused or failed *after* the label went on (the ticket changed, or a cap was reached, in that instant) | the label is taken off again; if that fails the error says the ticket is **left labelled with no job** and asks for the label to be removed in Linear |

A `202` always carries a `job_id`; there is no success without one.

GitLab drafts are not accepted here yet. The route dispatches per tracker
(`_ACCEPTORS` in `embry0/api/v1/cases.py`), so adding GitLab is one more
entry that labels through the GitLab client and dispatches through
`gitlab_sync`; until then a GitLab draft answers `409` and is accepted by
its label.

### A finding sent back by a `not_healed` verdict

When an accepted finding's fix ships and the confirm-healed sweep still sees
its error after the deploy, the verdict is `not_healed` and the sweep
reopens the case, once per case (`MAX_NOT_HEALED_RETRIES`). The reopen
dispatches nothing. The case goes back to `tracked`, the ticket is told,
and the retry waits for a person, as the first attempt did:

- It is listed by `awaiting_decision` (the Proposals list, and
  `case_list(awaiting_decision=true)`). It holds no slot of the watcher's
  draft cap: its draft was answered once, and the watcher filed nothing new.
- Its age in `tracked` starts at the reopen, and the failed attempt's
  `accepted_at` is cleared.
- Accepting it through this route starts a new job on the issue the failed
  attempt ran on, whatever the tracker. That issue was closed when the
  attempt's PR merged; it is reopened and dispatched like a retry from the
  dashboard, and the ticket is not touched. The job's link accepts the case.
- `POST /api/v1/cases/{case_id}/close` settles it instead.

The tracker's own trigger does not start a retry: a label never repeats
delivered work, so a trigger label left on the ticket, or a reconciler
sweep, cannot dispatch it unasked. A GitHub issue a person reopens and
labels again is the exception, because reopening it there is the same
decision.

Why not straight back to `accepted`: that stage means a job should be
starting, and `Embry0HealingAcceptedCaseNotDispatched` pages on a case that
waits there. Why not an automatic dispatch: the sweep is a ledger write by
design, and a verdict that measured the wrong error would pay for a fix of
the wrong thing.

Refusals specific to a retry: `409` when the issue was `cancelled` (a person
stopped it) or a job is already on it, and `502` when the dispatch failed
(the issue is left `open`; accept again).

### What the narrow key can do

The `403` is the point of the design. The route is on the merge-scope
allowlist, so the chat bot's narrow key can call it — and because the route
refuses every stack this orchestrator does not watch, that key still cannot
start a job anywhere else. "Only findings this instance surfaced" is
enforced here, not in a prompt. Within those findings the key holds the
whole acceptance, whichever tracker the draft is in: for a Linear draft that
means labelling the ticket and starting its job.

## Proving it in production

A deliberately induced error in the orchestrator (a bad env value in a
scratch deploy, or any endpoint made to raise) should produce, on the next
tick, exactly one `watcher_own_lines_dropped`-free analysis, one GitHub
draft issue, one notification — and no second issue on the tick after that.
A burst of the watcher's own failures (point its LogQL at an unreachable
Loki for an hour) should produce `skipped_quiet` ticks with a
`watcher_own_lines_dropped` line each, no analysis and no issue.
`Embry0WatcherNoVerdict3h` stays quiet — those ticks are verdicts — and
after two days of nothing else `Embry0WatcherOnlyQuiet2d` warns.
