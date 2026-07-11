# embry0 Glossary

> Single-paragraph definitions for every operator, scope, and node type embry0 exposes. Cross-referenced; entries link to one another so a reader can chase any thread without re-reading the docs.
>
> **When to update this file:** when you add a new operation, agent type, node type, pipeline primitive, or sandbox/scope concept. When a term gains a stable ID, record it here. When semantics change, bump the entry's date stamp.
>
> **Inspiration:** the Definiciones section of a hermetic practice protocol — a long-form glossary that makes every operator unambiguous. This is embry0's equivalent.

---

## Core Concepts

### embry0
The alchemical furnace. The product. A self-hosted, container-isolated agent platform that takes an issue + a repository and produces a reviewed pull request through a configurable [pipeline](#pipeline) of [agents](#agent). The name is literal: an embry0 is the slow, sustained-temperature furnace medieval alchemists used for transmutation work that needed days or weeks. The product is named for the patience and isolation it provides — not for spectacle.

### Job
A single end-to-end run of a [pipeline](#pipeline) against a specific [issue](#issue) and repository. Owns its own [sandbox](#sandbox), trajectory, logs, and final artifact ([Caja de Regalos](#caja-de-regalos)). Identified by a stable job ID. Re-runnable via [Replay-N](#replay-n).

### Pipeline
An ordered, possibly-branching graph of [agents](#agent), [functions](#function), and [commands](#command) that define how a [job](#job) progresses from intake to artifact. Edited via the [PipelineEditor](#pipelineeditor). Stored as a `PipelineGraph` with nodes, edges, and `metadata`. May carry an [operation](#operation) tag in its metadata to declare its alchemical character.

### Sandbox
A container-isolated execution environment created per [job](#job). Mirrors the Practice's [Contenedor](#contenedor) concept: a unit-of-work with its own boundary, its own inputs/outputs, and (once cleaned/sealed) immutability against further mutation. Sandboxes are spun up on the DinD layer with read-write rootfs (Claude CLI requires it), restricted network through credential proxies, and explicit per-sandbox bearer tokens for proxy enrollment.

### Issue
The unit of work that enters embry0. Originates from a webhook (GitHub) or manual API call. Pre-pipeline state. Becomes a [job](#job) when a [pipeline](#pipeline) is selected and dispatched.

### Template
A reusable, parametric [pipeline](#pipeline) shape. The [seven operations](#seven-operations) ship as templates plus three composite templates (`opus-full-cycle`, `op-refactor-suite`, `op-triage-and-decompose`). User-editable: clone, modify, save-as. Lives in the `pipeline_templates` table; loaded via the [TemplateDrawer](#templatedrawer).

---

## Pipeline Library — Building Blocks

The [PipelineEditor](#pipelineeditor) lets operators compose pipelines from four kinds of building block, ordered from atomic to composite:

### Energy
The atomic unit. A single capability, skill reference, or data value. Examples: a specific [agent](#agent) type, a specific tool (lint, typecheck), a specific environment variable. Identified by a stable [original ID](#stable-id) (e.g. `OP04` for the conjoin operation, `SP0014` for a superpowers skill). Energies are the leaves of pipeline graphs.

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
**OP07 — Stone.** The shipping operation. Merges an approved PR, deploys if the repo is configured, and captures the deployment receipt as the issue's closing comment. Performed by the [output](#output) agent. Output: a merged PR + deployment receipt. Sigil: 🜃.

---

## Agents

The five canonical agent **types**. Each is a system-prompt + tool configuration. Agents can be cloned and customized per-installation.

### Triage
The orchestrator-side agent that reads an [issue](#issue) and decides routing: which [pipeline](#pipeline), which [sandbox](#sandbox) profile, which agents downstream. Performs [Calcinate](#calcinate) and [Dissolve](#dissolve). Substance sigil: Mercury ☿.

### Developer
The agent that writes code. Performs [Separate](#separate) and [Conjoin](#conjoin). Substance sigil: Sulphur 🜍.

### Reviewer
The agent that critiques code before merge. Reads diffs, raises concerns, requests changes. Substance sigil: Salt 🜔.

### QA
The agent that exercises a change against the full test matrix. Performs [Ferment](#ferment). Reports without fixing. Substance sigil: Antimony ⚯.

### Output
The agent that closes the loop — merges the PR, performs [Distill](#distill) (extract reusable template) and [Coagulate](#coagulate) (ship + record). Substance sigils: Aqua Vitae 🜈 (Distill), Sol ☉ (Coagulate).

---

## Pipeline Editor Internals

### PipelineEditor
The visual canvas for composing pipelines. Located at `frontend/src/components/pipeline-editor/`. Built on `@xyflow/react`. Supports drag-from-bar agent placement, edge inference, circular and dagre auto-arrange, feedback edges, and operation-tag display in the canvas header.

### TemplateDrawer
The slide-in drawer in the [PipelineEditor](#pipelineeditor) that lists saved [templates](#template). Cards show the template name and (when set) the [operation](#seven-operations) sigil + tagline. Click a card to load its `PipelineGraph` into the canvas.

### AgentBar
The horizontal bar at the bottom of the [PipelineEditor](#pipelineeditor) holding draggable agent chips. Each chip carries its substance sigil and color. Drag a chip onto the canvas to place an agent node. Filters out [triage](#triage) (orchestrator-side, not a pipeline node).

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
A prefix-disambiguated, sequence-numbered identifier that survives renames. Format: `<PREFIX>##` (e.g., `OP04` for the conjoin operation, `SP0014` for a superpowers skill). The slug (`conjoin`, `tdd`) is the human handle; the stable ID is the durable reference cited in audit logs, PR metadata, and cross-document references. Borrowed from the Practice's `A0050`, `P0007`, `M_00` pattern. Pairs with [Revision Date](#revision-date).

Current ID prefixes:

| Prefix | Domain | Example |
|---|---|---|
| `OP` | Operations | `OP04` (conjoin) |
| `SP` | Superpowers skills | `SP0014` (planned) |

### Revision Date
ISO-8601 (`YYYY-MM-DD`) date marking the last semantic change to a [stable-ID](#stable-id) entity. Bump when behavior changes, not on cosmetic edits. Together, `(id, rev)` answers "what version of OP04 was this pipeline built against?" and lets the system flag drift when an old pipeline is loaded against a newer operation definition.

---

## Conventions

### "Practice" not "Ceremony"
Per the embry0 [project CLAUDE.md](../CLAUDE.md): "NEVER use 'ceremony' — use 'practice'. No ritualistic, new-age, or woo-woo framing." This rule extends to all docs, UI copy, and skill text. Hermetic vocabulary stays in the [divine layer](#divine-layer) where it's an aesthetic choice; functional language stays plain.

### Divine Layer
The atmospheric SVG/sigil layer that gives embry0 its alchemical identity. Lives in `frontend/src/components/divine/`. Strict rules in `frontend/src/components/divine/CLAUDE.md`: pure SVG only, `currentColor` stroke, animation suppressed by default, escape hatch via `<body data-divine="off">`, never on operator-critical paths (errors, destructive confirms, latency-sensitive lists), never combine more than one divine element per component instance.

---

## See also

- [`frontend/src/components/divine/operations.ts`](../frontend/src/components/divine/operations.ts) — operation definitions and stable IDs
- [`frontend/src/components/divine/CLAUDE.md`](../frontend/src/components/divine/CLAUDE.md) — divine layer hard rules
- [`docs/architecture.md`](architecture.md) — system architecture
- [`CLAUDE.md`](../CLAUDE.md) — project rules
