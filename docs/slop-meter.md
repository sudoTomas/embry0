# The Slop-O-Meter

The Slop-O-Meter scores how much of a repository, and of each of its areas, landed beyond human attention. Week by week it compares the attention the code demands (its changed lines, weighted by kind of file and dampened by the codebase's size) with the attention it received (people's commits, co-authorship, comments and reviews), and calls the excess slop. The score is the history's slop over its cost, from 0 to 1.

It is a clean-room reimplementation of [Slop-O-Meter](https://github.com/slop-o-meter/slop-o-meter)'s published slop score algorithm, written from its description alone. Every choice the description leaves open, and the two places embry0 departs from it, are in [the design](superpowers/specs/2026-10-26-slop-o-meter-design.md).

## Levels

| Score | Level |
|---|---|
| below 0.2 | Buttered Noodles |
| below 0.4 | Spaghetti Bolognese |
| below 0.6 | Lasagna |
| below 0.8 | Sloppy Joe |
| 0.8 and above | Just The Slop |

A score is **provisional** while some of its largest commits (the outliers) are not judged yet. A model judges a few a tick, on `background_models.slop_judge` of the settings document, else its default ([background models](providers.md#background-models)); with no model, they stay unjudged and count as not slop. A judgment on a hosted model is counted against the budget caps, and refused past them.

## Which repositories

The meter reads the registered, active repositories enrolled in it: `background.slop_meter` of the settings document, `false` until an operator sets it in an organization's layer (every repository of it), a repository's layer, or the deployment's document ([which repositories](configuration.md#which-repositories-background-features-read)). A job run on a repository enrolls nothing. A deployment that ran the meter before keeps the repositories it read then (those the world map showed: a job in the 90 days before the upgrade, owners or a watched stack, plus `SLOP_METER_REPOS` and minus `SLOP_METER_EXCLUDE`), each written into its repository's layer at the first start of that release.

A repository that leaves the meter's scope, opted out or unregistered, loses its mirror and everything the meter kept of it `background.prune_after_days` (a week) later, unless it comes back first; one made inactive is not read and keeps what was read ([what it keeps](#what-it-keeps)).

The loop is on by default and reads what is enrolled. `SLOP_METER_INTERVAL_SECONDS=0` turns the reading off.

## What it reads, and how much

- **History:** a bare mirror of each repository, cloned and fetched with its organization's forge credential (passed to git through the environment, never in a URL or the mirror's config). Each tick reads at most `SLOP_METER_COMMITS_PER_TICK` commits of a history, oldest first, so a large history is read over several ticks; the loop comes back after `SLOP_METER_CATCHUP_SECONDS` while one is behind. Every git call has a timeout (`SLOP_METER_GIT_TIMEOUT_SECONDS`).
- **Forge events** (GitHub only): issue and pull request comments, review comments and reviews, from a cursor per listing. These are API calls, bounded per tick (`SLOP_METER_API_CALLS_PER_TICK`) and stopped once the credential's remaining rate limit drops below `SLOP_METER_RATE_RESERVE`, because jobs use the same credential. Git fetches are not API calls.
- **Bots and AI agents** (known bot names and addresses, `[bot]` accounts, `noreply@` addresses, coding agents' addresses) add lines but no attention.

## What it keeps

- **On disk:** one bare mirror per repository in scope, under `SLOP_METER_DATA_PATH` (`/data/embry0/slop-meter`, inside the orchestrator's data volume), one directory per segment of its name, lowercased: `acme/widgets` at `acme/widgets.git`, a GitLab project in a subgroup, `group/sub/project`, at `group/sub/project.git` (at most 21 segments, GitLab's deepest). git runs in the mirror's directory as the meter opened it, never through a link ([the guard](ops/background-data.md)). A full mirror is the repository's whole history, so size the volume for it.
- **In the database:** each repository's commits as read (author name and email, date, per-file line counts, co-author trailers), its forge events (login, kind, time), a map from forge logins to commit emails, the model's judgments of outlier commits, and the weekly series and current score per repository and area (the `slop_meter_*` tables, migrations 121 and 122). Contributor names, emails and logins are personal data: they are kept while the repository is in the meter's scope, replaced when its history is read again from the start, and removed with the rest of its rows and its mirror `background.prune_after_days` after it leaves, on the first tick past that (with the schedule off, at a start), unless it comes back first. A login goes once no remaining event refers to it. The removal never deletes anything outside `SLOP_METER_DATA_PATH` nor follows a link out of it ([what the background features keep](ops/background-data.md)).

## Where it shows

- `GET /api/v1/repos/{repo}/-/slop-meter` and the repository page: the score, the level and a week-by-week line of cost, capacity and slop ([api.md](api.md)).
- The Slop Dungeons in the [world](world.md) carry each area's reading; an area at Spaghetti Bolognese or worse has a dungeon even with no open slop finding.
- Triage is told the job's area's (else the repository's) level, and the Slopmaster how much of the area landed beyond human attention.

## Not covered

GitLab repositories get commit signals only, no events. Regex-based or JavaScript-configured excludes are not read.
