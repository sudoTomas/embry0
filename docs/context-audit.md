# Context audit

A coding agent working in a repository reads some files as instructions: `CLAUDE.md` and `AGENTS.md` at any depth, a skill, a rules directory, a handoff note. A line in one of them that went stale (a command that was renamed, a directory that moved, a rule the code no longer follows) reaches the agent as a fact, and it acts on it. The context audit finds those lines. An organization turns on a schedule, and embry0 then audits the instruction files of every registered repository under it on its own: when one of the files changes, and again after an interval once the code moved under them. See [ADR 0020](adr/0020-context-audit-schedule.md) for the decision.

The schedule is off for every organization until an admin sets it.

## What it audits

The instruction files are the paths that match one glob list, `INSTRUCTION_FILE_GLOBS` in `embry0/services/instruction_files.py`; the schedule fingerprints those files and the auditor reads the same list. `**/` stands for any number of directories, none included, and `*` stays inside one path segment:

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

## When a repository is due

Every 15 minutes the orchestrator reads, for each active organization with the setting on, the default branch of each of its active registered repositories, with the organization's credential: the newest commit, then the recursive tree when the head moved since the last read. The **fingerprint** is the sha256 over the sorted `path<TAB>blob sha` lines of the instruction files, so it changes when one is added, removed, renamed or edited, and only then.

A repository is due when:

1. it has no context-audit job yet; or
2. the fingerprint differs from the one its newest context-audit job recorded; or
3. that job is `context_audit_days` old and the branch head moved since it was started.

It is never due while one of its context-audit jobs is pending, running, awaiting input or paused, whoever started it.

The newest job is the anchor whatever became of it. A completed job counts from when it finished; a failed or cancelled one from when it started, so a failure waits a full interval, the same as a success, and the schedule never retries on every tick. A start refused before any job existed counts the same way; the orchestrator remembers it until it restarts. A change to the files is due at once in every case. A context-audit job a person started counts too; it records no fingerprint, so only its age is read.

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

The organization must be `active` and its credential must be able to read the repositories, the same credential discovery uses. Only registered, active repositories are read ([onboarding.md](onboarding.md)).

## What it may spend

| Variable | Default | What it bounds |
|---|---|---|
| `CONTEXT_AUDIT_JOB_BUDGET_USD` | `2.0` | Each job's cap, always a hard stop (`ERR_BUDGET_OVERRUN`) whatever `BUDGET_OVERRUN_MODE` says: nobody watches a scheduled job. `0`, non-finite or invalid values fall back to the default |
| `CONTEXT_AUDIT_MAX_JOBS_PER_TICK` | `3` | Jobs one tick starts, across every organization. The due repositories left over wait for the next tick; `0` starts none |

The fleet-wide caps still apply to every job: `MAX_BUDGET_USD`, `DAILY_BUDGET_CAP_USD`, `MONTHLY_BUDGET_CAP_USD`, and `RATE_LIMIT_PER_AUTHOR_PER_HOUR`, which counts the schedule's jobs under its actor `embry0:context-audit` like any person's ([configuration.md](configuration.md)). A job the budget gate refuses fails, and counts as an attempt.

## Reading what it sees

```bash
curl http://localhost:8200/api/v1/orgs/acme/context-audit \
  -H "Authorization: Bearer $API_KEY"
```

The answer lists each active registered repository with at least one instruction file: how many it has, the fingerprint and head it was read at, its newest context-audit job (id, status, date), whether the schedule would start one now and why (`first`, `changed`, `aged`, `live`, `current`, or `off` while the setting is off). Repositories the forge did not answer for are listed under `unread`, with why. The read reads the forge and starts nothing, and works with the setting off. Field by field: [api.md](api.md#organizations).

Each job the schedule starts is a job like any other: the read above names each repository's newest one, `GET /api/v1/jobs?repo=<owner>/<name>` lists it among the repository's jobs, and `GET /api/v1/jobs/{job_id}` carries its report. Its `job_created` audit row names the actor `embry0:context-audit`, and its `pipeline_config` records the `context_fingerprint` and `head_sha` it was started for.

## Turning it off

Set the setting to `null`:

```bash
curl -X PATCH http://localhost:8200/api/v1/orgs/acme \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"settings": {"context_audit_days": null}}'
```

No new job starts for the organization from the next tick; a job already running finishes. Pausing the organization (`PUT /api/v1/orgs/{owner}/status`) stops it too, and `CONTEXT_AUDIT_MAX_JOBS_PER_TICK=0` stops it for every organization.
