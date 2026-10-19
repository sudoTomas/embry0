# Specialist agents

Eight built-in agents that each do one job on a cloned repository and return
one Markdown report. All eight are read-only: their tools are Read, Glob, Grep
and Bash, they edit nothing and open no pull request. Bash runs the
repository's own checks in a copy of the clone under `/tmp`, and the small
scripts an agent writes there to prove a claim.

They run on the generic agent node, like the research and analysis agents: an
agent's behavior is its system prompt
(`embry0/workflows/issue_to_pr/specialist_agents.py`), seeded into the agent
definitions at startup. Edit one on the Agents page; **Reset** there, `POST
/api/v1/agents/{type}/reset` or the MCP `agent_reset` tool restores the seed.

## The eight

| Agent | Template | Model | Turns | Wall clock |
|---|---|---|---|---|
| `kintsugi` | `kintsugi-trace` | claude-opus-5 | 60 | 30 min |
| `gardener` | `garden` | claude-sonnet-5 | 30 | 15 min |
| `interrogator` | `interrogate` | claude-opus-5 | 40 | 20 min |
| `why` | `why` | claude-sonnet-5 | 30 | 10 min |
| `how` | `how` | claude-sonnet-5 | 30 | 10 min |
| `blast-radius` | `blast-radius` | claude-sonnet-5 | 40 | 20 min |
| `comment-sicko` | `comment-pass` | claude-sonnet-5 | 25 | 10 min |
| `design-auditor` | `design-audit` | claude-sonnet-5 | 40 | 15 min |

