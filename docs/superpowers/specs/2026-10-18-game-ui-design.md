# Game UI: the roster, the party map, one live job page, and the surfaces the API already has

**Status:** draft for review, 2026-10-18
**Workstream:** 3 of 3 (trace fidelity → agent identity and skills → game UI)
**Tracker:** GitHub milestone "Game UI"
**Depends on:** workstream 2 (`docs/superpowers/specs/2026-10-18-agent-identity-and-skills-design.md`) for `display_name`, `avatar`, `color`, `tagline` on agent rows and `frontend/src/lib/agentIdentity.ts`

## 1. Problem

The dashboard is honest now (workstream 1) but it is not a place anyone wants to watch a job from, and it hides about a third of what the API can do.

1. **Agents are text.** A running job is a stage chip, a one-line ticker and a pulsing dot (`components/console/RunningCard.tsx`, `components/jobs/AgentCard.tsx:72-76`). Nothing conveys who is working, on what, or how it is going without reading.
2. **Two job pages, neither complete.** `/jobs/:id` has the agent cards, banners, deliverables, QA tab and traces, but no transcript and no graph; `/jobs/:id/logs` has the graph and the Conversation, Structured and Raw views, but no banners, inputs or deliverables. They share only the hooks. The detail page does not link to the logs page at all (`JobDetailPage.tsx:124-169`).
3. **Two node renderers.** The editor's `AgentNode` (sigil sphere) and the live `LiveAgentNode` in `PipelineGraphView` share nothing, including palettes (`#1a1d2e`, `#0f1120`, `#1e2040` hard-coded in the live one). The party does not look the same when you design it and when it runs. `circularLayout.ts:31-35` also mis-detects the start and end sentinels (`node.type === "start"` against the real `startEndNode`), so they are laid out as ordinary nodes.
4. **Theme drift.** A second token set (`--embry0-*`, `index.css:215-236`), `PIPELINE_PHASES` in `lib/constants.ts`, hard-coded hex in Sidebar, TopBar, the editor and the graph view, a stale IBM Plex font load in `index.html:8-13` that nothing references, a broken `popup-in` keyframe reference (`AgentDetailPopup.tsx:315`) and `tailwindcss-animate` classes with no plugin (`TemplateDrawer.tsx:105`). Every page hand-rolls its own dialog and overlay because `components/ui/` has none.
5. **The API has rooms the UI never enters.** Merge approvals (three routes), watcher stacks (four), the hosts registry (seven), organization probe, settings, status and webhook, template duplicate and `default_for_kind` (returned and dropped by `api/pipelines.ts` `mapSummary`), the environment resolve preview, the QA image build, two thirds of `GET /stats`, the `verification_result` event and the `job_verifications` table (typed in `events.ts`, rendered nowhere), and `agent_sessions` (no route at all).

## 2. Goals and non-goals

**Goals**

- An **arcane RPG** register on the existing navy and gold sigil identity: dark only, a pixel display face for headings and HUD numerals, body and code faces unchanged.
- A **roster** of twelve pixel sprites, one per `avatar` id, drawn as SVG in code, tinted by the agent's `color`, animated by CSS per state. No image files, no runtime dependency.
- **One node renderer** for the pipeline editor and the live graph.
- **One job page** that is the live view: party, HUD, banners, graph, transcript, QA, verification, ledger.
- Every surface in problem 5 reachable from the dashboard with a plain, tested page.
- Theme consolidated to one token set and one identity source; the dead references removed.

**Non-goals**

- A light theme. Dark only, as decided.
- Sound, canvas, WebGL, a motion library, or any new runtime dependency. The one addition is a font from Google Fonts, which the public dashboard CSP already permits (`frontend/nginx/dashboard-public.conf.template:32`).
- Changing what jobs do. No backend behaviour changes beyond the two read routes in 3.6 and the decision path in 3.8.
- Restyling the About page or the divine layer's own glyphs. They stay as they are.
- Mobile-first redesign. The sidebar drawer from #47 stays; new pages use the same breakpoint grids as existing ones.

## 3. Design

### 3.1 Theme

