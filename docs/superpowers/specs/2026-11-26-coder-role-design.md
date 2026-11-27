# A coder role, and a docs role, for agents (#395)

Status: decided (2026-11-26, section 2). Pull request 1 builds the roles (sections 3 and 4); pull requests 2 and 3
follow (section 5). Refs #159, #167, #332, #395, #521, #559. Line numbers are at `911bd331`.

## 1. Why

Only the built-in coding agents can run on the developer node, the step that works on a branch, pushes it and has a
pull request opened for it.

- **The coding agents are a list of names.**
  - `row_behaviour.CODING_AGENT_TYPES` is `("developer", "slopmaster")` (`embry0/workflows/row_behaviour.py:65`).
  - Every reader keys on it:
    - route_plan's step map (`embry0/workflows/issue_to_pr/route_plan.py:47`, `:111`);
    - the cursor's re-entry (`:237`) and the job's coding agent (`:247`);
    - the developer node's choice of agent (`embry0/workflows/issue_to_pr/nodes/_developer.py:1031`) and its
      cursor advance (`:1649`);
    - salvage's reading of the coding run (`embry0/workflows/issue_to_pr/nodes/_salvage.py:54`);
    - the agents API's read-only `coding_agent` flag (`embry0/api/v1/agents.py:213`);
    - the guild-task checks (`row_behaviour.py:159`, `:300`).
  - #167 moved every other name-keyed behaviour onto rows. It left this one, because "coder" is the one role with a
    real effect.
- **An operator's own definition runs on the generic agent node** whatever its tools and prompt
  (`embry0/workflows/issue_to_pr/generic_agent.py`). It runs once, with no branch of its own, no delivery, no salvage
  and no credit as the job's coder.
- **The push right is the job's, not a step's.**
  - init enrolls the job's sandbox with `git_push` and the job's branch when it creates it
    (`embry0/workflows/issue_to_pr/nodes/_init.py:310-313`). That is before triage runs and before the route is
    known.
  - A resume enrolls it again the same way (`embry0/services/_executor_run.py:1172`, `:1182`).
  - Every step of an issue-to-pr job then runs in that one sandbox.
  - A coder role is a grant of that right, so who holds it and when has to be explicit.

## 2. Decisions

Decided 2026-11-26.

- **D1. The job's branch opens for the whole job, from the first step that may put files on it.**
  - Two roles let a step do that: `coder` (the developer node's step) and `docs` (a step that is no coder but puts
    files on the branch: triage's spec or design, the reviewer's notes, a context audit's checked patch). The
    git-proxy cannot limit a push to paths, so `docs` opens the same branch as `coder`.
  - The sandbox is enrolled with `git_push` and no branch. The git-proxy refuses every ref update for such an
    enrollment (`embry0/execution/proxy/git_scope.py:273`).
  - The job's own branch opens when the first step whose agent holds `coder` or `docs` starts, triage included (its
    agent's row says), and stays open for the rest of the job. A job none of whose steps holds either never opens it.
  - A resume enrolls the sandbox again with exactly what the job had: the job's branch (the developer's chosen one
    once it has one) once it was opened, else none.
  - The git-proxy accepts an empty list already, so this works without a proxy image rebuild. It logged such an
    enrollment as an older orchestrator's, with a warning; it now logs one naming an empty list at info
    (`git_proxy_enrollment_no_push_branch_yet`), which takes effect when the proxy image is rebuilt.
- **D2. At most one coder step per template,** refused when the template is saved, and any number of docs steps. The
  reviewer still sends work back to the coder step, for as many rounds as the job allows. A template that has come to
  hold two coders since it was saved (an agent given the role later) is skipped at plan time, as an invalid one is.
- **D3. Who may hold each role.**
  - `coder`: any definition that runs in jobs, except a brief writer's voice (`runs_in_jobs` false) and the graph's
    own review, QA, triage and output types.
  - `docs`: any definition that runs in jobs. The seed gives it to triage, the reviewer (`review`) and the context
    auditor, so a context audit's pull request keeps being delivered.
  - A definition holding either whose tools cannot write (no `Write`, `Edit` or shell) is saved with a warning.
