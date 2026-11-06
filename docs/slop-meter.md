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
| `api_calls_per_tick` | `200` | Forge API calls one run makes with one credential. An organization's value only narrows its own share |
| `rate_reserve` | `2000` | The rate limit a credential keeps for jobs: below it, the run calls the forge no more with it. An organization's larger value stops only its own reads sooner |
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
| `bots` | none | Bots, by commit name, address or forge login; each layer adds to the list it inherits |
| `ai_agents` | none | AI coding agents beyond the known ones, by their commit address up to the `@` (`aider@`) or a whole address; each layer adds to the list |

**Globs.** A glob with no `/` matches a file's name in any directory (`*.py`); one with a `/` matches its path from the repository's root (`db/migrations/*.sql`, `generated/**`). `*` matches within one name, `?` one character, `**` any number of directories. Matching ignores case, so globs are written in lowercase, and quoted in YAML, which reads a bare `*` as an alias. A file no glob matches weighs nothing. Where several globs of `weights` match a file, the most specific wins:

1. A glob with a `/` wins over any glob without one, so `generated/**: 0` weighs every file under `generated/` nothing, `.py`, `.proto` and `.graphql` alike.
2. Between two globs with a `/`, in this order:
   1. the one with more segments free of wildcards (`src/legacy/**` over `src/**`);
   2. then the one with fewer `**` segments (`src/main.py` over `src/**/main.py`, `a/b/*.ts` over `a/**/b/*.ts`);
   3. then the one with fewer other segments holding a wildcard;
   4. then the one with more segments;
   5. then the one with more literal characters (any but `*`, `?` and `/`).
3. Between two globs without a `/`: the one with more literal characters (`*.min.js` over `*.js`).
4. Any tie: the glob first in alphabetical order.

The meter files each glob once and checks a path only against the globs that could match it, then weighs each path once a run:

| A glob… | is checked against |
|---|---|
| that names one file (`makefile`) | that name |
| of one name with an extension (`*.py`, `test_?*.py`) | names with that extension |
| of one name with three literal characters in a row (`*fixture*`) | names holding them |
| with a `/` whose first segment is a name (`src/**`) | paths under that directory |
| with a `/` and a segment that is a name (`**/vendored/**`) | paths holding that segment |
| with a `/` whose last segment has an extension (`*/*.py`) | names with that extension |
| with a `/` and three literal characters in a row (`**/*_gen*`) | paths holding them |

Any other glob (`*/*`, `*x*`) is checked against every path, so a write may hold at most 16 of them in `weights`, and 16 in `test_files`, counted with what it inherits. A write that leaves no more of them than readers read before is taken, so a layer a growing document below pushed over the bound can still change another key.

**Layers.** An organization's layer may set every key but `enabled` and `interval_hours`, for its repositories: what counts as code, how fast its team attends to it, where its levels fall, its judge's guidance, its own bots and a narrower forge budget. A repository's layer may set what counts as code in its own layout: `weights`, `excluded_dirs`, `test_dirs`, `test_files` and `test_marks`.