- `frontend/src/index.css` keeps one `@theme`. It gains `--font-pixel: 'Pixelify Sans', monospace` and eight palette tokens `--color-palette-{gold,cyan,violet,rose,emerald,amber,sky,slate}` that `agentIdentity.ts` resolves ids to. The `--embry0-*` set, `.embry0-card`, the role and stage color tokens, `PIPELINE_PHASES` and `AGENT_STATUS_COLORS` in `lib/constants.ts` are deleted and their uses moved to the theme tokens and identity classes. `index.css:1` imports Outfit, JetBrains Mono and Pixelify Sans; `index.html` loses the IBM Plex load and keeps the two preconnects.
- The pixel face is used by exactly three classes: `.hud` (HUD numerals and labels: cost, turns, tokens, elapsed, attempt), `.title-pixel` (the `h1` of each page), and the sprite nameplate. Body copy, tables, forms and the transcript never use it; a test greps the rendered page for `font-pixel` outside those classes.
- `data-divine="off"` keeps its meaning (divine layer only). Sprites are a separate layer, see 3.2.
- React Flow overrides (`index.css:159-182`), Sidebar `#0c1015`, TopBar gradient and the editor's hard-coded hex move to tokens. The broken `popup-in` reference and the `animate-in` classes are removed with the components they belong to (3.9).

### 3.2 The roster

`frontend/src/components/roster/`:

- `sprites.ts`: the twelve avatars. Each is `Record<SpriteState, Frame[]>` where a `Frame` is sixteen strings of sixteen characters: `.` transparent, `o` outline, `f` fill, `h` highlight, `e` eye. A module-level pass compiles each frame into one `<path d>` per character class (run-length rectangles), so a rendered sprite is at most four paths per frame. States and frame counts: `idle` 2, `thinking` 2, `tool` 2, `done` 1, `failed` 1, `pending` 1. Twelve avatars × nine frames × 256 cells is about 28 KB of source, well under the chunk the lazy routes already load.
- `Sprite.tsx`: `<Sprite avatar color state size={16|32|48|64} label? />`. Renders `<svg viewBox="0 0 16 16" shape-rendering="crispEdges">`, `o` in a dark mix of the palette token (`color-mix(in srgb, token 30%, black)`, so the silhouette holds on every surface), `f` in the palette token, `h` in the token's lighter mix (the face and props), `e` in `--color-background` (dark eyes on the light face). The frames come from one old-school MMO avatar template (a tall build with a tunic, belt and boots; a short, bearded one for the dwarf and the golem) plus per-class headgear, armour, capes and the gear each class holds, lowered at rest and raised in the tool state, in `scripts/roster_sprites.py`, which rewrites the avatar blocks of `sprites.ts`. Two-frame states render both frames stacked and alternate them with one `@keyframes roster-step` using `steps(2)` on `opacity`, 1.2 s cycle, gated by `prefers-reduced-motion: no-preference`. Under reduced motion the first frame shows, static. `role="img"` with `aria-label` from the display name and state.
- `spriteState.ts`: `stateFor(agentState) -> SpriteState`: `pending` → `pending`; `running` whose newest event is `tool_call` without its `tool_result` → `tool`; other `running` → `thinking`; `completed` → `done`; `failed` → `failed`; the editor uses `idle`.
- `frontend/src/components/divine/CLAUDE.md` gains rule 9: *The roster is not the divine layer.* Sprites may appear on live cards and lists (rule 5 does not apply to them), must obey `prefers-reduced-motion`, step no faster than two frames per second, and never carry a second animation. One sprite per card or node. Rule 1's byte budget does not cover them; their own budget is 40 KB of source, tested.

### 3.3 Identity everywhere

`agentIdentity.ts` (workstream 2) is the only source. This workstream swaps its placeholder tile for `<Sprite>`:

- Agents list: sprite, display name, tagline, model, provider, skills on hover. Clicking a row opens the form; the form's avatar select becomes a sprite grid with live preview of the chosen color.
- Console `RunningCard`: a 32 px sprite in `thinking` or `tool` state beside the ticker; `QueuedCard` shows the template's first party member in `pending`; `DoneFailedCard` shows the last run's sprite in `done` or `failed`.
- Job page (3.5): the party strip and each run card.
- Sidebar, TopBar and nav stay lucide.

### 3.4 One node renderer and the party map

- `frontend/src/components/pipeline/PartyNode.tsx` replaces both `pipeline-editor/AgentNode.tsx` and `PipelineGraphView`'s `LiveAgentNode`. Props: the agent identity, `mode: "editor" | "live"`, and for live a `NodeLiveState`. It draws a 96 px tile: sprite (48 px) in the state's frame, a ring in the agent's palette color (dim when pending, solid when running, with a one-shot `scale-in` on completion), the display name on a pixel nameplate, and a HUD line (`attempt`, cost, turns in live mode; skill count and model in editor mode). The start and end sentinels keep the gold sphere; `circularLayout.ts` detects them by `data.nodeRole`, which fixes the mislayout.
- `PipelineGraphView` and `PipelineEditor` both register `agentNode: PartyNode`. The editor's `AgentBar` chips show the sprite in `idle`. `EmptyCanvas` keeps its vessel glyph.
- Edges keep their current styling; feedback edges keep the loop label.

### 3.5 One job page

