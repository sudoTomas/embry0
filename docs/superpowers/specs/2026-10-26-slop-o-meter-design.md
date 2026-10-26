# The Slop-O-Meter: how much of a codebase landed beyond human attention

**Why.** The Slop Dungeon counts the slop findings that are open. It cannot say how sloppy a repository or an area is as a whole. The Slop-O-Meter does: it compares the human attention a codebase demands against the human attention it has received, week by week, and calls the excess slop.

**Credit.** This is a clean-room reimplementation of [Slop-O-Meter](https://github.com/slop-o-meter/slop-o-meter)'s published slop score algorithm (its `docs/slop-score-algorithm.md`), written from that description alone; none of its code was read or copied. Where the description leaves a choice open, the choice made here is marked **Choice**.

## The score

### Attention cost: weighted lines

Every changed line of every commit is weighted by its file:

- **By type:** core logic 1.0 (`.py`, `.ts`, `.go`, `.rs`, `.java`, …), SQL 0.8, UI code 0.5 (`.tsx`, `.jsx`, `.vue`, `.svelte`, `.html`, …), styling 0.3 (`.css`, `.scss`, …), non-code 0 (`.json`, `.yaml`, `.md`, `.svg`, lock files, …). **Choice:** an extension not in any list weighs 0, so data and binaries never count.
- **Tests and examples** (`.test.*`, `.spec.*`, `_test.go`, `test_*.py`, `__tests__/`, `tests/`, `test/`, `examples/`, `demos/`, `samples/`, …) weigh half their type.
- **Excluded (0):** vendored and third-party directories (`vendor/`, `node_modules/`, `third_party/`, `external/`, `deps/`), minified, bundled and map files, `.d.ts`, and every path a linter or formatter of the repository ignores. **Choice:** the ignore lists read are `.prettierignore`, `.eslintignore`, `.stylelintignore`, `.gitattributes` (`linguist-generated`, `linguist-vendored`), `biome.json` (`files.ignore`), and ruff's `exclude`/`extend-exclude` in `pyproject.toml` or `ruff.toml`, at the default branch's head; gitignore-style globs, negations skipped.
- **Outlier files:** a file whose current size is above `Q3 + 1.5·IQR` of the code files' sizes is excluded when it is at most its fair share (`total lines / files`), and kept when larger. **Choice:** sizes are current line counts, the sum of each path's additions minus deletions over the history (renames are counted as delete and add).
- **At least one outlier (embry0, 2026-10-26):** the count is `max(1, ⌈4 × age in years⌉)`. A history that spans no time (one commit) gave 0, so its first commit, which the spec always excuses as setup, counted as slop.

A week's **net weighted additions** are `max(0, weighted additions − weighted deletions)` over its commits, by author date, in weeks starting Monday 00:00 UTC.

**Size dampening.** With `C` the codebase's cumulative weighted size including the week (running sum of weighted additions minus deletions, never below 0), a week's lines weigh `min(1, C/5000) + 0.05·log2(1 + C/100000)`. The week's **cost** is its net weighted additions times that factor.

### Attention spent: human sessions

- **Commits:** each human-authored commit implies `1 h` of work before it; `× 0.8` when it has an AI co-author; a squash merge `1 h + 1 h` per sub-commit. **Choice:** a squash merge is a commit whose body lists two or more bullet lines (`* ` or `- `); the bullets are its sub-commits.
- **Co-authors:** each human `Co-authored-by` trailer implies `1 h × 0.5`, `0.25`, `0.125` (third and after) at the commit's time.
- **GitHub events:** issue and pull request comments, review comments and reviews each imply `1 h`. Logins become emails from `{id}+{login}@users.noreply.github.com` addresses in the history, else from the login's own commits on the forge. **Choice:** a login with no email found stays a contributor of its own, with no commits (so it adds nothing; see the core factor).
- **Bots** (known names, names ending `[bot]`, `[bot]@users.noreply.github.com`, `noreply@…`, AI agent addresses such as `codex@`, `claude@`) add lines but no sessions. An AI co-author is a bot co-author.
- **Sessions:** a contributor's neighborhoods are merged into sessions; a week's hours are the length of the union inside the week.
- **Core factor:** `clamp((commits − 10)/50, 0, 1) × (1 + 0.25·commit share)`, where commits are the contributor's human-authored commits. **Choice:** counted over the whole history at the time of computing, so a core contributor's early weeks count too.
- **Small repositories (embry0's departure, 2026-10-26):** when no author reaches 10 human commits, everyone's time counts in full (core factor 1). Read literally, the core factor makes every author of a small repository a drive-by worth nothing, so a 6-commit, 778-line repository read as "Just The Slop". A fast big dump still reads high: 47,000 lines in six days from one person is far beyond what its commits' hours cover.
- **Weekly capacity:** per contributor, effective hours `h = hours × core factor`, capped concavely: `h − h³/(3·80²)` up to 80 h, the value at 80 h above it; summed over contributors, `× 40` lines per hour.

### Slop and the score

`excess = max(0, cost − capacity)` per week, and `score = Σ excess / Σ cost` over the history (0 when nothing cost anything). **Choice:** the denominator is the dampened cost the excess is measured against, so the score stays in 0..1 however much was deleted.

**Outlier commits.** The top `⌈4 × project age in years⌉` commits by net weighted additions of at least 2,000 are asked of a model (the shared brief-model call, local model first), each with its message, files, the codebase's size and age at the time: slop, or not slop with a reason. A commit not slop keeps its lines in the codebase's size and the cost, but not in the excess. The first two commits are never slop. **Choice:** a commit not yet judged (no model, a failed call) is treated as not slop and the score is marked **provisional** until it is judged; judgments are kept per commit.

**Levels:** Buttered Noodles (< 0.2), Spaghetti Bolognese (< 0.4), Lasagna (< 0.6), Sloppy Joe (< 0.8), Just The Slop.

### Areas

An area's lines are its paths' lines (`.embry0/areas.yml`). **Choice:** its dampening uses the repository's size (the codebase is the repository), its outlier commits are the repository's judgments applied to the area's share of each commit, and its capacity is each contributor's weekly capacity apportioned by the share of their weighted churn that week that touched the area (their history-wide share in a week they only commented).

## Data

- **Scope:** the registered repositories the world map shows (its regions: repositories with jobs in the last 90 days, owners or a watched stack), plus `SLOP_METER_REPOS`, minus `SLOP_METER_EXCLUDE` (comma-separated). A repository that leaves the set is no longer read; its scores stay readable.
- **Mirror:** a bare clone per repository in scope under `SLOP_METER_DATA_PATH` (`/data/embry0/slop-meter`), cloned and fetched with the organization's forge credential, the one discovery reads with, passed to git through environment config (never in a URL or the mirror's config). Every git call runs in a subprocess with a timeout.
- **Commits** are read oldest first (`rev-list --reverse --date-order`) and stored with their per-file numstat and trailers, at most `SLOP_METER_COMMITS_PER_TICK` a tick; a history rewritten under the stored one starts over.
- **Events** (GitHub only) are read from a cursor per listing (issue comments, review comments, and reviews of the pull requests updated since), a bounded number of pages a tick; logins are mapped a few a tick. **Choice:** pull requests beyond the page cap are not read, so their reviews are skipped.
- **API budget:** the organization's credential is the one jobs clone, push and open pull requests with, so a tick's forge calls share one budget across every repository (`SLOP_METER_API_CALLS_PER_TICK`, 200), dealt one call per repository in turn (the first repository rotates each tick) so one backlog cannot starve the rest. Each response's `X-RateLimit-Remaining` is read, and the tick's forge calls stop, logged, once it drops below `SLOP_METER_RATE_RESERVE` (2,000). Git fetches are not API calls. A repository whose events were cut short is scored on a later tick, once they are read.
- **Weeks and scores** are recomputed from the stored rows when the history is caught up, off the event loop, and stored per repository and area: the weekly series (cost, capacity, slop, cumulative score) and the current score.
- **The loop** runs every `SLOP_METER_INTERVAL_SECONDS` (a day; 0 is off), and sooner (`SLOP_METER_CATCHUP_SECONDS`) while a repository has a backlog.

## Where it shows

1. **Slop Dungeons** carry `meter: {score, level, name, provisional, computed_at}`; an area whose level is Spaghetti Bolognese or worse has a dungeon even with no open slop finding.
2. **Triage** is told the job's area's (else repository's) level and leans toward slop on a borderline verdict at Sloppy Joe or worse; the **Slopmaster** is told how much of the area landed beyond human attention.
3. **The repository page** shows the score, the level and a week-by-week line of cost, capacity and slop (`GET /api/v1/repos/{repo}/slop-meter`).

## Not covered

GitLab repositories get commit signals only (no events). Regex-based or JavaScript-configured excludes are not read. The overtime curve is the concave one only.
