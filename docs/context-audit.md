# Context audit

A coding agent working in a repository reads some files as instructions: `CLAUDE.md` and `AGENTS.md` at any depth, a skill, a rules directory, a handoff note. A line in one of them that went stale (a command that was renamed, a directory that moved, a rule the code no longer follows) reaches the agent as a fact, and it acts on it. The context audit finds those lines. An organization turns on a schedule, and embry0 then audits the instruction files of every registered repository under it on its own: when one of the files changes, and again after an interval once the code moved under them. See [ADR 0020](adr/0020-context-audit-schedule.md) for the decision.

The schedule is off for every organization until an admin sets it.

## What it audits

The instruction files are the paths that match one glob list, `INSTRUCTION_FILE_GLOBS` in `embry0/services/instruction_files.py`; the schedule fingerprints those files, and each audit's task hands the auditor the same list as a `git ls-files` command. `**/` stands for any number of directories, none included, and `*` stays inside one path segment:

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

A file inside `.git`, `.venv`, `venv`, `node_modules`, `vendor`, `build` or `dist` is never one: those trees belong to another project.

Each audit is a job on the `context-audit` pipeline template ([specialist-agents.md](specialist-agents.md)), over a clone of the repository. Its task names the repository, the commit that was read and every instruction file found there. A repository with no instruction file gets no job.

The task then says how to run the audit: the command that lists the instruction files, the severities, and the shape of the report embry0 reads back (the `Stale:` and `Files:` lines, the findings table, the patch under `## Pull`). They are written when the task is built, from the running release's code, and not kept in the agent's prompt: a deployment keeps the context-auditor prompt it first stored, so a list or a shape kept there would stay as an older release left it. A changed list reaches the next audit without resetting the agent, and where an older stored prompt gives another command or shape, the task tells the agent to follow the task.

## When a repository is due

Every 15 minutes the orchestrator reads, for each active organization with the setting on, the default branch of each of its active registered repositories, with the organization's credential: the newest commit, then the recursive tree when the head moved since the last read. The **fingerprint** is the sha256 over the sorted `path<TAB>blob sha` lines of the instruction files, so it changes when one is added, removed, renamed or edited, and only then.

A repository is due when:

1. it has no context-audit job yet; or
2. the fingerprint differs from the one its newest context-audit job recorded; or
3. that job is `context_audit_days` old and the branch head moved since it was started.

It is never due while one of its context-audit jobs is pending, running, awaiting input or paused, whoever started it, a finding's fix job (below) included.

