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

Those are the defaults, except `enabled`, which is `false`; the words a backlog ticket's readiness is read in are keys of the section too ([backlog tickets](#backlog-tickets)). The loop always runs: every 5 minutes it reads the section, does nothing while `enabled` is false, and otherwise runs a pass `interval_hours` after the last one ended (it wakes for that even between its 5-minute looks, so passes are spaced exactly as before the section), so turning leads on, off, or changing any key needs no restart. `POST /api/v1/leads/run` runs a pass by hand whether leads are on or not, and the leads already stored stay until answered.

**Upgrading.** Leads used to be on by default (`LEADS_ENABLED=true`); they are off until `leads.enabled` is set. `LEADS_ENABLED` and the other `LEADS_*` variables are no longer read.

**Per organization.** An organization's [layer](configuration.md#settings-document) may set `finders`, `max_new_per_repo_per_day`, `review_stale_days`, `flaky_commits`, `offer_min`, `ask_min` and `ask_severities` for its repositories: each repository's pass, how its tickets are offered and asked, and which of its leads reach chat follow its organization's values. Whether the loop runs and how often stay the deployment's.

## Where it looks

A repository is read when it is registered and active and someone answers for it: its preferences name owners, or it is a watched stack. An organization's or a repository's [layer](configuration.md#which-repositories-background-features-read) can take it out (`background: {leads: false}`), and a repository's own layer bring it back; the next pass follows. `POST /api/v1/leads/run` with a `repo` reads that repository whatever its layer says. Each finder reads it with its organization's credential and adds at most `leads.max_new_per_repo_per_day` new leads a day, the most severe first.

| Kind | Finder | What it reads | Severity, effort | Accepting starts |
|---|---|---|---|---|
| `ticket` | Backlog intake, every `leads.backlog_interval_hours` | Linear tickets nobody started (unassigned, or assigned to the account embry0's key acts as) whose project resolves to the repository, and the repository's open GitHub issues nobody is assigned | From Linear priority or the labels; from size labels or the estimate | Its tracker's own path: on Linear the trigger label goes on and the ticket is dispatched, on GitHub the issue is brought into embry0 and dispatched |
| `ci` | Failing CI | Check runs and commit statuses on the newest commit of the default branch | high, medium | A fix job for that check |
| `alert` | Alerts | Open Dependabot alerts and open code-scanning alerts | The alert's, small for a dependency with a fixed version | An upgrade or fix job |
| `flaky` | Flaky checks | Every run of every check on the newest `leads.flaky_commits` commits of the default branch | medium, high from 3 commits; medium | A job that finds and fixes the cause |
| `review` | Unanswered reviews | Open, non-draft pull requests from the repository itself, not from its default branch, with review threads a reviewer started, neither resolved nor outdated, and unanswered for `leads.review_stale_days` days | low, medium from 3 threads; small | A follow-up job on the pull request's branch |
| `crack` | The scout (below) | A finished `kintsugi-trace` job's ledger: one lead per row | break high, fracture medium, hairline low; the seal's size | A job that checks the evidence still holds, then fixes the cause |
| `weed` | The scout (below) | A finished `garden` job's weed card | low, small | A job that removes it with the checked diff and changes no behavior |
| `weed` | A [knowledge map](maps.md) | A part someone marked bloat on a map, while anyone does | low, small | A job that removes the part and what only it uses, without re-assessing the mark |

A yes to any kind but `ticket` and `review` files the job's task as an issue in embry0 and runs it. The issue is labelled `leads.labels` (`embry0-lead` by default, an organization's layer may name its own) and `lead:<kind>`, with `area:<slug>` when the lead has an area. None of them may be one of the deployment's trigger labels: a write naming one is refused, and a label that has become one since is left off the issue, logged as `issue_label_skipped`.

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
- **Where.** The registered, active repositories enrolled in `background.scout`, and each of its [areas](areas.md), as embry0 keeps them (the job is told to look only under the area's paths). A repository taken out is not scouted on the next tick, and a job of it that finishes after that adds no leads. A unit, one repository or area and one template, is scouted again after `scout.revisit_hours`; never-scouted units go first, then the longest ago. A model reads the same code differently run to run, and the lead id of what it found before keeps a repeat from becoming a second lead.
- **What becomes a lead.** When a job finishes, its deliverable is read once: each ledger row a `crack`, a weed card a `weed`, at most `scout.max_leads_per_job` a job, the worst first. A failed job yields nothing.
- **What reaches chat.** By default only a crack graded `break` (high) is asked in chat; every other crack and every weed stays on the page, so a scout running all day does not fill the chat. `leads.ask_severities` says so, and can say otherwise (below).
- **Brakes.** `scout.max_in_flight` is the one that counts: never more jobs out at once. `0` starts none, while the jobs already out are still read into leads. `scout.max_jobs_per_day`, off by default, caps the jobs started in any 24 hours. Its jobs are dispatched by `embry0:scout`, which is exempt from `RATE_LIMIT_PER_AUTHOR_PER_HOUR` (that limit would hold it to a few jobs an hour), and each has a hard budget, `scout.job_budget_usd`, in case its pin is ever lost. The fleet's daily and monthly budget gates still apply.
- **Per organization.** An organization's [layer](configuration.md#settings-document) may set `scout.templates`, `scout.revisit_hours` and `scout.max_leads_per_job` for its own repositories (`PUT /api/v1/config/settings?owner=acme`). Each tick reads one snapshot of the section and each repository through its organization's layer, the deployment's document supplying the rest, so a layer's change applies on the next tick. `scout.max_in_flight`, `scout.max_jobs_per_day` and `scout.job_budget_usd` are the deployment's alone: they bound what the scout starts and spends across every organization. Which model an organization's repositories are scouted on is `background_models.scout`, read per repository from the same snapshot of the settings documents as the section, so a write that lands during a tick never mixes two versions of them.

## Backlog tickets

A ticket is offered only when it reads as ready, a 0 to 100 score from the ticket alone, and the score is at least `leads.offer_min`. The score adds the points of each signal the ticket shows and is kept within 0 to 100; its reasons are in the proposal's summary. A model-based reading of readiness is not part of this version. By default, before any document says otherwise:

<!-- readiness-table:start -->

| Signal | Points | When |
| --- | --- | --- |
| `description_short` | +5 | a description shorter than 80 characters, not blank |
| `description` | +20 | a description of 80 to 399 characters |
| `description_full` | +25 | a description of 400 characters or more |
| `paths` | +20 | the title or the description names a file path |
| `error` | +10 | the title or the description quotes error text (a traceback, an exception, an HTTP 5xx) |
| `done` | +15 | the description holds a done phrase |
| `ready_label` | +10 | a `ready` label |
| `small` | +10 | a `small` label, or an estimate of at most 2 |
| `large` | -10 | a `large` label, or an estimate of at least 5 |
| `not_ready` | -30 | a `not_ready` label, or else an epic by its title |
| `short_title` | -10 | a title of 3 words or fewer |

| Role | Labels | A ticket with one |
| --- | --- | --- |
| `ready` | `bug`, `chore`, `dependencies`, `docs`, `documentation`, `maintenance`, `refactor`, `tech debt`, `tech-debt`, `test`, `tests` | reads as ready to start |
| `small` | `easy`, `good first issue`, `good-first-issue`, `s`, `size/s`, `size/xs`, `size:s`, `size:xs`, `small`, `xs` | reads as small |
| `large` | `l`, `large`, `size/l`, `size/xl`, `size:l`, `size:xl`, `xl` | reads as large |
| `not_ready` | `blocked`, `design`, `discussion`, `epic`, `idea`, `needs design`, `needs discussion`, `needs-design`, `question`, `research`, `rfc`, `spike`, `wontfix` | reads as not ready |
| `urgent` | `critical`, `p0`, `security`, `sev1`, `urgent` | makes the lead critical when the tracker gives no priority |
| `high` | `high`, `p1`, `regression`, `sev2` | makes the lead high when the tracker gives no priority |

Done phrases (`done_phrases`): `acceptance criteria`, `done when`, `definition of done`, `expected behavior`, `expected behaviors`, `expected behaviour`, `expected behaviours`, `expected result`, `expected results`, `steps to reproduce`, `should`, `- [ ]`.

Epic title words (`epic_title_words`): `epic`.

<!-- readiness-table:end -->

Those defaults are what the code held before they became settings, so a deployment that writes none scores every ticket as it did. Organizations label and write their tickets their own way, so every word the score reads is a key of the `leads` section an organization's layer may set, per repository through that layer like the thresholds below:

```yaml
leads:
  readiness_labels:            # by role; a role named here replaces that role's labels
    ready: [kind/bug, kind/chore, type::bug]
    not_ready: [kind/epic, needs-triage]
  readiness_weights:           # by signal; a signal named here replaces its points
    done: 20
    short_title: 0
  done_phrases: [acceptance criteria, definition of done, akzeptanzkriterien]
  epic_title_words: [epic, initiative]
  small_estimate_max: 3        # estimates in the tracker's own scale
  large_estimate_min: 8
```

- **Labels** (`readiness_labels`) match a ticket's labels without regard to case, surrounding spaces, how an accent is written (one character, or a letter and a combining mark) or an emoji's presentation selector. A ticket can hold labels of several roles: each adds its signal.
- **Done phrases** (`done_phrases`) are matched as written, in any case and any language, as whole words: where a phrase starts or ends with a letter or a digit, the text beside it must not be one. An underscore is punctuation; an accent or an Indic vowel sign belongs to its letter, and a zero-width non-joiner or joiner to its word (Persian writes `می‌خواهم` with one), so `done when` reads in "Done when:" and in "\_Done when\_", not in "undone whenever", while `- [ ]` reads anywhere. In a script written without spaces (Chinese, Japanese, Thai) a phrase reads only where a space or punctuation sets it off, since a run of such letters is one word. No phrase can make reading a ticket slow: a phrase is never made into a pattern.
- **Epic title words** (`epic_title_words`) make an unlabelled ticket an epic when its title starts with one in brackets (`[Epic] ...`) or followed by a colon or a dash, with an optional scope in parentheses (`Epic: ...`, `epic(billing): ...`). It takes the `not_ready` points, once, as a `not_ready` label would.
- **Estimates** (`small_estimate_max`, `large_estimate_min`) read a tracker's estimate in its own scale; a ticket that reads as both small and large takes both signals, and the effort of a large one.
- **File paths and error text** are read by the code, not by words an organization sets.

`readiness_labels` and `readiness_weights` are written entry by entry, as `ask_severities` is: the deployment's document replaces only the roles and signals it names over the defaults, and an organization's layer only those it names over the deployment's, so an organization that names its own `ready` labels keeps every other role's. The lists of phrases and title words, and the estimates, are replaced whole. A value the section does not take is refused with a 422 naming each problem, never repeating what was written.

A change is read at the next backlog intake (`leads.backlog_interval_hours`, 24 hours by default) or at once by `POST /api/v1/leads/run` with `"backlog": true`. That intake scores every ticket again: a stored lead is updated, and one that now scores below `offer_min` is closed.

Two thresholds read that one score, each per repository through its organization's layer:

- **`leads.offer_min`** (40): a ticket scoring below it is not stored as a lead at all, so it is never offered, on the page or in chat.
- **`leads.ask_min`** (60): an offered ticket scoring below it is *held*. `GET /api/v1/proposals` leaves it out unless `include_held=true`, so a chat assistant does not ask it, and no ask is queued for it; the Owner proposals page shows it folded; the brief writer writes its brief after the others; and its decision card marks the readiness. It can still be read, accepted, declined and snoozed by id.

## What reaches chat

Every lead is offered on the page. Which ones a chat asks about is `leads.ask_severities`, per repository through its organization's layer: a kind it names is asked only at the severities it lists, and the rest of that kind are *held*, as a ticket below `ask_min` is (left out of `GET /api/v1/proposals` unless `include_held=true`, no ask queued, folded on the page, its brief written after the others). A kind it does not name is asked at every severity; an empty list asks none of that kind. Only a kind whose proposals carry a severity may be named: the watcher's cases (`low`, `medium`, `high`, `critical`, the alerts' `warning`, and `unrated` for a case that came with none) and the leads (`low` to `critical`). An open issue or a context audit finding carries none, so naming one is refused.

```yaml
leads:
  ask_severities:
    crack: [high, critical]   # the default: only a crack graded break is asked
    weed: []                  # no weed is asked (a weed is always low, so the default asks none either)
    alert: [critical]         # only critical alerts are asked
```

The default is what the code held before the key: only a crack or a weed graded high or critical is asked. The key is written entry by entry, not whole: the deployment's document overrides only the kinds it names and keeps the default for every other kind, and an organization's layer overrides only the kinds it names over the deployment's. So `{alert: [critical]}`, in the deployment's document or in an organization's layer, asks only critical alerts and still only cracks and weeds graded high or critical. It is merged entry by entry instead of replaced whole, as the readiness labels and weights are ([layers](configuration.md#settings-document)); `GET /api/v1/config/settings/effective` says where each kind's entry comes from. To ask every severity of a kind in one organization, list all of them. A kind or a severity the catalogue does not know is refused with a 422 naming the key, never repeating what was written.

## A lead's life

Each lead is one row, keyed by a hash of the repository and what the finder knows it by (a ticket's key, a check's name, an alert's number, a pull request's number), so the same thing seen again is the same lead and is answered once. A finder that reads its whole source and no longer sees a lead marks it gone; it is never deleted, and it comes back open if it is seen again. A read that failed or was cut short closes nothing.

Owners answer leads like every proposal: accept, decline with a reason, or snooze. The proposal carries the lead's `severity`, `effort`, `area` (from the files or labels it names, [areas.md](areas.md)), `paths`, `labels`, `last_seen` and `source`.

## API

- `GET /api/v1/leads?repo=&kind=&state=open|gone|all&limit=` (read): the stored leads, most recently seen first, with what a person or a game-like view needs (kind, area, severity, effort, first and last seen).
- `POST /api/v1/leads/run` (`{"repo": "...", "backlog": true}`, both optional): one pass now, and what each finder found, stored, capped and closed per repository.
- Answers go through `POST /api/v1/proposals/{id}/accept|decline|snooze`.

Settings: the `leads` section of the [settings document](configuration.md#settings-reference); the `LEADS_*` variables are only read once, at the upgrade ([configuration.md](configuration.md#leads)).