`/jobs/:jobId` becomes the only job page. `/jobs/:jobId/logs` redirects to it (a `Navigate` route; links in `JobsTable` change). Layout, top to bottom:

1. **Header**: back, `OperationGlyph` as today, repo, status badge, job id, issue link, PR link.
2. **Party strip**: one 32 px sprite per agent run from `useAgentStates`, in run order, in its state, with the display name underneath; clicking one scrolls the transcript to that run and filters the graph highlight. Pending nodes from the `pipeline_graph` event appear in `pending`.
3. **HUD bar** (`.hud`): status, elapsed (live ticker), cost, turns, tokens in and out, budget meter when `max_budget_usd` is set (amber at 80 percent, red at 100).
4. **Banners**, as today: paused, awaiting input (fed by WebSocket `interrupt` and `agent_paused_for_input`, not only the poll), failed with `AgentFailuresCard`, expired, PR created, deliverables.
5. **Two panes** from the `lg` breakpoint, stacked below it:
   - **Left, the party map**: `PipelineGraphView` with `PartyNode`, click to filter.
   - **Right, the record**: `Tabs` Conversation, Structured, Raw as today, plus **QA** when the stream has a `qa_apps_resolved` event or `GET /jobs/{id}/qa/attempts` is non-empty (no longer keyed on `pipeline_template === "qa"`), and **Verification** (3.6) when a `verification_result` event or a `job_verifications` row exists.
6. **Ledger** (collapsed `<details>`): `CostBreakdownTable` and `TracesTable`, as today's Traces tab.
7. **Footer**: connection dot and event count.

`ConversationView` keeps its run dividers and pagination; each run divider gets the sprite and display name. The per-run agent card (expanded view with thinking, text and tool stream) stays as the Conversation tab's body for a selected run. Fixed `calc(100vh - 280px)` heights become a flex column that fills the pane.

### 3.6 Two read routes

- `GET /jobs/{job_id}/verification` (operator): the `job_verifications` row for the job (`tier`, `gate_verdict`, `blocking`, `browser_qa`, `human_merge_only`, `base_sha`, `head_sha`, `report`, `published_at`), 404 when none. The Verification tab shows the verdict, the tier, the gate table from `report`, and the browser QA line. `docs/verification.md` gains the route.
- `GET /jobs/{job_id}/agents/{agent_type}/session` (operator): the `agent_sessions.messages` for the job and agent, each message passed through `redact_event` with `known_secret_values()`, 404 when no row, 204 when the row holds only a `session_blob` (CLI mode, not exposed). The Conversation tab offers "Full transcript" on a completed run whose events carry `truncated: true`; it opens a drawer with the untruncated messages. `docs/api.md` gains both routes.

### 3.7 Surfaces

Each is a page or section with a hook over the existing route, a loading skeleton, an error state through `PageError`, and render tests. Tier gates match the route's own.

| Surface | Where | Reads | Writes |
|---|---|---|---|
| Merge approvals | `/merge-approvals`, Work group | `GET /merge-approvals?status=`, `GET /merge-approvals/{id}` | `POST /merge-approvals/{id}/decide` (3.8) |
| Template badges and actions | `/pipelines` cards | `is_builtin`, `default_for_kind` kept by `mapSummary` | `POST /pipelines/templates/{id}/duplicate` (name prompt in a Dialog), `PUT` with `default_for_kind` ("Default for code / research / analysis / ops") |
| Deep stats | `/` below the tiles | `GET /stats`: `failure_categories`, `cost_by_repo`, `tokens_by_agent`, `top_expensive_issues`, `queue_depth`, `qa_prebuilt_images`, `credentials` | — |
| Watcher stacks | `/watcher`, "Stacks" tab (admin) | `GET /watcher/stacks`, `GET /watcher/stacks/{owner}/{name}` | `PUT` (logql, tracker, tracker_target, settings, enabled), `DELETE` |
| Environment resolve | `RepoDetailPage` env section, "Effective for this repo" toggle | `GET /repos/{repo}/environment/resolve` (masked) | — |
| QA image build | `QaRepoCachePage` and the repo QA section | existing cache analytics | `POST /qa/images/build` `{repo, branch, force}`, result toast with the tag |
| Job verification | job page tab (3.5) | `GET /jobs/{id}/verification` (3.6) | — |
| Hosts | `/hosts`, Infra group | `GET /hosts?source=&include_removed=`, `GET /hosts/{id}` | `POST /hosts` (name, address, transport, roles, log source, tags, repos), `PATCH /hosts/{id}`, `DELETE`, `POST /hosts/{id}/probe`, `POST /hosts/import/tailnet` with an explicit confirm Dialog (admin) |
| Organization actions | `OrganizationPage` header and a "Settings" tab | `GET /orgs/{owner}/webhook` | `POST /orgs/{owner}/probe`, `PATCH /orgs/{owner}` (display name, settings), `PUT /orgs/{owner}/status` (active, paused, removed; removed behind a confirm Dialog), `POST /orgs/{owner}/webhook` (admin) |