The newest job is the anchor whatever became of it. A completed job counts from when it finished, and so does one whose pull request merged or closed since (`pr_merged`, `pr_closed`): it did its work. A failed or cancelled one counts from when it started, so a failure waits a full interval, the same as a success, and the schedule never retries on every tick. A start refused before any job existed counts the same way; the orchestrator remembers it until it restarts. The budget gate is the exception: the schedule asks it before each start, and while it refuses (a daily or monthly budget; the schedule's actor, `embry0:context-audit`, skips the per-author hourly limit) no job is written, nothing counts as an attempt and the tick stops there, so the repositories it did not reach are due again on the next tick. A job the gate refused anyway, between the ask and the run, did no work and is no anchor either. A change to the files is due at once in every case. A job a person starts with the run below is the schedule's own start and counts like one. A context-audit job started any other way, such as one created with `POST /api/v1/jobs`, counts too; it records no fingerprint, so only its age is read.

A finding's fix job is no audit. Accepting a finding ([below](#findings-offered-to-owners)) starts a job on the `context-audit` template that fixes that one claim, dispatched by `embry0:proposals`, and the schedule never reads a job of that actor as a repository's newest job, so it never anchors the due rule. While it runs it still holds the repository, as every context-audit job does, so no audit starts beside it to open a pull request on the same files. Its recorded fingerprint names the finding, not the files, so read as the anchor it would make the repository due at once. Once its pull request merges, the file it changed no longer matches the fingerprint the audit before it recorded, so with the schedule on the repository is due again, as after any change to the files. Progression reads it as no audit too, and its merge as a merged proposal ([progression.md](progression.md#achievements-quests-and-diaries)).

The schedule reads GitHub repositories in this release. An organization on GitLab is skipped with one log line.

## Turning it on

The setting is `context_audit_days` on the organization, a whole number of days from 1 to 365. It needs the `admin` tier:

```bash
curl -X PATCH http://localhost:8200/api/v1/orgs/acme \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"settings": {"context_audit_days": 7}}'
```

In the dashboard, open **Organizations**, then the organization (`/orgs/<owner>`): the field **Audit agent instructions every … days** sits in the header, under the rediscovery schedule. Type the number of days and press Save. Without the admin tier the header says what the setting is and cannot change it.

The organization must be `active` and its credential must be able to read the repositories, the same credential discovery uses. Only registered, active repositories are read ([onboarding.md](onboarding.md)).

## What it may spend

Two settings on the organization bound what its audits may spend. An organization that sets neither gets the deployment's two variables ([ADR 0021](adr/0021-context-audit-caps-per-organization.md)):

| Setting | Variable, for an organization without the setting | What it bounds |
|---|---|---|
| `context_audit_budget_usd`: more than 0 and at most 50, or `null` | `CONTEXT_AUDIT_JOB_BUDGET_USD`, default `2.0` | Each job's cap in USD, always a hard stop (`ERR_BUDGET_OVERRUN`) whatever `BUDGET_OVERRUN_MODE` says: nobody watches a scheduled job. For the variable, `0`, non-finite or invalid values fall back to `2.0` |
| `context_audit_max_jobs_per_tick`: 0 to 50, or `null` | `CONTEXT_AUDIT_MAX_JOBS_PER_TICK`, default `3` | Jobs one tick starts for the organization. Its due repositories left over wait for the next tick; `0` starts none |

They are set the way the interval is, with the `admin` tier, and `null` hands a cap back to the deployment. The dashboard has no field for them in this release:

```bash
curl -X PATCH http://localhost:8200/api/v1/orgs/acme \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"settings": {"context_audit_budget_usd": 5, "context_audit_max_jobs_per_tick": 2}}'
```

Each organization's cap is its own: one that reached its cap does not hold back another, so one tick starts at most the sum of the caps of the organizations on the schedule.

The fleet-wide caps still apply to every job: `MAX_BUDGET_USD`, `DAILY_BUDGET_CAP_USD` and `MONTHLY_BUDGET_CAP_USD` ([configuration.md](configuration.md)). `RATE_LIMIT_PER_AUTHOR_PER_HOUR` does not: the schedule's actor `embry0:context-audit` is exempt from it (`RATE_LIMIT_EXEMPT` in `embry0/orchestration/dispatch_actors.py`), so the per-tick cap, one live job per repository and the per-job cap are what pace it. The schedule asks the gate before each start and stops at its first refusal, which counts as no attempt.

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

An organization can have each audit's fixes opened as a pull request ([ADR 0022](adr/0022-context-audit-pull-requests.md)). The setting is `context_audit_open_prs`, `false` by default, and needs the `admin` tier; in the dashboard it is the **Context audit pull requests** switch on the organization's Settings tab:

```bash
curl -X PATCH http://localhost:8200/api/v1/orgs/acme \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"settings": {"context_audit_open_prs": true}}'
```

With it on, a job that finds outdated claims takes the patch from its report, the one it checked with `git apply --check`, commits it on its own branch and pushes it through the git proxy. At completion embry0 opens the pull request: titled with the number of claims it corrects, its body the report's findings table, labelled `embry0`, and asked about in the merge-approval chat when the repository has merge approvals on. The pull request changes only instruction files, and a person merges it.

One is open per repository at a time: while an earlier audit's pull request is open, a later audit only reports. An audit that finds nothing, a report with no patch, and a patch that no longer applies open nothing; the findings stay in the report. The Context audit tab links each audit's pull request.

## Findings offered to owners

Each row of an audit's findings table is also offered to the repository's owners as an [owner proposal](api.md#owner-proposals) of kind `finding`; accepting one starts a job narrowed to that file, which opens a pull request for that claim. The findings offered are those of the repository's newest audit that finished its work:

- **Its pull request merged:** none, until the repository's next audit finishes. The pull request may have fixed any of them, so they are not offered, and an older audit's findings are never offered in their place: they may be fixed too. A merge changes the instruction files, so with the schedule on the repository is due again at once.
- **Its pull request was closed unmerged:** its findings are still offered. Nothing reached the default branch through it, so the claims still stand; an owner who disagrees with one declines it.
- **It completed, with no pull request or one still open:** its findings are offered.

An audit that failed, or one that has not finished, is passed over: the feed keeps to the newest audit that finished its work, as above.

A finding's fix job is no audit, here as for the schedule, so the feed reads past it. While it runs, once it completes, and once its pull request merges or closes, the audit before it is still the newest, and that audit's findings are still offered, all but the one the job fixes: its owner answered that one by accepting it. The fix job's merged pull request changed that one claim, so it is not the audit's pull request merging, and its own report is never offered.

## From an MCP client

The embry0 MCP server (`embry0-mcp`) does all of it. A tool that changes something takes `confirm=true`, passed once the person has approved that call; without it the tool answers `confirmation_required` and does nothing.

| To | Call |
|---|---|
| Turn it on for an owner, every 7 days | `org_update(owner="acme", settings={"context_audit_days": 7}, confirm=true)` |
| Set the caps: 5 USD a job, 2 jobs a tick | `org_update(owner="acme", settings={"context_audit_budget_usd": 5, "context_audit_max_jobs_per_tick": 2}, confirm=true)` |
| Hand the caps back to the deployment | `org_update(owner="acme", settings={"context_audit_budget_usd": null, "context_audit_max_jobs_per_tick": null}, confirm=true)` |
| Read what it sees, and why each repository is due or not | `context_audit_status(owner="acme")` |
| Run one repository now, when it is due | `context_audit_run(owner="acme", repo="acme/widgets", confirm=true)` |
| Run one repository now, whatever the due rule says | `context_audit_run(owner="acme", repo="acme/widgets", force=true, confirm=true)` |
| Run every due repository now | `context_audit_run(owner="acme", confirm=true)`, or with `limit=2` for at most two |
| Open a pull request from each audit's fixes | `org_update(owner="acme", settings={"context_audit_open_prs": true}, confirm=true)` |
| Turn it off | `org_update(owner="acme", settings={"context_audit_days": null}, confirm=true)` |

`org_update` needs an admin-scope key and `context_audit_run` an operate-scope key; `context_audit_status` reads. `org_update` merges: the settings it names change and the rest stay. Each job a run starts is a job like any other: follow it with `job_wait(job_id=...)` and read its report with `job_deliverables(job_id=...)`.

`job_create(task=..., repo="acme/widgets", pipeline_template="context-audit")` runs the auditor on a repository too, but the schedule does not see that job's fingerprint: it records none, names no instruction files in its task and has a hard cap only if given one, so a file changed after it is not compared with it. `context_audit_run` is the way to run one by hand.

## Turning it off

Set the setting to `null`:

```bash
curl -X PATCH http://localhost:8200/api/v1/orgs/acme \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"settings": {"context_audit_days": null}}'
```

In the dashboard, press **Turn off** beside the field, then Save. No new job starts for the organization from the next tick; a job already running finishes. Pausing the organization (`PUT /api/v1/orgs/{owner}/status`) stops it too, and so does `context_audit_max_jobs_per_tick: 0`. `CONTEXT_AUDIT_MAX_JOBS_PER_TICK=0` stops it for every organization that sets no cap of its own.