The turn caps are defaults (`pipeline_config.agent_max_turns` overrides them
per job). The model follows the usual resolution order in
[providers.md](providers.md#default-model): a default model, a template step's
model or a job's `agent_models` outranks the one above.

### What each returns

- **kintsugi** traces the cracks in a repository. It runs the gates it finds
  (CI workflow files, check scripts, package scripts), reads code against its
  docs, and returns a ledger: one row per crack with an ID (`K-01`, `K-02`),
  one sentence, the evidence (file:line, or the command and its output), a
  grade (`hairline`, `fracture` or `break`) and the seal in one line with its
  size. Symptoms of one root cause share a row. A gate it cannot run in the
  sandbox is listed as not run, with the reason. The report also names what
  the trace does not cover: the user journeys a person should walk in the
  running application, and checking each crack against the issue tracker for
  duplicates.
- **gardener** finds one weed that can be pulled with no behavior change: dead
  code with no callers, a doc line the code contradicts, an unused dependency,
  a duplicate helper. It returns a weed card: the weed kind, the proof, the
  exact diff, the check that shows nothing changed (run on a copy under
  `/tmp`), and the check that would catch that kind next time. One weed per
  run, or `no weeds found` and where it looked.
- **interrogator** reviews a branch or pull request from four independent
  angles: adversarial, quality, mechanical (it runs the checks that cover the
  changed files) and architecture. Each finding carries a severity and its
  evidence; a lead judgment sorts them into Act on, Consider, Noted and
  Dismissed, and the report opens with a verdict.
- **why** answers a "why is it this way" question from git history and blame,
  decision records, docs, tests and the issue or pull request text in the job.
  Each claim is marked Direct, Supported, Inferred or Speculative, and the
  report names what it could not find and which sources the sandbox cannot
  reach.
- **how** explains a subsystem before someone changes it: entry points,
  runtime flow, ownership and layering, a file:line map, and the gotchas.
- **blast-radius** finds what a change could break beyond its diff, names the
  one fact the change is safe because of, and proves that fact by running real
  code in the sandbox (a test or a script), or marks it `unproven`. Risks it
  checked and cleared are listed apart from the ones it confirmed.
- **comment-sicko** reviews the comments a diff adds or touches. It returns
  the deletions (narration, banners, commented-out code, workaround notes),
  the symbols whose behavior should be made obvious instead (`MUST KILL`),
  and for each constraint a comment claims, the type, test or lint rule that
  should enforce it. License headers, public API docs and notes about a
  dependency the project cannot change stay.
- **design-auditor** measures the frontend against a design standard. A
  script it writes counts how the app uses colors, font sizes and families,
  radii, shell values (layout widths and heights, control heights), and
  tables versus card grids, and computes two scores from 0 to 100 from those
  numbers by a fixed formula, so the same commit gives the same scores.
  Conformance is how closely the interface renders the standard: its
  palette, its one accent, its type scale, its radii, its shell values and
  tables over cards. Hygiene is how much drift the code carries: raw color
  literals outside the tokens, sizes off the scale, radii off the set, extra
  font families. Every part is a share or a rate per 1,000 lines of UI code,
  never an absolute count, so the scores rank apps of any size; hygiene
  weighs less and never changes conformance. The report's first line is
  `Score: N` (the conformance score), the second `Hygiene: M`, then a
  `## Findings` heading and one table with the columns Severity, Rule,
  Location, Evidence and Proposed fix: one row per finding, ranked by the
  conformance points it costs, with a `path:line` example and a fix. A
  program can read the score and count the findings from that start. The
  formula is in [standards.md](standards.md#how-a-design-audit-scores).

### What to put in the task

- **kintsugi**, **gardener**, **how**: what to look at, or nothing for the
  whole repository.
- **interrogator**, **blast-radius**, **comment-sicko**: the change. Put the
  branch in the job's ref, or name a pull request number or a commit range in
  the task. Without one they review the last commit.
- **why**: the question and its target (a file, a symbol, a decision). The
  sandbox has no access to a tracker or chat, so paste any issue or pull
  request text the answer may rest on.
- **design-auditor**: the standard: token roles, the type scale, the radius
  set, how many accent colors, where tables are required. Without one it uses
  a default (one accent color, one text font plus one monospace font, colors
  only through tokens, tables for tabular data) and says so.

## Running one

Each template is `triage → agent → output`. A job reaches a specialist only
by naming its template in the job's `pipeline_template`. No job kind defaults
to one, triage is not offered the eight when it picks a template for a job
that names none, and if triage names one anyway the job takes its kind's
default route. Triage still runs first and can ask for missing information.
The job is recorded as an analysis job, and the agent's report is its
deliverable. The repository must be registered.

### Dashboard

On the Jobs page, click **New Job**. Choose **Git repository** as the context,
fill in **Repository**, and **Branch / Ref** for a change to review. Describe
what to look at in **Task**, tick **Advanced options**, and pick the template
under **Pipeline Template**. When the job completes, the report is under
Deliverables on the job's page.

### API

```bash
# Trace the cracks in a repository
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"repo": "owner/repo", "pipeline_template": "kintsugi-trace",
       "task": "Trace the cracks in this repository."}'

# Review a branch
curl -X POST http://localhost:8200/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"context": {"type": "git", "repo": "owner/repo", "ref": "feature/login"},
       "pipeline_template": "interrogate",
       "task": "Review this branch. Intent: lock an account after five failed logins."}'

# Read the report once the job completes
curl http://localhost:8200/api/v1/jobs/{job_id}/deliverables
```

### MCP

```text
job_create(repo="owner/repo", pipeline_template="design-audit",
           task="Audit the frontend against this standard: ...")
job_wait(job_id)
job_deliverables(job_id)
```

## Reserved names

The eight template names (`kintsugi-trace`, `garden`, `interrogate`, `why`,
`how`, `blast-radius`, `comment-pass`, `design-audit`) and the eight agent
names (`kintsugi`, `gardener`, `interrogator`, `why`, `how`, `blast-radius`,
`comment-sicko`, `design-auditor`) are reserved. Check for them before you
upgrade a deployment that already has its own templates or agents:

- The template seeder writes every built-in template at each startup and wins
  over a saved template of the same name: that template is overwritten and
  marked built-in. Duplicate it under another name before you upgrade.
- The agent seeder only adds agents that are missing. An existing agent with
  one of the eight names keeps its definition, and the specialist template
  then runs it in place of the built-in agent. To get the built-in agent,
  delete the custom one (**Delete** on the Agents page, or
  `DELETE /api/v1/agents/{type}`) and restart the orchestrator: the seeder
  then inserts the built-in definition. The Agents page offers **Reset** only
  for built-in agents.

## Limits

- Read-only means no Write or Edit tool. Bash can still write files: the
  prompts forbid changes to `/workspace`, run installs, checks and builds in a
  copy under `/tmp`, and keep scratch files there. The clone lives only in the
  job's sandbox.
- The clone is shallow (50 commits). The why agent deepens it when history
  runs out.
- The report keeps the first 10,000 characters of the agent's final message;
  the prompts put the most important material first.
- A gate that needs a database, a service or a network the sandbox lacks is
  reported as not run, never as a pass.
