# Behaviour from agent and template rows, not built-in names (#167)

Part of #159 (the settings-in-DB rule: "rows, not name lists").

## Problem

Operators write pipeline templates and agent definitions as rows, but much of
what embry0 did with a template or an agent depended on the names of the
built-in ones:

- `SPECIALIST_TEMPLATE_NAMES` decided the triage skip, triage's list of
  templates and plan_route dropping triage's pick; `specialist_job_kind`
  recorded an all-specialist route as analysis.
- `_TEMPLATE_DELIVERS` said what a job's brief delivers, by template name.
- The coding agents were listed twice (route_plan and salvage).
- The progression and world SQL credited a job's pull request, its cleanup
  and its audit by template and agent names.
- `LEADER_BY_TEMPLATE`, `QUEST_TEMPLATE_BY_AGENT` and the frontend's
  `STATION_OF` decided who leads a party, which characters the guild can
  send, and where each one stands.

An operator's own specialist template was triaged, and an agent of their own
could never be sent from the guild.

## Design

One module, `embry0/workflows/row_behaviour.py`, answers "what does this row
say about its behaviour", tested on its own
(`tests/unit/workflows/test_row_behaviour.py`).

| Column | Table | Replaces | Read by |
|---|---|---|---|
| `fixed_route` | `pipeline_templates` | the name list, in `fixed_route_decision` | triage |
| `offered_to_triage` | `pipeline_templates` | the name list, in triage's template list and plan_route | triage, plan_route |
| `job_kind` | `pipeline_templates` | `specialist_job_kind` | triage's fixed decision, plan_route |
| `delivers` | `pipeline_templates` | `_TEMPLATE_DELIVERS` | job briefs (`template_delivers` in the candidates query) |
| `leader_agent` | `pipeline_templates` | `LEADER_BY_TEMPLATE`, the progression `_CODER` and audit credit, the world's cleanup count | world, progression |
| `station` | `agent_definitions` | `STATION_OF` | the world's characters, the hall |
| `quest_template` | `agent_definitions` | `QUEST_TEMPLATE_BY_AGENT`, `can_quest` | the world's characters |

- **A job's coder** is its template's leader when the leader is one of the
  developer node's agents (`CODING_AGENT_TYPES`: developer, slopmaster), else
  the developer: `row_behaviour.coder` in Python, `progression.coder_sql` in
  SQL, pinned to agree. A slop cleanup is a merged job the Slopmaster coded.
- **`quest_template`** is three-valued: NULL no task, `''` a task naming no
  template (triage picks, the developer's), a name a task running that
  template. A task naming a template that no longer exists is no task.
- **Pipelines the code defines** (`qa`, `onboard`, `pr-review`) have no row:
  `WORKFLOW_PIPELINES` gives their leader and deliverable, once.
- **Without a database** (or when it cannot be read), the built-in rows as
  seeded answer (`builtin_template`, `builtin_agent`), as the graph
  validator already falls back to `BUILTIN_SEED`.

### Write paths and validation

- API: `POST`/`PUT /pipelines/templates` and `POST`/`PUT /agents`; each
  malformed value is a 422 naming the field, a `leader_agent` that names no
  job agent and a `quest_template` that names no template are 422s too. On
  `PUT`, null clears `job_kind`, `leader_agent` and `quest_template` and is a
  422 for the NOT NULL columns.
- MCP: `agent_create` and `agent_update` take `station` and
  `quest_template`; `clear_quest_template=true` sends the one null. There is
  no MCP template write tool, so templates are the API's and the dashboard's.
- Dashboard: *Guild* on the agent form; *Behaviour…* in a template's menu on
  the Pipelines page.
- A fixed route with a `qa` step is saved with a warning: only triage asks
  for QA, so that step never runs.
- `duplicate` copies the columns, so a copy of a specialist route behaves
  like it.

### Upgrade and the boot seed

Migration 143 adds the columns and writes every row what the code said of
its name before, matched by name as the code matched it, built-in or not; a
template of the deployment's own whose every step is a specialist keeps its
analysis kind. Built-in agents are insert-if-missing, as before. Built-in
templates are upserted at every boot: the new columns follow
`default_for_kind`, written on a first insert only, so an operator's change
survives a restart.

A brief's input hash does not include `delivers`: the briefs already written
are not rewritten after the upgrade, and a changed `delivers` reaches the
briefs written after it.

The progression rollup now reads `pipeline_templates`; a change to a
template's `leader_agent` reaches the folded days at the next nightly
rebuild, like any change to a row of a folded day.

## Deviation from the issue: no `roles` column

The issue proposed `agent_definitions.roles` (coder, reviewer, qa). In the
code today a role would have one honest consumer: which agents run on the
developer node. The coding agents are the graph's own node vocabulary
(`FIXED_NODE_TYPES` includes `slopmaster`, and the developer node branches on
the Slopmaster), and the review and QA nodes run only `review` and `qa`. A
`coder` role that put an operator's agent on the developer node, with its
branch, push and pull-request path, is a new capability, not a refactor; a
role that only moved credit would say an agent codes when it cannot. The
reviewer and QA agents the achievements count are already the progression
pack's content (#176), which an operator edits. So this change keeps one
helper for the coding agents and leaves roles to that decision.

## Found, not changed here

Name-keyed behaviour outside the issue's list: `generic_agent.py` gives the
watcher log tool to `analysis` and a task to `context-auditor` by name, and
`GENERIC_AGENT_TIMEOUT_SECONDS` is keyed by agent type; plan_route picks the
slop route by `SLOP_TEMPLATE`; the facts fragments `areas_mapped`,
`followup_merged` and the audit ones name the templates their features
dispatch, as does `proposals.py`.
