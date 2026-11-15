# Context audit

A coding agent working in a repository reads some files as instructions: `CLAUDE.md` and `AGENTS.md` at any depth, a skill, a rules directory, a handoff note. A line in one of them that went stale (a command that was renamed, a directory that moved, a rule the code no longer follows) reaches the agent as a fact, and it acts on it. The context audit finds those lines. An organization turns on a schedule, and embry0 then audits the instruction files of every registered repository under it on its own: when one of the files changes, and again after an interval once the code moved under them. See [ADR 0020](adr/0020-context-audit-schedule.md) for the decision.

The schedule is off for every organization until an admin sets it.

## What it audits

The instruction files are the paths that match one glob list; the schedule fingerprints those files, and each audit's task hands the auditor the same list as a `git ls-files` command. `**/` stands for any number of directories, none included, and `*` stays inside one path segment; a glob is matched in time bounded by its length and the path's. Unless the [settings document](#choosing-the-files) names other files, the list is the built-in one, `INSTRUCTION_FILE_GLOBS` in `embry0/services/instruction_files.py`:

```
**/CLAUDE.md                **/CLAUDE.local.md          **/AGENTS.md
**/AGENTS.override.md       **/GEMINI.md                **/HANDOFF.md
**/llms.txt                 **/.cursorrules             **/.windsurfrules
**/.clinerules              .github/copilot-instructions.md
.github/instructions/**/*.instructions.md
**/.cursor/rules/**/*.mdc   **/.cursor/rules/**/*.md
**/.claude/skills/**/*.md   **/.claude/commands/**/*.md
**/.claude/agents/**/*.md   **/.claude/rules/**/*.md
**/.grok/skills/**/*.md     **/.grok/rules/**/*.md
**/.codex/**/*.md           **/.agents/**/*.md
```

A file inside `.git`, `.venv`, `venv`, `node_modules`, `vendor`, `build` or `dist` is never one, unless the settings document names other directories: those trees belong to another project.

### Choosing the files

Repositories differ: an organization may keep its agents' rules in `.roo/rules`, `.kiro/steering` or `docs/agents/*.md`, write a `CONVENTIONS.md`, or never use a handoff note. The settings document's `context_audit` section names the files instead ([its keys](configuration.md#settings-reference)):

```yaml
context_audit:
  files:                       # null, the default, is the built-in list above
    - "**/CLAUDE.md"
    - "**/AGENTS.md"
    - "**/CONVENTIONS.md"
    - ".roo/rules/**/*.md"
  exclude_dirs: [node_modules, vendor, dist, third_party]   # null is the built-in directories
```

- **Per organization and per repository.** The deployment's document sets the files for every repository, an organization's layer for its own repositories, and a repository's layer for itself, each over the one before ([layers](configuration.md#settings-document)). `files` and `exclude_dirs` are each replaced whole: a list names every glob or directory read, not the ones added, so copy the built-in list to extend it. `null` in a layer goes back to the built-in list under a layer that names another, and `exclude_dirs: []` skips no directory.
- **What a glob may hold.** Letters, digits, `.`, `_`, `-`, `+`, `@`, `*` and `/`, starting at the repository's root: `**` stands alone between slashes, and never last (`docs/**/*.md`, not `docs/**`). Those are the globs the schedule's matcher and git's glob pathspec read alike, so the files the schedule fingerprints are the files the auditor lists.
- **A directory selects everything under it.** A glob with no wildcard that names a directory, such as `.kiro/steering` or `.roo/rules`, selects every tracked file under it at any depth, as git's pathspec does: images and other binaries included, which the audit then fingerprints and lists. Neither side can tell a binary from the tree alone, so nothing is left out by kind; to read only the Markdown, write `.kiro/steering/**/*.md`. A glob or a directory outside them is refused with a 422 naming the key and which entry, and nothing is stored. A list holds at most 64 globs and 32 directories.
- **When it applies.** The schedule reads the section at every wake, and each audit's task lists the files the repository resolves at that moment. A change that selects other files changes a repository's fingerprint, so each repository whose files it changes is due once at the next wake, within the organization's cap per wake. A job created with `POST /api/v1/jobs` lists the files its repository resolved when the job started, and a finding's fix those of its repository when it is accepted.

```bash
curl -X PATCH "http://localhost:8200/api/v1/config/settings/context_audit?owner=acme" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"files": ["**/CLAUDE.md", "**/AGENTS.md", "**/CONVENTIONS.md", ".roo/rules/**/*.md"]}'
```

Use `?repo=acme/widgets` for one repository's layer, and `null` for a key to hand it back to the layer below. On the dashboard, the organization's Settings tab holds its **Instruction files** and **Skipped directories** fields, beside the schedule, and the organization's page and the repository's page link to their layers on the Settings → Policy tab.

Each audit is a job on the `context-audit` pipeline template ([specialist-agents.md](specialist-agents.md)), over a clone of the repository. Its task names the repository, the commit that was read and every instruction file found there. A repository with no instruction file gets no job.

The task then says how to run the audit: the command that lists the instruction files, the severities, and the shape of the report embry0 reads back (the `Stale:` and `Files:` lines, the findings table, the patch under `## Pull`). They are written when the task is built, from the running release's code, and not kept in the agent's prompt: a deployment keeps the context-auditor prompt it first stored, so a list or a shape kept there would stay as an older release left it. A changed list reaches the next audit without resetting the agent, and where an older stored prompt gives another command or shape, the task tells the agent to follow the task.

## When a repository is due

Every `context_audit.check_minutes` of the settings document, 15 minutes by default, the orchestrator reads the default branch of each active registered repository of an active organization whose `context_audit.days` is set ([turning it on](#turning-it-on)), with the organization's credential: the newest commit, then the recursive tree when the head moved since the last read, or when the files the repository reads changed. It reads `context_audit.read_concurrency` repositories of an organization at once, 8 by default. Both are the deployment's alone, from 15 minutes to a day and from 1 to 32: they pace the forge calls every organization's audits make together, on the credentials jobs use too. The schedule reads them at every wake, and while it waits it reads `check_minutes` again at least every 15 minutes, so a shorter interval applies within 15 minutes. The **fingerprint** is the sha256 over the sorted `path<TAB>blob sha` lines of the instruction files, so it changes when one is added, removed, renamed or edited, and only then.

**What it spends of the forge's rate limit.** Each head read sends the `ETag` of the last one as `If-None-Match`. A branch that has not moved answers `304 Not Modified`, which GitHub does not count against the credential's limit, and nothing more is read for that repository: its last reading stands. A branch that moved costs that read and one tree read. Every answer's `X-RateLimit-Remaining`, `X-RateLimit-Limit` and `X-RateLimit-Reset` are kept per credential, and once the calls left fall below `context_audit.rate_reserve_percent` of its limit (20% by default, the deployment's alone; 0 never defers) the organizations reading with that credential are **deferred**: no paid read and no start until the low window ends, so the jobs that use the same credential keep the last calls. 20% of GitHub's usual 5,000 an hour is 1,000, and a token with a smaller limit keeps the same share; a forge that sends no `X-RateLimit-Limit` is never deferred.

- **The window** is the reset the forge named, when it lies ahead by the orchestrator's clock, an hour at most. With no reset it can use (none, not a number, or already past because the two clocks disagree), it is 15 minutes; then one call, the probe, asks the forge again (`context_audit_rate_reserve_probe`, info, once per window), and every other call waits for its answer. A credential found low is never deferred for good.
- **While deferred**, a head read that carries an `ETag` still goes out: a branch that did not move answers 304 for free, and its rate headers keep the state current (one that names no reset starts its 15 minutes again: the forge has just said the calls are still low). The first such read the forge answers with a moved branch costs one call and closes reading for the rest of that survey, so a low credential pays at most one call per survey. Nothing is started.
- The warning `context_audit_rate_reserve_reached` says so once per window. A deferred repository is not refused and counts as no attempt: the first tick after the window reads it. A read of the [Context audit tab](#reading-what-it-sees) and a run by hand stop reading at the reserve too, and list the repositories they did not read under `deferred`; a run by hand starts nothing while the credential is low, and lists a repository it read for free under `deferred` as well.

A repository is due when:

1. it has no context-audit job yet; or
2. the fingerprint differs from the one its newest context-audit job recorded; or
3. that job is `days` old (the repository's `context_audit.days`) and the branch head moved since it was started.

It is never due while one of its context-audit jobs is pending, running, awaiting input or paused, whoever started it, a finding's fix job (below) included.

The newest job is the anchor whatever became of it. A completed job counts from when it finished, and so does one whose pull request merged or closed since (`pr_merged`, `pr_closed`): it did its work. A failed or cancelled one counts from when it started, so a failure waits a full interval, the same as a success, and the schedule never retries on every tick. A start refused before any job existed counts the same way; the orchestrator remembers it until it restarts. The budget gate is the exception: the schedule asks it before each start, and while it refuses (a daily or monthly budget; the schedule's actor, `embry0:context-audit`, skips the per-author hourly limit) no job is written, nothing counts as an attempt and the tick stops there, so the repositories it did not reach are due again on the next tick. A job the gate refused anyway, between the ask and the run, did no work and is no anchor either. A change to the files is due at once in every case. A job a person starts with the run below is the schedule's own start and counts like one. A context-audit job started any other way, such as one created with `POST /api/v1/jobs`, counts too; it records no fingerprint, so only its age is read.

A finding's fix job is no audit. Accepting a finding ([below](#findings-offered-to-owners)) starts a job on the `context-audit` template that fixes that one claim, dispatched by `embry0:proposals`, and the schedule never reads a job of that actor as a repository's newest job, so it never anchors the due rule. While it runs it still holds the repository, as every context-audit job does, so no audit starts beside it to open a pull request on the same files. Its recorded fingerprint names the finding, not the files, so read as the anchor it would make the repository due at once. Once its pull request merges, the file it changed no longer matches the fingerprint the audit before it recorded, so with the schedule on the repository is due again, as after any change to the files. Progression reads it as no audit too, and its merge as a merged proposal ([progression.md](progression.md#achievements-quests-and-diaries)).

The schedule reads GitHub repositories in this release. An organization on GitLab is skipped with one log line.

## Turning it on

The setting is the [settings document](configuration.md#settings-document)'s `context_audit.days`, a whole number of days from 1 to 365; `false` or `null` is off, the default. The deployment's document sets it for every organization, an organization's layer for its repositories and a repository's layer for itself, each over the one before ([its keys](configuration.md#settings-reference)). It needs the `admin` tier:

```bash
curl -X PATCH "http://localhost:8200/api/v1/config/settings/context_audit?owner=acme" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"days": 7}'
```

- **One repository out, or more often.** `?repo=acme/widgets` with `{"days": false}` takes that repository off its organization's schedule, and `{"days": 2}` audits it every two days. `null` takes the key out of a layer, so it reads the layer below again.
- **In the dashboard**, open **Organizations**, then the organization (`/orgs/<owner>`), then its **Settings** tab: the **Context audit** fields hold the schedule, the pull request switch and the [files](#choosing-the-files), each with where its value comes from (the organization's own, the deployment's, or the default) and "As the deployment" to hand it back. The header says how often the organization's repositories are audited and links there. Without the admin tier the fields show their values and change nothing.
- **Moved from the organization (#170).** Until this release the setting was `context_audit_days` on the organization row (`PATCH /api/v1/orgs/{owner}`). The first start of the release copied each organization's value into its layer once; until an organization's copy succeeds, its audits read its row, and the copy is tried again while the orchestrator runs. While it is pending, choosing *As the deployment* writes nothing, since the layer holds no `days` yet: the row still applies, and the copy writes it later; choose *Off* or a number instead. A repository that `REPO_RENAMES` moves to another owner reads that owner's layer, so the copy also wrote its old organization's values into the repository's own layer, and it is audited as before. `PATCH /api/v1/orgs/{owner}` still takes `context_audit_days` until 0.2.0 and writes it into the layer ([api.md](api.md#organizations)).

The organization must be `active` and its credential must be able to read the repositories, the same credential discovery uses. Only registered, active repositories are read ([onboarding.md](onboarding.md)).

## What it may spend

Two keys of the [settings document](configuration.md#settings-document)'s `budget` section bound what the audits may spend. The deployment's document sets them for every organization, and an organization's layer for its own ([ADR 0021](adr/0021-context-audit-caps-per-organization.md)); the schedule reads them at every tick, so a change needs no restart:

| `budget` key | Default | What it bounds |
|---|---|---|
| `context_audit_job_usd`: more than 0 | `2.0` | Each job's cap in USD, always a hard stop (`ERR_BUDGET_OVERRUN`) whatever `budget.overrun_mode` says: nobody watches a scheduled job. Never unlimited, and bounded by `budget.max_budget_per_job_usd` where the job runs |
| `context_audit_max_jobs_per_tick`: 0 to 50 | `3` | Jobs one tick starts for the organization. Its due repositories left over wait for the next tick; `0` starts none |

They are written with the `admin` tier, as the rest of the document is; taking a key out of the organization's layer (`null`) hands it back to the deployment. On the dashboard, they are the **What context audits may spend** fields of the organization's Settings tab, beside `budget.fix_job_usd`, the cap of a finding's fix (below), each with where its value comes from; the organization's page also links to its layer on the Settings → Policy tab:

```bash
curl -X PATCH "http://localhost:8200/api/v1/config/settings/budget?owner=acme" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"context_audit_job_usd": 5, "context_audit_max_jobs_per_tick": 2}'
```

Until #171 the two were the organization's own settings, `context_audit_budget_usd` and `context_audit_max_jobs_per_tick` (`PATCH /orgs/{owner}`), with `CONTEXT_AUDIT_JOB_BUDGET_USD` and `CONTEXT_AUDIT_MAX_JOBS_PER_TICK` for an organization without them. The first start of that release copied each organization's values into its layer and the variables into the deployment's document, once; until an organization's copy succeeds, its audits read its old settings, and the copy is tried again while the orchestrator runs. `PATCH /orgs/{owner}` still takes the old names until 0.2.0 and writes them into the layer ([api.md](api.md#organizations)).

Each organization's cap is its own: one that reached its cap does not hold back another, so one tick starts at most the sum of the caps of the organizations on the schedule.

The fleet-wide caps still apply to every job: the `budget` section's `max_budget_per_job_usd`, `daily_cap_usd` and `monthly_cap_usd` ([configuration.md](configuration.md#settings-reference)). Its `rate_limit_per_author_per_hour` does not: the schedule's actor `embry0:context-audit` is exempt from it (`RATE_LIMIT_EXEMPT` in `embry0/orchestration/dispatch_actors.py`), so the per-tick cap, one live job per repository and the per-job cap are what pace it. The schedule asks the gate before each start and stops at its first refusal, which counts as no attempt.

## Reading what it sees

```bash
curl http://localhost:8200/api/v1/orgs/acme/context-audit \
  -H "Authorization: Bearer $API_KEY"
```

The answer lists each active registered repository with at least one instruction file: how many it has, the fingerprint and head it was read at, its newest context-audit job (id, status, date; never a finding's fix job), whether the schedule would start one now and why (`first`, `changed`, `aged`, `live`, `current`, or `off` while the setting is off). Repositories the forge did not answer for are listed under `unread`, with why. The read reads the forge and starts nothing, and works with the setting off. Field by field: [api.md](api.md#organizations).

Each job the schedule starts is a job like any other: the read above names each repository's newest one, `GET /api/v1/jobs?repo=<owner>/<name>` lists it among the repository's jobs, and `GET /api/v1/jobs/{job_id}` carries its report. Its `job_created` audit row names the actor `embry0:context-audit`, and its `pipeline_config` records the `context_fingerprint` and `head_sha` it was started for.

The organization page's **Context audit** tab shows the same read: each repository with instruction files, the outdated claims its newest audit counted (still shown once that audit's pull request merged or closed), that audit's status, cost and failure if it failed, and whether the schedule will audit it again, most outdated claims first. A merged pull request probably fixed the claims its audit counted, so that repository is listed last and its claims stay out of the tab's total. Each row links to the job, whose page carries the findings table and the patch. The tab serves the last reading of the default branches; **Read again** reads them anew.

## Running it now

An audit can be started by hand, whether or not the schedule is on, with the tier that creates a job (`operator`):

```bash
# Every due repository of the owner, at most its cap per tick (or "limit": N, 1 to 50)
curl -X POST http://localhost:8200/api/v1/orgs/acme/context-audit/run \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{}'

# One repository: when it is due, or whatever the due rule says with "force": true
curl -X POST http://localhost:8200/api/v1/orgs/acme/context-audit/run \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "acme/widgets", "force": true}'
```

Due is the rule above without the setting: with the setting off no interval applies, so a repository is due when it has no context-audit job yet or its files changed. Nothing starts while one of the repository's context-audit jobs is live, `force` or not. Each job starts as a scheduled one does, under the actor `embry0:context-audit`, with the organization's per-job cap and the fingerprint and head it was read at, so the schedule anchors on it and does not repeat it; the audit row `organization.context_audit_run` names the person who asked. The answer lists the jobs started and every repository passed over, with why. Field by field: [api.md](api.md#organizations).

A job created with `POST /api/v1/jobs` and `"pipeline_template": "context-audit"` runs the same auditor, with the same command and report shape added to its task when the agent runs, but records no fingerprint, names no instruction files in its task, and has a hard cap only if it is given one; the schedule sees it as the newest audit of its repository without its fingerprint, so a file changed later is not compared with it until the next scheduled audit. Start one by hand with the run above.

## Pull requests from its fixes

Each audit's fixes can be opened as a pull request ([ADR 0022](adr/0022-context-audit-pull-requests.md)). The setting is `context_audit.open_prs`, `false` by default, set like `days` above: by the deployment, an organization or a repository, with the `admin` tier. In the dashboard it is the **Pull requests** field of the organization's Context audit settings, on its Settings tab:

```bash
curl -X PATCH "http://localhost:8200/api/v1/config/settings/context_audit?owner=acme" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"open_prs": true}'
```

It was `context_audit_open_prs` on the organization row until #170, copied into each organization's layer once, as `days` was, and with the same catch: while an organization's copy is pending, *As the deployment* writes nothing and the row still applies; `PATCH /api/v1/orgs/{owner}` still takes it until 0.2.0. A job reads it for its repository when it starts.

With it on, a job that finds outdated claims takes the patch from its report, the one it checked with `git apply --check`, commits it on its own branch and pushes it through the git proxy. At completion embry0 opens the pull request: titled with the number of claims it corrects, its body the report's findings table, labelled `embry0`, and asked about in the merge-approval chat when the repository has merge approvals on. The pull request changes only instruction files, and a person merges it.

One is open per repository at a time: while an earlier audit's pull request is open, a later audit only reports. An audit that finds nothing, a report with no patch, and a patch that no longer applies open nothing; the findings stay in the report. The Context audit tab links each audit's pull request.

## Findings offered to owners

Each row of an audit's findings table is also offered to the repository's owners as an [owner proposal](api.md#owner-proposals) of kind `finding`; accepting one starts a job narrowed to that file, which opens a pull request for that claim, with the hard cap `budget.fix_job_usd` (2 USD by default; an organization may set its own). The findings offered are those of the repository's newest audit that finished its work:

- **Its pull request merged:** none, until the repository's next audit finishes. The pull request may have fixed any of them, so they are not offered, and an older audit's findings are never offered in their place: they may be fixed too. A merge changes the instruction files, so with the schedule on the repository is due again at once.
- **Another audit's pull request merged after it started:** none either, until an audit that started after that merge finishes. It read the instruction files before the merge, so the merge may have fixed what it found. That happens when it ran while an earlier audit's pull request merged, and when it started before an audit that finished and merged ahead of it. A merge counts from when the forge says it merged; one recorded without the forge's time counts from its job's last change, which is no earlier.
- **Its pull request was closed unmerged:** its findings are still offered. Nothing reached the default branch through it, so the claims still stand; an owner who disagrees with one declines it.
- **It completed, with no pull request or one still open,** and no other audit's pull request merged since it started: its findings are offered.

An audit that failed, or one that has not finished, is passed over: the feed keeps to the newest audit that finished its work, as above.

A finding's fix job is no audit, here as for the schedule, so the feed reads past it. While it runs, once it completes, and once its pull request merges or closes, the audit before it is still the newest, and that audit's findings are still offered, all but the one the job fixes: its owner answered that one by accepting it. The fix job's merged pull request changed that one claim, so it is neither the audit's pull request merging nor another audit's, and its own report is never offered.

## From an MCP client

The embry0 MCP server (`embry0-mcp`) does all of it. A tool that changes something takes `confirm=true`, passed once the person has approved that call; without it the tool answers `confirmation_required` and does nothing.

| To | Call |
|---|---|
| Turn it on for an owner, every 7 days | `settings_set_keys(section="context_audit", values={"days": 7}, owner="acme", confirm=true)` |
| Take one repository off its organization's schedule | `settings_set_keys(section="context_audit", values={"days": false}, repo="acme/widgets", confirm=true)` |
| Set the caps: 5 USD a job, 2 jobs a tick | `settings_set_keys(section="budget", values={"context_audit_job_usd": 5, "context_audit_max_jobs_per_tick": 2}, owner="acme", confirm=true)` |
| Name the organization's own instruction files | `settings_set_keys(section="context_audit", values={"files": ["**/CLAUDE.md", "**/CONVENTIONS.md"]}, owner="acme", confirm=true)` |
| Hand them back to the built-in list | `settings_set_keys(section="context_audit", values={"files": null}, owner="acme", confirm=true)` |
| Look every hour instead of every 15 minutes | `settings_set_keys(section="context_audit", values={"check_minutes": 60}, confirm=true)` |
| Hand the caps back to the deployment | `settings_set_keys(section="budget", values={"context_audit_job_usd": null, "context_audit_max_jobs_per_tick": null}, owner="acme", confirm=true)` |
| Read what it sees, and why each repository is due or not | `context_audit_status(owner="acme")` |
| Run one repository now, when it is due | `context_audit_run(owner="acme", repo="acme/widgets", confirm=true)` |
| Run one repository now, whatever the due rule says | `context_audit_run(owner="acme", repo="acme/widgets", force=true, confirm=true)` |
| Run every due repository now | `context_audit_run(owner="acme", confirm=true)`, or with `limit=2` for at most two |
| Open a pull request from each audit's fixes | `settings_set_keys(section="context_audit", values={"open_prs": true}, owner="acme", confirm=true)` |
| Defer at 10% of the rate limit left instead of 20% | `settings_set_keys(section="context_audit", values={"rate_reserve_percent": 10}, confirm=true)` |
| Turn it off for an owner | `settings_set_keys(section="context_audit", values={"days": false}, owner="acme", confirm=true)` |

`settings_set_keys` needs an admin-scope key and `context_audit_run` an operate-scope key; `context_audit_status` reads. `settings_set_keys` changes the keys it names and leaves the rest of the layer as it is. `org_update(settings={"context_audit_days": 7})` and `{"context_audit_open_prs": true}` still work until 0.2.0, writing into the organization's layer and answering `deprecated`. Each job a run starts is a job like any other: follow it with `job_wait(job_id=...)` and read its report with `job_deliverables(job_id=...)`.

`job_create(task=..., repo="acme/widgets", pipeline_template="context-audit")` runs the auditor on a repository too, but the schedule does not see that job's fingerprint: it records none, names no instruction files in its task and has a hard cap only if given one, so a file changed after it is not compared with it. `context_audit_run` is the way to run one by hand.

## Turning it off

Set `days` to `false` in the organization's layer, which turns it off there whatever the deployment's document says; `null` hands the key back to the deployment's, off unless the deployment set one:

```bash
curl -X PATCH "http://localhost:8200/api/v1/config/settings/context_audit?owner=acme" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"days": false}'
```

In the dashboard, choose **Off** for the schedule in the organization's Context audit settings, then Save. No new job starts for the organization from the next tick; a job already running finishes. Pausing the organization (`PUT /api/v1/orgs/{owner}/status`) stops it too, and so does `budget.context_audit_max_jobs_per_tick: 0` in its layer; in the deployment's document, that stops it for every organization that sets no cap of its own.
