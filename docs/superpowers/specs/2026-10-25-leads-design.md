# Leads: embry0 finds its own work and asks owners for a yes

**Goal.** Work reaches embry0 only when someone hands it over: a trigger label on a ticket, a watcher draft, a context audit. A deployment that runs for months sees a few dozen issues, while its repositories hold a backlog nobody labelled, checks failing on the default branch, open security alerts, flaky tests and review comments nobody answered. Leads are what embry0 finds there by itself, offered to each repository's owners as owner proposals (spec 2026-10-24), so the only thing a person does is say yes, no or later.

The same rows are meant to feed a game-like view later, where each lead is something to send an agent at: so each carries a severity, a rough effort, an area and when it was first and last seen, not only the words a chat message needs.

**Non-goals.** No work starts without a yes. No model is asked whether a ticket is ready (a seam for it, off). No GitLab sources in this version. No change to how the tracker paths, the watcher or the context audit work.

## Words

| Word | Meaning |
|---|---|
| finder | Reads one source on one repository and returns what it sees as leads |
| lead | One thing a finder saw: a stable id, kind, repository, area, title, summary, details, url, severity, effort, what accepting starts, first and last seen |
| kind | `ticket`, `ci`, `alert`, `flaky`, `review`: one per finder |
| gone | A lead a whole read of its source no longer sees; kept, never deleted, open again if seen again |

## The finders

| Kind | Source | One lead per | Accepting starts |
|---|---|---|---|
| `ticket` | Linear tickets in a backlog or unstarted state, unassigned or assigned to the key's own account, whose project resolves to the repository (the trigger path's own resolution); the repository's open GitHub issues nobody is assigned | ticket | the tracker's own path: Linear's label-and-dispatch, GitHub's mirror-and-dispatch |
| `ci` | check runs and commit statuses on the newest commit of the default branch | failing check name | an issue in embry0 with the task, dispatched |
| `alert` | open Dependabot and code-scanning alerts | alert | the same |
| `flaky` | every run (`filter=all`) of every check on the newest commits of the default branch | check name that failed and passed on one commit | the same |
| `review` | open pull requests' review threads (GraphQL: REST cannot say whether a thread is resolved) | pull request with unanswered threads | a `pr-followup` job on its branch, as a tagged PR comment starts one |

A ticket carrying the trigger label, or one embry0 already tracks, is left to its own path. A pull request from a fork or a draft is left out: a follow-up cannot push to a fork. A source the organization's credential cannot read is skipped, one log line per standing reason.

**Readiness.** A backlog ticket is offered only when a cheap reading of its text says an agent could start on it: description length, file paths, quoted errors, what done looks like, labels that say bug or chore, size by label or estimate, minus epics, questions, spikes and one-line titles. The threshold is a setting. The reading decides what a person is asked, never what runs.

## Storage

`leads` (migration 110), one row per lead keyed by `kind:` and a hash of the repository and what the finder knows it by (a ticket key, a check name, an alert number, a pull request number). An upsert keeps `first_seen` and reopens a gone lead. A finder whose read was whole marks the open leads it no longer saw as gone; a failed, partial or skipped read closes nothing, and for tickets a tracker that could not be read keeps its leads open while the other tracker's are judged. `proposal_decisions.kind` widens to the new kinds.

## The loop

A background loop, wired like the context-audit schedule: every `LEADS_INTERVAL_HOURS` it runs the enabled finders on each repository that is registered, active, and has owners or a watched stack; the backlog intake on its own `LEADS_BACKLOG_INTERVAL_HOURS`, reading Linear once per pass for every repository. Each finder adds at most `LEADS_MAX_NEW_PER_REPO_PER_DAY` new leads per repository a day, the most severe first, and always refreshes the ones stored. `LEADS_ENABLED=false` stops it; `LEADS_FINDERS` picks the finders; `POST /leads/run` runs one pass by hand either way. Each stored lead gets its area from `.embry0/areas.yml`: its files, else its labels, else what its title names.

## The feed

Open leads join the owner proposals feed as five more kinds, routed to the area's owners first as every proposal is, with their severity, effort, paths, labels and source. Accepting one goes through `embry0/api/v1/leads.py`: a ticket through its tracker's own path; a `ci`, `alert` or `flaky` lead as an issue in embry0 whose body is the task (what was seen, where, what done is, and the rule a quick fix would break: never silence the check, the alert or the flake), labelled `embry0-lead`, `lead:<kind>` and its area, then dispatched; a `review` lead as a `pr-followup` job. Every start takes `run_with`. The MCP proposal tools describe the new kinds, so an assistant asking owners in a chat knows what each is.

## Tests

The readiness reading and the ticket filters on both trackers; each finder against a fake forge (failing and passing checks, statuses, flaky runs, both alert sources and the quiet skip, review threads by state and age, forks and drafts); the loop's storing, capping, closing, never closing on a failed read, keeping a tracker's tickets it could not read, and the backlog's own interval; the repository on a real database; the feed with leads, and each kind's yes.

## Open

- Alerts need the credential to read Dependabot and code-scanning alerts (`security_events` on a classic token, the repository's alert permissions on an app); without them the finder offers nothing, quietly.
- Review threads need GraphQL with the same credential; GitHub Enterprise hosts need their GraphQL path.
- Linear tickets assigned to the key's own account count as unassigned only when that account is the one embry0's key acts as.