- **D4. The agent write routes' own guards, an audit row, and a fixed developer.**
  - The guards are an admin key or session, refused to a key with a repository allowlist (`deployment_writes`,
    `embry0/api/v1/agents.py:37`). Both roles are granted and revoked the same way.
  - A change of roles, either one, on create, update or reset, writes `agent.roles_changed` with the roles before
    and after.
  - The built-in `developer` always keeps `coder`: it is the coder of a route that names none and of the legacy
    route.
- **D5. A job reads the roles when its route is planned, and a step asks again when it starts.** plan_route records
  each step's roles on the step, as #167 records `coding_agent`, and routing follows them. A step holding `coder` or
  `docs` asks its agent's row again when it starts: an agent deleted since, or one that no longer holds the role, does
  not run as it (a coder's step fails with `ERR_AGENT_ROLE_REVOKED`; a docs step opens no branch). The code's
  definition stands in only for a built-in agent, or where there is no database. Triage, which runs before planning,
  reads its own agent's row when it starts.
- **D6. A plan recorded before roles reads them from the built-in seed** (`row_behaviour.step_roles`), so a job
  paused mid-route resumes on the developer node. No list of names stays in the code.
- **D7. A step starts with no process of an earlier step running.** Before each step starts, and so before the push
  window opens, the processes the earlier steps left in the sandbox are stopped: everything but its init and the
  process that keeps it alive. A process a step starts in the background ends with that step.
  - Each process is stopped with its whole process group, and the stop goes over the process table again, a short
    pause apart, until a pass finds nothing left or 10 s have gone by: a process holding gigabytes can take seconds to
    end on a busy host. It is run again a few times when it does not end clean, or is itself stopped.
  - When no run ends clean, a step that may push (it holds `coder` or `docs`, or the branch is open already) does
    not start: the job fails with `ERR_SANDBOX_LEFTOVERS`. A step with no role, before the branch is open, still
    runs, and opens nothing.
  - **The stop covers processes only.** Files a step leaves in the sandbox stay for the steps after it: a git hook
    in the workspace, a program earlier on the `PATH`, a shell start-up file. A later step that may push runs with
    them. As seeded, triage holds `docs`, so the branch opens before the first step and every step of a job can push
    it; a file a step leaves gives a later step nothing that step could not do itself. An operator who takes `docs`
    from triage, to keep the early steps of a job closed, keeps them from pushing, not from changing the sandbox a
    later coder works in: holding those steps to a sandbox of their own is outside this design.

## 3. Data

Migration `max+1`, taken at merge time (163):

```sql
ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS roles TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE agent_definitions
    ADD CONSTRAINT agent_definitions_roles_known CHECK (roles <@ ARRAY['coder', 'docs']::text[]);
UPDATE agent_definitions SET roles = ARRAY['coder'] WHERE type IN ('developer', 'slopmaster');
UPDATE agent_definitions SET roles = ARRAY['docs'] WHERE type IN ('triage', 'review', 'context-auditor');
```

- `BUILTIN_SEED` gives the same: `coder` to `developer` and `slopmaster`, `docs` to `triage`, `review` and
  `context-auditor`, none to every other built-in. The boot seed stays insert-if-missing, and reset puts the seed's
  roles back.
- `row_behaviour.AGENT_DEFAULTS` gains `roles: ()`: a row read without the column holds none.
- The check constraint names the roles there are.

## 4. Behaviour

- **Planning.** plan_route reads the agents' rows (`row_behaviour.roles_by_type`) and records `roles` on each step,
  for the agent the step runs as (`route_plan.step_run_type`: a `reviewer` step runs the `review` row).
  - `_step_target` sends the step holding `coder` to the developer node.
  - `coding_agent_of` is that step's agent.
  - `cursor_at(state, "developer")` finds it.
  - The developer node runs the recorded coding agent's definition, not one of two names.
  - A template's save checks D2 against the rows.
- **What the developer node gives a coder agent:**
  - the job's branch, chosen as today (#478), open to push from its step on, or from an earlier docs step (D1);
  - delivery of that branch as a pull request at the job's end;
  - salvage of its unpushed work;
  - the review loop's re-entry;
  - done-when pacing;
  - the job's `coding_agent`, to which the pull request is credited.

  Its definition gives the model, the tools, the skills, the MCP servers and the system prompt.
- **What does not change.**
  - The Slopmaster's slop-cleanup extras stay keyed to the slop-cleanup route: its brief, the Slop-O-Meter, the
    cleanup diff and the review guide.
  - The built-in developer's prompt scaffolding is the node's, for any coder.
  - A guild task that names no template (`quest_template: ''`) stays a coder's, now read from the role.
- **Triage and run-time config (#332).**
  - Nothing a job decides at run time gives a role. Triage's decision, a job's `pipeline_config` and a template
    step's `config` carry no role, and a `roles` key in any of them is ignored.
  - Triage may narrow a coder's tools and skills as it narrows any agent's, never widen them.
  - Triage picks a template with a coder step only when the operator offered it (`offered_to_triage`).
- **Starting a step (D1, D7).** Each step's node starts by stopping the sandbox's leftover processes, then opens the
  job's branch if the step holds `coder` or `docs` and the job has not opened it yet, and records that it is open
  (`push_open`), which a resume reads.
- **The API.** `GET /agents` and `GET /agents/{type}` return `roles`. `POST` and `PUT` take it under D3's and D4's
  rules. `coding_agent` stays one release as `"coder" in roles`, then goes. The agent form shows each role an agent
  may hold, and the guild-task choice reads `coder`.

## 5. Pull requests

1. The column with both roles, the seed, the roles recorded on each planned step, the developer node and salvage
   reading the recorded coder, a second coder step refused, and `CODING_AGENT_TYPES` removed (D2, D5, D6).
2. The job's push window and the stop of leftover processes (D1, D7): init enrolls with no branch, each step stops
   what earlier steps left running and opens the job's branch if it holds `coder` or `docs`, and a resume restores
   what the job had.
3. The API, the audit row and the dashboard's agent form (D3, D4).

## 6. Acceptance tests

- **An operator's coder opens a pull request.** An agent of the operator's own with the `coder` role, named in a
  template, runs on the developer node, pushes its branch while its step runs, is the job's `coding_agent`, and has
  the branch delivered as a pull request.
- **Nothing without a role has a branch to push to.** A fake proxy manager records every branch list:
  - init enrolls with none;
  - triage holding `docs` opens the job's branch before it runs; holding none, it runs with none;
  - a step holding neither role, before any step that does, runs with none; the first that does opens it, and it is
    opened once;
  - a job none of whose steps holds either never opens it;
  - the developer's chosen branch replaces the job's own as today.
- **A resume restores what the job had:** the branch once it was opened, else none.
- **A background process from one step is gone when the next starts,** and the sandbox's init and keep-alive stay.
- **Setting the roles:**
  - refused to a non-admin caller and to a key with a repository allowlist;
  - `coder` refused on a definition that does not run in jobs and on the graph's own non-coding steps; `docs` refused
    on a definition that does not run in jobs;
  - `coder` refused when removing it from `developer`;
  - written to the audit log on every change of either.
- **No run-time path gives a role:** a triage decision or a `pipeline_config` that names one changes no step.
- **Templates:** one with two coder steps is refused at save; any number of docs steps saves.
- **Old plans:** a plan recorded without roles still sends `developer` and `slopmaster` steps to the developer node.
- **The migration:** it seeds both roles on the built-ins the seed gives them, refuses an unknown role, and
  `CODING_AGENT_TYPES` no longer exists (`tests/unit/workflows/test_no_builtin_name_lists.py`).