The breadcrumb maps in `TopBar.tsx` and the nav in `Sidebar.tsx` gain the two new paths.

### 3.8 Merge decisions from the dashboard

`MergeApprovalService.decide` accepts a decision only from the configured approval chat (`services/merge_approvals.py:556-559`). The dashboard is a second legitimate channel. `decide` gains `via: Literal["chat", "dashboard"]` (default `"chat"`, unchanged behaviour); for `"dashboard"` the chat check is skipped, `decided_by` is the session user's email or the API key's name from `request_actor`, and the stored `chat_id` is the literal `dashboard`. The route gains a sibling `POST /merge-approvals/{id}/decide` body form `{decision}` for admin sessions that sets `via="dashboard"` server-side; the relay keeps its `DecideBody`. The audit row gains `via`. The repo allowlist check stays.

### 3.9 Primitives

`components/ui/` gains `Dialog` (focus trap, Escape, body scroll lock, portal), `Menu` (button-anchored popover list), `Switch`, `Meter` (the budget and progress bar), `Kbd`, and `Sprite` moves in from the roster folder as a re-export. The hand-rolled overlays in the components this workstream already touches (`AgentDetailPopup`, `EdgeDetailPopup`, `TemplateDrawer`, `TemplatePicker`, `CreateJobDialog`) move onto `Dialog` and `Menu`; `CredentialModal` and the others are left alone.

### 3.10 Error handling and degraded states

- A sprite id the frontend does not know (a newer backend) renders `golem`; an unknown color renders `slate`. Both are tested.
- A job with no `pipeline_graph` event (legacy rows) shows the party strip from runs alone and hides the map pane.
- The verification and session routes 404 → the tab is hidden, not errored.
- `prefers-reduced-motion`: every sprite static, the ring's completion flash disabled, the divine layer already handles itself.
- Phone width: the party strip scrolls horizontally, panes stack, the HUD wraps to two rows.

## 4. Testing

- `sprites.test.ts`: every avatar has every state; every frame is 16 × 16 over the five characters; compiled paths are non-empty; total source under 40 KB.
- `Sprite` render tests: state frames, reduced motion, unknown ids.
- `PartyNode` tests in both modes from the job-event fixtures; `circularLayout` sentinel test.
- Job page: redirect from `/logs`, party strip order, HUD values from the fixtures (`tests/fixtures/job-events/*.json`), QA tab keyed on events, verification tab from a mocked row, transcript drawer on `truncated`.
- One render test per surface with the API mocked at the module boundary, including the admin-only gating and each confirm Dialog.
- `userFacingText.test.ts` extended with the pixel-class rule from 3.1.
- Contract test (`events.contract.test.ts`) unchanged and still green; `identity.json` test from workstream 2 still green.
- Backend: route tests for 3.6 (404, 204, redaction of a planted token) and 3.8 (`via="dashboard"` skips the chat check, audit carries `via`, `via="chat"` unchanged).
- Manual: the job page watched live on a deployment through the SSH tunnel recipe, one running job and one legacy job, screenshots in the PR.

## 5. Deploy notes

Frontend image (rebuilt by `infra/deploy.sh`). Orchestrator image for the two read routes and the decision path. No migration, no sandbox image rebuild, no compose change. The public dashboard CSP needs no edit: Google Fonts is already allowed for styles and fonts.

## 6. Issue breakdown (milestone "Game UI")

| Issue | Scope |
|---|---|
| H | Theme consolidation: tokens, pixel face, dead token sets and references removed, `index.html` fonts |
| I | Roster: `sprites.ts`, `Sprite`, `spriteState`, rule 9, budget test |
| J | Identity everywhere: agents list and form, console cards |
| K | `PartyNode`, both graphs on it, sentinel fix, `AgentBar` chips |
| L | One job page: layout, party strip, HUD, redirect, QA keyed on events, ledger |
| M | Backend: verification and session routes, redaction, docs |
| N | Verification tab and transcript drawer |
| O | Primitives: Dialog, Menu, Switch, Meter, Kbd; the five touched overlays moved |
| P | Surfaces: merge approvals (with 3.8 backend), templates, deep stats, watcher stacks, environment resolve, QA image build |
| Q | Surfaces: hosts page, organization actions |

H carries this spec. I depends on H and on workstream 2's identity module; J and K depend on I; L depends on K; M is independent; N depends on L and M; O depends on H; P and Q depend on O. Three plans: H to K (theme and roster), L to N (the job page), O to Q (primitives and surfaces).
