# API Reference

Two-level API: **low-level graph execution** + **high-level job management**. All endpoints live under `/api/v1/` behind the frontend's nginx on port 8200.

Authentication: send `Authorization: Bearer <API_KEY>` on every request (omitted from the examples below for brevity). An empty `API_KEY` is only tolerated when a dev-mode flag is set — see [configuration.md](configuration.md).

See [architecture.md](architecture.md#api-structure) for the full route map.

## Issues

```bash
# Create an issue (with optional auto-triage and GitHub sync)
curl -X POST http://localhost:8200/api/v1/issues \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"title": "Fix the auth bug", "repo": "owner/repo", "auto_triage": true, "github_sync_enabled": true}'
# -> 201 with the issue plus "job_id", "dispatch_error_code" and "dispatch_error".
#    With auto_triage, a non-null "dispatch_error_code" ("ERR_DISPATCH_FAILED")
#    means the issue was created but NO job started (it is back in "open").
#    Check it — the status code is 201 either way, so a retry never creates the
#    issue twice. "dispatch_error" is a safe sentence; the cause is in the
#    orchestrator log. POST /issues/{issue_id}/triage retries and reports the
#    same three fields (status 200).

# List issues
curl http://localhost:8200/api/v1/issues -H "X-Requested-With: XMLHttpRequest"

# Get issue with children and jobs
curl http://localhost:8200/api/v1/issues/{issue_id} -H "X-Requested-With: XMLHttpRequest"

# Answer a triage question
curl -X POST http://localhost:8200/api/v1/issues/{issue_id}/inputs/{input_id}/answer \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"answer": "The bug is in the JWT validation logic"}'
```

## Jobs (high-level)

```bash
# Create a job
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "task": "Fix the auth bug in login.py"}'

# List jobs
curl http://localhost:8200/api/v1/jobs

# Cancel a job
curl -X POST http://localhost:8200/api/v1/jobs/{job_id}/cancel \
  -H "X-Requested-With: XMLHttpRequest"

# Submit a QA job (skips triage; needs branch + pipeline=qa). See docs/running-qa.md.
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "branch": "main", "pipeline": "qa",
       "qa": {"acceptance_criteria": ["home page loads with no console errors"]}}'

# Run a specialist agent: name its template (docs/specialist-agents.md).
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "pipeline_template": "kintsugi-trace",
       "task": "Trace the cracks in this repository."}'

# QA artifact endpoints (presigned-GET redirects + SSE log stream).
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts
curl http://localhost:8200/api/v1/jobs/{job_id}/qa/attempts/1/result
curl http://localhost:8200/api/v1/jobs/{job_id}/artifacts/screenshots/latest
curl "http://localhost:8200/api/v1/jobs/{job_id}/artifacts/logs/gateway?follow=true"

# The job's verification result (docs/verification.md): 404 when the verify node never ran.
# Read tier, like the rest of the job's routes.
curl http://localhost:8200/api/v1/jobs/{job_id}/verification

# One agent's stored conversation for the job (one row per job and agent type,
# merged across the agent's runs), secrets redacted: 204 when only a CLI session
# blob is stored, 404 when the agent kept no session. Needs an operator or admin
# key, or an operator session; a read key gets 403.
curl http://localhost:8200/api/v1/jobs/{job_id}/agents/developer/session
```

A job's `area` (on the list and on `GET /jobs/{job_id}`) is the part of a monorepo the pull request embry0 opened for it changes, from the repository's `.embry0/areas.yml` ([areas](areas.md)); `null` when the repository names none or the change falls in none. `GET /repos/{repo}/areas` reads that file, and `POST /repos/{repo}/areas/map` starts the agent that writes it as a pull request ([map areas](areas.md#map-areas)).

`GET /repos/{repo}/slop-meter?area=` is the repository's [Slop-O-Meter](glossary.md#slop-o-meter) (after [Slop-O-Meter](https://github.com/slop-o-meter/slop-o-meter)'s published slop score algorithm): `meter` (the repository's `{score, level, name, provisional, computed_at}`, `null` before its first score), `areas` (each scored area's `{area, meter}`), `area` (the series' area, `null` for the repository), `weeks` (`{week, cost, capacity, slop, lines, score}` oldest first, in weighted lines; `score` cumulative) and `reading` (`{commits_read, commits_total, computed_at, error}`, how far the history is read). 404 for an unregistered repository or an area with no reading.

`POST /jobs/{job_id}/resume` takes `{"choice": "continue_retrying"|"merge_as_is"|"abandon", "guidance", "extra_budget_usd"}`. On a job over its budget, `continue_retrying` lets it spend one more cap past what it has spent, or `extra_budget_usd` (at most 1,000) when given; the pause's `interrupt.summary.budget` says `{spent, cap, continue_adds}` ([per-job cap](architecture.md)).

A job reads in plain words ([job briefs](superpowers/specs/2026-10-26-job-briefs-design.md)). `GET /jobs/{job_id}` carries `brief`, `{"title", "what", "why", "done", "size", "model", "written_at"}` (`size` is `small`, `medium` or `large`), written by a model in the background from the job's task and what embry0 knows about it, or `null` until it is written (or while it was written from an older task or prompt); and `status_words`, `{"state", "headline", "next", "actions", "until"}`: what is happening now and what a person can do, worked out from the job's status, stage, latest pause (with its options and, over budget, `summary.budget`), account wait and error, with no model (`until` is a wait's end in UTC ISO). `GET /jobs` carries each row's `brief_title`, left out until the brief is written. The world's parties, inbox, loot, quest log and quest scroll take the brief's title as their `title` when there is one.

A code job carries its finish line, `done_when`, on `GET /jobs/{job_id}` ([done when](superpowers/specs/2026-10-26-done-when-design.md)): `{"items": [{"id", "done", "shown_by"}], "out_of_scope", "source", "edited_by", "edited_at", "report"}`.
- **Who sets it.** `source` is `triage` (drafted for a code job), `ticket` (the ticket's `## Acceptance criteria`) or `person`.
- **The report.** `report` is the latest coding run's report on it as embry0 checked it: `{"items": [{"id", "met", "evidence"}], "unmet", "reported", "checked_at"}`, or `null` until a run reported.
- **No list.** `null` for a job that has none.
- **Editing.** `PUT /jobs/{job_id}/done-when` (`{"items": [{"done", "shown_by"}], "out_of_scope"}`, 1–6 items) replaces it while the job is pending, running, paused or awaiting input. The response is the new list (`source: "person"`, the report dropped), and the edit is audited as `job.done_when_edited`. It answers 409 for a finished job and 422 for an empty list. The next coding run works to the new list.

A paused job that waits for an LLM account carries `account_wait` on `GET /jobs/{job_id}` (lists say only `paused`): `{"provider", "reason", "until", "model", "agent", "chosen", "watch", "mode"}`, with `until` in UTC ISO; `null` for every other job ([spent account waits](providers.md#spent-account-waits)). `POST /jobs/{job_id}/resume` on such a job goes on now whatever the `choice` ("Try now", answering `{"status": "resuming", "choice": "account_ready"}`), except `abandon`, which cancels it. It answers 409 when the job no longer waits, and 503 while embry0 shuts down. A job whose account is still spent is parked again. `mode` says how a job goes on: `resume` from its last checkpoint, or `relaunch` from its beginning, which is how qa, onboard and pr-review jobs and any job held before it started go on.

## The world

The guild screen's API ([the guild](world.md), [spec](superpowers/specs/2026-10-25-world-design.md)). Sending a party is `POST /proposals/{id}/accept` or `POST /jobs`; these routes add only the reading and the judging.

- `GET /api/v1/world` (read): `characters` (with `state`: idle, travelling, working, waiting, returning, defeated, recalled or following; the `job_id` it is drawn at; `stats`; `idle_too_long`; `can_quest` and `quest_template`), `parties` (jobs out now and back in the last three minutes: `leader`, `party`, `working`, `lead_id`, `stuck`, `failure`; a defeated one also its `error_code` and `error_message`, the message as the job stored it, at most 2,000 characters; a waiting one held by a spent account its `account_wait`: `provider`, `reason`, `until`, `model`, `agent`, `chosen`), `quests` (the board: each proposal with `level` 1-126, `verb`, `creature`, `leader`; a proposal judged [slop](glossary.md#slop) is a `slop` lead: verb `Clean up`, creature `slop_ooze`, leader `slopmaster`), `dungeons` (one [Slop Dungeon](glossary.md#slop-dungeon) per repository and area with open slop or recent cleanups: `repo`, `area`, `score` 0-100, `open`, `cleaned` in the last 30 days, and `meter`, its [Slop-O-Meter](glossary.md#slop-o-meter) reading `{score, level, name, provisional, computed_at}` or `null`; an area at Spaghetti Bolognese or worse has one even with no open slop; worst first), `inbox` (finished quests a person sent, or that brought back a pull request, not yet judged, each with its `failure` and `error_code`), `loot`, `recent`, `regions`, `frontier`.
- `GET /api/v1/world/characters/{agent_type}` (read): the character, its `stats` and its `quest_log` (each job it ran in: outcome, pull request, its share of cost and time, `rating`, `verdict`).
- `GET /api/v1/world/quests/{job_id}` (read): the scroll: `report`, `failure`, `error_code`, `error_message`, `pr` (files, additions, deletions, from the forge when it answers in time), `merge_approval`, `verification`, `party`, `fallbacks` (the runs that ran on a [provider fallback](providers.md#provider-fallback) model: `agent_type`, `model`, `instead_of`, `provider`, `reason`, `until`, `runs`), `account_wait` (what a paused quest waits for, as on its party; `null` otherwise), `work_nature` (the slopometer's verdict on the job: `nature` bug, slop or noise, `slop_score`, `kinds`, `evidence`, and a Slopmaster cleanup's `cleanup` `{added, removed}`; `null` when unclassified), `xp` per character and skill (a slop cleanup's pull request is the Slopmaster's, and its merge adds `cleaned` experience), `cost_usd`, `seconds`, `evaluation`. A `failure` anywhere is the reason in words; an account failure (a spent usage window, a rate limit, a refused token) quotes the provider's own words when the job kept them (`the LLM account it ran on refused it: weekly limit · resets ...`).
- `PUT /api/v1/world/quests/{job_id}/evaluation` (`{"rating": "good"|"needs_work"|"bad", "verdict": "merged", "note"}`, any of them): 409 while the quest is out.
- `POST /api/v1/world/quests/{job_id}/send-back` (`{"notes", "run_with"}`, 202): a pr-followup job on the quest's GitHub pull request branch with the notes; 409 without one, or when the pull request is not open.
- `POST /api/v1/world/quests/{job_id}/reject` (`{"note", "close_pr"}`): records the rejection; with `close_pr`, closes the pull request with the note as a comment.
- `POST /api/v1/world/quests/{job_id}/run-again` (`{"run_with"}`, 202): starts the quest again as a new job from its launch record (same repo, task, pipeline and settings; a Run with choice replaces the old one) and records the new job as the quest's follow-up, so it leaves the inbox. 409 while the quest is still out, or for a job with no launch record.
- `POST /api/v1/world/quests/{job_id}/dismiss`: seen, nothing to do; the quest leaves the inbox with no rating or verdict. 409 while it is still out.

## Run with

Every route that starts agent work takes an optional `run_with`: `{"model": "<model id>", "credential_id": "<stored LLM key>"}`, either or both ([the design](superpowers/specs/2026-10-25-run-with-design.md)). Every agent of the job runs the model, except one the request names itself in `agent_models`; the key serves its provider for the job ([precedence](credentials.md)). Leaving it out is the usual run.

- Taken by `POST /jobs` (every pipeline), `POST /issues/{id}/triage`, `POST /cases/{id}/accept`, `POST /proposals/{id}/accept`, `POST /repos/{repo}/setup/propose`, `POST /standards/{owner}/{kind}/audit/{repo}`, `POST /orgs/{owner}/context-audit/run`, `POST /orgs/{owner}/actions` (onboard) and `POST /watcher/run`.
- Refused with 422, before anything starts, when the model is unpriced or disabled, the key is not a usable LLM key, it is not bound to the repository, its owner or the global pool, or it is not of the model's provider.
- The job's `pipeline_config.run_with` says what was chosen (`model`, `credential_id`, `provider`, `credential_label`); the job page shows it.
- `GET /api/v1/run-with/keys?repo=` (read): the keys a job on `repo` may use, `{"keys": [{"id", "label", "provider", "scope", "status"}]}`, each once from its most specific binding (`repo`, `owner`, `global`). Without `repo`, the global ones. Never a secret.

## Runtime configuration

```bash
# Budget controls
curl http://localhost:8200/api/v1/config/budget
curl -X PUT http://localhost:8200/api/v1/config/budget \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"max_budget_per_job_usd": 25.0}'

# Context injection
curl http://localhost:8200/api/v1/config/context
curl -X PUT http://localhost:8200/api/v1/config/context \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"system_context": "Use TypeScript strict mode."}'

# Model providers (admin): the fallback order, and the local model server's
# run cap and the watcher's local-first model (docs/providers.md#local-models)
curl http://localhost:8200/api/v1/config/provider
curl -X PUT http://localhost:8200/api/v1/config/provider \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"fallback_models": ["grok-4.7", "custom/my-model"], "local_max_runs": 1, "watcher_local_model": "custom/my-model"}'

# Sandbox profiles
curl http://localhost:8200/api/v1/sandbox-profiles
curl -X POST http://localhost:8200/api/v1/sandbox-profiles \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "java-17", "base_image": "embry0-sandbox-java:17", "memory": "12g"}'

# OpenRouter's live model list: read it now (admin); it is read daily by itself
curl -X POST http://localhost:8200/api/v1/config/providers/openrouter/catalog/refresh \
  -H "X-Requested-With: XMLHttpRequest"
# -> {"source": "live", "models": 380, "fetched_at": "...", "skipped": {"batch": 74, ...}, "last_error": null, ...}
```

`GET /api/v1/config/models` lists every model a provider offers with its `enabled`, `tool_calling` and `pricing_usd_per_mtok`. OpenRouter's entry lists its [live model list](providers.md#openrouters-live-model-list), each model with its `name`, `vendor` and `context_length` too, and carries `catalog`: where the list comes from (`source`: `live` or `static`), when it was read, what it left out by reason, and the last failed read.
`PUT /config/provider` takes any subset of its fields. `local_max_runs` is 1 to 64 (422 outside). `watcher_local_model` is `""` (off) or a `custom/<model>` with the custom provider enabled; anything else is 422.

## Skills

The skills agents load, and where they come from ([architecture](architecture.md#skills-store-and-mounts)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes an audit row (`skill_source.created`, `skill_source.updated`, `skill_source.synced`, `skill_source.deleted`, `inline_skill.written`, `inline_skill.deleted`).

A skill is named `<plugin_name>:<skill>`, for example `superpowers:writing-plans`: the plugin name from its source's `.claude-plugin/plugin.json` (the source id when there is none), then the skill's directory or frontmatter name. A source is `git` (a public repository pinned to a tag, branch or commit), `path` (a directory inside the orchestrator container) or `inline` (the SKILL.md documents written through `/skills/inline`, published as the builtin `local` source). Two sources are builtin: `superpowers` (git, pinned in code) and `local` (inline).

| Endpoint | Tier | Body and answer |
|---|---|---|
| `GET /api/v1/skills` | read | The catalog, sorted by name: `[{name, source_id, description, available, status}]`. `available` is true when the source is `ready` and enabled; `status` is the source's, or `disabled` |
| `GET /api/v1/skills/sources` | read | Every source row (`id`, `kind`, `url`, `ref`, `resolved_sha`, `plugin_name`, `skills`, `status`, `error`, `enabled`, `is_builtin`, `synced_at`, ...) plus `syncing` |
| `GET /api/v1/skills/sources/{id}` | read | One source; 404 when there is none |
| `POST /api/v1/skills/sources` | admin | `{id, kind, url, ref, enabled}` with `kind` `git` or `path`; 201 with the row in `pending`, and a sync starts in the background. 422 for a refused source (`kind: inline`, which only the builtin `local` source is, `id` not `^[a-z][a-z0-9-]{0,39}$`, a git url that is not `https://`, carries credentials, or names an IP literal or a local name, a relative path, any `credential_id`); 409 when the id exists |
| `PATCH /api/v1/skills/sources/{id}` | admin | `{ref?, enabled?}`; a `ref` change starts a sync. 403 for a `ref` on a builtin (its pin is in code); 422 for a `ref` on a non-git source |
| `POST /api/v1/skills/sources/{id}/sync` | admin | 202 `{"id", "status": "syncing"}`; 409 while a sync of that source runs |
| `DELETE /api/v1/skills/sources/{id}` | admin | Removes the row and its store directory. 403 for a builtin; 409 with `agents` and `templates` naming what still references its skills |
| `GET /api/v1/skills/inline` | read | The inline skill rows: `name`, `description`, `body`, `created_at`, `updated_at` |
| `PUT /api/v1/skills/inline/{name}` | admin | `{description, body}`, upsert; `body` is the SKILL.md content below the frontmatter, at most 64 KiB. Re-syncs `local`. 422 for a name not `^[a-z][a-z0-9-]{0,63}$` |
| `DELETE /api/v1/skills/inline/{name}` | admin | Re-syncs `local`. 409 with `agents` and `templates` while anything references `local:<name>` |

```bash
# Add a pinned public skill repository; it answers "pending" and syncs in the background
curl -X POST http://localhost:8200/api/v1/skills/sources \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"id": "team", "kind": "git", "url": "https://example.com/acme/skills.git", "ref": "v1.2.0"}'

# Write a skill in place; agents name it local:release-notes
curl -X PUT http://localhost:8200/api/v1/skills/inline/release-notes \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"description": "Write release notes from merged PRs", "body": "# Release notes\n\nList each merged PR ..."}'
```

A sync failure never fails the request that started it: the source shows `status: error` and the redacted message in `error`, and the tree it had stays in the store. A sync clones or copies into a staging directory and renames it into place, so a sandbox created mid-sync sees the old tree or the new one.

**Validation and effective skills.** `POST /agents`, `PUT /agents/{type}`, `POST /agents/{type}/skills/{name}` and template saves check only the names the write adds: every name on a create, and on an update only the names the stored agent or template does not have yet, so names already saved keep saving even while the catalog is empty. An added name no source knows is a 422 that names the closest known names. A known name whose source is pending, disabled or in error, or a name prefixed by such a source before its first sync lists it, is accepted, and the response lists it in `warnings`; these write responses always carry `warnings`, empty when there is nothing to say.

**Repository skills.** A name without a colon, for example `commit-standard`, is a skill the target repository ships under `.claude/skills`. The catalog cannot list those, so such a name is never checked, never warned about and never an orphan: every write accepts it, and it passes through to the agent's allowlist as written. List it whenever the agent's effective skills are non-empty and it should keep loading.

An agent runs with its *effective* skills: the definition's list, then the template node's delta (`config.skills`), then triage's (`pipeline_config.agent_skills[<type>]`). A delta is `{"add": [...], "remove": [...]}` with `remove` applied first, or `"remove": "*"` to drop everything before the adds; a bare list is the older replace form, read as `{"add": list, "remove": "*"}`. When the effective list is non-empty it is an allowlist, so a skill the target repository ships under `.claude/skills` loads only when it is listed too; when it is empty, the repository's own skills load unfiltered.

## Tools

`GET /api/v1/tools` (read) lists the tool names an agent definition can name, as `[{name, source, description}]`: the Claude Code builtins under `source: "builtin"` (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`, `Task`), each registered browser backend's MCP tools under `source: "mcp:<backend>"` (`embry0/workflows/qa/browser_backends.py`), and the log tool under `source: "mcp:context"`.

## Agents

Agent definitions: the model, tools, skills and prompt each agent type runs with, and how it looks on the dashboard. Reads need any authenticated principal; every mutation needs the `admin` tier.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/agents` | read |
| `GET /api/v1/agents/{type}` | read |
| `POST /api/v1/agents` | admin |
| `PUT /api/v1/agents/{type}` | admin |
| `DELETE /api/v1/agents/{type}` | admin; 403 for a builtin |
| `POST /api/v1/agents/{type}/reset` | admin |
| `POST /api/v1/agents/{type}/skills/{name}` | admin |
| `DELETE /api/v1/agents/{type}/skills/{name}` | admin |

**Identity.** Every definition carries four identity fields, which `POST` accepts, `PUT` changes and both reads return:

| Field | Default | Rule |
|---|---|---|
| `display_name` | `""` | At most 60 characters; empty shows the slug |
| `avatar` | `golem` | One of the roster ids |
| `color` | `slate` | One of the palette ids |
| `tagline` | `""` | At most 140 characters |

The roster, the palette and the builtins' own values are in [`docs/contracts/identity.json`](contracts/identity.json), written from `embry0/agents/identity.py`; an id outside them is a 422 that lists the allowed ones. These columns are not nullable, so a `PUT` that sends one as `null` is a 422. `POST /agents/{type}/reset` restores a builtin's identity with the rest of the row.

**Agents in pipeline templates.** A template node's `agent_type` is one of the fixed node types (`triage`, `developer`, `slopmaster`, `reviewer`, `review`, `qa`, `output`) or the `type` of any agent definition, builtin or custom. `slopmaster` runs on the developer node, as the builtin `slop-cleanup` template uses it; plan_route picks that template for a code job triage judged [slop](glossary.md#slop), unless the job named a template. A custom type runs on the generic agent node with its row's model, tools, skills and prompt; template and triage skill deltas apply to it like any other step. Saving a template that names a type with no row answers 422 (`unknown agent_type`). Deleting an agent that a saved template names does not fail the delete: at plan time the template is skipped with the `plan_route_template_invalid` log and the job runs the kind's default template.

**One skill at a time.** `POST /agents/{type}/skills/{name}` adds one skill and `DELETE` removes one, each returning the agent; adding a skill it already has, or removing one it does not, changes nothing and writes no audit row. Otherwise each writes `agent.skill_added` or `agent.skill_removed`. An unknown name is a 422 naming the closest known ones; a name from a source that is not ready is added and listed in `warnings`. `GET /agents/{type}` also returns `orphan_skills`: the agent's catalog-shaped skills that no source lists (repository skills are never orphans). They keep running, and a write that keeps them is accepted; only adding a name no source knows is refused. Builtin agents keep operator-added skills across restarts: the startup seeders write a builtin's skills only when they create its row, and `POST /agents/{type}/reset` returns them to the seed.

```bash
# Give the developer one more skill
curl -X POST http://localhost:8200/api/v1/agents/developer/skills/superpowers:writing-plans \
  -H "X-Requested-With: XMLHttpRequest"
```

## Progression

`GET /api/v1/progression` (read tier) answers the account, its characters' skills and its achievements ([the design](superpowers/specs/2026-10-24-progression-and-achievements-design.md)): `{"account": {"xp", "total_level", "characters", "quest_points", "divine_trim"}, "skills": [{"id", "name", "group", "echo"}], "characters": [{"agent_type", "level", "xp", "total_level", "best_skill", "tier", "capes", "rares": [{"id", "at"}], "quest_cape", "skills": [{"stat", "xp", "level", "level_xp", "xp_to_next"}]}], "achievements": [...], "achievement_categories": [...], "quests": [...], "diaries": [...], "recent": [...], "computed_at"}`. Every agent type, built-in or custom, is a character with the same 23 skills (`skills` lists them in sheet order, each in its group, `build`, `verify`, `investigate` or `care`, with the classic skill it `echo`es). A run that ended without an error earns each of its tool calls in the skill that call trains (`traces.skill_actions`, read from `tools_called` for runs before it), and an outcome earns more: a merge, an opened pull request, a QA job, a finished review or triage, a context audit and its outdated claims, a question asked, a merge after a failure. The watcher's and the schedules' work earns no levels; only what it achieved counts toward achievements. Experience is recomputed from the jobs, traces, audit log, healing case events and watcher runs, never stored, at most once a minute; a level follows the classic curve from 1 to 99, `level_xp` is where the current level starts, and `xp_to_next` is `null` at 99. A character's `level` is its one number, 1 to 126: the combat-level formula with its best skills in the combat slots, `floor(0.25 * (s3 + s4 + floor(s5 / 2)) + 0.325 * (s1 + s2))` over its skill levels best first. Its `total_level` is its 23 levels added up (23 to 2,277), `best_skill` its highest level, and `tier` the gear that level wears: `bronze` from 1, `iron` 15, `steel` 30, `mithril` 45, `adamant` 60, `rune` 75, `divine` 90. `capes` lists its skills at 99, the most experience first (the first is the skill cape it wears), and `rares` the rares its work earned, first earned first: `party-hat` for a merge on 25 December, `holiday-mask` for one on 31 October, `white-party-hat` for the first run of a new year (UTC dates). The account's `xp` and `total_level` add up its characters'.

`achievements` holds the fifty entries of the achievement diary ([the design](superpowers/specs/2026-10-25-achievement-diary-design.md)), each `{"id", "name", "category", "kind", "icon", "what_counts", "flavour", "secret", "state", "tier", "tiers_reached", "value", "next_threshold", "thresholds", "unlocked_at", "latest_at", "evidence"}`, and `achievement_categories` their eight categories in page order, each `{"id", "name", "blurb"}`. Every metric is an outcome the whole account achieved (merged pull requests and how they merged, production errors found healed, issues finished, owner answers, outdated claims found and corrected, reviews and QA runs, changes the verification gate stopped, comebacks, character levels); nothing counts spend, runs or jobs started. A `tiered` entry has seven thresholds, Bronze to Divine, and `tier` names the highest reached; an `objective` has one, and `tier` reads `complete` once it is met. `state` is `unlocked` from the first tier, `discovered` before it, and `secret` for a secret entry before its first signal, which then carries `"name": "???"`, its `category` and nothing else. `unlocked_at` is when the first tier was crossed and `latest_at` the highest; `evidence` is `{"label", "url", "at"}` for the step that last moved the metric (a pull request, a repository, an error's title), or null. `recent` lists the latest 20 events, newest first: `{"kind": "level", "at", "agent_type", "stat", "level"}` for a level a character's skill reached, `{"kind": "agent_level", "at", "agent_type", "level"}` for its own level rising, `{"kind": "rare", "at", "agent_type", "id"}` for a rare it earned, `{"kind": "achievement", "at", "id", "name", "tier"}` for a tier unlocked, `{"kind": "quest", "at", "id", "name"}` for a quest completed and `{"kind": "diary", "at", "repo", "tier"}` for a diary tier completed.

`quests` holds the four quests, each `{"id", "name", "points", "reward", "state", "requirement", "requirement_met", "where", "steps": [{"text", "done_at"}], "completed_at"}`: `state` is `not_started`, `in_progress` or `complete`, `where` the repository or issue whose steps got furthest, and a completed quest's XP reward is already in its characters' levels. The account's `quest_points` adds up the completed quests' points; every quest done gives the highest total level `quest_cape: true`, and Divine Slayer sets `divine_trim`. `diaries` holds a diary per repository with at least one task done, furthest along first, each `{"repo", "tiers": [{"tier", "tasks": [{"text", "done_at"}], "completed_at"}]}` for `easy`, `medium`, `hard` and `elite`. An orchestrator that did not start the service answers 503.

## Owner proposals

Work embry0 asks a repository's owners to start ([the design](superpowers/specs/2026-10-24-owner-proposals-design.md)). A chat assistant reads the feed through the MCP server, messages an owner, and answers with the owner's reply.

- `GET /api/v1/proposals?owner=&kind=&since=&include_held=` (read): `{"proposals": [{"id", "kind", "repo", "area", "owners", "title", "summary", "details", "action", "url", "created_at", ...}], "count"}`, newest first. `kind` is `case` (a watcher finding at stage `tracked` on an enabled stack), `issue` (an issue open with no job) or `finding` (one row of a repository's newest completed context audit's findings table, which adds `path`, `line`, `claim`, `fix` and `audit_job_id`; its id is a hash of the repository, file and claim, so the next audit finding the same thing asks nothing new), or a lead embry0 found by itself: `ticket`, `ci`, `alert`, `flaky` or `review` ([leads](leads.md)), which adds `severity`, `effort`, `paths`, `labels`, `last_seen` and `source`. `area` is the part of a monorepo it is about, from the repository's `.embry0/areas.yml` ([areas](areas.md)), or `null`. A `case` the watcher judged with the slopometer adds `nature`, `slop_score` and `slop_kinds`; a slop one reads as a cleanup (its `summary` says slop, its `action` is the Slopmaster's cleanup). `owners` is that area's owners when it names some, else the repository's `owners` preference, else the users its `CODEOWNERS` names for `*` (teams skipped), read with its organization's credential and kept an hour. `owner` filters by GitHub login, without regard to case or a leading `@`.
- Each proposal also carries `brief` and `held` ([proposal briefs](superpowers/specs/2026-10-25-proposal-briefs-design.md)). `brief` is the proposal in plain words, `{"title", "problem", "fix", "size", "model", "written_at"}` (`size` is `small`, `medium` or `large`), written by a model in the background from what embry0 holds about it, or `null` until it is written or while it was written from an older version of the proposal. `held` is true for a `ticket` lead below `PROPOSALS_TICKET_MIN_READINESS` (60): `GET /proposals` leaves held ones out unless `include_held=true`, so an assistant reading the feed does not ask them; they are still read, accepted, declined and snoozed by id.
- `GET /api/v1/proposals/{id}`: one proposal still asked; 404 once it is answered or has moved on (a case accepted on the dashboard, an issue triaged).
- `POST /api/v1/proposals/{id}/accept` (`{"by": "<login>"}`, 202): starts it through its kind's own path, `POST /cases/{id}/accept` or `POST /issues/{id}/triage`, or for a finding a context-audit job narrowed to its file that opens a pull request for that claim (whatever the organization's `context_audit_open_prs`: the owner asked for this one; refused 429 while the budget gate refuses). The answer is recorded only once the start worked; the response carries the `job_id`.
- `POST /api/v1/proposals/{id}/decline` (`{"by", "reason"}`) and `/snooze` (`{"by", "until"}`, a future time): it is not asked again, or not until `until`. Every answer is a `proposal.answered` audit row.


## Decisions

Every choice waiting on a person, a quest on offer or a quest to review, in one shape that the dashboard's cards and a chat's cards both render ([the design](superpowers/specs/2026-10-27-decisions-design.md)). Read only: the actions a decision lists stay on their own routes (owner proposals above, the world's quest routes, merge approvals).

- `GET /api/v1/decisions?stage=offered|done|all&owner=&repo=&include_held=true&limit=100` (read): `{"decisions": [...], "count"}`, the worst first (`bad`, `warn`, `good`, `info`), then the newest. Each is `{"id", "stage", "kind", "kind_label", "verdict": {"label", "tone"}, "title", "repo", "area", "at", "facts": [{"label", "value", "tone"}], "why", "yes_starts", "actions", "owners", "held", "assigned_to", "links", "details"}`.
  - `id` is `offer:<proposal id>` for a quest on offer (the owner proposals feed, with its brief) or `done:<job id>` for a quest to review (the world's inbox: finished in the last 14 days and not yet rated).
  - `verdict`: a quest on offer reads its severity, else its kind's name. A quest to review reads `Ready to merge` (checks passing, the merge recommendation is merge, every done-when item met), `Checks failing`, `Needs a look`, `Failed`, `Recalled`, `Merged`, `Closed` or `Report ready`.
  - `facts`: at most six, in a fixed order, with no empty ones.
  - `actions`: on offer `start`, `decline`, `later`, `send_to`; to review, what the quest allows (`merge`, `send_back`, `reject`, `run_again`, `good`, `bad`, `dismiss`), then `send_to`.
  - `owner` keeps the quests on offer a GitHub login owns; `include_held=false` leaves out what a chat does not ask.
- `GET /api/v1/decisions/{id}`: one decision by its id; 404 for an answered proposal or an unknown job.

## Hand-offs

Send a decision to the teammate better placed to make it; it is then theirs to decide ([the design](superpowers/specs/2026-10-27-decisions-design.md)).

- `GET /api/v1/people`: who a decision can go to, everyone who signs in (`operator` or `admin`), as `{"people": [{"id", "email", "name", "github_login", "tier"}]}`.
- `PUT /api/v1/me/github-login` (`{"login": "octocat"}`, empty to clear): the GitHub login you are, which owners are named by. A login belongs to one person (409 otherwise). `PUT /api/v1/users/{id}/github-login` does the same for someone else (admin).
- `POST /api/v1/decisions/{id}/send-to` (`{"to": "<email>", "note": "..."}`, 201): opens a hand-off to someone else who signs in. A hand-off already open for that decision closes as `passed`. It writes a `handoff.created` audit row and emits the `handoff.created` event.
- **What the recipient may do.** The recipient may take every action the decision lists, including merge (merge approvals admit them for that quest's approval only).
- **How it closes.** Whoever answers the decision closes its open hand-off as `decided`, with the outcome: `start`, `decline`, `later`, `merge`, `send_back`, `reject`, `run_again`, `good`, `bad` or `dismiss`. This emits `handoff.closed`.
- `GET /api/v1/handoffs?for=me|all`: the open hand-offs sent to you, or the latest of all.
- `POST /api/v1/handoffs/{id}/return` (`{"note"}`, by the recipient) and `/cancel` (by the sender), or either by an admin: the hand-off closes as `returned` or `cancelled`.
- Each decision carries `assigned_to`, its open hand-off: `{"handoff_id", "email", "name", "github_login", "from", "note", "at"}`, else `null`. `GET /api/v1/decisions?for=me` keeps what was handed to you, plus the quests on offer that your GitHub login owns and nobody was handed.
- Who answered a proposal is now the signed-in person the server knows. A key that acts for people (a chat assistant) still names the person in `by`.

## Asks

One message to one person about one decision, tracked from `queued` to `answered`, `expired` or `withdrawn`, for a chat assistant to send ([the design](superpowers/specs/2026-10-27-decisions-design.md)). embry0 decides who is asked about what and when; the assistant only renders, sends, and passes a button press back. No model turns a reply into an answer. Off until `ASKS_ENABLED`. A `propose` key may call all of these.

- **Who is asked.** While a hand-off is open, only its person. Otherwise each owner of a decision that is not `held`: by email when a person named that GitHub login, else as `github:<login>`.
- **When.** Within `ASK_HOURS` on `ASK_WEEKDAYS` in `ASK_TIMEZONE`. A person holds at most `ASK_MAX_OPEN_PER_PERSON` (3) unanswered asks at once; the next, worst first, is released as one is answered or expires. One reminder after `ASK_REMIND_HOURS` unanswered. After `ASK_EXPIRE_DAYS` the ask expires, which answers nothing: that person is not asked about that decision again.
- **Codes.** Each decision gets one code: four letters from its id, lengthened on a collision, never given to another decision. A late press cannot land on the wrong quest.
- `GET /api/v1/asks/due?limit=` lists what to send now: queued and due, or a first reminder due within working hours. Each item is `{"id", "code", "recipient": {"email", "name", "github_login"}, "reminder", "decision", "actions", "dashboard_url"}`. `decision` is the decision as `GET /decisions/{id}` shows it. `actions` are what a chat may answer by itself: `start`, `decline`, `later` and `send_to` on offer; `merge` (through a pending merge approval) and `send_to` on a quest to review. Everything else opens `dashboard_url`. An ask whose decision was answered meanwhile is withdrawn.
- `POST /api/v1/asks/{id}/sent` (`{"message_ref"}`): the assistant sent it. A second report counts as the reminder.
- `POST /api/v1/asks/answer` (`{"code", "action", "person", "reason", "to", "note"}`): a button press by `person` (an email the assistant verified). It is answered through the paths the dashboard uses, as that person, and only when they may decide: the hand-off's person while one is open, else an owner (by their GitHub login) or an admin. Every live ask of the decision closes as `answered`, and the `ask.answered` event lists each message to update. A second press returns `{"already": true, "answer", "by"}` and runs nothing.
- `GET /api/v1/asks?limit=`: the latest asks.

## Leads

Work embry0 finds by itself and offers as owner proposals ([leads.md](leads.md), [the design](superpowers/specs/2026-10-25-leads-design.md)).

- `GET /api/v1/leads?repo=&kind=&state=open|gone|all&limit=` (read): `{"leads": [{"id", "kind", "repo", "area", "title", "summary", "details", "url", "severity", "effort", "action", "state", "first_seen", "last_seen", "gone_at", "paths", "labels", "source"}], "count"}`, most recently seen first. `kind` is `ticket`, `ci`, `alert`, `flaky` or `review`; `severity` `low` to `critical`; `effort` `small`, `medium` or `large`.
- `POST /api/v1/leads/run` (`{"repo": "owner/name", "backlog": true}`, both optional): one pass of the finders now, on that repository or on every repository the loop reads. `backlog` forces the backlog intake on (`true`) or off (`false`) whatever its interval says. Answers `{"repos": {"<repo>": {"<kind>": {"found", "new", "capped", "gone", "skipped"}}}}`.
- A lead is answered through the owner proposals routes above; accepting one starts its kind's work: a ticket through its tracker's own path, a `ci`, `alert` or `flaky` lead as an issue in embry0 whose task says what was seen, a `review` lead as a follow-up job on the pull request's branch. All take `run_with`.

## Knowledge maps

A scanner's map of a codebase or an organization, marked and sent to agents ([maps.md](maps.md), [the design](superpowers/specs/2026-10-27-knowledge-maps-design.md)).

- `GET /api/v1/maps` (read): `{"maps": [{"slug", "title", "repo", "commit", "source", "generated_at", "uploaded_at", "uploaded_by", "nodes", "edges"}]}`.
- `PUT /api/v1/maps/{slug}`: stores an `embry0.map/1` document (`Content-Encoding: gzip` or plain JSON; at most 64 MB decompressed, and 32 MB on the wire through the LAN vhost), replacing the last upload. Answers `{"slug", "nodes", "edges", "leads", "leads_closed"}`: the weed leads of parts still marked bloat, refreshed, and those of parts that left the map, closed. 422 names what is wrong with a document that is not a map; a key limited to a repository allowlist may upload only a map of a repository on it.
- `GET /api/v1/maps/{slug}` (read): the parts and links as stored, `{"slug", "title", "repo", "commit", "source", "generated_at", "kinds", "areas", "nodes", "edges"}`, with an `ETag` per upload (`If-None-Match` answers 304).
- `GET /api/v1/maps/{slug}/parts/{id}` (read): one part, `{"map", "node", "repo", "removable", "detail", "marks": [{"by", "verdict", "note", "at"}], "work", "lead": {"id", "state"} | null}`. `repo` is the repository the part lives in, or `null`; `removable` is whether an agent can remove it: a file or folder in it (a `path`), or a part with a `removal` note.
- `GET /api/v1/maps/{slug}/marks?by=` (read): `{"marks": {"<part id>": {"bloat", "keep", "unsure", "mine"}}}`, `mine` being the verdict of `by`.
- `POST /api/v1/maps/{slug}/marks` (`{"node_id", "verdict": "bloat" | "keep" | "unsure" | "clear", "note", "by"}`): sets or takes back `by`'s mark. A removable part that someone marks bloat keeps a `weed` lead open; answers `{"node_id", "marks", "lead_id"}`.
- `POST /api/v1/maps/{slug}/send` (`{"node_ids": [...], "kind": "remove" | "fix" | "analysis", "run_with", "note", "by", "together"}`, 202, at most 200 parts): one job per part, or with `together: true` one job per repository and kind (one pull request; each part's lead is accepted with that job, and `jobs` lists them as `{"repo", "kind", "parts", "job_id", "error"}`; a batch whose parts do not fit one job's task is refused with 413, never cut). `remove` (a removable part only; a whole system or repository answers with an error) records `by`'s bloat mark and accepts the part's lead (the yes is recorded as `by`'s); `fix` files an issue labelled `embry0-map` and `map:<slug>` and dispatches it; `analysis` starts a read-only `analysis-default` job. Without `kind`, a removable part `by` marked bloat is removed and any other analysed. Answers `{"sent", "failed", "results": [{"node_id", "kind", "lead_id", "job_id", "error"}]}`; a part that cannot go says why and the others still go.
- `GET /api/v1/maps/{slug}/work` (read): `{"work": {"<part id>": {"kind", "repo", "lead_id", "job_id", "job_url", "status", "pr_url", "cost_usd", "error", "by", "note", "sent_at", "finished_at"}}}`, each part's newest send with its job.
- `DELETE /api/v1/maps/{slug}` (admin): removes the map; its marks and sends stay and come back with a new upload under the same slug.

## Pipeline templates

The saved pipeline graphs the editor and the Pipelines page work on, under `/api/v1/pipelines/templates`: list, read, create, update, delete and duplicate. Every authenticated principal may read and write them; the two writes with answers worth knowing:

| Endpoint | Answer |
|---|---|
| `PUT /api/v1/pipelines/templates/{id}` | Updates the fields sent. `default_for_kind` set to a kind makes the template that kind's default route; an explicit `null` clears it. 409 when another template already holds the kind (clear it first), or when the new name is taken |
| `POST /api/v1/pipelines/templates/{id}/duplicate` | `{"name": "..."}` copies the graph and sandbox profile under the new name and answers 201. 409 when a template of that name already exists |

## Environment variables

```bash
# List global environment variables (secrets masked)
curl http://localhost:8200/api/v1/environment/global \
  -H "X-Requested-With: XMLHttpRequest"

# Set global environment variables
curl -X PUT http://localhost:8200/api/v1/environment/global \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"variables": [{"key": "DATABASE_URL", "value": "postgres://...", "var_type": "secret"}]}'

# Reveal a masked secret (audit-logged)
curl http://localhost:8200/api/v1/environment/global/DATABASE_URL/reveal \
  -H "X-Requested-With: XMLHttpRequest"

# Per-repo environment (same pattern, scoped)
curl http://localhost:8200/api/v1/repos/owner/repo/environment \
  -H "X-Requested-With: XMLHttpRequest"

# Auto-detect env vars from repo's .env.example
curl http://localhost:8200/api/v1/repos/owner/repo/environment/detect \
  -H "X-Requested-With: XMLHttpRequest"
```

A list response carries each secret as `****` plus a `masked_preview`:
eight `•` and the secret's last 2 characters when it has 12 or more, eight
`•` alone otherwise.

A PUT replaces the whole set. A secret sent back as `****`, the value the
list responses carry, keeps its stored value, so a client can write one
variable without revealing the others: the MCP `env_set` tool, and
`env_delete` on the global set, work that way and need only operator tier. A
secret sent as `****` for a key with no stored secret is refused with a 400.

Some keys are refused with a 422, matched case-insensitively. The same check drops them again at sandbox start, from stored rows and from profile `env_defaults`. These keys are the infrastructure credentials and proxy URLs, plus every variable that would redirect a sandbox's traffic or load code into its processes: proxy routing, TLS verification, the dynamic loader, `PATH`/`HOME`/`SHELL`, shell and interpreter startup hooks, and the `GIT_`, `CLAUDE_`, `BUN_` and `GROK_` namespaces. `NODE_OPTIONS` and the JVM option variables (`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`, `JAVA_OPTS`, `MAVEN_OPTS`, `GRADLE_OPTS`) are accepted, but only with memory flags. The authoritative list is `embry0/execution/auth_provider.py`. A 422 on these routes never echoes the submitted value.

## Container logs

`GET /api/v1/logs/query` reads container logs from Loki, newest first:
`container` matches a container name by substring, `containers` is a
comma-separated list of exact names, `search` is a regex line filter,
`since_minutes` goes up to 1440 and `limit` up to 1000, and `logql` replaces
all of them with a raw LogQL query. `GET /api/v1/logs/containers` lists the
container names Loki holds. The dashboard's Logs page and the MCP
`logs_query` tool use them.

Production logs carry personal data, so both routes need operator tier: an
operate or admin key, an SSO session or the master key. A read or merge key
gets a 403. The one read-scope key they admit is the context-proxy's own
service key (`svc-context-proxy`, rotated at every boot), which forwards
allowlisted queries for the sandboxes enrolled with it.

Every line returned goes through the log-text redactor
(`embry0/safety/redaction.py`), whoever asks: secrets, e-mail addresses, long
opaque runs and the values of the `LOG_REDACTION_FIELDS` fields
([configuration.md](configuration.md)) are replaced. A line longer than 2000
characters is cut and ends with `… [line truncated]`. The redactor is
regex-based, so it is not a guarantee.

```bash
curl "http://localhost:8200/api/v1/logs/query?container=api&search=error&since_minutes=30&limit=50"
```

## Credentials

Forge accounts and their bindings ([`docs/credentials.md`](credentials.md)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes a `credential.*` audit row. Secrets are write-only: no endpoint returns one, and there is no reveal endpoint.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/credentials` | read |
| `POST /api/v1/credentials` | admin |
| `PUT /api/v1/credentials/{id}` | admin |
| `POST /api/v1/credentials/{id}/rotate` | admin |
| `POST /api/v1/credentials/{id}/verify` | admin |
| `DELETE /api/v1/credentials/{id}` | admin |
| `GET /api/v1/credentials/resolve?repo=&role=forge` | read |
| `GET /api/v1/credentials/bindings?scope_kind=&scope_value=` | read |
| `PUT /api/v1/credentials/bindings` | admin |
| `POST /api/v1/credentials/import-env` | admin |
| `GET /api/v1/repos/{repo}/credentials` | read |
| `PUT /api/v1/repos/{repo}/credentials` | admin |

```bash
# Create a GitHub account (the response carries masked_preview, never the secret)
curl -X POST http://localhost:8200/api/v1/credentials \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"kind": "forge_token", "provider": "github", "label": "alice", "secret": "github_pat_..."}'

# Bind every acme repo to it
curl -X PUT http://localhost:8200/api/v1/credentials/bindings \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"scope_kind": "owner", "scope_value": "github:acme", "bindings": [{"credential_id": "cred-0123456789ab", "role": "forge"}]}'

# Dry run: which account would a job on acme/widgets use, and why
curl "http://localhost:8200/api/v1/credentials/resolve?repo=acme/widgets&role=forge" \
  -H "X-Requested-With: XMLHttpRequest"

# Move GITHUB_TOKEN* / GITLAB_TOKEN* from the orchestrator env into the registry
curl -X POST http://localhost:8200/api/v1/credentials/import-env \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"replace": false}'
```

### Discovery

`GET /api/v1/github/discoverable-repos` and `GET /api/v1/gitlab/discoverable-projects` fan out across registry credentials for that forge and the env tokens, then drop repos already registered. Behavior is in [credentials.md](credentials.md). `refresh=true` bypasses the cache.

| Field | GitHub | GitLab |
|---|---|---|
| `credential_id` | registry id, or null for an env token | same |
| `credential_label` | registry label; an env row repeats the env key | registry label, or the env key when the row came from the environment |
| `token_env_key` | the env key for an env row; empty for a registry row | not a field on this response |
| `failed` | labels whose listing raised. Never a token | same |

GitHub also returns `cached`, `truncated_env_keys` (an env key, or `credential:<label>` when a registry listing was cut short), and an advisory `push` (GitHub's `permissions.push` for the token's user, not what a fine-grained PAT grants). GitLab returns `cached` and `configured` (`false`, with an empty project list, when the deployment has no GitLab credential — not an error).

## Hosts

The machines that run the code ([`docs/hosts.md`](hosts.md)). Reads need any authenticated principal and a probe needs an operator; every other mutation needs the `admin` tier and writes a `host.*` audit row.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/hosts?source=&include_removed=` | read |
| `POST /api/v1/hosts` | admin |
| `GET /api/v1/hosts/{id}` | read |
| `PATCH /api/v1/hosts/{id}` | admin |
| `DELETE /api/v1/hosts/{id}` | admin |
| `POST /api/v1/hosts/{id}/probe` | operator |
| `POST /api/v1/hosts/import/tailnet` | admin, body `{"confirm": true}` |

```bash
# An ssh host serving acme/widgets, with its logs in journald
curl -X POST http://localhost:8200/api/v1/hosts \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"name": "web-1", "transport": "ssh", "address": "web-1.example.com:22", "roles": ["app"],
       "log_source": {"kind": "journald", "selector": "widgets.service"},
       "repos": [{"repo": "acme/widgets", "role": "app"}]}'

# One host per device of the tailnet bound at global scope
curl -X POST http://localhost:8200/api/v1/hosts/import/tailnet \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"confirm": true}'
```

`GET /api/v1/repos/{repo}` lists the repository's hosts under `hosts`, each with its `role`.

## Standards

An owner's versioned standards and each repository's mode under them ([`docs/standards.md`](standards.md), [ADR 0014](adr/0014-organization-standards.md)). Reads need any authenticated principal and an audit needs an operator; writing a standard and setting a mode need the `admin` tier and write a `standard.*` audit row.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/standards/{owner}` | read |
| `GET /api/v1/standards/{owner}/{kind}` | read |
| `PUT /api/v1/standards/{owner}/{kind}` | admin |
| `GET /api/v1/standards/{owner}/{kind}/versions` | read |
| `GET /api/v1/standards/{owner}/{kind}/repos/{repo}` | read |
| `PUT /api/v1/standards/{owner}/{kind}/repos/{repo}` | admin |
| `POST /api/v1/standards/{owner}/{kind}/audit/{repo}` | operator |

`PUT /standards/{owner}/{kind}` stores a whole new version, `{"schema", "values", "prose", "references"}`, and supersedes the active one; every key of `values` must be a role `schema.roles` declares, or the answer is a 422 listing the problems. `PUT .../repos/{repo}` sets `{"mode": "off" | "advise" | "enforce", "exceptions": [...]}` for a registered repository under the owner; every repository starts in `off`. The audit starts a read-only job on the `design-audit` pipeline template and answers 202 with its `job_id`, or 409 in mode `off`, without an active standard or the template, or while the previous audit runs (of requests sent together, one starts and the others are 409 naming its job); its completed report's `Score: N` line and findings table become the repository's `score` and `findings_open`. `GET /api/v1/repos/{repo}` lists the state under `standards`, one entry per kind.

```bash
# Put acme/widgets in advise mode, then audit it against acme's design standard
curl -X PUT http://localhost:8200/api/v1/standards/acme/design/repos/acme/widgets \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"mode": "advise"}'

curl -X POST http://localhost:8200/api/v1/standards/acme/design/audit/acme/widgets \
  -H "X-Requested-With: XMLHttpRequest"
```

## Organizations

The forge owners embry0 maintains as a whole ([ADR 0010](adr/0010-organizations.md)). Reads need any authenticated principal; every mutation needs the `admin` tier and writes an `organization.*` audit row. Registering an owner changes nothing for any repository until a person acts.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/orgs` | read |
| `GET /api/v1/orgs/{owner}` | read |
| `POST /api/v1/orgs` | admin |
| `POST /api/v1/orgs/{owner}/probe` | admin |
| `PUT /api/v1/orgs/{owner}/status` | admin |
| `POST /api/v1/orgs/{owner}/discover` | admin |
| `GET /api/v1/orgs/{owner}/repos` | read |
| `GET /api/v1/orgs/{owner}/runs` | read |
| `GET /api/v1/orgs/{owner}/needs` | read |
| `POST /api/v1/orgs/{owner}/needs/{id}/dismiss` | admin |
| `POST /api/v1/orgs/{owner}/needs/{id}/reopen` | admin |
| `PATCH /api/v1/orgs/{owner}` | admin |
| `GET /api/v1/orgs/{owner}/webhook` | read |
| `POST /api/v1/orgs/{owner}/webhook` | admin |
| `GET /api/v1/orgs/{owner}/context-audit` | read |
| `POST /api/v1/orgs/{owner}/context-audit/run` | operator, with the key's repository allowlist, like `POST /api/v1/jobs` |
| `POST /api/v1/orgs/{owner}/actions` | admin |
| `POST /api/v1/orgs/probe` | admin, stores nothing |

`POST /api/v1/orgs/{owner}/discover` answers 202 and starts one discovery run for the owner; a request while a run is in progress, or for a paused or removed owner, answers 409, and an unknown owner 404. Poll `GET /api/v1/orgs/{owner}/runs` until the newest run is `done` or `error`. `GET /api/v1/orgs/{owner}/repos` returns one fact row per repository, replaced on each run, each with `maintain_hint`, one sentence on what it takes embry0 to maintain the repository, and `maintain_steps`, the needs actions that sentence names in the order they are taken (`register`, `credential`, `hook`, `onboard`, `watch`, `setting`); both are read from the facts and the organization's probe by rules, with no model call, and a repository the latest finished run did not list asks for nothing ([ADR 0015](adr/0015-maintain-hints.md)). `runs` keeps the history of every run with its progress and summary. A row whose `run_id` is not the newest run's is a repository that run did not list; the summary's `vanished` counts them. The listing alone cannot say such a repository is gone: it pages over newest push first, so a push during the listing can move a repository onto a page already read. Each one embry0 still holds as an active registration is therefore confirmed one by one with a direct read, `GET /repos/{owner}/{repo}` with the run's own credential: a 404, a 301 (the forge redirects a renamed or transferred repository's old name) or the record under another full name confirms it gone and stamps the row's `facts.vanished_run` with the run; the same repository, the case aside, means the listing missed it; any other answer (a rate limit, a 5xx) is unknown, which is not gone. The summary's `vanished_confirmed` counts the confirmed ones. A run confirms none, and logs `org_vanished_unchecked` with the reason, when the page cap (20 pages of 100) stopped its listing with a next page unread (the summary's `listing_complete` is then false), or when more than 50 candidates are held. A listing page the forge refuses fails the run rather than ending the listing early, and a run still marked `running` when the orchestrator starts is closed as `error` (it was cut off by the restart).

An owner with the `rediscover_hours` setting on is also rediscovered on a schedule ([ADR 0016](adr/0016-rediscovery-schedule.md)): every few minutes the orchestrator starts a run, the one this endpoint starts, for each active owner whose newest run is that many hours old, and never while one is running. A failed run waits a full interval too, and so does a start refused before any run exists (the owner's credential cannot be resolved), which the orchestrator remembers until it restarts. The audit row's actor is `embry0:org-rediscovery`.

An owner with the `context_audit_days` setting on has its instruction files audited on a schedule ([ADR 0020](adr/0020-context-audit-schedule.md), [context-audit.md](context-audit.md)): every 15 minutes the orchestrator reads the default branch of each active registered repository under each such active owner, with the organization's credential, and starts a `context-audit` job for each one that is due, at most the owner's `context_audit_max_jobs_per_tick` per tick, each with a hard cap of its `context_audit_budget_usd`; an owner that sets neither gets `CONTEXT_AUDIT_MAX_JOBS_PER_TICK` and `CONTEXT_AUDIT_JOB_BUDGET_USD` ([ADR 0021](adr/0021-context-audit-caps-per-organization.md)). The jobs and their `job_created` audit rows name the actor `embry0:context-audit`. GitHub only in this release.

`GET /api/v1/orgs/{owner}/context-audit` answers what that schedule reads, whatever the setting says, and starts nothing: `{"owner", "context_audit_days", "read_at", "template": "context-audit", "repos": [...], "unread": [{"repo", "detail"}]}`. It serves each repository's last reading of its default branch, the schedule's or an earlier read's, with the jobs as they are now; `read_at` is when the oldest reading was taken. `?refresh=true` reads every default branch again, eight repositories at a time, and so does the first read of a repository nothing read yet. `repos` holds one entry per active registered repository with at least one instruction file: `{"repo", "files", "fingerprint", "head_sha", "newest_job": {"job_id", "status", "created_at", "finished_at", "total_cost_usd", "error_code", "pr_url", "stale", "files_read"} | null, "refused_at", "due", "reason"}`. `stale` and `files_read` are the counts a completed job's report starts with, its outdated claims and the instruction files it read; they are `null` for any other job and for a report that lost them. `files` counts the instruction files on the default branch, `fingerprint` is the sha256 over their sorted `path<TAB>blob sha` lines, and `refused_at` is when the schedule's last start for the repository was refused before a job existed (`null` when none was). `reason` is one of `first` (no context-audit job yet), `changed` (the fingerprint differs from the one the newest job recorded), `aged` (the newest job is `context_audit_days` old, from when it finished or, when it did not complete, from when it started, and the branch head moved since), `live` (one is pending, running, awaiting input or paused), `current` or `off`; `due` is true for the first three. `unread` lists the repositories whose newest commit or tree the forge did not return, with why. An unknown owner answers 404, a GitLab owner 422, a credential that cannot be resolved the 404 or 422 the other organization routes answer, and an orchestrator that did not start the schedule 503.

`POST /api/v1/orgs/{owner}/context-audit/run` starts the owner's due audits now, whether or not `context_audit_days` is on. Body, every field optional: `{"repo": "acme/widgets", "force": false, "limit": 3}`. Without `repo` it starts every due repository, at most `limit` (1 to 50; absent, the owner's `context_audit_max_jobs_per_tick`, else `CONTEXT_AUDIT_MAX_JOBS_PER_TICK`). With `repo`, one of the owner's active registered repositories, it starts that one when it is due, or with `force: true` whatever the due rule says; `force` without `repo` answers 422. Due is the schedule's rule without its off switch: with the setting off no interval applies, so a repository is due with no context-audit job yet or with changed files. Nothing starts while a context-audit job of the repository is live, `force` or not: right before each start the jobs are read again, under one lock that the schedule's own starts also hold. Each job starts the way the schedule starts one, under the actor `embry0:context-audit`, with the owner's `context_audit_budget_usd` (else `CONTEXT_AUDIT_JOB_BUDGET_USD`) as its hard cap and the fingerprint and head it was read at, so the schedule anchors on it and does not repeat it. One `organization.context_audit_run` audit row names the caller, with the ids of the jobs started. The response: `{"owner", "context_audit_days", "repo", "force", "limit", "budget_usd", "started": [{"repo", "job_id", "reason"}], "skipped": [{"repo", "reason", "detail"}]}`, where `limit` is `null` with `repo`. A started job's `reason` is `first`, `changed`, `aged` or `forced` (started with `force` while the due rule said `current`); a skipped repository's is `live`, `current`, `limit` (past the limit), `refused` (the start was refused before a job existed, `detail` says why, such as no `context-audit` template; the schedule then waits an interval on that repository, as on its own refusals), `gated` (the budget gate refused the start, `detail` says which limit; every repository the run would have started after it is `gated` too, no job is written and none counts as an attempt, so each is due again once the gate lets jobs through), `unread` (the forge did not answer, `detail` says how) or `no_instruction_files` (only for the named `repo`). The tier is that of `POST /api/v1/jobs`, an operate-scope key or above, and a key limited to a repository allowlist must name one of its repositories with `repo`. An unknown owner, or a `repo` that is not one of its active registered repositories, answers 404, a paused or removed owner 409, a GitLab owner 422, a credential that cannot be resolved the 404 or 422 the other organization routes answer, and an orchestrator that did not start the schedule 503.

`GET /api/v1/orgs/{owner}/needs` is the needs list ([ADR 0011](adr/0011-organization-needs.md)): what embry0 still needs from a person to maintain the owner, derived from the newest finished discovery run, one need per kind and repository set. `dismiss` and `reopen` keep a person's answer across rediscovery. `repo_vanished` lists the repositories an earlier run listed, the newest did not and confirmed gone with a direct read, that embry0 still holds as active registrations; its action is `deactivate`, which a person runs: nothing is deactivated on its own. Each open need is also a healing case with origin `org`, listed by `GET /api/v1/cases?awaiting_decision=true` ([Healing cases](#healing-cases)); closing that case dismisses the need.

`PATCH /api/v1/orgs/{owner}` sets `display_name` and merges `settings` from a closed list; an unknown key, a wrong type or a value out of range answers 422.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `auto_register` | boolean | off | A webhook delivery for an unregistered repository under the owner registers it first ([webhooks.md](webhooks.md#organization-webhooks-github)) |
| `rediscover_hours` | integer from 1 to 168, or `null` | `null` (off) | Rediscover the owner on a schedule, this many hours after its newest run |
| `context_audit_days` | integer from 1 to 365, or `null` | `null` (off) | Audit the files that steer a coding agent in each registered repository on a schedule: when they change, and this many days after the newest audit once the branch moved ([context-audit.md](context-audit.md)) |
| `context_audit_budget_usd` | number more than 0 and at most 50, or `null` | `null` (`CONTEXT_AUDIT_JOB_BUDGET_USD`) | The hard cap in USD of each context-audit job started for the owner; a whole number is stored as a float |
| `context_audit_max_jobs_per_tick` | integer from 0 to 50, or `null` | `null` (`CONTEXT_AUDIT_MAX_JOBS_PER_TICK`) | The most context-audit jobs one tick of the schedule starts for the owner; `0` starts none |
| `context_audit_open_prs` | boolean | off | A context audit that finds outdated claims opens a pull request from the patch it checked, one at a time per repository ([ADR 0022](adr/0022-context-audit-pull-requests.md)) |

`GET` and `POST /api/v1/orgs/{owner}/webhook` inspect and install the organization's one GitHub webhook, and the receiver's gate handles deliveries for repositories nobody registered ([webhooks.md](webhooks.md#organization-webhooks-github), [ADR 0012](adr/0012-organization-webhook.md)).

`POST /api/v1/orgs/{owner}/actions` runs one action over repositories discovery saw under the owner. Body: `{"action": "register" | "hook" | "onboard" | "watch" | "deactivate", "repos": ["acme/widgets"], "dry_run": false, "force": false}`. The response carries one result per repository: `{"repo", "outcome", "detail"}` with `outcome` one of `done`, `already` (the state exists), `skipped` (a reason the caller can fix) or `failed` (one repository's error, or a refusal from the path the action reuses; the rest still run). `repo` is the spelling the forge reported, whatever case was asked; a repository discovery has not seen, an archived one (for every action but `deactivate`), or one under another owner is `skipped`. Every action reuses an existing path and is idempotent: a second identical call reports `already` for every repository the first one changed, and changes nothing. `dry_run` reports what would happen without side effects and writes no audit row; a real run writes one `organization.action` row with every repository's outcome, plus the rows the reused paths write. A real run with any `done` result starts a discovery run for the owner, the one `POST /orgs/{owner}/discover` starts, so the needs list catches up; the response's `rediscovery` is that run's id, or `null` when nothing changed, a dry run, or a run already in progress.

- `register` adds the repository to the repo registry under the organization's forge, the call `POST /repos` makes. A row under any case counts as `already`.
- `hook` creates a GitHub webhook on the repository posting to `WEBHOOK_PUBLIC_URL` + `/api/v1/webhook` with `GITHUB_WEBHOOK_SECRET`, for issues, issue comments and pull requests ([webhooks.md](webhooks.md#github)). It runs only when the owner's probe found the credential can manage webhooks, and it is skipped while `WEBHOOK_PUBLIC_URL` is unset or a loopback address, or the secret is empty. A hook in the forge's live listing whose URL starts with `WEBHOOK_PUBLIC_URL` is `already`; a hook on the dashboard's origin is not, since that vhost answers 404 on the webhook paths. GitHub only in this release.
- `onboard` starts the onboard job ([onboarding.md](onboarding.md)) on the repository's default branch through the function `POST /jobs {"pipeline": "onboard"}` uses, so it runs the same checks (the calling key's repository allowlist among them: a repository the key may not start work on is `failed` with `repo_not_allowed`) and starts it under the caller's actor, which its `onboard.job_created` audit row names. A repository that is not registered is skipped (the job would fail `ERR_REPO_UNREGISTERED`); a stored QA config or an onboard job in flight is `already`; a repository whose facts carry no boot hint is skipped. When the newest onboard job completed without storing a QA config, the repository is skipped with that job's id unless the body carries `"force": true`.
- `watch` adds a watcher stack for the repository, disabled, filing to the organization's forge, with a placeholder LogQL selector to edit before enabling. It stores through the function `PUT /watcher/stacks/{owner}/{name}` uses and writes its `watcher_stack_updated` audit row. A stack under any case counts as `already`.
- `deactivate` sets the repository's registry row inactive, the call `PUT /repos/{repo}/active` with `{"active": false}` makes: the row stays, hidden from listings, and the same route turns it back on. It answers the `repo_vanished` need, and it reaches an archived repository too: one archived when it was last listed and deleted since is what that need proposes. An inactive row, or no row at all, is `already`.

The MCP tool `org_act(owner, action, repos, dry_run, force, confirm)` calls this endpoint; like every gated tool it needs `confirm=true` on every call, dry runs included.

```bash
# What would registering two repositories do
curl -X POST http://localhost:8200/api/v1/orgs/acme/actions \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"action": "register", "repos": ["acme/widgets", "acme/gadgets"], "dry_run": true}'
```

## Repository preferences

```bash
# Get per-repo sandbox profile override
curl http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "X-Requested-With: XMLHttpRequest"

# Set sandbox profile + language hint for a repo
curl -X PUT http://localhost:8200/api/v1/repos/owner/repo/preferences \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"sandbox_profile": "java-17", "language_hint": "Java", "notes": "Uses Maven"}'
```

The PUT replaces every field it does not mention — except `verification`
(the orchestrator-run test gate and tier path rules, `docs/verification.md`):
omitted or `null` keeps the stored value, `{}` clears it.

`owners` is a list of GitHub logins that answer for the repository's owner
proposals (below); `null` or `[]` falls back to the users its `CODEOWNERS`
names for every file. A login is validated and kept once, without its `@`.

## Repository history and undo

Every change to a repository's stored test setup (`PUT` and `DELETE
/repos/{repo}/qa-config`, bootstrap, onboarding) and to its preferences
(`PUT` and `DELETE /repos/{repo}/preferences`) is recorded with who made it
and how. The newest 100 changes of each are kept per repository. The API
writes also add an audit row: `repo.qa_config_set`,
`repo.qa_config_deleted`, `repo.preferences_set`,
`repo.preferences_cleared`, and `repo.change_undone` for an undo.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/repos/{repo}/history?limit=50` | any authenticated principal |
| `POST /api/v1/repos/{repo}/history/{revision_id}/undo` | operator |

`GET` answers `{"entries": [{"id", "surface", "what", "actor", "source",
"created_at", "undoable", "why_not"}], "before_history": bool}`, newest
first. `what` describes the change in plain words. `surface` is
`qa_config` or `preferences`; `source` is `person`, `bootstrap`, `onboard`,
`proposal` or `restore`. `before_history` is true when a test setup is
stored but none of its changes are recorded: it was saved before the
history existed.

Undo puts back what one change replaced. Only the newest change of its
surface can be undone, and only while the stored value is still what that
change wrote; otherwise the answer is 409 with the `why_not` text. An
earlier value that would no longer be accepted is a 422 and nothing is
written. The undo is itself recorded, with source `restore`, so it can be
undone in turn. The `PUT /qa-config` response carries the new change's
`revision_id`, `null` when the text was unchanged.

```bash
# Put back the test setup the newest change replaced
curl -X POST http://localhost:8200/api/v1/repos/acme/widgets/history/42/undo \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "X-Requested-With: XMLHttpRequest"
# -> {"revision_id": 43}
```

## Setting up testing with embry0

The onboarding agent can write a repository's test setup for a person to
review ([ADR 0019](adr/0019-setup-proposals.md)). In propose mode it reads
the code, writes a setup, starts each app with it and waits for its ready
checks, then stores the setup as a proposal. Nothing reaches the test
setup until a person applies the proposal.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/repos/{repo}/setup/cost` | any authenticated principal |
| `POST /api/v1/repos/{repo}/setup/propose` | operator |
| `GET /api/v1/repos/{repo}/setup/proposal` | any authenticated principal |
| `POST /api/v1/repos/{repo}/setup/proposal/{proposal_id}/apply` | operator |
| `POST /api/v1/repos/{repo}/setup/proposal/{proposal_id}/reject` | operator |
| `POST /api/v1/repos/{repo}/setup/skip-testing` | operator |
| `GET /api/v1/setup/proposals?status=pending` | any authenticated principal |
| `GET /api/v1/repos/{repo}/readiness` | any authenticated principal |

`GET .../setup/cost` answers `{"recorded_runs", "typical_cost_usd",
"per_job_budget_usd", "model", "paid_from"}`: how many setup runs completed
on this embry0, the median recorded cost of the newest 20 (`null` when
there are none), the per-job budget, the model the run uses (the medium
tier) and the label of the AI account that would pay (`null` when none
resolves). These are recorded facts, not an estimate. With the default
`BUDGET_OVERRUN_MODE` of `soft`, the per-job budget is not a hard stop
([configuration](configuration.md)).

`POST .../setup/propose` takes `{"confirm_spend": true}`; without it the
answer is 422, as it is for any value but a JSON `true`, such as `"yes"` or
`1`. It retires a proposal that is waiting (it becomes `stale`),
keeps the time the test setup was last saved, and starts an onboard job on
the repository's base branch setting, else its default branch as discovery
saw it, else the branch the forge reports as its default (else `main`). It answers `{"proposal_id", "job_id"}`, or 409 while a
run is going for the repository. The calling key's repository allowlist
applies, as for `POST /jobs`, and to apply, reject and skip-testing too.

`GET .../setup/proposal` answers the latest proposal, or 404 when there is
none: `{"id", "repo", "status", "job_id", "job_status", "created_by",
"created_at", "decided_by", "decided_at", "notes_md", "failure", "smoke",
"proposed_yaml", "current_yaml", "check"}`. `status` is `running`,
`pending` (waiting for a person), `applied`, `rejected`, `stale` or
`failed`. `check` is the answer `POST /repos/{repo}/qa-config/check` gives
for `proposed_yaml`, `null` when there is none. A `running` proposal whose
job has ended is stored as `failed` when it is read, with the job's error.

Apply takes `{"acknowledged": [<warning codes>]}`. It answers 409 when the
proposal is not waiting, or when the test setup was saved after the run
started: the proposal becomes `stale` and nothing is written. The decision
and the write are one transaction, so a turn-down or a save that lands while
apply runs wins: apply then answers 409 and writes nothing. It answers
422 with `{"message", "problems"}` when the check finds a problem every run
would stop on, and 422 with `{"message", "missing"}` when a warning that
needs confirming is not in `acknowledged`. Otherwise it writes the proposal
as the test setup, recorded with source `proposal` so History can undo it,
and answers `{"revision_id"}`. Apply and reject add the audit rows
`repo.setup_applied` and `repo.setup_rejected`.

`POST .../setup/skip-testing` stores the starter with `qa_required: never`
when the repository has no test setup, and answers 409 when it has one.

`GET .../readiness` is the checklist the repository page opens on. It is
built from facts embry0 already stores, with no model call and no forge
call: the account that resolves for the repository, what discovery read of
it, the settings stored for it and globally, the test setup and its check,
the latest proposal, and the newest pull request a job opened. It answers
`{"repo", "status", "steps_left", "summary", "next_step", "rows",
"needs_docker", "facts_seen_at"}`, or 404 for a repository that is not
registered. `rows` are, in order, `code_access`, `app_values`,
`testing`, `first_task` and the optional `work_trigger`, each
`{"key", "required", "state", "title", "says", "action"}` where `action`
is `{"kind", "label"}` or `null`. A fact embry0 does not have reads
`unknown` and is not counted in `steps_left`; `facts_seen_at` says when
discovery last saw the repository. `next_step` is the first required row a
person can act on, `null` while a setup run is going. `needs_docker` is
true when the stored test setup will not start without the Docker access an
admin grants.

```bash
# Start a setup run, then apply the proposal it leaves
curl -X POST http://localhost:8200/api/v1/repos/acme/widgets/setup/propose \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"confirm_spend": true}'
# -> {"proposal_id": "prop-0123456789ab", "job_id": "job-0123456789ab"}
curl -X POST http://localhost:8200/api/v1/repos/acme/widgets/setup/proposal/prop-0123456789ab/apply \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"acknowledged": ["live_address"]}'
# -> {"revision_id": 44}
```

## Docker access (admin)

A sandbox profile with `dind_enabled` only *asks* for the shared Docker daemon
(DinD). A sandbox gets the daemon's client certificates and `DOCKER_HOST` when
its repo also holds this grant. **Code from a granted repo can control the
shared Docker daemon, including the proxies holding your tokens**: the daemon
runs every credential proxy and every other job's sandbox, so its certificates
are root on the Docker host. A profile, a stored qa.yaml or a job cannot set it.

| Endpoint | Tier |
|---|---|
| `PUT /api/v1/repos/{repo}/docker-access` | admin |

```bash
# Grant (or {"enabled": false} to revoke). 404 for an unregistered repo.
curl -X PUT http://localhost:8200/api/v1/repos/owner/repo/docker-access \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"enabled": true}'
```

`GET /api/v1/repos` and `GET /api/v1/repos/{repo}` return the flag as
`docker_access`. Each change writes a `repo_docker_access_granted` or
`repo_docker_access_revoked` audit row. A sandbox whose profile asks for
Docker on a repo without the grant is refused before it starts, with
`ERR_INCOHERENT_CONFIG` and a message naming this endpoint.

## Retention preview (admin)

What the three retention sweeps (`JOB_LOG_RETENTION_DAYS`,
`AGENT_SESSION_RETENTION_DAYS`, `QA_IMAGE_RETENTION_DAYS`; see SECURITY.md,
"Data retention") would remove if they ran now. It is a dry run of the same
code, so it removes nothing.
Each sweep runs at its configured number of days, or at the value in the
query, to see the effect of a setting before turning it on. A sweep with
neither reports zeros and `"retention_days": null`.

| Endpoint | Tier |
|---|---|
| `GET /api/v1/admin/retention/preview` | admin |

```bash
curl "http://localhost:8200/api/v1/admin/retention/preview?job_log_days=30&agent_session_days=30&qa_image_days=30" \
  -H "Authorization: Bearer $EMBRY0_API_KEY"
# -> {"job_logs": {"configured_days": null, "retention_days": 30, "jobs": 412,
#      "events": 9031, "bytes": 21400000},
#     "agent_sessions": {"configured_days": null, "retention_days": 30,
#      "sessions": 380, "bytes": 61000000},
#     "qa_images": {"configured_days": null, "retention_days": 30, "images": 3,
#      "image_bytes": 1200000000, "volumes": 0, "volume_bytes": 0,
#      "kept": {"pinned": 1, "recent": 1}, "failed": 0, "errors": [],
#      "removed_images": [{"id": "sha256:…", "refs": ["…"], "last_used": "…", "bytes": 400700000}],
#      "removed_volumes": []}}
```

`bytes` is what the redacted fields held in `job_logs`, and what the
sessions hold in `agent_sessions`; `image_bytes` and `volume_bytes` are the
sizes the daemon reports (`docker system df`, rounded), each image's own
layers only, and `null` when the daemon could not report them. `kept`
counts what stays, by reason: `pinned` (an active `qa_image_tags` row names
it), `recent`, `in_use` (a container uses it), `live_job` (its repo has a job
that has not ended), `not_ours`, `unchecked` (a lookup failed). The job log
dry run reads every old event, so on a large database it takes a while.

## API keys

Database API keys authenticate MCP, CLI and other machine clients with
`Authorization: Bearer <key>`. An operator mints keys for themselves, scoped at
or below their own tier; an admin also mints unowned service keys and sees and
revokes every key. The plaintext appears once, in the mint response. Settings
→ API keys in the dashboard does the same.

| Endpoint | Who |
|---|---|
| `GET /api/v1/apikeys` | operator (own keys), admin (all) |
| `POST /api/v1/apikeys` | operator, admin |
| `PUT /api/v1/apikeys/{key_id}/allowed-repos` | the key's owner, admin |
| `DELETE /api/v1/apikeys/{key_id}` | the key's owner, admin |

Two scopes sit off the read < operate < admin ladder, and only an admin mints
them: `merge`, the chat relay's (read, decide merge approvals, accept watcher
findings, answer agent questions), and `propose`, a chat assistant's for owner
proposals (read, and answer proposals: `POST /proposals/{id}/accept`,
`/decline`, `/snooze`). Any other mutation answers 403.

### Repository allowlist

A key may be limited to a list of repositories with `allowed_repos`. A
restricted key is refused, with a 403 whose detail starts with
`repo_not_allowed:`, whenever it would start, continue or stop work on any
other repository:

- `POST /jobs` (every pipeline; a job with no repository, such as an `http`
  or `none` context, is refused too), `POST /jobs/{id}/resume`,
  `POST /jobs/{id}/inputs/{input_id}/answer` and `POST /jobs/{id}/cancel`;
- `POST /issues` (an issue without a repository is refused),
  `PUT /issues/{id}` (the issue's repository and, when it changes, the new
  one), `POST /issues/{id}/triage`, `POST /issues/{id}/sync`,
  `POST /issues/{id}/inputs/{input_id}/answer` and `DELETE /issues/{id}`
  (the issue and each child issue it cancels);
- `POST /orgs/{owner}/context-audit/run` (the `repo` it names; a run that
  names none is refused, since it would start work on any repository under
  the owner);
- `POST /cases/{id}/accept`, `POST /merge-approvals/{id}/decide`,
  `POST /proposals/{id}/accept`, `/decline` and `/snooze` (the proposal's
  repository), `POST /watcher/run` (the stack ticked; with none named, every watched
  stack, all checked before any ticks), `POST /qa/images/build` and
  `POST /repos/bootstrap`;
- the steps of setting up testing: `POST /repos/{repo}/setup/propose`,
  `POST /repos/{repo}/setup/proposal/{id}/apply` and `.../reject`,
  `POST /repos/{repo}/setup/skip-testing` and
  `POST /repos/{repo}/history/{id}/undo`.

The MCP tools reach embry0 through these routes with the caller's key, so
they are covered too. Matching ignores case and follows `REPO_RENAMES`
([configuration.md](configuration.md)): an entry naming a repository's old
slug also matches its current one, and the reverse. Each refusal logs
`api_key_repo_refused` with the key's name and id (never the key), the
repository and the action, and writes an `apikey.repo_refused` audit row.

Omitting the list, or sending an empty one, leaves the key unrestricted;
every key minted before the allowlist existed is unrestricted. Beyond the
routes above, the list does not narrow what the key may read, nor the
settings routes its scope allows, `PUT /repos/{repo}/qa-config` and
`PUT /repos/{repo}/preferences` among them. A restricted key may not mint
keys or change any key's allowlist, its own included, since a key it minted
would carry no restriction. For an agent that should only dispatch, mint an
`operate` key with the list.

```bash
# Mint a key that may only start work on acme/widgets
curl -X POST http://localhost:8200/api/v1/apikeys \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "dispatch-bot", "scope": "operate", "allowed_repos": ["acme/widgets"]}'

# Replace the list (or send {"allowed_repos": []} to lift it). Effective at once.
curl -X PUT http://localhost:8200/api/v1/apikeys/key-0123456789ab/allowed-repos \
  -H "Authorization: Bearer $EMBRY0_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"allowed_repos": ["acme/widgets", "acme/widgets-docs"]}'
```

Each change writes an `apikey.allowed_repos_changed` audit row with the list
before and after.

## Audit log

```bash
# Newest-first rows; admins see every row, other callers the rows they wrote
curl "http://localhost:8200/api/v1/audit?action=qa.job_created&limit=50" \
  -H "Authorization: Bearer $EMBRY0_API_KEY"

# Every action a row can carry, with its label (the dashboard's filter list)
curl http://localhost:8200/api/v1/audit/actions \
  -H "Authorization: Bearer $EMBRY0_API_KEY"
# -> {"actions": [{"action": "user.login", "label": "Signed in"}, ...]}
```

The action names live in one registry, `embry0/audit/actions.py`. Each
event has one name: a QA, onboard or pull request review job writes one
`qa.job_created`, `onboard.job_created` or `pr_review.job_created` row,
whose actor is the job's actor. Rows written before that also carry a
second, underscore-spelled copy (`qa_job_created`, `onboard_job_created`,
`pr_review_job_created`); the registry does not list those.

## Sandbox visibility

```bash
# List running sandbox containers (ops)
curl http://localhost:8200/api/v1/sandboxes/active \
  -H "X-Requested-With: XMLHttpRequest"
```

## WebSocket streaming

The handshake takes a short-lived token scoped to one job, never an API key.
Mint it with an operator or admin key; a read or merge key gets 403, and can
page the same events from `GET /api/v1/jobs/{job_id}/logs/events?since_seq=`
instead. A browser signed in with SSO needs no token: the session cookie
authenticates the handshake.

```javascript
// 1. Mint a token for this job (expires_in: 900 seconds).
const { token } = await fetch(`http://localhost:8200/api/v1/jobs/${jobId}/events-token`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${apiKey}`, 'X-Requested-With': 'XMLHttpRequest' },
}).then((r) => r.json());

// 2. Present it as the subprotocol; the server echoes it back on accept.
const ws = new WebSocket(`ws://localhost:8200/ws/jobs/${jobId}/events`, [`embry0.token.${token}`]);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // every event: { type, timestamp, event_seq, ...fields }
};
```

Every event carries `type` and `timestamp`; the orchestrator stamps `job_id` and `event_seq` (the replay cursor, also accepted as `?since_seq=`). Sandbox and node events carry `run_id` (one agent run), `node` (graph node id), `agent` (agent type) and, on subagent events, `parent_tool_use_id`. The table is generated from `embry0/execution/events.py` (`python -m embry0.execution.events --render-markdown`); `docs/contracts/events.json` is the same contract as data.

The route closes with code 1000 after forwarding `job_finished`, or straight after the replay when the job is already finished (a terminal row, or one whose PR was since merged or closed): the stream is over and a client must not reconnect. A client keeps the highest `event_seq` it processed as its `?since_seq=` cursor for the next connect, and treats a live event whose `prev_seq` is greater than that cursor as a gap (this job had a persisted event the client never received) to fill from `GET /jobs/{id}/logs/events?since_seq=<cursor>` before applying it. A larger `event_seq` alone is not a gap: ids are `job_logs.id`, shared by every job.

<!-- events:begin -->
| Type | Required fields | Optional fields |
|---|---|---|
| `agent_started` | `max_turns`, `model`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `skill_sources` |
| `turn_start` | `model`, `timestamp`, `turn_number`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `turn_usage` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `input_tokens`, `model`, `output_tokens`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `text` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `thinking` | `text`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `tool_call` | `summary`, `timestamp`, `tool_id`, `tool_input`, `tool_name`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `redacted`, `run_id` |
| `tool_result` | `content`, `is_error`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `redacted`, `run_id`, `truncated` |
| `subagent_start` | `name`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `subagent_end` | `is_error`, `timestamp`, `tool_id`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `context_compacted` | `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `pre_tokens`, `prev_seq`, `run_id` |
| `agent_ask_user` | `category`, `importance`, `options`, `question`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `suggested_answer` |
| `cost_update` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `duration_api_ms`, `duration_ms`, `num_turns`, `timestamp`, `tokens_in`, `tokens_out`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `agent_completed` | `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `duration_ms`, `error_code`, `error_message`, `input_tokens`, `is_error`, `output`, `output_tokens`, `policy_denials`, `timestamp`, `tools_called`, `type` | `agent`, `event_seq`, `job_id`, `node`, `original_length`, `parent_tool_use_id`, `prev_seq`, `run_id`, `truncated` |
| `error` | `message`, `timestamp`, `type` | `agent`, `code`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id`, `tool_name` |
| `node_started` | `attempt`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `round`, `run_id` |
| `node_completed` | `attempt`, `timestamp`, `type` | `action`, `agent`, `deliverables`, `error`, `event_seq`, `job_id`, `job_kind`, `node`, `ok`, `parent_tool_use_id`, `prev_seq`, `round`, `run_id`, `template` |
| `pipeline_graph` | `graph`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `progress` | `message`, `timestamp`, `type` | `agent`, `app`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `interrupt` | `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `questions`, `reason`, `run_id`, `summary` |
| `agent_paused_for_input` | `count`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `review_decision` | `decision`, `summary`, `timestamp`, `type` | `agent`, `duplicates_open_pr`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `validation_result` | `timestamp`, `type`, `validation` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `verification_result` | `blocking`, `gate_verdict`, `tier`, `timestamp`, `type` | `agent`, `browser_qa`, `event_seq`, `human_merge_only`, `job_id`, `mode`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `pr_created` | `branch`, `pr_url`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_apps_resolved` | `apps`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_started` | `app`, `target`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_finished` | `app`, `duration_ms`, `status`, `timestamp`, `type` | `agent`, `event_seq`, `failure_summary`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_subtask_status` | `app`, `status`, `timestamp`, `type` | `agent`, `boot_phase`, `duration_ms`, `event_seq`, `failure_summary`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `qa_agent_transient_retry` | `app`, `reason`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `system` | `message`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `job_failed` | `kind`, `message`, `timestamp`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
| `job_finished` | `error_code`, `error_message`, `pr_url`, `status`, `timestamp`, `total_cost_usd`, `type` | `agent`, `event_seq`, `job_id`, `node`, `parent_tool_use_id`, `prev_seq`, `run_id` |
<!-- events:end -->

## Healing cases

- `GET /api/v1/cases` and `GET /api/v1/cases/{case_id}` — the ledger's read API (#278): one case per finding, from detection to a healed verdict. See `docs/glossary.md`, "Healing case". A watcher case carries the slopometer's `work_nature` when the watcher gave one (`null` otherwise; [slop](glossary.md#slop)).
  - `?awaiting_decision=true` is "what is waiting for a person": watcher drafts with a ticket that nobody has accepted or rejected — the list the dashboard shows as **Proposals** above the issue list. It is the proposal cap's own rule without the age window, so a draft that aged out of the cap is still listed, and a draft whose ticket was finished or deleted in its tracker is not. It also lists a finding a `not_healed` verdict sent back for a retry nobody has accepted yet (#658), which holds no slot of the cap, and an organization need waiting for a person (origin `org`, [ADR 0011](adr/0011-organization-needs.md)), which holds none either and is answered on the needs list, never accepted. `?open_only=true` is a different question (every case not yet `closed`, which includes alerts, findings with no ticket, and merged, deployed and verified cases); `?stage=tracked` is not it either (a person's ticket passes through `tracked`, and a draft whose ticket is gone stays there).
- `POST /api/v1/cases/{case_id}/accept` — accept a watcher finding and dispatch the pipeline on its draft, exactly as adding the trigger label would; refuses anything that is not a `tracked` watcher finding of an enabled watcher stack of this orchestrator (#378). A GitHub draft is dispatched directly. A Linear draft gets the trigger label put on the ticket and is dispatched through the Linear trigger path (#460); a refusal says what happened to the ticket, and a ticket whose Linear project resolves to another repo than the case's is refused untouched. A GitLab draft is still accepted in GitLab (`409`). A finding a `not_healed` verdict sent back is accepted here whatever its tracker: the retry is a new job on the issue of the failed attempt, which is reopened if its merge closed it, and the ticket is not touched (#658). Body: `{"accepted_by": "<name>"}`. `202` always carries a `job_id`. On the merge-scope allowlist. See `docs/self-watching.md`, "Accepting a finding without the label".
- `POST /api/v1/cases/{case_id}/close` — settle a case a person has decided: `{"resolution_path": "out_of_band", "note": "…", "closed_by": "<name>"}`, with `resolution_path` required unless the case already carries one; `409` on a closed case. A case whose origin is `org` is an organization need, and closing it dismisses the need, so it needs the admin tier, as `POST /api/v1/orgs/{owner}/needs/{need_id}/dismiss` does; any other case is operator tier.