- `weights` is merged glob by glob, through every layer: a document adds or changes the globs it names and keeps every other, so a glob set to `0` stops counting.
- `bots` and `ai_agents` add to what a layer inherits: a bot the deployment lists counts for every organization, and no layer takes one out. A bot spelled otherwise (`CI-Bot`, `ci-bot`) is the same bot, kept once.
- **Counts.** A write is refused (422, naming the key and the count) when `weights`, `bots` or `ai_agents`, merged with what it inherits, would hold more than the key may (1000 globs, 200 bots, 200 agents). When a document below grows later, a layer read past the bound keeps the most specific entries, with a warning (`settings_layer_entries_left_out`), never only the inherited list: every entry of its own, then its organization's, then the deployment's, then the default's, each in its order. So two repositories of one organization agree on the organization's globs. A layer left over the bound that way can still be written: only a write that makes the total larger than it was is refused, so it may change another key, or take entries out.
- `excluded_dirs` and the `test_*` lists replace what they inherit whole, so a repository can take back a directory the default excludes (a `deps/` of its own code), restating the rest.
- Every other key a layer sets replaces the inherited value.
- **The forge budget.** A credential's budget is the deployment's `api_calls_per_tick` and `rate_reserve`. An organization's values only narrow its own repositories' share of it: fewer calls, or a larger reserve they stop at. They never widen it, and never lower what another organization reading with the same credential gets. Only the credential's own budget running out brings the meter back sooner (`SLOP_METER_CATCHUP_SECONDS`).
  - A repository stopped only by its organization's share keeps its previous score, or stays unscored, and waits for the next run. Its events are stored and its listings' cursors move, but each run starts reading at the first listing again.
  - So a share too small for one whole read (on the order of the repository's listings: comments, review comments, pull requests, reviews) never reaches the reviews. A score from it would look complete, so none is written, and a settings change does not rescore it either.
  - Keeping the reader's place between runs (#396) would let a small share finish over several runs.

**Before this section.** `SLOP_METER_INTERVAL_SECONDS` (`0` turned the reading off), `SLOP_METER_API_CALLS_PER_TICK` and `SLOP_METER_RATE_RESERVE` are imported once, at the first start of the release that added the section, as `interval_hours` (or `enabled: false`), `api_calls_per_tick` and `rate_reserve`, and ignored after that. Each organization's `slop_meter_bots` was copied into its layer as `bots` ([api.md](api.md#organizations)). Everything else was a constant in the meter's code, and its default is that constant.

## How a change rescores

Each score row records `config_version`: a digest of every key its repository's score is computed from, as that repository reads them through its organization's layer and its own. Every five minutes the meter compares each enrolled repository's stored version with the one it reads now. It rescores the repositories whose versions differ, and only those, from what it already read: no fetch, no forge call and no judgment, so a change costs a rescore of what it touches. The next full run keeps its time. Undoing a change rescores back the same way. `GET /api/v1/repos/{repo}/-/slop-meter` shows the version of the repository's score in `reading.config_version` ([api.md](api.md)).

- A repository still reading its history is left to its catch-up, which scores it.
- A rescore that fails is recorded on the repository and not tried again for that version.
- An outlier commit a change makes new is judged at the next full run; until then the score is provisional.
- A score stored before the version was recorded holds none, and a release that adds a key to what a score is computed from changes every digest. Either way the first run after the start, which scores every repository it reads, records the new versions.

Four keys are not what a score is computed from, and change no version: `enabled`, `interval_hours`, `api_calls_per_tick` and `rate_reserve`. Neither does `judge_prompt`: a judgment is kept per commit and never asked again, so new guidance applies to the outlier commits judged after it.

## Which repositories

The meter reads the registered, active repositories enrolled in it: `background.slop_meter` of the settings document, `false` until an operator sets it in an organization's layer (every repository of it), a repository's layer, or the deployment's document ([which repositories](configuration.md#which-repositories-background-features-read)). A job run on a repository enrolls nothing. A deployment that ran the meter before keeps the repositories it read then (those the world map showed: a job in the 90 days before the upgrade, owners or a watched stack, plus `SLOP_METER_REPOS` and minus `SLOP_METER_EXCLUDE`), each written into its repository's layer at the first start of that release.

A repository that leaves the meter's scope, opted out or unregistered, loses its mirror and everything the meter kept of it `background.prune_after_days` (a week) later, unless it comes back first; one made inactive is not read and keeps what was read ([what it keeps](#what-it-keeps)).

The loop is on by default and reads what is enrolled. `slop_meter.enabled: false` turns the reading off; turned on again, the meter reads within five minutes. Until the first start's import has imported `SLOP_METER_INTERVAL_SECONDS`, `SLOP_METER_API_CALLS_PER_TICK` and `SLOP_METER_RATE_RESERVE` (a start whose import failed: it is tried again while the orchestrator runs), the meter reads them as the release before did, so a meter that was off stays off (`slop_meter_settings_not_imported`).

## What it reads, and how much

- **History:** a bare mirror of each repository, cloned and fetched with its organization's forge credential (passed to git through the environment, never in a URL or the mirror's config). Each tick reads at most `SLOP_METER_COMMITS_PER_TICK` commits of a history, oldest first, so a large history is read over several ticks; the loop comes back after `SLOP_METER_CATCHUP_SECONDS` while one is behind. Every git call has a timeout (`SLOP_METER_GIT_TIMEOUT_SECONDS`). These are the process's own limits, and stay variables.
- **Forge events** (GitHub only): issue and pull request comments, review comments and reviews, from a cursor per listing. These are API calls, bounded per tick and credential (`slop_meter.api_calls_per_tick`) and stopped once the credential's remaining rate limit drops below `slop_meter.rate_reserve`, because jobs use the same credential. Git fetches are not API calls.

## What it keeps

- **On disk:** one bare mirror per repository in scope, under `SLOP_METER_DATA_PATH` (`/data/embry0/slop-meter`, inside the orchestrator's data volume), one directory per segment of its name, lowercased: `acme/widgets` at `acme/widgets.git`, a GitLab project in a subgroup, `group/sub/project`, at `group/sub/project.git` (at most 21 segments, GitLab's deepest). git is started in the mirror's directory as the meter opened it, so a link swapped in before git starts is never followed; what git resolves for itself once it is running is git's, and the meter checks the way again after each clone and fetch ([the guard](ops/background-data.md)). A full mirror is the repository's whole history, so size the volume for it.
- **In the database:** each repository's commits as read (author name and email, date, per-file line counts, co-author trailers), its forge events (login, kind, time), a map from forge logins to commit emails, the model's judgments of outlier commits, and the weekly series and current score per repository and area, with the version of the settings each score was computed with (the `slop_meter_*` tables, migrations 121, 122 and 144). Contributor names, emails and logins are personal data: they are kept while the repository is in the meter's scope, replaced when its history is read again from the start, and removed with the rest of its rows and its mirror `background.prune_after_days` after it leaves, on the first tick past that (with the meter off, at its next run), unless it comes back first. A login goes once no remaining event refers to it. The removal never deletes anything outside `SLOP_METER_DATA_PATH` nor follows a link out of it ([what the background features keep](ops/background-data.md)).

## Where it shows

- `GET /api/v1/repos/{repo}/-/slop-meter` and the repository page: the score, the level and a week-by-week line of cost, capacity and slop ([api.md](api.md)).
- The Slop Dungeons in the [world](world.md) carry each area's reading; an area at `slop_policy.dungeon_min_level` or worse (level 2 by default) has a dungeon even with no open slop finding.
- Triage is told the job's area's (else the repository's) level and leans toward slop on a borderline verdict from `slop_policy.triage_lean_min_level` (Sloppy Joe by default), and the Slopmaster is told how much of the area landed beyond human attention. What a verdict of slop does to a job is [slop routing](slop-routing.md).

## Not covered

GitLab repositories get commit signals only, no events. Regex-based or JavaScript-configured excludes are not read.
