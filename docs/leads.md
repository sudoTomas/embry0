# Leads: work embry0 finds by itself

Until leads, work reached embry0 in three ways: a person put the trigger label on a ticket, the watcher drafted one from production logs, or the context audit read the files that steer coding agents. Everything else waited for someone to think of embry0. Leads are the work embry0 finds on its own and offers to a repository's owners as [owner proposals](api.md#owner-proposals): a person only says yes. The design is in [the spec](superpowers/specs/2026-10-25-leads-design.md).

The backlog intake and the lead client work only with Linear and GitHub today; other forges and trackers are tracked in #181.

## Where it looks

A repository is read when it is registered and active and someone answers for it: its preferences name owners, or it is a watched stack. Each finder reads it with its organization's credential and adds at most `LEADS_MAX_NEW_PER_REPO_PER_DAY` new leads a day, the most severe first.

| Kind | Finder | What it reads | Severity, effort | Accepting starts |
|---|---|---|---|---|
| `ticket` | Backlog intake, every `LEADS_BACKLOG_INTERVAL_HOURS` | Linear tickets nobody started (unassigned, or assigned to the account embry0's key acts as) whose project resolves to the repository, and the repository's open GitHub issues nobody is assigned | From Linear priority or the labels; from size labels or the estimate | Its tracker's own path: on Linear the trigger label goes on and the ticket is dispatched, on GitHub the issue is brought into embry0 and dispatched |
| `ci` | Failing CI | Check runs and commit statuses on the newest commit of the default branch | high, medium | A fix job for that check |
| `alert` | Alerts | Open Dependabot alerts and open code-scanning alerts | The alert's, small for a dependency with a fixed version | An upgrade or fix job |
| `flaky` | Flaky checks | Every run of every check on the newest `LEADS_FLAKY_COMMITS` commits of the default branch | medium, high from 3 commits; medium | A job that finds and fixes the cause |
| `review` | Unanswered reviews | Open, non-draft pull requests from the repository itself with review threads a reviewer started, neither resolved nor outdated, and unanswered for `LEADS_REVIEW_STALE_DAYS` | low, medium from 3 threads; small | A follow-up job on the pull request's branch |
| `crack` | The scout (below) | A finished `kintsugi-trace` job's ledger: one lead per row | break high, fracture medium, hairline low; the seal's size | A job that checks the evidence still holds, then fixes the cause |
| `weed` | The scout (below) | A finished `garden` job's weed card | low, small | A job that removes it with the checked diff and changes no behavior |
| `weed` | A [knowledge map](maps.md) | A part someone marked bloat on a map, while anyone does | low, small | A job that removes the part and what only it uses, without re-assessing the mark |

Every kind, these and the ones read from other features (a watcher case, an open issue, a context audit finding, slop), is one entry in `embry0/services/lead_kinds.py`: its names, what a yes starts, its verb, creature, leader and starting level, and which severities are asked in chat. The leads loop, the proposals feed, the world, decision cards and briefs read it, and the dashboard gets it from `GET /world`; the world's art manifest names each kind's creature on its own, and a test holds the two equal.

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

Those are the defaults. `SCOUT_REPOS` and `SCOUT_EXCLUDE` stay environment variables, read at startup, until the one repository scope rule (#165) moves them.

- **What it runs.** Every 5 minutes it starts read-only jobs while fewer than `scout.max_in_flight` are out: `garden` (one provable weed) and `kintsugi-trace` (a ledger of graded cracks), the templates `scout.templates` names. Each job is pinned to its repository's scout model, read at that tick: a pinned run waits for its model (a free slot, for a local one) and never moves to another. On a local model the scout spends nothing; on a hosted one each job is metered and capped like any job.
- **Where.** The repositories the world map shows, plus `SCOUT_REPOS` and minus `SCOUT_EXCLUDE`, and each area of one that declares [areas](areas.md) (the job is told to look only under the area's paths). A unit, one repository or area and one template, is scouted again after `scout.revisit_hours`; never-scouted units go first, then the longest ago. A model reads the same code differently run to run, and the lead id of what it found before keeps a repeat from becoming a second lead.
- **What becomes a lead.** When a job finishes, its deliverable is read once: each ledger row a `crack`, a weed card a `weed`, at most `scout.max_leads_per_job` a job, the worst first. A failed job yields nothing.
- **What reaches chat.** Only a crack graded `break` (high) is asked in chat; every other crack and every weed stays on the page, so a scout running all day does not fill the chat.
- **Brakes.** `scout.max_in_flight` is the one that counts: never more jobs out at once. `0` starts none, while the jobs already out are still read into leads. `scout.max_jobs_per_day`, off by default, caps the jobs started in any 24 hours. Its jobs are dispatched by `embry0:scout`, which is exempt from `RATE_LIMIT_PER_AUTHOR_PER_HOUR` (that limit would hold it to a few jobs an hour), and each has a hard budget, `scout.job_budget_usd`, in case its pin is ever lost. The fleet's daily and monthly budget gates still apply.

## Backlog tickets

A ticket is offered only when it reads as ready, a 0 to 100 score from the ticket alone, and the score is at least `LEADS_TICKET_MIN_SCORE`:

- a description (+5 short, +20 from 80 characters, +25 from 400);
- file paths (+20), quoted error text (+10), what done looks like ("expected", "steps to reproduce", a checklist; +15);
- a label such as bug, chore, refactor, docs or dependencies (+10); small by label or estimate (+10), large (-10);
- an epic, design, question, spike or blocked label (-30); a title of three words or fewer (-10).

The score and its reasons are in the proposal's summary. A model-based reading of readiness is not part of this version.

## A lead's life

Each lead is one row, keyed by a hash of the repository and what the finder knows it by (a ticket's key, a check's name, an alert's number, a pull request's number), so the same thing seen again is the same lead and is answered once. A finder that reads its whole source and no longer sees a lead marks it gone; it is never deleted, and it comes back open if it is seen again. A read that failed or was cut short closes nothing.

Owners answer leads like every proposal: accept, decline with a reason, or snooze. The proposal carries the lead's `severity`, `effort`, `area` (from the files or labels it names, [areas.md](areas.md)), `paths`, `labels`, `last_seen` and `source`.

## API

- `GET /api/v1/leads?repo=&kind=&state=open|gone|all&limit=` (read): the stored leads, most recently seen first, with what a person or a game-like view needs (kind, area, severity, effort, first and last seen).
- `POST /api/v1/leads/run` (`{"repo": "...", "backlog": true}`, both optional): one pass now, and what each finder found, stored, capped and closed per repository.
- Answers go through `POST /api/v1/proposals/{id}/accept|decline|snooze`.

Settings: the `LEADS_*` variables in [configuration.md](configuration.md).
