# The Slop-O-Meter

The Slop-O-Meter scores how much of a repository, and of each of its areas, landed beyond human attention. Week by week it compares the attention the code demands (its changed lines, weighted by kind of file and dampened by the codebase's size) with the attention it received (people's commits, co-authorship, comments and reviews), and calls the excess slop. The score is the history's slop over its cost, from 0 to 1.

It is a clean-room reimplementation of [Slop-O-Meter](https://github.com/slop-o-meter/slop-o-meter)'s published slop score algorithm, written from its description alone. Every choice the description leaves open, and the two places embry0 departs from it, are in [the design](superpowers/specs/2026-10-26-slop-o-meter-design.md). Every number the algorithm names is a key of the settings document's `slop_meter` section ([settings](#settings)), and each default is the algorithm's value, so a deployment that writes nothing scores exactly as the algorithm does, with embry0's two departures.

## What it measures

- **Attention cost.** Each changed line weighs what its file's glob says (`weights`: core logic 1, SQL 0.8, user interface code 0.5, styling 0.3; data, documents, binaries and anything no glob matches 0), a share of that for tests and examples (`test_weight`, a half), and nothing in a vendored directory (`excluded_dirs`), in a generated file (minified, bundled, source-map and declaration files weigh 0 in `weights`), in a path a linter or formatter of the repository ignores, or in an outlier file smaller than its fair share. A week's net weighted additions are dampened by the codebase's size: `min(1, size / bootstrap_lines) + complexity_weight × log2(1 + size / 100,000)`.
- **Capacity.** Each human commit implies `commit_hours` of attention before it, plus `subcommit_hours` for each commit a squash merge folded in, times `ai_coauthor_share` with an AI co-author; each human co-author `coauthor_shares` of that; each comment, review comment and review on the forge `event_hours`. A contributor's spans are merged into sessions, weighed by how core they are (nothing below `core_min_commits` human commits, in full from `core_full_commits`, up to a quarter more for a large share), capped on a concave curve at `weekly_hours_cap` a week, and turned into lines at `lines_per_hour`.
- **Slop and the score.** A week's slop is its cost beyond its capacity; the score is the history's slop over its cost.
- **Outlier commits.** The largest commits (at least `outlier_min_lines` net weighted lines, `outliers_per_year` per year of the project's age, at least one) are asked of a model, one by one: slop, or a code drop, an import or a move that is not. A commit judged not slop keeps its lines in the cost and the codebase's size, but not in the slop. The first two commits are never slop.
- **Bots and AI agents** add lines but no attention: known bot names and addresses, `[bot]` accounts, `noreply@` addresses, known coding agents' addresses, embry0's own commit identities, and the owner's own `bots` and `ai_agents`. An AI co-author makes its commit an AI-assisted one.

### Levels

A score reads as one of five levels. Their bounds and names are `level_bounds` and `level_names`; by default:

| Score | Level |
|---|---|
| below 0.2 | 1, Buttered Noodles |
| below 0.4 | 2, Spaghetti Bolognese |
| below 0.6 | 3, Lasagna |
| below 0.8 | 4, Sloppy Joe |
| 0.8 and above | 5, Just The Slop |

A score is **provisional** while some of its outlier commits are not judged yet. A model judges a few a tick, on `background_models.slop_judge` of the settings document, else its default ([background models](providers.md#background-models)); with no model, they stay unjudged and count as not slop. A judgment on a hosted model is counted against the budget caps, and refused past them.

### embry0's departures from the published algorithm

- **Small repositories** (`small_repository_in_full`, on by default). When no author has reached `core_min_commits` human commits, everyone's time counts in full: read literally, the core factor makes every author of a small repository a drive-by worth nothing, so a six-commit, 778-line repository read as Just The Slop. A fast big dump still reads high. `small_repository_in_full: false` scores such a repository as the algorithm is written.
- **At least one outlier.** A history that spans no time (one commit) still has one outlier judged, so its first commit, setup the algorithm always excuses, is excused.

## Settings

The `slop_meter` section of the settings document ([settings document](configuration.md#settings-document); every key, its bounds and the variable it was imported from are in the [reference](configuration.md#slop_meter)). An operator writes only what differs:

```yaml
slop_meter:
  interval_hours: 12          # score twice a day
  weights:                    # merged entry by entry over the defaults
    "*.sql": 1.0              # SQL is core logic here
    "db/migrations/**": 0     # generated migrations weigh nothing
  lines_per_hour: 30
  level_names: [Clean, Fine, Mixed, Sloppy, Slop]
  bots: [release-bot, ci@acme.example]
```

| Key | Default | What it says |
|---|---|---|
| `enabled` | `true` | Whether the meter reads and scores at all. Off, it still removes what it kept of a repository that left its scope |
| `interval_hours` | `24` | Hours between two runs over every enrolled repository, at least a quarter hour |
| `api_calls_per_tick` | `200` | Forge API calls one run makes with one credential |
| `rate_reserve` | `2000` | The rate limit a credential keeps for jobs: below it, the run calls the forge no more with it |
| `weights` | the published file types | What a changed line weighs, by glob, from 0 to 10 |
| `excluded_dirs` | `vendor`, `node_modules`, `third_party`, `third-party`, `external`, `deps`, `bower_components` | Directories whose files weigh nothing |
| `test_dirs` | `__tests__`, `tests`, `test`, `spec`, `specs`, `examples`, `example`, `demos`, `demo`, `samples`, `sample`, `fixtures`, `testdata` | Directories of tests and examples |
| `test_files` | `*_test.go`, `*_test.py`, `*_test.rb`, `*_test.ex`, `*_test.exs`, `*_spec.rb`, `test_?*.py` | Globs of test and example files |
| `test_marks` | `test`, `spec`, `stories`, `story` | Words before a file's extension that mark it a test: `a.test.ts` |
| `test_weight` | `0.5` | A test's lines weigh this share of their glob's weight |
| `bootstrap_lines`, `complexity_weight` | `5000`, `0.05` | The size dampening |
| `commit_hours`, `subcommit_hours`, `event_hours` | `1`, `1`, `1` | Hours of attention a commit, a folded commit and a forge event imply |
| `coauthor_shares`, `ai_coauthor_share` | `[0.5, 0.25, 0.125]`, `0.8` | A co-author's share of a commit's hours, in trailer order, and an AI-assisted commit's share |
| `lines_per_hour` | `40` | Weighted lines an hour of attention covers |
| `weekly_hours_cap` | `80` | The most hours a person's week counts |
| `core_min_commits`, `core_full_commits` | `10`, `60` | The core team's ramp |
| `small_repository_in_full` | `true` | embry0's small-repository departure |
| `outlier_min_lines`, `outliers_per_year` | `2000`, `4` | Which commits are outliers |
| `judge_prompt` | what the model is told slop is | The judge's guidance; the answer's form is added after it |
| `level_bounds`, `level_names` | `[0.2, 0.4, 0.6, 0.8]`, the five names above | The levels |
| `bots` | none | The owner's own bots, by commit name, address or forge login |
| `ai_agents` | none | AI coding agents beyond the known ones, by the start of their commit address (`aider@`) |

**Globs.** A glob with no `/` matches a file's name in any directory (`*.py`); one with a `/` matches its path from the repository's root (`db/migrations/*.sql`, `generated/**`). `*` matches within one name, `?` one character, `**` any number of directories. Matching ignores case, so globs are written in lowercase, and quoted in YAML, which reads a bare `*` as an alias. Where several globs of `weights` match a file, the longest wins (`*.min.js` over `*.js`), and between two of one length the first in alphabetical order. A file no glob matches weighs nothing.

**Layers.** An organization's layer may set every key but `enabled` and `interval_hours`, for its repositories: what counts as code, how fast its team attends to it, where its levels fall, its judge's guidance, its own bots and the forge budget of the credential it reads with. A repository's layer may set what counts as code in its own layout: `weights`, `excluded_dirs`, `test_dirs`, `test_files` and `test_marks`. `weights` is merged glob by glob, through every layer: a document adds or changes the globs it names and keeps every other, so a glob set to `0` stops counting. Every other key a layer sets replaces the inherited value whole. Where repositories of organizations that set different forge budgets are read with one credential, the fewest calls and the largest reserve apply, since the rate limit is the credential's.

**Before this section.** `SLOP_METER_INTERVAL_SECONDS` (`0` turned the reading off), `SLOP_METER_API_CALLS_PER_TICK` and `SLOP_METER_RATE_RESERVE` are imported once, at the first start of the release that added the section, as `interval_hours` (or `enabled: false`), `api_calls_per_tick` and `rate_reserve`, and ignored after that. Each organization's `slop_meter_bots` was copied into its layer as `bots` ([api.md](api.md#organizations)). Everything else was a constant in the meter's code, and its default is that constant.

## How a change rescores

Each score row records `config_version`: a digest of every key its repository's score is computed from, as that repository reads them through its organization's layer and its own. The meter looks at the section every five minutes. When a repository it read last reads another version (an operator changed a weight, a level, the pace, the bots), the meter runs at once, without waiting for `interval_hours`, rescores it and records the new version; undoing the change rescores it back. `GET /api/v1/repos/{repo}/-/slop-meter` shows the version of the repository's score in `reading.config_version` ([api.md](api.md)).

Four keys are not what a score is computed from, and change no version: `enabled`, `interval_hours`, `api_calls_per_tick` and `rate_reserve`. Neither does `judge_prompt`: a judgment is kept per commit and never asked again, so new guidance applies to the outlier commits judged after it, and a change to the weights that makes another commit an outlier has that one judged. A repository still reading its history is scored once it is caught up.

## Which repositories

The meter reads the registered, active repositories enrolled in it: `background.slop_meter` of the settings document, `false` until an operator sets it in an organization's layer (every repository of it), a repository's layer, or the deployment's document ([which repositories](configuration.md#which-repositories-background-features-read)). A job run on a repository enrolls nothing. A deployment that ran the meter before keeps the repositories it read then (those the world map showed: a job in the 90 days before the upgrade, owners or a watched stack, plus `SLOP_METER_REPOS` and minus `SLOP_METER_EXCLUDE`), each written into its repository's layer at the first start of that release.

A repository that leaves the meter's scope, opted out or unregistered, loses its mirror and everything the meter kept of it `background.prune_after_days` (a week) later, unless it comes back first; one made inactive is not read and keeps what was read ([what it keeps](#what-it-keeps)).

The loop is on by default and reads what is enrolled. `slop_meter.enabled: false` turns the reading off; turned on again, the meter reads within five minutes.

## What it reads, and how much

- **History:** a bare mirror of each repository, cloned and fetched with its organization's forge credential (passed to git through the environment, never in a URL or the mirror's config). Each tick reads at most `SLOP_METER_COMMITS_PER_TICK` commits of a history, oldest first, so a large history is read over several ticks; the loop comes back after `SLOP_METER_CATCHUP_SECONDS` while one is behind. Every git call has a timeout (`SLOP_METER_GIT_TIMEOUT_SECONDS`). These are the process's own limits, and stay variables.
- **Forge events** (GitHub only): issue and pull request comments, review comments and reviews, from a cursor per listing. These are API calls, bounded per tick and credential (`slop_meter.api_calls_per_tick`) and stopped once the credential's remaining rate limit drops below `slop_meter.rate_reserve`, because jobs use the same credential. Git fetches are not API calls.

## What it keeps

- **On disk:** one bare mirror per repository in scope, under `SLOP_METER_DATA_PATH` (`/data/embry0/slop-meter`, inside the orchestrator's data volume), one directory per segment of its name, lowercased: `acme/widgets` at `acme/widgets.git`, a GitLab project in a subgroup, `group/sub/project`, at `group/sub/project.git` (at most 21 segments, GitLab's deepest). git is started in the mirror's directory as the meter opened it, so a link swapped in before git starts is never followed; what git resolves for itself once it is running is git's, and the meter checks the way again after each clone and fetch ([the guard](ops/background-data.md)). A full mirror is the repository's whole history, so size the volume for it.
- **In the database:** each repository's commits as read (author name and email, date, per-file line counts, co-author trailers), its forge events (login, kind, time), a map from forge logins to commit emails, the model's judgments of outlier commits, and the weekly series and current score per repository and area, with the version of the settings each score was computed with (the `slop_meter_*` tables, migrations 121, 122 and 144). Contributor names, emails and logins are personal data: they are kept while the repository is in the meter's scope, replaced when its history is read again from the start, and removed with the rest of its rows and its mirror `background.prune_after_days` after it leaves, on the first tick past that (with the meter off, at its next run), unless it comes back first. A login goes once no remaining event refers to it. The removal never deletes anything outside `SLOP_METER_DATA_PATH` nor follows a link out of it ([what the background features keep](ops/background-data.md)).

## Where it shows

- `GET /api/v1/repos/{repo}/-/slop-meter` and the repository page: the score, the level and a week-by-week line of cost, capacity and slop ([api.md](api.md)).
- The Slop Dungeons in the [world](world.md) carry each area's reading; an area at level 2 or worse has a dungeon even with no open slop finding.
- Triage is told the job's area's (else the repository's) level, and the Slopmaster how much of the area landed beyond human attention.

## Not covered

GitLab repositories get commit signals only, no events. Regex-based or JavaScript-configured excludes are not read.
