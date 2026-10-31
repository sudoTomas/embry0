# Leads: work embry0 finds by itself

Until leads, work reached embry0 in three ways: a person put the trigger label on a ticket, the watcher drafted one from production logs, or the context audit read the files that steer coding agents. Everything else waited for someone to think of embry0. Leads are the work embry0 finds on its own and offers to a repository's owners as [owner proposals](api.md#owner-proposals): a person only says yes. The design is in [the spec](superpowers/specs/2026-10-25-leads-design.md).

The backlog intake and the lead client work only with Linear and GitHub today; other forges and trackers are tracked in #181.

## Turning leads on

Leads are off until an operator turns them on. Their policy is the `leads` section of the [settings document](configuration.md#settings-document) ([its keys](configuration.md#settings-reference)):

```yaml
leads:
  enabled: true
  finders: [ticket, ci, alert, flaky, review]
  interval_hours: 6
  backlog_interval_hours: 24
  max_new_per_repo_per_day: 5
  review_stale_days: 3
  flaky_commits: 10
  offer_min: 40
  ask_min: 60
  ask_severities:
    crack: [high, critical]
    weed: [high, critical]
```

Those are the defaults, except `enabled`, which is `false`. The loop always runs: every 5 minutes it reads the section, does nothing while `enabled` is false, and otherwise runs a pass `interval_hours` after the last one ended (it wakes for that even between its 5-minute looks, so passes are spaced exactly as before the section), so turning leads on, off, or changing any key needs no restart. `POST /api/v1/leads/run` runs a pass by hand whether leads are on or not, and the leads already stored stay until answered.

**Upgrading.** Leads used to be on by default (`LEADS_ENABLED=true`), so they ran on every deployment that left the variable unset, whether or not a finder ever stored anything. The first start of the release that moved them into the section keeps them on wherever they ran: when `LEADS_ENABLED` is not set and the deployment existed before, it writes `enabled: true` into the document, under a comment saying why, once. The evidence is any record a deployment in use holds from before that release first started: a registered repository, a job, or a `ticket`, `ci`, `alert`, `flaky` or `review` lead (open or gone). The import records that moment once (the database's clock when the process began, before the migrations), so a new deployment, whose tables did not exist then, never shows any, even if it registers a repository while a failed check waits for its retry or before its next start: its leads start off. `LEADS_ENABLED=true` or `false`, when set, is imported as any variable is. If the check fails at the start, leads read off and the check is asked again in the same run until it answers ([configuration.md](configuration.md#settings-document), *From the environment*). `embry0 settings import-env` leaves `enabled` out of the file it writes and says on stderr what a running deployment keeps.

**Per organization.** An organization's [layer](configuration.md#settings-document) may set `finders`, `max_new_per_repo_per_day`, `review_stale_days`, `flaky_commits`, `offer_min`, `ask_min` and `ask_severities` for its repositories: each repository's pass, how its tickets are offered and asked, and which of its leads reach chat follow its organization's values. Whether the loop runs and how often stay the deployment's.

## Where it looks

A repository is read when it is registered and active and someone answers for it: its preferences name owners, or it is a watched stack. An organization's or a repository's [layer](configuration.md#which-repositories-background-features-read) can take it out (`background: {leads: false}`), and a repository's own layer bring it back; the next pass follows. `POST /api/v1/leads/run` with a `repo` reads that repository whatever its layer says. Each finder reads it with its organization's credential and adds at most `leads.max_new_per_repo_per_day` new leads a day, the most severe first.

| Kind | Finder | What it reads | Severity, effort | Accepting starts |
|---|---|---|---|---|
| `ticket` | Backlog intake, every `leads.backlog_interval_hours` | Linear tickets nobody started (unassigned, or assigned to the account embry0's key acts as) whose project resolves to the repository, and the repository's open GitHub issues nobody is assigned | From Linear priority or the labels; from size labels or the estimate | Its tracker's own path: on Linear the trigger label goes on and the ticket is dispatched, on GitHub the issue is brought into embry0 and dispatched |
| `ci` | Failing CI | Check runs and commit statuses on the newest commit of the default branch | high, medium | A fix job for that check |
| `alert` | Alerts | Open Dependabot alerts and open code-scanning alerts | The alert's, small for a dependency with a fixed version | An upgrade or fix job |
| `flaky` | Flaky checks | Every run of every check on the newest `leads.flaky_commits` commits of the default branch | medium, high from 3 commits; medium | A job that finds and fixes the cause |
| `review` | Unanswered reviews | Open, non-draft pull requests from the repository itself with review threads a reviewer started, neither resolved nor outdated, and unanswered for `leads.review_stale_days` days | low, medium from 3 threads; small | A follow-up job on the pull request's branch |
| `crack` | The scout (below) | A finished `kintsugi-trace` job's ledger: one lead per row | break high, fracture medium, hairline low; the seal's size | A job that checks the evidence still holds, then fixes the cause |
| `weed` | The scout (below) | A finished `garden` job's weed card | low, small | A job that removes it with the checked diff and changes no behavior |
| `weed` | A [knowledge map](maps.md) | A part someone marked bloat on a map, while anyone does | low, small | A job that removes the part and what only it uses, without re-assessing the mark |

Every kind, these and the ones read from other features (a watcher case, an open issue, a context audit finding, slop), is one entry in `embry0/services/lead_kinds.py`: its names, what a yes starts, its verb, creature, leader and starting level. It is content, kept in the code. The leads loop, the proposals feed, the world, decision cards and briefs read it, and the dashboard gets each kind's plain names from `GET /api/v1/lead-kinds`, which answers with the game off too; the verb, creature, leader and level are the game's, carried by each quest of `GET /world`. The places that must still spell kinds out on their own (the world's art manifest, the MCP tools' descriptions, the stored kinds' database checks) are each held equal to it by a test. Which severities of a kind are asked in chat is the operator's: `leads.ask_severities`, below.

A ticket that already carries the trigger label, or that embry0 already tracks, is left to the path that has it. A source the credential cannot read (alerts without the scope, a forge other than GitHub) is skipped quietly, one log line per reason.

## The scout: cracks and weeds

The scout keeps a model busy finding work around the clock, usually one on the operator's own server. Its model is `background_models.scout` of the [settings document](configuration.md#settings-document) ([background models](providers.md#background-models)), `false` until a model is named for it, so the scout starts nothing until then; `scout: null` runs it on the section's `default`. An organization may set its own for its repositories.

Its policy is the `scout` section of the [settings document](configuration.md#settings-document), which the scout reads every tick, so a change applies within 5 minutes with no restart:

```yaml
scout:
  templates: [garden, kintsugi-trace]
  max_in_flight: 2
  revisit_hours: 24
  max_leads_per_job: 5
  job_budget_usd: 1.0
  max_jobs_per_day: null
```

Those are the defaults. Which repositories it reads is `background.scout`, off until enrolled ([which repositories](configuration.md#which-repositories-background-features-read)): set it in an organization's layer to scout every repository of it, in a repository's to scout that one or keep it out, or in the deployment's document for every repository no layer decides for. A deployment that ran the scout before keeps the repositories it read then, and what `SCOUT_REPOS` and `SCOUT_EXCLUDE` named, each written into its repository's layer at the first start of that release.

- **What it runs.** Every 5 minutes it starts read-only jobs while fewer than `scout.max_in_flight` are out: `garden` (one provable weed) and `kintsugi-trace` (a ledger of graded cracks), the templates `scout.templates` names. Each job is pinned to its repository's scout model, read at that tick: a pinned run waits for its model (a free slot, for a local one) and never moves to another. On a local model the scout spends nothing; on a hosted one each job is metered and capped like any job.
- **Where.** The registered, active repositories enrolled in `background.scout`, and each area of one that declares [areas](areas.md) (the job is told to look only under the area's paths). A repository taken out is not scouted on the next tick, and a job of it that finishes after that adds no leads. A unit, one repository or area and one template, is scouted again after `scout.revisit_hours`; never-scouted units go first, then the longest ago. A model reads the same code differently run to run, and the lead id of what it found before keeps a repeat from becoming a second lead.
- **What becomes a lead.** When a job finishes, its deliverable is read once: each ledger row a `crack`, a weed card a `weed`, at most `scout.max_leads_per_job` a job, the worst first. A failed job yields nothing.
- **What reaches chat.** By default only a crack graded `break` (high) is asked in chat; every other crack and every weed stays on the page, so a scout running all day does not fill the chat. `leads.ask_severities` says so, and can say otherwise (below).
- **Brakes.** `scout.max_in_flight` is the one that counts: never more jobs out at once. `0` starts none, while the jobs already out are still read into leads. `scout.max_jobs_per_day`, off by default, caps the jobs started in any 24 hours. Its jobs are dispatched by `embry0:scout`, which is exempt from `RATE_LIMIT_PER_AUTHOR_PER_HOUR` (that limit would hold it to a few jobs an hour), and each has a hard budget, `scout.job_budget_usd`, in case its pin is ever lost. The fleet's daily and monthly budget gates still apply.
- **Per organization.** An organization's [layer](configuration.md#settings-document) may set `scout.templates`, `scout.revisit_hours` and `scout.max_leads_per_job` for its own repositories (`PUT /api/v1/config/settings?owner=acme`). Each tick reads one snapshot of the section and each repository through its organization's layer, the deployment's document supplying the rest, so a layer's change applies on the next tick. `scout.max_in_flight`, `scout.max_jobs_per_day` and `scout.job_budget_usd` are the deployment's alone: they bound what the scout starts and spends across every organization. Which model an organization's repositories are scouted on is `background_models.scout`, read per repository from the same snapshot of the settings documents as the section, so a write that lands during a tick never mixes two versions of them.

## Backlog tickets

A ticket is offered only when it reads as ready, a 0 to 100 score from the ticket alone, and the score is at least `leads.offer_min`:

- a description (+5 short, +20 from 80 characters, +25 from 400);
- file paths (+20), quoted error text (+10), what done looks like ("expected", "steps to reproduce", a checklist; +15);
- a label such as bug, chore, refactor, docs or dependencies (+10); small by label or estimate (+10), large (-10);
- an epic, design, question, spike or blocked label (-30); a title of three words or fewer (-10).

The score and its reasons are in the proposal's summary. A model-based reading of readiness is not part of this version.

Two thresholds read that one score, each per repository through its organization's layer:

- **`leads.offer_min`** (40): a ticket scoring below it is not stored as a lead at all, so it is never offered, on the page or in chat.
- **`leads.ask_min`** (60): an offered ticket scoring below it is *held*. `GET /api/v1/proposals` leaves it out unless `include_held=true`, so a chat assistant does not ask it, and no ask is queued for it; the Owner proposals page shows it folded; the brief writer writes its brief after the others; and its decision card marks the readiness. It can still be read, accepted, declined and snoozed by id.

The labels, weights and words the score reads are in the code for now; they become an organization's own vocabulary in a later change (#168).

## What reaches chat

Every lead is offered on the page. Which ones a chat asks about is `leads.ask_severities`, per repository through its organization's layer: a kind it names is asked only at the severities it lists, and the rest of that kind are *held*, as a ticket below `ask_min` is (left out of `GET /api/v1/proposals` unless `include_held=true`, no ask queued, folded on the page, its brief written after the others). A kind it does not name is asked at every severity; an empty list asks none of that kind. Only a kind whose proposals carry a severity may be named: the watcher's cases (`low`, `medium`, `high`, `critical`, the alerts' `warning`, and `unrated` for a case that came with none) and the leads (`low` to `critical`). An open issue or a context audit finding carries none, so naming one is refused.

```yaml
leads:
  ask_severities:
    crack: [high, critical]   # the default: only a crack graded break is asked
    weed: []                  # no weed is asked (a weed is always low, so the default asks none either)
    alert: [critical]         # only critical alerts are asked
```

The default is what the code held before the key: only a crack or a weed graded high or critical is asked. The key is written entry by entry, not whole: the deployment's document overrides only the kinds it names and keeps the default for every other kind, and an organization's layer overrides only the kinds it names over the deployment's. So `{alert: [critical]}`, in the deployment's document or in an organization's layer, asks only critical alerts and still only cracks and weeds graded high or critical. It is the one key merged entry by entry instead of replaced whole ([layers](configuration.md#settings-document)); `GET /api/v1/config/settings/effective` says where each kind's entry comes from. To ask every severity of a kind in one organization, list all of them. A kind or a severity the catalogue does not know is refused with a 422 naming the key, never repeating what was written.

## A lead's life

Each lead is one row, keyed by a hash of the repository and what the finder knows it by (a ticket's key, a check's name, an alert's number, a pull request's number), so the same thing seen again is the same lead and is answered once. A finder that reads its whole source and no longer sees a lead marks it gone; it is never deleted, and it comes back open if it is seen again. A read that failed or was cut short closes nothing.

Owners answer leads like every proposal: accept, decline with a reason, or snooze. The proposal carries the lead's `severity`, `effort`, `area` (from the files or labels it names, [areas.md](areas.md)), `paths`, `labels`, `last_seen` and `source`.

## API

- `GET /api/v1/leads?repo=&kind=&state=open|gone|all&limit=` (read): the stored leads, most recently seen first, with what a person or a game-like view needs (kind, area, severity, effort, first and last seen).
- `POST /api/v1/leads/run` (`{"repo": "...", "backlog": true}`, both optional): one pass now, and what each finder found, stored, capped and closed per repository.
- Answers go through `POST /api/v1/proposals/{id}/accept|decline|snooze`.

Settings: the `leads` section of the [settings document](configuration.md#settings-reference); the `LEADS_*` variables are only read once, at the upgrade ([configuration.md](configuration.md#leads)).
