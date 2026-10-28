# embry0 Glossary

> Single-paragraph definitions for every operator, scope, and node type embry0 exposes. Cross-referenced; entries link to one another so a reader can chase any thread without re-reading the docs.
>
> **When to update this file:** when you add a new operation, agent type, node type, pipeline primitive, or sandbox/scope concept. When a term gains a stable ID, record it here. When semantics change, bump the entry's date stamp.
>
> **Inspiration:** the Definiciones section of a hermetic practice protocol — a long-form glossary that makes every operator unambiguous. This is embry0's equivalent.

---

## Core Concepts

### embry0
The alchemical furnace. The product. A self-hosted, container-isolated agent platform that takes an issue + a repository and produces a reviewed pull request through a configurable [pipeline](#pipeline) of [agents](#agents). The name is literal: an embry0 is the slow, sustained-temperature furnace medieval alchemists used for transmutation work that needed days or weeks. The product is named for the patience and isolation it provides — not for spectacle.

### Job
A single end-to-end run of a [pipeline](#pipeline) against a specific [issue](#issue) and repository. Owns its own [sandbox](#sandbox), trajectory, logs, and final artifact ([Caja de Regalos](#caja-de-regalos)). Identified by a stable job ID. Re-runnable via [Replay-N](#replay-n).

### Pipeline
An ordered, possibly-branching graph of [agents](#agents), [functions](#function), and [commands](#command) that define how a [job](#job) progresses from intake to artifact. Edited via the [PipelineEditor](#pipelineeditor). Stored as a `PipelineGraph` with nodes, edges, and `metadata`. May carry an [operation](#the-seven-operations) tag in its metadata to declare its alchemical character.

### Sandbox
A container-isolated execution environment created per [job](#job). Mirrors the Practice's [Contenedor](#contenedor) concept: a unit-of-work with its own boundary, its own inputs/outputs, and (once cleaned/sealed) immutability against further mutation. Sandboxes are spun up on the DinD layer with read-write rootfs (Claude CLI requires it), restricted network through credential proxies, and explicit per-sandbox bearer tokens for proxy enrollment.

### Issue
The unit of work that enters embry0. Originates from a webhook ([forge](#forge) issue events or a Linear [tracker](#tracker) event) or manual API call. Pre-pipeline state. Becomes a [job](#job) when a [pipeline](#pipeline) is selected and dispatched.

### Forge
The platform that hosts a repository's git remote and its change-request surface — GitHub or GitLab. The word is the industry term for an SCM host (SourceForge begat it), chosen over "provider" (overloaded by model providers) and "platform" (means nothing). A repo has exactly **one** forge, recorded as the `forge` discriminator on the `repos` table (#162); it decides which credential proxy a sandbox enrolls with, whether job completion opens a pull request or a merge request (`_open_change_request`), and which API client comments on the change. Not the same thing as a [tracker](#tracker): a repo's code can live on one forge while its issues live in a separate tracker. (2026-08)

### Organization
A forge owner embry0 maintains as a whole: a GitHub organization or user, or a GitLab group, held as a row in `organizations` (ADR 0010) with its [forge](#forge), the [credential](#credential) embry0 acts with there, and `access`, what the probe learned that credential can do by calling the forge. A discovery run (`org_discovery_runs`) reads every repository under the owner into one fact row per repository (`org_repo_facts`). Registering an organization changes nothing for any repository until a person acts. See [ADR 0010](adr/0010-organizations.md). (2026-10)

### Need
One thing embry0 still needs from a person to maintain an [organization](#organization), a row in `org_needs` (ADR 0011). Needs are derived from the newest finished discovery run, from a closed vocabulary of kinds (`KINDS` in `embry0/services/org_needs.py`), one row per owner, kind and repository set, so a person's answer (dismiss or reopen) survives rediscovery and a need no longer derived is `done`. Each open need is a [healing case](#healing-case) with origin `org`, awaiting a decision. See [ADR 0011](adr/0011-organization-needs.md). (2026-10)

### Tracker
The system an [issue](#issue) originates from and is written back to — GitHub Issues, GitLab Issues, or Linear. Recorded per-issue as the generic `tracker` / `tracker_key` / `tracker_url` columns (migration 60): `tracker_key` is TEXT so Linear's `ENG-123` identifiers fold in alongside the forges' integer numbers. A [forge](#forge) always implies a same-named tracker, but a tracker need not be a forge — Linear hosts no git remotes, which is exactly why the two concepts are separate. (2026-08)

### Credential
One account embry0 acts as: a forge token (GitHub or GitLab), an LLM OAuth token or API key, an SSH key, or a tailnet API key. Stored encrypted in `credentials` with a label, a host, an optional git author, and a status (`ok`, `disabled`, `quarantined`). Never read from the environment by new code; see ADR 0006. (2026-09)

### Binding
The link from a scope — a repo, an owner or group, a [forge](#forge) host, or the whole deployment — to a [credential](#credential) for one role (`forge`, `llm`, `deploy`, `tailnet`). The resolver walks repo, owner, host, global, then the environment. A `pinned` LLM binding never falls back to the pool. (2026-09)

### Pool
The global-scope LLM bindings of one provider. A job with no repo or owner binding takes the first `ok` member by priority, then least recently used. Quarantine removes a member until its `quarantined_until` passes or an operator clears it. (2026-09)

### Account wait
A job parked because the LLM account it needs is spent or rate-limited until a known time and nothing ready can take its work: `status` `paused` with `jobs.account_wait` (provider, reason, until, mode; migration 116). It never fails for that. The account-wait loop goes on with it when `until` passes, or sooner once the registry says the account is usable again: from its last checkpoint (`resume`), or from the start (`relaunch`) when it was held before it began or is a qa, onboard or pr-review job, which park after their own cleanup. A run that finds its account spent first quarantines it and runs again on a ready model of the fallback order, when there is one. See [providers](providers.md#spent-account-waits). (2026-10)

### Host
A machine that runs code embry0 maintains, a row in `hosts` (ADR 0013): a name, an address, a `transport` (`tailnet`, `ssh`, `http`), roles, a `log_source` (journald, docker or loki, with a selector) and a status (`active`, `unreachable`, `removed`). `repo_hosts` links it to the repositories it serves, each with a role. Added by hand or imported from a tailnet; a probe checks it is reachable. See [hosts.md](hosts.md).

### Standard
What a forge owner holds every repository under it to, one row per version in `standards` (ADR 0014). A standard of a `kind` (`design` first) is a **schema**, the token roles, type scale and component rules every standard of the kind names, and **values**, the owner's fill of those roles; every value names a declared role. embry0 ships the schema's vocabulary and the audit, never an organization's values. One version per owner and kind is `active`; a write supersedes it. Each repository has a mode under it in `repo_standard_state` (`off`, `advise`, `enforce`; `off` until an admin sets another, and never audited in `off`), with the score and finding count of its newest completed audit. See [standards.md](standards.md). (2026-10)

### Settings document
The deployment's operator policy, one YAML document in `settings_documents` (ADR 0026), with one **section** per feature: a model in `embry0/settings/sections/` that holds the section's defaults and bounds. The document stores only what an operator wrote; every other key reads as its default. A write is validated as a whole, and every change is a revision in `config_revisions` whose newest can be undone. Loops read their section from a **snapshot** at the top of each tick, refreshed when the document's `generation` moves. A policy environment variable a section replaces is imported once per key, its **bootstrap**. Not secrets, which stay in the [credential](#credential) registry or the environment. See [configuration.md](configuration.md#settings-document). (2026-10)

### Context audit
A read of the files that steer a coding agent in a repository (`CLAUDE.md`, `AGENTS.md`, skills, rules: `INSTRUCTION_FILE_GLOBS` in `embry0/services/instruction_files.py`) for statements that went stale, run as a job on the `context-audit` pipeline template. An [organization](#organization) whose `context_audit_days` setting is on has its registered repositories audited on a schedule: when the fingerprint of those files changes, and again that many days after the newest audit once the branch moved. A person starts the due ones at once with `POST /api/v1/orgs/{owner}/context-audit/run`, through the same start. See [context-audit.md](context-audit.md) and [ADR 0020](adr/0020-context-audit-schedule.md). (2026-10)

### Healing case
The one lifecycle record for a finding, from detection to a healed verdict (#278). A case links `signature → watcher run → tracker ticket → issue → jobs[] → PRs[] → merge sha → deploy marker → healed verdict` with real foreign keys, plus an `origin` (watcher / human / webhook / alert / ci / org) and a `resolution_path` (embry0 / out_of_band / cancelled / non_code / self_resolved / abandoned). Its `stage` walks `detected → tracked → accepted → pr_open → merged → deployed → verified → closed`; the rules are the pure state machine in `embry0/services/healing/lifecycle.py`, which tolerates redelivered, reordered and stage-skipping events (an out-of-band human merge takes a `tracked` case straight to `merged`). Two events move a case back: `pr_closed` (its last open PR closed unmerged → `accepted`) and `reopened` (a `not_healed` verdict earns a retry): the confirm-healed sweep sends a `verified` case with that verdict back to `tracked`, at most `MAX_NOT_HEALED_RETRIES` (1) times per case, counted from the case's own `reopened` events (#451). The reopen only reopens the ledger and dispatches nothing, so the retry is offered to a person, not started (#658): the case waits at `tracked`, listed by `awaiting_decision` and holding no draft slot, aged from the reopen (it stamps `tracked_at` and clears `accepted_at`). Accepting it (`POST /cases/{id}/accept`) starts a new job on the issue of the failed attempt, whatever the tracker, and that job's link accepts the case; a tracker label never repeats delivered work, so a label left on the ticket cannot start the retry by itself. It never waits at `accepted`, which means a job should be starting and is what `Embry0HealingAcceptedCaseNotDispatched` pages on. Every `not_healed` verdict states in its evidence what follows (`retries: {used, allowed, next}`); once the budget is spent, `next` is `a person decides`, the ticket is told so, and the case stays `verified`. One event ends a case that never shipped anything: `abandoned` (#410) closes an `accepted` case as `resolution_path=abandoned` — a person answered "abandon" at the retry pause and no PR came of it. It is deliberately narrow in both directions: only that choice raises it (a job that merely *failed* leaves its case open, because the acceptance stands and an undelivered acceptance is what `Embry0HealingAcceptedCaseNotDispatched` is for), and it closes only from `accepted` (past `pr_open` the change request outlives the job, so the event is history and the data is *held*, never written). Before it there was no terminal state for an accepted case at all: it sat at `accepted` forever, nothing could move it, and that critical alert paged on a decision already made — masking any genuinely undispatched acceptance behind it. `closed` is terminal — what arrives afterwards is kept as history only, and a later regression, reopened ticket or reused ticket is a **new** case pointing at the old one via `parent_case_id` (a ticket has at most one *open* case). A case keeps every PR it ever had (`healing_case_prs`, each with its own outcome) and the **first** deploy carrying its merge as the confirm-healed anchor. **`verified` does not mean a fix shipped:** a verdict needs no merge, because findings also heal by themselves or outside the code. "A fix is deployed" is `lifecycle.fix_deployed` — a merge sha, and a deploy that is not older than the merge — exposed by the API as the `fix_deployed` field and list filter; never a comparison of stages. A deploy is never refused: one that arrives before its merge is kept in the history and becomes the anchor when the merge lands, and `reopen` clears merge and anchor so a retry is never judged from the failed attempt's deploy — a redelivered merge of the discarded attempt (its PR already merged, its sha already reported, or its time not later than the reopen) is kept as history and never becomes the retry's merge. Replacing the anchor with an earlier deploy voids a verdict measured from the old one (back to `pending`). Timestamps on every write must be timezone-aware and not in the future; the executable `signature` is refused when over-long, never truncated. A `healed` verdict on a case nothing resolved sets `resolution_path=self_resolved` at once; with a merge, healing is credited to the merge's path, and a merge that turns up later replaces `self_resolved` — so `self_resolved` always means "no merge". **Evidence is counts and queries, never raw log lines:** history rows copy the call's data and the API serves them, so `verdict_evidence` is refused above 16 KiB and human-readable free text (`title`, `note`, reopen `reason`) is truncated at the service boundary. Every write goes through `HealingCaseService`; each history row stores its exact column change, so the case row can be rebuilt from `healing_case_events`. Reads are `GET /api/v1/cases[/{id}]` and the `case_list` / `case_get` MCP tools. "Which drafts is someone still to answer?" is the list's `awaiting_decision` filter (the dashboard's Proposals list, and the same flag on `case_list`; it also lists a case a reopen sent back, and an organization need waiting for a person, origin `org`) — not `open_only`, which is every case short of `closed`, and not `stage=tracked`. The two writes a human makes are `POST /cases/{id}/accept` (dispatch the fix, #378) and `POST /cases/{id}/close` (settle it, #410 — `resolution_path` required unless the case already has one, refused 409 on a closed case, exposed as MCP `case_close`); everything else on a case is written by the pipeline. Tables: `healing_cases`, `healing_case_jobs`, `healing_case_prs`, `healing_case_events` (migration 69). **Writers (part 2):** the pipeline reports through the fail-open `CaseRecorder` (`embry0/services/healing/recorder.py`) — a ledger error is logged with the ids to repair and never stops a ticket, a dispatch or a PR; every call carries an `event_key` from a stable id. The watcher opens a case per proposal (and counts a `skipped_duplicate` tick as a recurrence, and closes a draft cancelled in Linear as `cancelled`), keyed on a `signature_id` that is a window cluster the proposal's own evidence quotes (`signature_source=evidence`, #657); only when nothing it quotes is a line of the window is it the window's loudest line (`fallback`), and confirm-healed answers `inconclusive` for such a case instead of judging a line the finding never mentioned; `IssueExecutor.execute` — where every dispatch ends — links each job, accepting the case, and opens an `origin=human` case for a labelled Linear ticket nobody opened one for; a job's PR is attached where `pr_url` is first persisted. A PR's `opened_at` is the FORGE's (`GitHubPrTimes` reads `created_at`), never the moment the job that opened it finished — readers judge staleness by it; when the forge cannot say, the job's start is used as a bound that is never later than the truth, and the gap is logged. A PR is matched across every spelling of a renamed repo (`repo_url_variants`), because nothing rewrites stored URLs after a rename: a row written before it keeps the old slug. Deploy markers (`deployed <sha>: <services>` under `WATCHER_DEPLOY_LOGQL`) are reported by `DeployLedgerSync` only after the forge confirms ancestry — `GET /repos/{repo}/compare/{deployed}...{merge}` must say `behind`/`identical` AND name the merge as the merge base; anything undetermined is not reported. (2026-09)

### Done when
A code job's finish line (`jobs.done_when`, migration 120; spec `docs/superpowers/specs/2026-10-26-done-when-design.md`): 2–5 checkable conditions, each with how it is shown (a test passes, a command prints something, a written diagnosis names the cause), plus what is out of scope.
- **Who sets it.** Triage drafts it for every code job, from the ticket's own acceptance criteria when it has them. Those criteria are also the fallback. A person can replace it on the job page.
- **The developer's contract.** The developer, or the [Slopmaster](#slopmaster), gets it as its contract, with pacing: by half its turns it commits; by three quarters it finishes against the list. It ends its final JSON with a report, each item met or not with evidence.
- **The check.** embry0 reads the report strictly. An item unmet or unreported sends the coding step once more with the unmet items named. Still unmet, the job pauses (`done_when_unmet`), naming them.
- **Review and QA** judge the change against the same list. (2026-10)

### Delivery state
What became of the pull request a job produced — `none` / `pr_open` / `merged` / `closed` (`jobs.delivery_state`, #280) — kept apart from the job's `status`, which says how the JOB ended. The two are independent: a job can end `failed` (a late stage broke, a budget ran out) after opening a PR that a human then finished and merged, and that is delivered work, not a failure. `status` values keep their meaning: `pr_merged` / `pr_closed` are a job that `completed` and whose PR was then merged / closed unmerged, so `status = pr_merged` implies `delivery_state = merged` but never the reverse; `failed`, `cancelled`, `partial` and `expired` never change because of a PR. One code path maintains both — `services/merge_sync` (`DeliveryRecorder`), fed by the `pull_request` webhook, the periodic GitHub sweep and the backfill CLI. A PR is matched to jobs by its stored URL (renamed-repo forms included) and, for jobs that never stored one, by the branch the job pushes to, re-derived from the job row. `delivery_state_at` is the FORGE's time of the state (`merged_at` / `closed_at` / `updated_at`), which is what orders redelivered and out-of-order webhooks: a merge is final, otherwise the newer fact wins. `/stats` reports both outcomes: `job_success_rate` (status only), `delivery` (counts per state, `merged_despite_job_status`, `merge_rate`) and `success_rate` (completed OR PR merged). (2026-09)

### Pull request review
The `pr-review` pipeline (#30, `docs/pr-review.md`): an advisory review of someone's pull request or merge request, posted on it as a summary plus inline comments, never an approval or a block. It starts when a pull request becomes ready (or opens as non-draft) on a repository whose `repo_preferences.pr_review.enabled` is on (by default only when the pull request's author has write access, `pr_review.ready_authors: team`; `anyone` lifts that), or on a `/embry0 review` comment from someone with write access; a re-review continues the previous review's agent session. One row per review in `pr_reviews`, whose UNIQUE `trigger_key` makes a redelivered webhook a no-op. A fork is always reviewed read-only. (2026-10)

### Verification (tier, test gate, human-merge-only)
The agent-free `verify` step after an approved review (#298, #299; `docs/verification.md`). The **tier** is a pure classification of the diff — `neutral_only`, `backend_only`, `frontend`; a path that matches no rule, or an unknown diff, is the stricter `frontend`. The **test gate** is the orchestrator itself running the repo's stored test commands (`repo_preferences.verification`, never a file in the target repo) on the base commit and on the job's head in the dev sandbox: only failures that are *new* on head fail the job (`ERR_VERIFY_NEW_TEST_FAILURES`); red-on-base-too is *pre-existing*, red-then-green is *flaky*, both recorded and not blocking. **Human-merge-only** is the result's one bit for auto-merge: embry0 did not verify the change well enough for a machine to merge it (a frontend change with no branch-booted QA app, a gate that could not run or is not configured, a base too red to compare). A **branch job never QAs a `target: deployed` app** — the deployed site does not contain the branch; deployed-target QA is for standalone QA jobs. One result per job lives in `job_verifications` (migration 72), looked up by head sha. (2026-09)

### Deferred finding
A watcher proposal made while the draft cap was full (#283): the tick is recorded as `deferred`, and the finding is a [healing case](#healing-case) at `detected` with **no tracker ticket** — so it holds no slot of the cap, which counts only open, unaccepted watcher cases that have a ticket (`count_open_unaccepted_watcher_cases`). The cap defers *filing*, never *looking*: a full cap still fetches and analyses the window. When a slot frees, the oldest deferred finding is filed — at most one per tick, in place of that tick's own filing — from the proposal its own analysis job wrote (no second analysis), and its run row becomes the `proposed` row of the new ticket. One not seen again for a whole `WATCHER_PROPOSAL_WINDOW_DAYS` is closed as `cancelled` instead. At most `WATCHER_MAX_DEFERRED_FINDINGS` (3) wait; with the cap and that queue both full the paid analysis is skipped (`skipped_backlog`, with the window's line count). **Spend:** a full cap used to end the tick before Loki was queried, so it was also the de-facto daily spend cap; the analyses it now pays for have their own — `WATCHER_MAX_FULL_CAP_ANALYSES_PER_DAY` (6) per rolling 24 h, counted durably from `watcher_runs.analysed_under_full_cap` (migration 70) so a restart cannot refill it, and failing CLOSED when the counter cannot answer. A tick that runs out records `skipped_analysis_cap` — never a silent `no_issue`. Ordinary ticks are neither counted nor limited. (2026-09)

### Template
A reusable, parametric [pipeline](#pipeline) shape. The [seven operations](#the-seven-operations) ship as templates plus three composite templates (`opus-full-cycle`, `op-refactor-suite`, `op-triage-and-decompose`). User-editable: clone, modify, save-as. Lives in the `pipeline_templates` table; loaded via the [TemplateDrawer](#templatedrawer). Any agent definition's type is a valid template node; the fixed node types are the six the interpreter executes itself (see docs/api.md, "Agents in pipeline templates").

---

## Pipeline Library — Building Blocks

The [PipelineEditor](#pipelineeditor) lets operators compose pipelines from four kinds of building block, ordered from atomic to composite:

### Energy
The atomic unit. A single capability, skill reference, or data value. Examples: a specific [agent](#agents) type, a specific tool (lint, typecheck), a specific environment variable. Identified by a stable [original ID](#stable-id) (e.g. `OP04` for the conjoin operation). Energies are the leaves of pipeline graphs. A skill is referenced by its `plugin:skill` name; it has no second id.

### Function
A pure, side-effect-light transform over a [container](#contenedor). Takes inputs, returns diagnostics or a transformed value, does not mutate persistent state. Examples (proposed): `f_lint(target)`, `f_typecheck(target)`, `f_test(target)`, `f_search(query)`, `f_format(target)`. Functions can be chained; a function's output is another function's input. Mirrors the Practice's `f_luz()`, `f_conocimiento()`.

### Command
A named, idempotent operation with explicit guards/preconditions. Mutates state. Safe to retry. Examples (proposed): `Cleanup(scope)`, `Migrate(scope)`, `Deploy(scope)`, [`Replay-N`](#replay-n). Mirrors the Practice's `Limpieza()`, `Salud()`, `Repito los N Comandos`. Distinguished from [Functions](#function) by mutation; distinguished from [Templates](#template) by atomicity.

### Template
See [above](#template). The composite of the four — a parametric pipeline assembled from energies, functions, commands, and other templates.

---

## The Seven Operations

The canonical alchemical operations, used as opinionated [pipeline templates](#template). Defined in `frontend/src/components/divine/operations.ts`. Each carries a stable [ID](#stable-id) (`OP01`–`OP07`), a Roman numeral position, an element, and a one-sentence coding meaning.

| ID | Operation | Coding meaning |
|---|---|---|
| `OP01` | [Calcinate](#calcinate) | Burn down a feature spec to its essential intent |
| `OP02` | [Dissolve](#dissolve) | Decompose a monolith into independent units |
| `OP03` | [Separate](#separate) | Separate concerns inside a chunk of code |
| `OP04` | [Conjoin](#conjoin) | Merge two divergent things into one |
| `OP05` | [Ferment](#ferment) | Let a change cook in CI / staging |
| `OP06` | [Distill](#distill) | Extract reusable essence from a successful job |
| `OP07` | [Coagulate](#coagulate) | Solidify ephemeral work into a shipped artifact |

### Calcinate
**OP01 — Fire.** The triage operation. Reduces an issue body to a 1-2 sentence statement of what + why, stripped of noise. Performed by the [triage](#triage) agent. Output: a crisp intent statement. Sigil: 🜂.

### Dissolve
**OP02 — Water.** The decomposition operation. Splits a monolithic ask into the smallest set of independent units. Performed by the [triage](#triage) agent. Output: a list of subtasks with no shared state. Sigil: 🜄.

### Separate
**OP03 — Air.** The refactor operation. Identifies mixed concerns inside a chunk of code (pure logic vs side effects, business rules vs framework, public API vs private helpers) and refactors to separate them while preserving behavior. Performed by the [developer](#developer) agent. Output: a refactor PR. Sigil: 🜁.

### Conjoin
**OP04 — Sacred Marriage.** The merge operation. Unifies two divergent things — branches, services, type systems, vendors. Performed by the [developer](#developer) agent. Output: a unified PR. Sigil: ☿☉.

### Ferment
**OP05 — Earth (salt under pressure).** The soak operation. Runs a change through the full test matrix in CI / staging and reports what broke. Does not fix — reports only. Performed by the [qa](#qa) agent. Output: pass/fail evidence. Sigil: 🜔.

### Distill
**OP06 — Aether.** The extraction operation. Reads a successful job's trajectory and emits a new pipeline template named `<auto>-distilled-N`. Performed by the [output](#output) agent as a post-completion hook. Output: a new template. Sigil: 🝫.

### Coagulate
**OP07 — Stone.** The shipping operation. Merges an approved PR and captures the outcome as the issue's closing comment; deployment itself is the target repo's own concern (registry-pull pipelines, watchtower, etc.). Performed by the [output](#output) agent. Output: a merged PR + closing receipt. Sigil: 🜃.

---

## Agents

The five canonical agent **types**. Each is a system-prompt + tool configuration. Agents can be cloned and customized per-installation.

### Triage
The orchestrator-side agent that reads an [issue](#issue) and decides routing: which [pipeline](#pipeline), which [sandbox](#sandbox) profile, which agents downstream. Performs [Calcinate](#calcinate) and [Dissolve](#dissolve). Substance sigil: Mercury ☿.

### Developer
The agent that writes code. Performs [Separate](#separate) and [Conjoin](#conjoin). Substance sigil: Sulphur 🜍.

### Reviewer
The agent that critiques code before merge. Reads diffs, raises concerns, requests changes. Substance sigil: Salt 🜔.

### PR reviewer
The `pr-reviewer` agent of the [pull request review](#pull-request-review) pipeline. Unlike the [Reviewer](#reviewer), which judges the developer agent's change inside issue-to-pr, it reviews a change someone else made, reports findings without a decision, and runs the repository's `dev_checks` (or works them out) at the repository's review depth. (2026-10)

### QA
The agent that exercises a change against the full test matrix. Performs [Ferment](#ferment). Reports without fixing. Drives the target app's frontend through a [browser backend](#browser-backend). Substance sigil: Antimony ⚯.

### Browser Backend
The pluggable unit that supplies the [QA](#qa) agent's browser. A backend bundles four things: an MCP stdio server config, an exact-name tool allowlist, an auth-injection mechanism for pre-seeded login state, and a system-prompt fragment teaching the agent that backend's idioms. Two exist: `playwright` (default — `@playwright/mcp`, env-var storageState injection) and `vibium` (WebDriver-BiDi Go binary, tool-call `browser_restore_storage` injection, evaluate-shim console/network diagnostics). Selected per repo/app via the `browser_backend` field in qa.yaml v2 (defaults → app → app_local), overridable per job; both coexist in the `embry0-sandbox-qa` image. Registry lives in `embry0/workflows/qa/browser_backends.py`; design record in `docs/spikes/2026-08-27-vibium-browser-backend.md`. (2026-08)

### Roster
The pixel avatars an agent can wear (`avatar` on its row; ids in `frontend/src/lib/agentIdentity.ts`): twelve legacy ones drawn as 16x16 character grids in `frontend/src/components/roster/sprites.ts` and compiled to SVG paths at load, tinted by the agent's palette `color`, and twenty adventurers composed from an [appearance](#appearance). Not part of the [divine layer](#divine-layer) (its CLAUDE.md rule 9).

### Appearance
How an adventurer avatar is defined, the way a classic pixel-art RPG defines a player: a body kit (skin, hair, beard, four colour slots) plus nine equipment slots (head, cape, neck, weapon, body, shield, legs, hands, feet), rendered by layering parts and items on a 32x32 grid (`frontend/src/components/roster/appearance/`, spec `docs/superpowers/specs/2026-10-20-adventurer-roster-design.md`). Gear colours are fixed per tier; only the cape wears the party colour. The named appearances are the presets (`starter`, the tier warriors, `champion`, the mages, the archers, and the extras), each an avatar id in the identity contract.

### Sprite
One roster avatar in one state (`idle`, `thinking`, `tool`, `done`, `failed`, `pending`), rendered by `<Sprite>` and stepped between two frames by one CSS keyframe under `prefers-reduced-motion: no-preference`. `stateFor` maps an agent run to a state, `stateForNode` a live graph node. A `still` sprite draws its first frame only, for one repeated down a list such as a transcript's agent markers. Assistive tech hears the display name and the state in words ("Developer, using a tool", "Developer, waiting"). Every surface that shows an agent shows its sprite (agents list and form, console cards, job page, [PartyNode](#partynode)).

### PartyNode
The one agent node renderer (`frontend/src/components/pipeline/PartyNode.tsx`) the [PipelineEditor](#pipelineeditor) and the live job graph both register: the agent's [sprite](#sprite) inside a ring in its palette colour, the display name on a pixel nameplate, and one HUD line (skill count and model in the editor; attempt, cost and turns live). The start and end sentinels keep their gold sphere.

### Party strip
The row of run sprites at the top of the job page (`frontend/src/components/jobs/PartyStrip.tsx`): one 32 px [sprite](#sprite) per agent run in run order and in its state, the display name underneath, the graph's not-yet-started nodes in `pending`. Selecting a run filters the Conversation tab to that run and highlights its node on the party map; the map's selection does the same in reverse.

### HUD
The job page's one-line status bar (`frontend/src/components/jobs/JobHud.tsx`, the `.hud` pixel class): status, elapsed, cost, turns, tokens in and out, and a budget meter when the job carries `max_budget_usd` (amber from 80 percent, red at 100). Also the one HUD line under a [PartyNode](#partynode).

### Output
The agent that closes the loop — merges the PR, performs [Distill](#distill) (extract reusable template) and [Coagulate](#coagulate) (ship + record). Substance sigils: Aqua Vitae 🜈 (Distill), Sol ☉ (Coagulate).

### Slopmaster
The `slopmaster` agent: the cleanup of slop, not the fix of a bug. A code job that triage judges [slop](#slop) runs the `slop-cleanup` template (triage → slopmaster → reviewer → qa → output), and its `slopmaster` step runs on the developer node, under its own agent type and definition: it works out what the code is meant to do, keeps that behavior (its tests prove it), deletes and merges instead of adding guards or retries, and opens a pull request like the developer. The reviewer approves a cleanup only when behavior is unchanged, the change shrinks or plainly simplifies the code, and it adds no guard, retry or fallback. A merged cleanup earns the Slopmaster its `cleaned` experience: 20,000 plus 300 per net line removed. Spec: `docs/superpowers/specs/2026-10-25-slop-dungeon-design.md`. (2026-10)

### Slop
What the slopometer calls a finding or a code job whose code does what it says, where the trouble is the code's shape: duplication, dead code, half-wired features, layers that contradict each other, symptom patches (guards, retries, catch-alls stacked on a symptom), docs that say what the code does not do, or code that ignores the repository's own patterns. The other natures are **bug** (behavior differs from what is intended) and **noise** (nothing to do). The watcher (its `NATURE:`/`SLOP_*` contract lines) and triage (its decision's `work_nature`) both classify, as `{nature, slop_score 0-100, kinds, evidence}`; the verdict is stored on the job and the case (`work_nature`, migration 118). A slop finding's draft ticket opens with a **Slop, not a bug** section, its proposal reads as a cleanup, and its job runs the [Slopmaster](#slopmaster). (2026-10)

### Slop Dungeon
The world map's place for a repository's or an area's open [slop](#slop): a cave beside its town (or its region), with a slop score that is `100 × (1 − Π(1 − sᵢ/100))` over its open slop's scores, so it rises with one heavy item or many light ones and falls as each is cleaned, and the count of cleanups merged in it in the last 30 days. A slop finding on the quest board is a `slop` lead (verb **Clean up**, an ooze) led by the Slopmaster. Each dungeon carries its area's [Slop-O-Meter](#slop-o-meter) reading, and an area that reads Spaghetti Bolognese or worse has a dungeon even with no open finding. (2026-10)

### Slop-O-Meter
How much of a repository's (or an area's) code landed beyond human attention, after [Slop-O-Meter](https://github.com/slop-o-meter/slop-o-meter)'s published slop score algorithm, reimplemented from that description. Each week's **attention cost** is its net weighted additions (lines weighed by file type, half for tests, nothing for non-code, vendored, generated, linter-ignored or small outlier files), dampened by the codebase's size; its **capacity** is the human session time behind it (commits, co-authors, squash merges, comments and reviews, bots left out, each contributor's hours weighed by how core they are and capped concavely at 80 a week) at 40 lines an hour. The excess is slop, and the score is the history's slop over its cost: Buttered Noodles (< 0.2), Spaghetti Bolognese, Lasagna, Sloppy Joe, Just The Slop (≥ 0.8). Outlier commits (thousands of lines at once) are judged by a model; until one is, the score is **provisional**. Read daily for the repositories the world map shows (plus `SLOP_METER_REPOS`, minus `SLOP_METER_EXCLUDE`), from a bare mirror and the forge's events under a per-tick API budget that stops at a rate-limit reserve; triage and the Slopmaster are told the job's area's reading. Spec: `docs/superpowers/specs/2026-10-26-slop-o-meter-design.md`. (2026-10)

### Character
An agent type as the progression layer sees it: the content pack's skills (23 in the shipped pack) trained by its runs and outcomes, a level (1 to 126 in the shipped pack), a tier it wears, and the mastery cloaks and rares it earned. Recomputed from history, never stored. See [progression.md](progression.md). (2026-10)

### Content pack
The document that holds everything the progression layer names, counts and says: the skills and the action categories that train each, the curve and rates, the tiers and the gear art each wears, the rares, the achievements (a metric and its parameters each), the quests and diary tasks (a kind and its parameters each), the timezone and the chat box's words. Its schema is code and its values are data; embry0 ships one, `embry0/services/progression_pack_default.yaml`, and one pack is active per deployment. A different pack re-prices the whole history. See [progression.md](progression.md#the-content-pack). (2026-10)

### Action category
What one tool call of a run was, as the classifier sees it, such as `shell:test-runner` or `edit:docs` (`embry0/execution/skill_actions.py`). A trace stores its calls' counts by category, never by skill, so the [content pack](#content-pack) decides which skill each category trains. (2026-10)

### Tier
A rung of the content pack's ladder: the gear a character wears, from its best skill's level, and an achievement's steps, which climb the same ladder. The shipped pack's ladder is a medal ladder, Bronze, Silver, Gold, Platinum, Diamond, Obsidian, Divine. See [progression.md](progression.md). (2026-10)

### Quest and diary
A **quest** is a short goal the account completes once, for quest points and an experience reward; a **diary** is a per-repository checklist in tiers (Easy to Elite in the shipped pack). Both are read from history, and both are the content pack's. See [progression.md](progression.md). (2026-10)

### Knowledge map
A scanner's picture of a codebase or of an organization's systems, uploaded as one `embry0.map/1` document: its parts on a plane, the links between them, and per part a bloat score with its reasons, its use and its history. embry0 shows it on the Maps page and does not scan. People mark parts bloat, keep or not sure; a part in a repository that someone marks bloat becomes a `weed` [lead](leads.md) whose job removes it without re-assessing the decision, and any part can be sent for a fix or an analysis. Spec: `docs/superpowers/specs/2026-10-27-knowledge-maps-design.md`; see [maps.md](maps.md). (2026-10)

### Specialist agents
Ten read-only built-in agents that each do one job on a cloned repository and return one Markdown report: `kintsugi` (template `kintsugi-trace`), `gardener` (`garden`), `interrogator` (`interrogate`), `why` (`why`), `how` (`how`), `blast-radius` (`blast-radius`), `comment-sicko` (`comment-pass`), `design-auditor` (`design-audit`), `context-auditor` (`context-audit`) and `area-mapper` (`map-areas`). Like research and analysis they run on the generic agent node, so each one's behavior is its system prompt; their tools stop at Read, Glob, Grep and Bash, and they open no pull request. Each template is `triage → agent → output`, and a job reaches one only by naming it: triage is not offered them. The job is recorded as an analysis job, and the report is its deliverable. See [docs/specialist-agents.md](specialist-agents.md). (2026-10)

---

## Pipeline Editor Internals

### PipelineEditor
The visual canvas for composing pipelines. Located at `frontend/src/components/pipeline-editor/`. Built on `@xyflow/react`. Supports drag-from-bar agent placement, edge inference, circular and dagre auto-arrange, feedback edges, and operation-tag display in the canvas header.

### TemplateDrawer
The drawer in the [PipelineEditor](#pipelineeditor) that lists saved [templates](#template): a `Dialog` at the left edge, closed by Escape, its backdrop or its Close button. Cards show the template name and description; click a card to load its `PipelineGraph` into the canvas. Each card's menu renames, duplicates or deletes the template.

### SkillsPage
The dashboard page at `/skills` (Infra, admin) that lists [skill sources](api.md#skills) with their sync status, adds git or path sources, edits inline skills (published as the builtin `local` source), and shows the resulting catalog of `plugin:skill` names.

### MergeApprovalsPage
The dashboard page at `/merge-approvals` (Work) that lists the merge asks ([merge-approvals.md](merge-approvals.md)) by status, pending first; each ask has its own page at `/merge-approvals/{id}` with the change, the CI checks, the verification gate, the reviewer's verdict, the recommendation and, for an admin, Approve and Reject behind a `ConfirmDialog`. The decision uses the dashboard form of `POST /merge-approvals/{id}/decide` (spec 3.8).

### HostsPage
The dashboard page at `/hosts` (Infra) over the [host](#host) routes: the list with its source filter and a Show-removed switch, a probe from each row's menu (operator), and for an admin the add and edit form (`HostFormDialog`), Remove and Restore, and the tailnet import, with Remove and the import behind a `ConfirmDialog`. See [hosts.md](hosts.md).

### AgentBar
The horizontal bar at the bottom of the [PipelineEditor](#pipelineeditor) holding draggable agent chips, one per agent definition plus the terminal `output` step. Each chip shows the agent's sprite and display name in its identity colour, with the tagline as its tooltip. Drag a chip onto the canvas to place an agent node. A "New agent" link after the chips opens the agent form. Filters out [triage](#triage) (orchestrator-side, not a pipeline node).

### Replay-N
A [command](#command) that re-executes the last N completed stages of a [job](#job) as a unit. Mirrors the Practice's `Repito los N Comandos`. Use case: a config or environment variable changed; you want stages 5-8 re-run with the new value, but not the entire pipeline.

---

## Scope & Container Concepts

### Contenedor
The Practice's term for a unit-of-work scope. embry0's [sandbox](#sandbox) is a Contenedor. So is a [job](#job). The Contenedor pattern includes auto-encapsulation: once the work inside is complete and validated, the boundary becomes immutable. This maps to embry0's run records — once a job is done, its trajectory and artifacts are sealed.

### Conjunto
The Practice's term for a composite scope built by explicit inclusion. Each inclusion is a guarded clause: "Que no exista la posibilidad de que haya X que no esté Y." embry0's [pipelines](#pipeline) and [templates](#template) should adopt this pattern: explicit declaration of what's in scope, guarded by preconditions, rather than implicit globals.

### Caja de Regalos
The Practice's term for a wrapped output artifact: a labeled box, a ribbon, an [Armadura](#armadura), and an explicit sender. Maps to embry0's per-job output envelope:

- **Label** — commit subject + embry0 run-ID
- **Ribbon** — PR description (auto-generated from the spec)
- **Armadura** — integrity hash + signing tag (bidirectional protection: run can't be modified post-hoc, source spec can't be retroactively edited without invalidating the run)
- **Sender** — explicit `agent_id` (e.g., `developer-v2.3 + qa-v1.7`) recorded on the run

### Armadura
Bidirectional integrity envelope. The Practice's pattern: "nada de afuera afecte adentro y nada de adentro afecte afuera." Applied to embry0's audit log: protects the run record from retroactive modification AND protects the source spec from edits that would invalidate the run.

---

## Identifiers

### Stable ID
A prefix-disambiguated, sequence-numbered identifier that survives renames. Format: `<PREFIX>##` (e.g., `OP04` for the conjoin operation). The slug (`conjoin`) is the human handle; the stable ID is the durable reference cited in audit logs, PR metadata, and cross-document references. Borrowed from the Practice's `A0050`, `P0007`, `M_00` pattern. Pairs with [Revision Date](#revision-date).

Current ID prefixes:

| Prefix | Domain | Example |
|---|---|---|
| `OP` | Operations | `OP04` (conjoin) |

### Revision Date
ISO-8601 (`YYYY-MM-DD`) date marking the last semantic change to a [stable-ID](#stable-id) entity. Bump when behavior changes, not on cosmetic edits. Together, `(id, rev)` answers "what version of OP04 was this pipeline built against?" and lets the system flag drift when an old pipeline is loaded against a newer operation definition.

---

## Conventions

### "Practice" not "Ceremony"
A project rule: never use "ceremony" — use "practice". No ritualistic, new-age, or woo-woo framing. The rule covers all docs, UI copy, and skill text. Hermetic vocabulary stays in the [divine layer](#divine-layer) where it's an aesthetic choice; functional language stays plain.

### Divine Layer
The atmospheric SVG/sigil layer that gives embry0 its alchemical identity. Lives in `frontend/src/components/divine/`. Strict rules in `frontend/src/components/divine/CLAUDE.md`: pure SVG only, `currentColor` stroke, animation suppressed by default, escape hatch via `<body data-divine="off">`, never on operator-critical paths (errors, destructive confirms, latency-sensitive lists), never combine more than one divine element per component instance.

---

## See also

- [`frontend/src/components/divine/operations.ts`](../frontend/src/components/divine/operations.ts) — operation definitions and stable IDs
- [`frontend/src/components/divine/CLAUDE.md`](../frontend/src/components/divine/CLAUDE.md) — divine layer hard rules
- [`docs/architecture.md`](architecture.md) — system architecture
- [`CLAUDE.md`](../CLAUDE.md) — project rules
